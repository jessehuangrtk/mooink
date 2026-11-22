/* Stage 1: one native USB host, read-only MTP probe. No Wi-Fi/BT yet. */
#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "tusb.h"
#include "host/usbh_pvt.h"
#include "mtp_codec.h"
#include "mtp_stream.h"
#include "pico/sha256.h"

#define LIMIT 16384
static uint8_t addr, itf, ep_in, ep_out;
static bool configured, mounted, failed;
static uint32_t tid, deadline, total_deadline;
static mtp_capabilities caps;
static bool probe_complete;
static uint16_t operation;
static enum {IDLE, TX, RX_DATA, RX_RESPONSE} state;
static enum {PROBE_INFO, PROBE_SESSION, PROBE_STORES, PROBE_STORAGE, PROBE_ROOT, PROBE_OBJECT,
             PROBE_CHILDREN, PROBE_CHILD, PROBE_HASH, PROBE_CLOSE, PROBE_DONE} step;
static uint8_t tx[32] __attribute__((aligned(4)));
static uint8_t rx[512] __attribute__((aligned(4)));
static uint8_t payload[LIMIT];
static size_t used, tx_size;
static mtp_stream stream;
static pico_sha256_state_t hash;
static bool hash_active;
static uint32_t image_handle,image_size;
static unsigned image_matches;
static uint32_t stores[16], handles[512], sleep_handle, sleep_store;
static size_t store_n, store_i, handle_n, handle_i;
static unsigned sleep_matches;
static uint32_t now_ms(void){return to_ms_since_boot(get_absolute_time());}
static void hash_cleanup(void){if(hash_active){pico_sha256_cleanup(&hash);hash_active=false;}}
static void fail(const char *message){hash_cleanup();printf("ERROR %s; unplug/replug mooInk to retry\n",message);failed=true;state=IDLE;}
static bool consume(const uint8_t *p,size_t n,void *ctx){
    (void)ctx;
    if(operation==MTP_GET_OBJECT){
        if(!hash_active||n>image_size-used)return false;
        pico_sha256_update_blocking(&hash,p,n);used+=n;
    }else{
        if(n>LIMIT-used)return false;
        memcpy(payload+used,p,n);used+=n;
    }
    return true;
}
static bool data_operation(void){return operation!=MTP_OPEN&&operation!=MTP_CLOSE;}
static bool queue_in(void){
    if(!usbh_edpt_xfer(addr,ep_in,rx,sizeof(rx))){fail("bulk IN submit");return false;}
    return true;
}
static void start(uint16_t op,const uint32_t *params,size_t n){
    operation=op;used=0;
    if(op!=MTP_DEVICE_INFO&&op!=MTP_OPEN&&tid==UINT32_MAX){fail("transaction ID exhausted; reconnect");return;}
    uint32_t id=op==MTP_DEVICE_INFO||op==MTP_OPEN?0:++tid;
    mtp_stream_init(&stream,op,id,data_operation(),op==MTP_GET_OBJECT?image_size:LIMIT,consume,NULL);
    tx_size=mtp_command(tx,sizeof(tx),op,id,params,n);
    if(!tx_size){fail("invalid command");return;}
    state=TX;deadline=now_ms()+5000;total_deadline=now_ms()+120000;
    if(!usbh_edpt_xfer(addr,ep_out,tx,(uint16_t)tx_size))fail("bulk OUT submit");
}
static void storage(void){
    step=PROBE_STORAGE;start(MTP_STORAGE_INFO,&stores[store_i],1);
}
static void root(void){
    uint32_t args[]={stores[store_i],0,0xffffffffu};
    step=PROBE_ROOT;start(MTP_HANDLES,args,3);
}
static void next_root(void){
    if(handle_i<handle_n){step=PROBE_OBJECT;start(MTP_OBJECT_INFO,&handles[handle_i],1);return;}
    if(++store_i<store_n){storage();return;}
    if(sleep_matches!=1){printf("sleep folder matches=%u\n",sleep_matches);step=PROBE_CLOSE;start(MTP_CLOSE,NULL,0);return;}
    printf("sleep folder handle=%08lx storage=%08lx\n",(unsigned long)sleep_handle,(unsigned long)sleep_store);
    uint32_t args[]={sleep_store,0,sleep_handle};
    step=PROBE_CHILDREN;start(MTP_HANDLES,args,3);
}
static void hash_image_or_close(void){
    if(!image_matches){
        puts("Target image absent; no image hash available");
        step=PROBE_CLOSE;start(MTP_CLOSE,NULL,0);return;
    }
    if(image_matches!=1||!image_size||image_size>16u*1024u*1024u){
        fail("target image ambiguous or invalid size");return;
    }
    if(pico_sha256_try_start(&hash,SHA256_BIG_ENDIAN,false)!=PICO_OK){fail("SHA256 hardware unavailable");return;}
    hash_active=true;step=PROBE_HASH;start(MTP_GET_OBJECT,&image_handle,1);
}
static void completed(void){
    uint32_t one=1;
    mtp_object obj;char name[256];
    switch(step){
    case PROBE_INFO:
        if(!mtp_device_info(payload,used,&caps)){fail("malformed DeviceInfo");return;}
        const uint16_t required[]={MTP_OPEN,MTP_CLOSE,MTP_STORAGE_IDS,MTP_STORAGE_INFO,MTP_HANDLES,MTP_OBJECT_INFO,MTP_GET_OBJECT};
        for(size_t i=0;i<sizeof(required)/sizeof(required[0]);i++)if(!mtp_supports(&caps,required[i])){
            printf("Missing advertised operation %04x\n",required[i]);fail("read-only operation unsupported");return;
        }
        printf("GetDeviceInfo OK, events=%u formats=%u PNG=%u write-opcodes=%u\n",
               (unsigned)caps.event_count,(unsigned)caps.format_count,(unsigned)caps.png,
               (unsigned)(mtp_supports(&caps,0x100b)&&mtp_supports(&caps,0x100c)&&mtp_supports(&caps,0x100d)));
        step=PROBE_SESSION;start(MTP_OPEN,&one,1);break;
    case PROBE_SESSION:
        puts("OpenSession OK");step=PROBE_STORES;start(MTP_STORAGE_IDS,NULL,0);break;
    case PROBE_STORES:
        if(!mtp_array32(payload,used,stores,16,&store_n)||!store_n||!mtp_unique_ids(stores,store_n)){fail("storage list invalid, duplicate or empty");return;}
        printf("storage count=%u\n",(unsigned)store_n);store_i=0;storage();break;
    case PROBE_STORAGE:{
        mtp_storage info;
        if(!mtp_storage_info(payload,used,&info)){fail("malformed StorageInfo");return;}
        printf("Storage %08lx access=%u capacity=%llu free=%llu bytes\n",(unsigned long)stores[store_i],
               info.access,(unsigned long long)info.capacity,(unsigned long long)info.free_bytes);
        root();break;
    }
    case PROBE_ROOT:
        if(!mtp_array32(payload,used,handles,512,&handle_n)||!mtp_unique_ids(handles,handle_n)){fail("root handle list exceeds limit or malformed");return;}
        handle_i=0;next_root();break;
    case PROBE_OBJECT:
        if(!mtp_object_info(payload,used,&obj)||obj.storage!=stores[store_i]||
           (obj.parent!=0&&obj.parent!=UINT32_MAX)){fail("invalid root ObjectInfo identity");return;}
        mtp_name_display(&obj,name,sizeof(name));
        printf("ROOT %08lx %04x %s\n",(unsigned long)handles[handle_i],obj.format,name);
        if(mtp_name_is(&obj,"sleep",true)){
            if(!mtp_name_is(&obj,"sleep",false)||obj.format!=MTP_FORMAT_ASSOCIATION||
               obj.association!=1||++sleep_matches!=1){fail("ambiguous sleep folder");return;}
            sleep_handle=handles[handle_i];sleep_store=stores[store_i];
        }
        ++handle_i;next_root();break;
    case PROBE_CHILDREN:
        if(!mtp_array32(payload,used,handles,512,&handle_n)||!mtp_unique_ids(handles,handle_n)){fail("sleep handle list exceeds limit or malformed");return;}
        printf("sleep objects=%u\n",(unsigned)handle_n);handle_i=0;image_matches=0;image_size=0;
        if(handle_n){step=PROBE_CHILD;start(MTP_OBJECT_INFO,&handles[0],1);}
        else hash_image_or_close();break;
    case PROBE_CHILD:
        if(!mtp_object_info(payload,used,&obj)||obj.storage!=sleep_store||
           obj.parent!=sleep_handle){fail("invalid sleep ObjectInfo identity");return;}
        mtp_name_display(&obj,name,sizeof(name));
        printf("SLEEP %08lx %04x %s\n",(unsigned long)handles[handle_i],obj.format,name);
        if(mtp_name_is(&obj,"mooink_display.png",true)){
            if(!mtp_name_is(&obj,"mooink_display.png",false)||++image_matches!=1||
               (obj.format!=MTP_FORMAT_PNG&&obj.format!=MTP_FORMAT_UNDEFINED)||
               obj.association!=0||obj.protection!=0){fail("ambiguous or protected target image");return;}
            image_handle=handles[handle_i];image_size=obj.size;
        }
        if(++handle_i<handle_n)start(MTP_OBJECT_INFO,&handles[handle_i],1);
        else hash_image_or_close();break;
    case PROBE_HASH:{
        if(used!=image_size||stream.total!=image_size||!hash_active){fail("image size mismatch");return;}
        sha256_result_t result;
        pico_sha256_finish(&hash,&result);hash_active=false;
        printf("mooink_display.png SHA256=");
        for(unsigned i=0;i<SHA256_RESULT_BYTES;i++)printf("%02x",result.bytes[i]);
        printf(" bytes=%lu\n",(unsigned long)used);
        probe_complete=true;
        step=PROBE_CLOSE;start(MTP_CLOSE,NULL,0);break;
    }
    case PROBE_CLOSE:
        step=PROBE_DONE;puts(probe_complete?"READ-ONLY PROBE COMPLETE: target hash verified":"READ-ONLY PROBE INCOMPLETE: target not hashed");break;
    default:fail("unexpected step");break;
    }
}
static bool init(void){return true;}
static bool deinit(void){return true;}
static bool open_driver(uint8_t rhport,uint8_t d,tusb_desc_interface_t const * desc,uint16_t len){
    (void)rhport;
    if(!desc||len<sizeof(*desc)||addr||desc->bAlternateSetting||desc->bInterfaceClass!=6||desc->bInterfaceSubClass!=1||desc->bInterfaceProtocol!=1)return false;
    const uint8_t *p=(const uint8_t*)desc;
    if(len<sizeof(*desc)||p[0]<sizeof(*desc)||p[0]>len)return false;
    const tusb_desc_endpoint_t *in=NULL,*out=NULL;
    size_t offset=p[0];
    while(offset+2<=len){
        const uint8_t *q=p+offset;
        if(q[0]<2||offset+q[0]>len)return false;
        if(q[1]==TUSB_DESC_INTERFACE)break;
        if(q[1]==TUSB_DESC_ENDPOINT&&q[0]>=7&&(q[3]&3)==2){
            if(q[2]&0x80){if(in)return false;in=(const tusb_desc_endpoint_t*)q;}
            else {if(out)return false;out=(const tusb_desc_endpoint_t*)q;}
        }
        offset+=q[0];
    }
    if(!in||!out)return false;
    if((in->bEndpointAddress&0x0f)==0||(out->bEndpointAddress&0x0f)==0||
       mtp_u16((const uint8_t*)in+4)!=64||mtp_u16((const uint8_t*)out+4)!=64)return false;
    if(!tuh_edpt_open(d,in)||!tuh_edpt_open(d,out))return false;
    addr=d;itf=desc->bInterfaceNumber;ep_in=in->bEndpointAddress;ep_out=out->bEndpointAddress;
    printf("MTP interface=%u IN=%02x OUT=%02x\n",itf,ep_in,ep_out);return true;
}
static bool set_config(uint8_t d,uint8_t i){
    if(d!=addr||i!=itf)return false;
    configured=true;usbh_driver_set_config_complete(d,i);return true;
}
static void close_driver(uint8_t d){
    if(d==addr){hash_cleanup();addr=0;configured=mounted=failed=false;state=IDLE;puts("MTP detached");}
}
static bool xfer(uint8_t d,uint8_t ep,xfer_result_t result,uint32_t n){
    if(d!=addr||failed)return true;
    if(result!=XFER_RESULT_SUCCESS){fail("USB transfer failed");return true;}
    if(state==TX&&ep==ep_out&&n==tx_size){state=RX_DATA;deadline=now_ms()+5000;queue_in();return true;}
    if((state!=RX_DATA&&state!=RX_RESPONSE)||ep!=ep_in||n>sizeof(rx)){fail("unexpected transfer");return true;}
    if(n)deadline=now_ms()+5000;
    if(!mtp_stream_feed(&stream,rx,n)){fail("invalid MTP container, size or transaction");return true;}
    if(stream.done){
        if(stream.response!=MTP_OK){printf("MTP op=%04x response=%04x %s\n",operation,stream.response,mtp_response_name(stream.response));fail("MTP rejected operation");return true;}
        state=IDLE;completed();return true;
    }
    queue_in();return true;
}
static const usbh_class_driver_t driver={.name="MTP probe",.init=init,.deinit=deinit,.open=open_driver,.set_config=set_config,.xfer_cb=xfer,.close=close_driver};
usbh_class_driver_t const *usbh_app_driver_get_cb(uint8_t *count){*count=1;return &driver;}
void tuh_mount_cb(uint8_t d){
    uint16_t v=0,p=0;tuh_vid_pid_get(d,&v,&p);
    printf("USB mounted VID=%04x PID=%04x\n",v,p);
    if(d!=addr||!configured){puts("No supported MTP interface");return;}
    if(v!=0x1f85||p!=0x4e16){fail("USB identity differs from tested mooInk");return;}
    mounted=true;tid=0;sleep_matches=0;probe_complete=false;step=PROBE_INFO;start(MTP_DEVICE_INFO,NULL,0);
}
void tuh_umount_cb(uint8_t d){(void)d;puts("USB unmounted");}
int main(void){
    stdio_init_all();sleep_ms(500);
    puts("Pico_wifi_display stage 1: native USB MTP read-only");
    if(!tuh_init(0)){puts("USB host init failed");return 1;}
    while(true){
        tuh_task_ext(0,false);
        if(mounted&&!failed&&state!=IDLE&&((int32_t)(now_ms()-deadline)>=0||(int32_t)(now_ms()-total_deadline)>=0))fail("MTP transfer timeout");
        sleep_ms(1);
    }
}
