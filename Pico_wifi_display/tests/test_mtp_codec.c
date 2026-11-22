#include <assert.h>
#include <string.h>
#include "mtp_codec.h"
int main(void){
    uint8_t b[80]={0};uint32_t p[]={1,0,0xffffffffu};
    assert(mtp_command(b,sizeof b,MTP_HANDLES,2,p,3)==24);
    mtp_header h;assert(mtp_decode_header(b,24,&h)&&h.length==24&&h.code==MTP_HANDLES&&h.transaction==2);
    assert(!mtp_command(b,sizeof b,MTP_HANDLES,2,p,2));
    assert(!mtp_command(b,sizeof b,MTP_HANDLES,0,p,3));
    assert(!mtp_command(b,sizeof b,MTP_GET_OBJECT,1,p+1,1));
    assert(!mtp_command(b,sizeof b,MTP_OPEN,1,p,1));
    assert(mtp_command(b,sizeof b,MTP_OPEN,0,p,1)==16);
    assert(mtp_command(b,sizeof b,MTP_STORAGE_INFO,1,p,1)==16);
    assert(!mtp_command(b,sizeof b,0x100b,2,p,1));
    assert(!mtp_command(b,sizeof b,0x100d,2,NULL,0));
    uint8_t arr[]={2,0,0,0,1,0,0,0,2,0,0,0};
    uint32_t out[2];size_t n;
    assert(mtp_array32(arr,sizeof arr,out,2,&n)&&n==2&&out[1]==2&&mtp_unique_ids(out,n));
    out[1]=out[0];assert(!mtp_unique_ids(out,n));
    out[1]=0;assert(!mtp_unique_ids(out,n));
    assert(!mtp_array32(arr,8,out,2,&n));assert(!mtp_array32(arr,sizeof arr,out,1,&n));
    memset(b,0,sizeof b);b[0]=1;b[4]=1;b[5]=0x30;b[42]=1;b[52]=6;
    const char *s="sleep";for(size_t i=0;i<5;i++)b[53+2*i]=(uint8_t)s[i];
    mtp_object obj;char name[16];
    assert(mtp_object_info(b,68,&obj)&&obj.format==MTP_FORMAT_ASSOCIATION&&obj.storage==1&&obj.association==1);
    assert(mtp_name_is(&obj,"sleep",false)&&mtp_name_is(&obj,"SLEEP",true)&&!mtp_name_is(&obj,"SLEEP",false));
    mtp_name_display(&obj,name,sizeof name);assert(!strcmp(name,"sleep"));
    b[53]='S';assert(mtp_object_info(b,68,&obj)&&!mtp_name_is(&obj,"sleep",false)&&mtp_name_is(&obj,"sleep",true));
    b[54]=0xd8;assert(!mtp_object_info(b,68,&obj)); /* invalid surrogate */
    b[54]=1;assert(mtp_object_info(b,68,&obj)&&!mtp_name_is(&obj,"sleep",true));
    mtp_name_display(&obj,name,sizeof name);assert(!strcmp(name,"?leep"));
    assert(!mtp_object_info(b,56,&obj));assert(!mtp_object_info(b,67,&obj));
    uint8_t storage[28]={0};storage[0]=3;storage[6]=100;storage[14]=30;
    mtp_storage si;assert(mtp_storage_info(storage,sizeof storage,&si)&&si.capacity==100&&si.free_bytes==30);
    storage[4]=3;assert(!mtp_storage_info(storage,sizeof storage,&si));
    const uint8_t info[]={
        100,0,0,0,0,0,0,0,0,0,0, /* fixed header, empty vendor description, mode */
        2,0,0,0,9,0x10,0x0b,0x10, /* operations */
        0,0,0,0, /* events */
        0,0,0,0, /* device properties */
        0,0,0,0, /* capture */
        1,0,0,0,0x0b,0x38, /* image formats */
        0,0,0,0 /* four strings */
    };
    mtp_capabilities caps;
    assert(mtp_device_info(info,sizeof info,&caps)&&caps.png&&caps.format_count==1);
    assert(mtp_supports(&caps,MTP_GET_OBJECT)&&mtp_supports(&caps,0x100b)&&!mtp_supports(&caps,0x100c));
    assert(!mtp_device_info(info,sizeof info-1,&caps));
    return 0;
}
