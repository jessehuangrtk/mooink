#include "mtp_stream.h"
#include <string.h>
void mtp_stream_init(mtp_stream *s,uint16_t op,uint32_t tid,bool want_data,uint32_t limit,mtp_sink sink,void *ctx){
    memset(s,0,sizeof(*s));s->op=op;s->tid=tid;s->want_data=want_data;s->limit=limit;s->sink=sink;s->ctx=ctx;
}
static bool bad(mtp_stream *s){s->failed=true;return false;}
bool mtp_stream_feed(mtp_stream *s,const uint8_t *p,size_t n){
    if(!s||s->failed||(!p&&n))return false;
    while(n){
        if(s->done)return bad(s);
        if(s->in_data||s->in_response){
            size_t take=n<s->remaining?n:s->remaining;
            if(s->in_data&&take&&(!s->sink||!s->sink(p,take,s->ctx)))return bad(s);
            if(s->in_response){
                if(take>sizeof(s->response_bytes)-s->response_n)return bad(s);
                memcpy(s->response_bytes+s->response_n,p,take);s->response_n+=take;
            }
            p+=take;n-=take;s->remaining-=(uint32_t)take;
            if(!s->remaining){
                if(s->in_response){
                    s->response_count=s->response_n/4;
                    for(size_t i=0;i<s->response_count;i++)s->response_params[i]=mtp_u32(s->response_bytes+4*i);
                    s->done=true;
                }
                s->in_data=s->in_response=false;
            }
            continue;
        }
        size_t take=12-s->header_n;
        if(take>n)take=n;
        memcpy(s->header+s->header_n,p,take);s->header_n+=take;p+=take;n-=take;
        if(s->header_n!=12)continue;
        mtp_header h;
        if(!mtp_decode_header(s->header,12,&h)||h.transaction!=s->tid)return bad(s);
        s->header_n=0;s->remaining=h.length-12;
        if(h.type==MTP_DATA){
            if(!s->want_data||s->seen_data||h.code!=s->op||s->remaining>s->limit)return bad(s);
            s->seen_data=true;s->total=s->remaining;s->in_data=s->remaining!=0;
        }else if(h.type==MTP_RESPONSE){
            if(s->remaining>20||s->remaining%4||(h.code==MTP_OK&&s->want_data&&!s->seen_data))return bad(s);
            s->response=h.code;s->in_response=s->remaining!=0;s->done=!s->remaining;
        }else return bad(s);
    }
    return true;
}
