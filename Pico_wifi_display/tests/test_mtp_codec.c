#include <assert.h>
#include <string.h>
#include "mtp_codec.h"
int main(void){
    uint8_t b[80]={0};uint32_t p[]={1,0,0xffffffffu};
    assert(mtp_command(b,sizeof b,MTP_HANDLES,2,p,3)==24);
    mtp_header h;assert(mtp_decode_header(b,24,&h)&&h.length==24&&h.code==MTP_HANDLES&&h.transaction==2);
    assert(mtp_command(b,sizeof b,0x100b,2,p,1)==0); /* DeleteObject rejected */
    assert(mtp_command(b,sizeof b,0x100d,2,NULL,0)==0); /* SendObject rejected */
    uint8_t arr[]={2,0,0,0,1,0,0,0,2,0,0,0};
    uint32_t out[2];size_t n;assert(mtp_array32(arr,sizeof arr,out,2,&n)&&n==2&&out[1]==2);
    assert(!mtp_array32(arr,8,out,2,&n));assert(!mtp_array32(arr,sizeof arr,out,1,&n));
    memset(b,0,sizeof b);b[4]=1;b[5]=0x30;b[52]=6;
    const char *s="sleep";for(size_t i=0;i<5;i++)b[53+2*i]=s[i];
    uint16_t format;uint32_t parent;char name[16];
    assert(mtp_object_info(b,sizeof b,&format,&parent,name,sizeof name)&&format==0x3001&&!strcmp(name,"sleep"));
    assert(!mtp_object_info(b,56,&format,&parent,name,sizeof name));
    return 0;
}
