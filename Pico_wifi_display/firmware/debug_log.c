#include "debug_log.h"
#include <stdio.h>
#include <string.h>
#include <inttypes.h>
void display_log_init(display_debug_log *log,bool enabled){
    memset(log,0,sizeof(*log));log->enabled=enabled;
}
void display_log_event(display_debug_log *log,uint64_t ms,const char *message){
    if(!log->enabled||!message)return;
    char line[256];
    int n=snprintf(line,sizeof(line),"[%010" PRIu64 " ms #%06" PRIu32 "] ",ms,++log->sequence);
    if(n<0||(size_t)n>=sizeof(line)-2)return;
    size_t pos=(size_t)n;
    for(size_t i=0;message[i]&&pos<sizeof(line)-2;i++){
        unsigned char ch=(unsigned char)message[i];
        line[pos++]=(ch>=32&&ch<127)?(char)ch:'?';
    }
    line[pos++]='\n';
    while(log->used+pos>sizeof(log->text)){
        char *end=memchr(log->text,'\n',log->used);
        size_t remove=end?(size_t)(end-log->text)+1:log->used;
        memmove(log->text,log->text+remove,log->used-remove);
        log->used-=remove;log->dropped++;
    }
    memcpy(log->text+log->used,line,pos);log->used+=pos;log->dirty=true;
}
