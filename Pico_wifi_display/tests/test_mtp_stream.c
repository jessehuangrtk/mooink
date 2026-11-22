#include <assert.h>
#include <string.h>
#include "mtp_stream.h"
typedef struct {uint8_t b[32];size_t n;} sink_state;
static bool sink(const uint8_t *p,size_t n,void *ctx){
    sink_state *b=ctx;if(n>sizeof(b->b)-b->n)return false;
    memcpy(b->b+b->n,p,n);b->n+=n;return true;
}
int main(void){
    const uint8_t bytes[]={15,0,0,0,2,0,9,16,3,0,0,0,'a','b','c',
                           12,0,0,0,3,0,1,32,3,0,0,0};
    for(size_t cut=0;cut<=sizeof(bytes);cut++){
        mtp_stream s;sink_state b={0};
        mtp_stream_init(&s,MTP_GET_OBJECT,3,true,3,sink,&b);
        assert(mtp_stream_feed(&s,bytes,cut));
        assert(mtp_stream_feed(&s,NULL,0));
        assert(mtp_stream_feed(&s,bytes+cut,sizeof(bytes)-cut));
        assert(s.done&&s.response==MTP_OK&&s.total==3&&b.n==3&&!memcmp(b.b,"abc",3));
    }
    mtp_stream s;sink_state b={0};
    mtp_stream_init(&s,MTP_GET_OBJECT,3,true,3,sink,&b);
    for(size_t i=0;i<sizeof(bytes);i++)assert(mtp_stream_feed(&s,bytes+i,1));
    assert(s.done&&b.n==3);
    assert(!mtp_stream_feed(&s,bytes,1));
    mtp_stream_init(&s,MTP_GET_OBJECT,3,true,2,sink,&b);
    assert(!mtp_stream_feed(&s,bytes,sizeof(bytes)));
    mtp_stream_init(&s,MTP_GET_OBJECT,4,true,3,sink,&b);
    assert(!mtp_stream_feed(&s,bytes,sizeof(bytes)));
    mtp_stream_init(&s,MTP_GET_OBJECT,3,true,3,sink,&b);
    assert(!mtp_stream_feed(&s,bytes+15,12)); /* successful response with no data */
    const uint8_t error[]={12,0,0,0,3,0,2,32,3,0,0,0};
    mtp_stream_init(&s,MTP_GET_OBJECT,3,true,3,sink,&b);
    assert(mtp_stream_feed(&s,error,sizeof(error))&&s.done&&s.response==0x2002);
    return 0;
}
