#include "mtp_codec.h"
#include <string.h>
uint16_t mtp_u16(const uint8_t *p){return (uint16_t)(p[0]|((uint16_t)p[1]<<8));}
uint32_t mtp_u32(const uint8_t *p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static uint64_t u64(const uint8_t *p){return mtp_u32(p)|((uint64_t)mtp_u32(p+4)<<32);}
static void w16(uint8_t *p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void w32(uint8_t *p,uint32_t v){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i));}
static bool valid_id(uint32_t id){return id!=0&&id!=UINT32_MAX;}
size_t mtp_command(uint8_t *dst,size_t cap,uint16_t op,uint32_t tid,const uint32_t *params,size_t n){
    if(!dst||n>5||cap<12+4*n||(n&&!params))return 0;
    switch(op){
    case MTP_DEVICE_INFO:if(n||tid)return 0;break;
    case MTP_OPEN:if(n!=1||tid||!valid_id(params[0]))return 0;break;
    case MTP_CLOSE:case MTP_STORAGE_IDS:if(n||!tid)return 0;break;
    case MTP_STORAGE_INFO:case MTP_OBJECT_INFO:case MTP_GET_OBJECT:
        if(n!=1||!tid||!valid_id(params[0]))return 0;
        break;
    case MTP_HANDLES:
        if(n!=3||!tid||!valid_id(params[0])||params[1]!=0||params[2]==0)return 0;
        break;
    default:return 0; /* Never authorize destructive commands in this codec. */
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
bool mtp_unique_ids(const uint32_t *ids,size_t n){
    if(!ids&&n)return false;
    for(size_t i=0;i<n;i++){
        if(!valid_id(ids[i]))return false;
        for(size_t j=0;j<i;j++)if(ids[i]==ids[j])return false;
    }
    return true;
}
/* Counted UTF-16LE string. Validate all code units, including surrogate pairs. */
static bool take_string(const uint8_t **p,size_t *n,uint16_t *dst,size_t *len){
    if(!*n)return false;
    size_t k=(*p)[0],bytes=1+2*k;
    if(bytes>*n)return false;
    const uint8_t *s=*p+1;
    if(k&&mtp_u16(s+2*(k-1))!=0)return false;
    bool high=false;
    for(size_t i=0;i+1<k;i++){
        uint16_t ch=mtp_u16(s+2*i);
        if(!ch)return false;
        if(high){if(ch<0xdc00||ch>0xdfff)return false;high=false;}
        else if(ch>=0xd800&&ch<=0xdbff)high=true;
        else if(ch>=0xdc00&&ch<=0xdfff)return false;
        if(dst)dst[i]=ch;
    }
    if(high)return false;
    if(len)*len=k?k-1:0;
    *p+=bytes;*n-=bytes;return true;
}
bool mtp_object_info(const uint8_t *p,size_t n,mtp_object *out){
    if(!p||!out||n<53)return false;
    mtp_object obj={0};
    obj.storage=mtp_u32(p);obj.format=mtp_u16(p+4);obj.protection=mtp_u16(p+6);
    obj.size=mtp_u32(p+8);obj.parent=mtp_u32(p+38);obj.association=mtp_u16(p+42);
    p+=52;n-=52;
    if(!valid_id(obj.storage)||!take_string(&p,&n,obj.name,&obj.name_len)||!obj.name_len)return false;
    /* CaptureDate, ModificationDate, Keywords are also counted strings. */
    for(unsigned i=0;i<3;i++)if(!take_string(&p,&n,NULL,NULL))return false;
    if(n)return false;
    *out=obj;return true;
}
static uint16_t fold(uint16_t ch){return ch>='A'&&ch<='Z'?(uint16_t)(ch+32):ch;}
bool mtp_name_is(const mtp_object *obj,const char *ascii,bool fold_ascii){
    if(!obj||!ascii||strlen(ascii)!=obj->name_len||obj->name_len>254)return false;
    for(size_t i=0;i<obj->name_len;i++){
        unsigned char ch=(unsigned char)ascii[i];
        if(ch>127||(fold_ascii?fold(obj->name[i])!=fold(ch):obj->name[i]!=ch))return false;
    }
    return true;
}
void mtp_name_display(const mtp_object *obj,char *dst,size_t cap){
    if(!dst||!cap)return;
    size_t k=obj?obj->name_len:0;
    if(k>254)k=254;
    if(k>=cap)k=cap-1;
    for(size_t i=0;i<k;i++){uint16_t ch=obj->name[i];dst[i]=(ch>=32&&ch<127)?(char)ch:'?';}
    dst[k]=0;
}
bool mtp_storage_info(const uint8_t *p,size_t n,mtp_storage *out){
    if(!p||!out||n<26)return false;
    mtp_storage s={mtp_u16(p),mtp_u16(p+2),mtp_u16(p+4),u64(p+6),u64(p+14),mtp_u32(p+22)};
    p+=26;n-=26;
    if(s.access>2||!take_string(&p,&n,NULL,NULL)||!take_string(&p,&n,NULL,NULL)||n)return false;
    *out=s;return true;
}
static bool array16(const uint8_t **p,size_t *n,const uint8_t **values,uint32_t *count){
    if(*n<4)return false;
    uint32_t k=mtp_u32(*p);
    if(k>(*n-4)/2)return false;
    *values=*p+4;*count=k;
    size_t bytes=4+(size_t)k*2;*p+=bytes;*n-=bytes;return true;
}
bool mtp_device_info(const uint8_t *p,size_t n,mtp_capabilities *out){
    if(!p||!out||n<8)return false;
    p+=8;n-=8; /* StandardVersion, VendorExtensionID, VendorExtensionVersion */
    if(!take_string(&p,&n,NULL,NULL)||n<2)return false;
    p+=2;n-=2; /* FunctionalMode */
    mtp_capabilities caps={0};
    for(unsigned group=0;group<5;group++){
        const uint8_t *v;uint32_t k;
        if(!array16(&p,&n,&v,&k))return false;
        if(group==0)for(uint32_t i=0;i<k;i++){
            uint16_t code=mtp_u16(v+2*i);
            if(code>=0x1000&&code<0x1020)caps.operations|=UINT32_C(1)<<(code-0x1000);
        }
        if(group==1)caps.event_count=k;
        if(group==4){
            caps.format_count=k;
            for(uint32_t i=0;i<k;i++)if(mtp_u16(v+2*i)==MTP_FORMAT_PNG)caps.png=true;
        }
    }
    for(unsigned i=0;i<4;i++)if(!take_string(&p,&n,NULL,NULL))return false;
    if(n)return false;
    *out=caps;return true;
}
bool mtp_supports(const mtp_capabilities *caps,uint16_t op){
    return caps&&op>=0x1000&&op<0x1020&&(caps->operations&(UINT32_C(1)<<(op-0x1000)))!=0;
}
const char *mtp_response_name(uint16_t code){
    switch(code){
    case 0x2001:return "OK";
    case 0x2002:return "GeneralError";
    case 0x2003:return "SessionNotOpen";
    case 0x2004:return "InvalidTransactionID";
    case 0x2005:return "OperationNotSupported";
    case 0x2008:return "InvalidStorageID";
    case 0x2009:return "InvalidObjectHandle";
    case 0x200c:return "StoreFull";
    case 0x200d:return "ObjectWriteProtected";
    case 0x200e:return "StoreReadOnly";
    case 0x200f:return "AccessDenied";
    case 0x2019:return "DeviceBusy";
    case 0x201e:return "SessionAlreadyOpen";
    default:return "OtherResponse";
    }
}
