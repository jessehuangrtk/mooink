#ifndef MTP_STREAM_H
#define MTP_STREAM_H
#include "mtp_codec.h"
typedef bool (*mtp_sink)(const uint8_t *,size_t,void *);
typedef struct {
    uint8_t header[12];
    size_t header_n;
    uint32_t remaining, total, limit, tid;
    uint16_t op, response;
    bool want_data, seen_data, in_data, in_response, done, failed;
    mtp_sink sink;
    void *ctx;
} mtp_stream;
void mtp_stream_init(mtp_stream *s,uint16_t op,uint32_t tid,bool want_data,uint32_t limit,mtp_sink sink,void *ctx);
bool mtp_stream_feed(mtp_stream *s,const uint8_t *p,size_t n);
#endif
