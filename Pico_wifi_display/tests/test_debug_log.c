#include "debug_log.h"
#include <assert.h>
#include <string.h>
int main(void){
    display_debug_log log;
    display_log_init(&log,false);
    display_log_event(&log,10,"offline");
    assert(!log.used&&!log.dirty);
    display_log_init(&log,true);
    display_log_event(&log,123,"boot\nsecret-control");
    assert(log.dirty&&log.sequence==1);
    assert(log.text[log.used-1]=='\n');
    assert(memchr(log.text,'\n',log.used)==log.text+log.used-1);
    for(unsigned i=0;i<2000;i++)
        display_log_event(&log,i,"SMB poll / image unchanged");
    assert(log.used<=DISPLAY_LOG_CAPACITY&&log.dropped>0&&log.text[0]=='[');
    assert(log.text[log.used-1]=='\n');
    assert(log.sequence==2001);
    return 0;
}
