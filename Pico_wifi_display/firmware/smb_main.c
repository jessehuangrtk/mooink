/* Independent read-only SMB/Wi-Fi probe; no USB Host writes or BT. */
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <errno.h>
#include <inttypes.h>
#include "pico/stdlib.h"
#include "pico/cyw43_arch.h"
#include "pico/sha256.h"
#include "FreeRTOS.h"
#include "task.h"
#include "smb2/smb2.h"
#include "smb2/libsmb2.h"
#include "smb_snapshot.h"
#include "debug_log.h"
#include "config.local.h"

#define SOURCE "mooink_display.png"
static uint8_t block[4096];
static display_seen seen;
static display_debug_log debug_log;
static void event(const char *message){
    puts(message);
    display_log_event(&debug_log,to_ms_since_boot(get_absolute_time()),message);
}
/* One fixed dedicated file. The source PNG is always opened read-only.
 * A snapshot is bounded; failed writes retain RAM history for next poll.
 * Never truncate an existing file until its type and identity are checked. */
static bool log_file_ok(const struct smb2_stat_64 *st){
    return st->smb2_type==SMB2_TYPE_FILE&&st->smb2_nlink==1&&
           !(st->smb2_attributes&0x400u)&&!st->smb2_reparse_tag;
}
static bool flush_log(struct smb2_context *ctx){
    if(!debug_log.enabled||!debug_log.dirty)return true;
    struct smb2_stat_64 before,opened;
    struct smb2fh *fh=NULL;
    int rc=smb2_stat(ctx,"pico_log.txt",&before);
    if(rc==0){
        if(!log_file_ok(&before))return false;
        fh=smb2_open(ctx,"pico_log.txt",O_WRONLY);
    }else if(rc==-ENOENT){
        fh=smb2_open(ctx,"pico_log.txt",O_WRONLY|O_CREAT|O_EXCL);
    }else return false;
    if(!fh)return false;
    bool ok=false;
    if(smb2_fstat(ctx,fh,&opened)<0||!log_file_ok(&opened)||
       (rc==0&&(before.smb2_ino!=opened.smb2_ino)))goto done;
    char header[128];
    int len=snprintf(header,sizeof(header),"Pico_wifi_display | uptime ms | dropped RAM entries: %lu\n",
                     (unsigned long)debug_log.dropped);
    if(len<=0||(size_t)len>=sizeof(header))goto done;
    /* Check short writes. Use explicit offsets, not append semantics. */
    if(smb2_pwrite(ctx,fh,(const uint8_t *)header,(uint32_t)len,0)!=len)goto done;
    for(size_t offset=0;offset<debug_log.used;){
        size_t n=debug_log.used-offset;if(n>1024)n=1024;
        int wrote=smb2_pwrite(ctx,fh,(const uint8_t *)debug_log.text+offset,(uint32_t)n,(uint64_t)len+offset);
        if(wrote<=0||(size_t)wrote>n)goto done;
        offset+=(size_t)wrote;
    }
    if(smb2_ftruncate(ctx,fh,(uint64_t)len+debug_log.used)<0)goto done;
    if(smb2_fsync(ctx,fh)<0)goto done;
    ok=true;
done:
    if(smb2_close(ctx,fh)<0)ok=false;
    if(ok)debug_log.dirty=false;
    return ok;
}
static display_version version(const struct smb2_stat_64 *s){
    return (display_version){s->smb2_ino,s->smb2_size,s->smb2_mtime,s->smb2_mtime_nsec,
                             s->smb2_ctime,s->smb2_ctime_nsec,s->smb2_type};
}
static bool valid_file(const struct smb2_stat_64 *s){
    return s->smb2_type==SMB2_TYPE_FILE&&s->smb2_size>=33&&s->smb2_size<=DISPLAY_MAX_IMAGE;
}
static bool same(const struct smb2_stat_64 *a,const struct smb2_stat_64 *b){
    display_version x=version(a),y=version(b);return display_version_equal(&x,&y);
}
static bool probe(void){
    struct smb2_context *ctx=smb2_init_context();
    if(!ctx){event("SMB: context allocation failed");return false;}
    bool connected=false,hashing=false,ok=false;
    struct smb2fh *fh=NULL;
    struct smb2_stat_64 a,b,opened,after;
    pico_sha256_state_t sha;
    sha256_result_t digest;
    uint64_t total=0;
    uint8_t header[33];size_t header_n=0;
    const char *stage="authentication/connect";
    smb2_set_timeout(ctx,15);
    smb2_set_version(ctx,SMB2_VERSION_ANY);
    smb2_set_authentication(ctx,SMB2_SEC_NTLMSSP);
    smb2_set_security_mode(ctx,SMB2_NEGOTIATE_SIGNING_ENABLED|SMB2_NEGOTIATE_SIGNING_REQUIRED);
    smb2_set_sign(ctx,1);
    if(DISPLAY_REQUIRE_SEAL)smb2_set_seal(ctx,1);
    smb2_set_domain(ctx,DISPLAY_SMB_DOMAIN);
    smb2_set_user(ctx,DISPLAY_SMB_USER);
    smb2_set_password(ctx,DISPLAY_SMB_PASSWORD);
    if(smb2_connect_share(ctx,DISPLAY_SMB_SERVER,DISPLAY_SMB_SHARE,DISPLAY_SMB_USER)<0)goto finish;
    connected=true;
    event("SMB authenticated; checking source");
    stage="stat/stability";
    if(smb2_stat(ctx,SOURCE,&a)<0||!valid_file(&a))goto finish;
    vTaskDelay(pdMS_TO_TICKS(1000));
    if(smb2_stat(ctx,SOURCE,&b)<0||!same(&a,&b))goto finish;
    stage="open/fstat";
    fh=smb2_open(ctx,SOURCE,O_RDONLY);
    if(!fh||smb2_fstat(ctx,fh,&opened)<0||!same(&b,&opened))goto finish;
    stage="SHA256";
    if(pico_sha256_try_start(&sha,SHA256_BIG_ENDIAN,false)!=PICO_OK)goto finish;
    hashing=true;
    stage="read";
    while(total<opened.smb2_size){
        uint64_t left=opened.smb2_size-total;
        uint32_t want=left<sizeof(block)?(uint32_t)left:sizeof(block);
        int n=smb2_read(ctx,fh,block,want);
        if(n<=0||(uint32_t)n>want)goto finish;
        if(header_n<sizeof(header)){
            size_t copy=sizeof(header)-header_n;if(copy>(size_t)n)copy=(size_t)n;
            memcpy(header+header_n,block,copy);header_n+=copy;
        }
        pico_sha256_update_blocking(&sha,block,(size_t)n);total+=(uint32_t)n;
    }
    if(smb2_read(ctx,fh,block,1)!=0)goto finish;
    stage="PNG header";
    if(!display_png_header(header,header_n,DISPLAY_WIDTH,DISPLAY_HEIGHT))goto finish;
    stage="post-read version";
    if(smb2_fstat(ctx,fh,&after)<0||!same(&opened,&after))goto finish;
    if(smb2_close(ctx,fh)<0){fh=NULL;goto finish;}
    fh=NULL;
    if(smb2_stat(ctx,SOURCE,&after)<0||!same(&opened,&after))goto finish;
    pico_sha256_finish(&sha,&digest);hashing=false;
    char message[160],hex[65];
    static const char digits[]="0123456789abcdef";
    for(unsigned i=0;i<32;i++){hex[2*i]=digits[digest.bytes[i]>>4];hex[2*i+1]=digits[digest.bytes[i]&15];}
    hex[64]=0;
    snprintf(message,sizeof(message),"SMB %s bytes=%lu SHA256=%s (source observation only)",
             display_observe(&seen,digest.bytes)?"CHANGED":"UNCHANGED",(unsigned long)total,hex);
    event(message);ok=true;
finish:
    if(hashing)pico_sha256_cleanup(&sha);
    if(fh)smb2_close(ctx,fh);
    if(!ok){
        char message[128];
        snprintf(message,sizeof(message),"SMB source probe failed: %s; image untouched",stage);
        event(message);
    }
    if(connected){
        char memory[120];
        snprintf(memory,sizeof(memory),"FreeRTOS free heap=%lu stack watermark=%lu words",
                 (unsigned long)xPortGetFreeHeapSize(),(unsigned long)uxTaskGetStackHighWaterMark(NULL));
        event(memory);
        if(!flush_log(ctx))event("Debug log write failed; RAM history retained");
        smb2_disconnect_share(ctx);
    }
    smb2_destroy_context(ctx);
    return ok;
}
static void worker(void *arg){
    (void)arg;
    display_log_init(&debug_log,DISPLAY_DEBUG_MODE!=0);
    event(DISPLAY_DEBUG_MODE?"Boot: debug mode ON":"Boot: debug mode OFF");
    if(cyw43_arch_init()){event("Wi-Fi init failed");vTaskDelete(NULL);return;}
    cyw43_arch_enable_sta_mode();
    for(;;){
        if(cyw43_tcpip_link_status(&cyw43_state,CYW43_ITF_STA)!=CYW43_LINK_UP){
            event("Wi-Fi connecting");
            if(cyw43_arch_wifi_connect_timeout_ms(DISPLAY_WIFI_SSID,DISPLAY_WIFI_PASSWORD,CYW43_AUTH_WPA2_AES_PSK,30000)){
                event("Wi-Fi failed; retry in 10 seconds");vTaskDelay(pdMS_TO_TICKS(10000));continue;
            }
            event("Wi-Fi connected");
        }
        probe();
        vTaskDelay(pdMS_TO_TICKS(DISPLAY_POLL_SECONDS*1000u));
    }
}
int main(void){
    stdio_init_all();
    if(xTaskCreate(worker,"SMB probe",8192,NULL,2,NULL)!=pdPASS){puts("Task allocation failed");return 1;}
    vTaskStartScheduler();return 1;
}
