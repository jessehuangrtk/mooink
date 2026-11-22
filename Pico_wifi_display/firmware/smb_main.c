/* Independent read-only SMB/Wi-Fi probe; no USB Host writes or BT. */
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include "pico/stdlib.h"
#include "pico/cyw43_arch.h"
#include "pico/sha256.h"
#include "FreeRTOS.h"
#include "task.h"
#include "smb2/smb2.h"
#include "smb2/libsmb2.h"
#include "smb_snapshot.h"
#include "config.local.h"

#define SOURCE "mooink_display.png"
static uint8_t block[4096];
static display_seen seen;
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
    if(!ctx){puts("SMB: context allocation failed");return false;}
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
    printf("SMB %s bytes=%lu SHA256=",display_observe(&seen,digest.bytes)?"CHANGED":"UNCHANGED",(unsigned long)total);
    for(unsigned i=0;i<32;i++)printf("%02x",digest.bytes[i]);
    puts(" (source observation only)");ok=true;
finish:
    if(hashing)pico_sha256_cleanup(&sha);
    if(fh)smb2_close(ctx,fh);
    if(connected)smb2_disconnect_share(ctx);
    smb2_destroy_context(ctx);
    if(!ok)printf("SMB probe failed: %s; no files modified\n",stage);
    return ok;
}
static void worker(void *arg){
    (void)arg;
    if(cyw43_arch_init()){puts("Wi-Fi init failed");vTaskDelete(NULL);return;}
    cyw43_arch_enable_sta_mode();
    for(;;){
        if(cyw43_tcpip_link_status(&cyw43_state,CYW43_ITF_STA)!=CYW43_LINK_UP){
            puts("Wi-Fi connecting");
            if(cyw43_arch_wifi_connect_timeout_ms(DISPLAY_WIFI_SSID,DISPLAY_WIFI_PASSWORD,CYW43_AUTH_WPA2_AES_PSK,30000)){
                puts("Wi-Fi failed; retry in 10 seconds");vTaskDelay(pdMS_TO_TICKS(10000));continue;
            }
            puts("Wi-Fi connected");
        }
        probe();
        printf("FreeRTOS free heap=%lu stack watermark=%lu words\n",
               (unsigned long)xPortGetFreeHeapSize(),(unsigned long)uxTaskGetStackHighWaterMark(NULL));
        vTaskDelay(pdMS_TO_TICKS(DISPLAY_POLL_SECONDS*1000u));
    }
}
int main(void){
    stdio_init_all();
    if(xTaskCreate(worker,"SMB probe",8192,NULL,2,NULL)!=pdPASS){puts("Task allocation failed");return 1;}
    vTaskStartScheduler();return 1;
}
