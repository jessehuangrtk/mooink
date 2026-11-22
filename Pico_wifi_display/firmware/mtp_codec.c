#include "mtp_codec.h"
uint16_t mtp_u16(const uint8_t *p){return (uint16_t)(p[0]|((uint16_t)p[1]<<8));}
uint32_t mtp_u32(const uint8_t *p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static void w16(uint8_t *p,uint16_t v){p[0]=v;p[1]=v>>8;}
static void w32(uint8_t *p,uint32_t v){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i));}
size_t mtp_command(uint8_t *dst,size_t cap,uint16_t op,uint32_t tid,const uint32_t *params,size_t n){
    if(!dst||n>5||cap<12+4*n||(n&&!params))return 0;
    switch(op){case MTP_DEVICE_INFO:case MTP_OPEN:case MTP_CLOSE:case MTP_STORAGE_IDS:
        case MTP_HANDLES:case MTP_OBJECT_INFO:case MTP_GET_OBJECT:break;
        default:return 0; /* Read-only transport: no write/delete/vendor operations. */
    }
    w32(dst,(uint32_t)(12+4*n));w16(dst+4,MTP_COMMAND);w16(dst+6,op);w32(dst+8,tid);
    for(size_t i=0;i<n;i++)w32(dst+12+4*i,params[i]);
    return 12+4*n;
}
bool mtp_decode_header(const uint8_t *p,size_t n,mtp_header *out){
    if(!p||!out||n<12)return false;
    mtp_header h={.length=mtp_u32(p),.type=mtp_u16(p+4),.code=mtp_u16(p+6),.transaction=mtp_u32(p+8)};
    if(h.length<12||h.type<1||h.type>4)return false;
    *out=h;return true;
}
bool mtp_array32(const uint8_t *p,size_t n,uint32_t *out,size_t cap,size_t *count){
    if(!p||!out||!count||n<4)return false;
    uint32_t k=mtp_u32(p);
    if(k>cap||k>(n-4)/4||n!=4+(size_t)k*4)return false;
    for(uint32_t i=0;i<k;i++)out[i]=mtp_u32(p+4+4*i);
    *count=k;return true;
}
bool mtp_object_info(const uint8_t *p,size_t n,uint16_t *format,uint32_t *parent,char *name,size_t cap){
    /* PTP ObjectInfo fixed fields: 52 bytes, then counted UTF-16LE filename. */
    if(!p||!format||!parent||!name||n<53||!cap)return false;
    uint8_t k=p[52];
    if(!k||k>cap||n<53+(size_t)k*2||mtp_u16(p+53+2*(k-1))!=0)return false;
    for(unsigned i=0;i+1<k;i++){
        uint16_t ch=mtp_u16(p+53+2*i);
        if(!ch)return false;
        name[i]=(ch>=32&&ch<127)?(char)ch:'?';
    }
    name[k-1]=0;*format=mtp_u16(p+4);*parent=mtp_u32(p+38);return true;
}
