/* Menu-only CDC preview. Never accesses mooInk or transmits keys. */
#include <stdio.h>
#include "pico/stdlib.h"
#include "display_menu.h"
static display_menu menu;
static void output(void *ctx,const char *s){(void)ctx;fputs(s,stdout);}
int main(void){
    stdio_init_all();
    display_menu_init(&menu,output,NULL);
    for(;;){
        for(unsigned i=0;i<64;i++){
            int ch=getchar_timeout_us(0);
            if(ch<0)break;
            display_menu_feed(&menu,(uint8_t)ch);
        }
        sleep_ms(5);
    }
}
