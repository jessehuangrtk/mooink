#ifndef MOOINK_KEY_COMMAND_H
#define MOOINK_KEY_COMMAND_H

#include <stdbool.h>
#include <string.h>

typedef enum {
    KEY_PARSE_OTHER, KEY_PARSE_INVALID, KEY_PARSE_OK
} key_parse_result_t;

typedef struct {
    unsigned mod, usage, hold;
    bool extended;
} key_command_t;

/* ASCII decimal only. Do not depend on embedded libc scanf support.
 * Reject overflow as soon as any number exceeds the maximum valid field. */
static inline bool key_read_uint(const char **cursor, unsigned *result){
    const char *p=*cursor;
    if(*p!=' ')return false;
    while(*p==' ')++p;
    if(*p<'0'||*p>'9')return false;
    unsigned n=0;
    do{
        n=n*10u+(unsigned)(*p-'0');
        if(n>2000u)return false;
        ++p;
    }while(*p>='0'&&*p<='9');
    *cursor=p;*result=n;return true;
}

static inline key_parse_result_t parse_key_command(const char *s,key_command_t *out){
    key_command_t k={.hold=60};
    const char *p;
    if(!strncmp(s,"KEYH",4)&&(s[4]==' '||s[4]=='\0')){
        k.extended=true;p=s+4;
    }else if(!strncmp(s,"KEY",3)&&(s[3]==' '||s[3]=='\0')){
        p=s+3;
    }else return KEY_PARSE_OTHER;
    if(!key_read_uint(&p,&k.mod)||!key_read_uint(&p,&k.usage)||
       (k.extended&&!key_read_uint(&p,&k.hold)))return KEY_PARSE_INVALID;
    while(*p==' ')++p;
    if(*p||k.mod>255||k.usage<4||k.usage>231||k.hold<20||k.hold>2000)
        return KEY_PARSE_INVALID;
    *out=k;
    return KEY_PARSE_OK;
}
#endif
