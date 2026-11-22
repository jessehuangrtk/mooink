#include "smb_snapshot.h"
#include <string.h>
bool display_version_equal(const display_version *a,const display_version *b){
    return a->identity==b->identity&&a->size==b->size&&a->mtime==b->mtime&&
           a->mtime_ns==b->mtime_ns&&a->ctime==b->ctime&&a->ctime_ns==b->ctime_ns&&a->type==b->type;
}
static uint32_t be32(const uint8_t *p){
    return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3];
}
bool display_png_header(const uint8_t *p,size_t n,uint32_t width,uint32_t height){
    static const uint8_t sig[]={137,80,78,71,13,10,26,10};
    return p&&n>=33&&!memcmp(p,sig,8)&&be32(p+8)==13&&!memcmp(p+12,"IHDR",4)&&
           be32(p+16)==width&&be32(p+20)==height&&p[24]==8&&p[25]==0&&
           p[26]==0&&p[27]==0&&p[28]==0; /* header only; not full PNG validation */
}
bool display_observe(display_seen *seen,const uint8_t digest[32]){
    bool changed=!seen->valid||memcmp(seen->digest,digest,32)!=0;
    memcpy(seen->digest,digest,32);seen->valid=true;return changed;
}
