#include <assert.h>
#include <string.h>
#include "display_menu.h"
typedef struct {char data[8192];size_t used;} logbuf;
static void out(void *ctx,const char *s){
    logbuf *l=ctx;size_t n=strlen(s);assert(n<sizeof(l->data)-l->used);
    memcpy(l->data+l->used,s,n+1);l->used+=n;
}
static void feed(display_menu *m,const char *s){for(;*s;s++)display_menu_feed(m,(uint8_t)*s);}
int main(void){
    display_menu m;logbuf l={0};
    display_menu_init(&m,out,&l);
    feed(&m,"abc");assert(l.used==0&&!m.active);
    feed(&m,"\r\n");assert(m.active&&strstr(l.data,"Wi-Fi settings"));
    size_t n=l.used;feed(&m,"1");assert(l.used==n);
    feed(&m,"\r1\rMyWiFi\r2\rmysecret123\r0\r2\r1\r192.168.1.100\r2\rmooink\r3\rreader\r4\rhiddenpass\r0\r");
    assert(!strcmp(m.draft.ssid,"MyWiFi")&&!strcmp(m.draft.wifi_password,"mysecret123"));
    assert(!strcmp(m.draft.smb_server,"192.168.1.100")&&!strcmp(m.draft.smb_password,"hiddenpass"));
    assert(!strstr(l.data,"mysecret123")&&!strstr(l.data,"hiddenpass"));
    feed(&m,"3\r1\rAA:BB:CC:DD:EE:FF\r0\r4\r1\r13.3\r2\r120\r2\r0\r");
    assert(!strcmp(m.draft.bt_peer,"AA:BB:CC:DD:EE:FF")&&m.draft.display_size==13&&m.draft.poll_seconds==120);
    /* Invalid interval retained; leave value field then general menu. */
    feed(&m,"0\r5\r");assert(strstr(l.data,"[set]")&&!strstr(l.data,"hiddenpass"));
    feed(&m,"0\r");assert(!m.active);
    return 0;
}
