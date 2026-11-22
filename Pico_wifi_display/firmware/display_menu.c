#include "display_menu.h"
#include <stdio.h>
#include <string.h>

enum {ROOT,WIFI,SMB,BLUETOOTH,GENERAL};
static void say(display_menu *m,const char *s){m->write(m->ctx,s);}
static void clear_input(display_menu *m){
    volatile char *p=m->input;
    for(size_t i=0;i<sizeof(m->input);i++)p[i]=0;
    m->len=0;m->overflow=false;
}
static void show(display_menu *m){
    m->active=true;m->field=0;
    switch(m->page){
    case ROOT:
        say(m,"\n=== Pico_wifi_display ===\n"
              "1. Wi-Fi settings\n"
              "2. SMB settings\n"
              "3. Bluetooth settings\n"
              "4. Display and poll interval\n"
              "5. Show settings and status\n"
              "0. Exit menu\n"
              "Select: ");break;
    case WIFI:
        say(m,"\n[Wi-Fi]\n1. SSID\n2. Password\n0. Back\nSelect: ");break;
    case SMB:
        say(m,"\n[SMB2/3 - signing required]\n1. Server\n2. Share\n3. Username\n4. Password\n0. Back\nSelect: ");break;
    case BLUETOOTH:
        say(m,"\n[Bluetooth Classic]\n1. Target MAC address\n2. Pair / reconnect (not implemented)\n0. Back\nSelect: ");break;
    case GENERAL:
        say(m,"\n[Display]\n1. Size (10.3 / 13.3)\n2. Poll interval (5-86400 seconds)\n0. Back\nSelect: ");break;
    }
}
static void status(display_menu *m){
    char s[768];const display_settings *d=&m->draft;
    snprintf(s,sizeof(s),"\nWi-Fi SSID: %s\nWi-Fi password: %s\n"
             "SMB server: %s\nSMB share: %s\nSMB username: %s\nSMB password: %s\n"
             "SMB signing: required\nSource: mooink_display.png\nBluetooth peer: %s\n"
             "Display: %s\nPoll: %u s\n"
             "Settings are RAM preview only; Wi-Fi/SMB/BT not connected in this build.\n",
             d->ssid,d->wifi_password[0]?"[set]":"[not set]",
             d->smb_server,d->smb_share,d->smb_user,d->smb_password[0]?"[set]":"[not set]",
             d->bt_peer[0]?d->bt_peer:"[not set]",d->display_size==13?"13.3":"10.3",d->poll_seconds);
    say(m,s);
}
static bool text_field(char *dst,size_t cap,const char *value,bool empty_ok){
    size_t n=strlen(value);
    if(n>=cap||(!n&&!empty_ok))return false;
    memcpy(dst,value,n+1);return true;
}
static bool hexchar(char c){return (c>='0'&&c<='9')||(c>='a'&&c<='f')||(c>='A'&&c<='F');}
static bool mac(char *dst,const char *s){
    if(strlen(s)!=17)return false;
    bool nonzero=false;
    for(unsigned i=0;i<17;i++){
        if(i%3==2){if(s[i]!=':')return false;}
        else{if(!hexchar(s[i]))return false;if(s[i]!='0')nonzero=true;}
    }
    if(!nonzero)return false;
    memcpy(dst,s,18);return true;
}
static bool interval(unsigned *dst,const char *s){
    unsigned n=0;if(!*s)return false;
    for(;*s;s++){if(*s<'0'||*s>'9')return false;n=n*10u+(unsigned)(*s-'0');if(n>86400)return false;}
    if(n<5)return false;
    *dst=n;return true;
}
static void set_value(display_menu *m){
    const char *s=m->input;display_settings *d=&m->draft;bool ok=false;
    const bool password=(m->page==WIFI&&m->field==2)||(m->page==SMB&&m->field==4);
    if(m->page==WIFI){
        if(m->field==1)ok=text_field(d->ssid,sizeof(d->ssid),s,false);
        else if(m->field==2)ok=!*s||strlen(s)>=8?text_field(d->wifi_password,sizeof(d->wifi_password),s,true):false;
    }else if(m->page==SMB){
        switch(m->field){
        case 1:ok=text_field(d->smb_server,sizeof(d->smb_server),s,false);break;
        case 2:ok=text_field(d->smb_share,sizeof(d->smb_share),s,false);break;
        case 3:ok=text_field(d->smb_user,sizeof(d->smb_user),s,false);break;
        case 4:ok=text_field(d->smb_password,sizeof(d->smb_password),s,false);break;
        }
    }else if(m->page==BLUETOOTH&&m->field==1)ok=mac(d->bt_peer,s);
    else if(m->page==GENERAL){
        if(m->field==1){if(!strcmp(s,"10.3")){d->display_size=10;ok=true;}else if(!strcmp(s,"13.3")){d->display_size=13;ok=true;}}
        else if(m->field==2)ok=interval(&d->poll_seconds,s);
    }
    if(password)clear_input(m);
    say(m,ok?"\nUpdated in RAM.\n":"\nInvalid value. Previous setting retained.\n");
    show(m);
}
static void submit(display_menu *m){
    if(m->overflow){say(m,"\nInput too long or invalid; discarded.\n");show(m);return;}
    m->input[m->len]=0;
    if(!m->active){m->page=ROOT;show(m);return;}
    if(m->field){set_value(m);return;}
    if(!m->len){show(m);return;}
    if(m->len!=1||m->input[0]<'0'||m->input[0]>'9'){say(m,"\nInvalid choice.\n");show(m);return;}
    unsigned n=(unsigned)(m->input[0]-'0');
    if(!n){
        if(m->page==ROOT){m->active=false;say(m,"\nMenu closed. Press Enter to open.\n");}
        else{m->page=ROOT;show(m);}
        return;
    }
    if(m->page==ROOT){
        if(n<=4){m->page=n;show(m);}
        else if(n==5){status(m);show(m);}
        else{say(m,"\nInvalid choice.\n");show(m);}
        return;
    }
    if(m->page==BLUETOOTH&&n==2){say(m,"\nBluetooth transport not implemented in menu preview.\n");show(m);return;}
    unsigned max=m->page==SMB?4:m->page==BLUETOOTH?1:2;
    if(n>max){say(m,"\nInvalid choice.\n");show(m);return;}
    m->field=n;
    const char *prompt="New value (Enter to submit): ";
    if((m->page==WIFI&&n==2)||(m->page==SMB&&n==4))prompt="Password (hidden; Enter to submit): ";
    say(m,prompt);
}
void display_menu_init(display_menu *m,menu_write write,void *ctx){
    memset(m,0,sizeof(*m));m->write=write;m->ctx=ctx;
    m->draft.poll_seconds=60;m->draft.display_size=10;
}
void display_menu_feed(display_menu *m,uint8_t ch){
    if(ch=='\n'&&m->cr){m->cr=false;return;}
    m->cr=ch=='\r';
    if(ch=='\r'||ch=='\n'){submit(m);clear_input(m);return;}
    if(!m->active)return; /* any non-Enter input is ignored while menu is closed */
    if(ch==8||ch==127){if(!m->overflow&&m->len)m->input[--m->len]=0;return;}
    if(ch==0x15){clear_input(m);return;}
    if(ch<32||ch>126||m->len>=sizeof(m->input)-1){m->overflow=true;return;}
    if(!m->overflow)m->input[m->len++]=(char)ch;
}
