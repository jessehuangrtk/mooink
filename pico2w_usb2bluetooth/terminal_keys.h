#ifndef MOOINK_TERMINAL_KEYS_H
#define MOOINK_TERMINAL_KEYS_H
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

typedef struct { uint8_t ascii, mod, usage; } terminal_key_t;

static inline bool terminal_key(uint8_t ch, terminal_key_t *out){
    terminal_key_t k={ch,0,0};
    if(ch>='a'&&ch<='z')k.usage=(uint8_t)(4+ch-'a');
    else if(ch>='A'&&ch<='Z'){k.mod=2;k.usage=(uint8_t)(4+ch-'A');}
    else if(ch>='1'&&ch<='9')k.usage=(uint8_t)(30+ch-'1');
    else if(ch=='0')k.usage=39;
    else if(ch=='\r'||ch=='\n')k.usage=40;
    else if(ch==8||ch==127)k.usage=42;
    else if(ch=='\t')k.usage=43;
    else if(ch==' ')k.usage=44;
    else{
        const char *plain="-=[]\\;'\x60,./";
        const uint8_t usages[]={45,46,47,48,49,51,52,53,54,55,56};
        const char *shifted="!@#$%^&*()_+{}|:\"~<>?";
        const uint8_t shift_usages[]={30,31,32,33,34,35,36,37,38,39,45,46,47,48,49,51,52,53,54,55,56};
        if(ch<33||ch>126)return false;
        const char *p=strchr(plain,ch);
        if(p)k.usage=usages[p-plain];
        else{
            p=strchr(shifted,ch);
            if(!p)return false;
            k.mod=2;k.usage=shift_usages[p-shifted];
        }
    }
    *out=k;return true;
}
#endif
