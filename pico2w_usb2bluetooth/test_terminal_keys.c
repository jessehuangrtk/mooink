#include <assert.h>
#include "terminal_keys.h"
int main(void){
    terminal_key_t k;
    assert(terminal_key('a',&k)&&k.usage==4&&k.mod==0);
    assert(terminal_key('Z',&k)&&k.usage==29&&k.mod==2);
    assert(terminal_key(' ',&k)&&k.usage==44&&k.mod==0);
    assert(terminal_key('\r',&k)&&k.usage==40);
    assert(terminal_key('\n',&k)&&k.usage==40);
    assert(terminal_key(8,&k)&&k.usage==42);
    assert(terminal_key(127,&k)&&k.usage==42);
    assert(terminal_key('\t',&k)&&k.usage==43);
    assert(terminal_key('!',&k)&&k.usage==30&&k.mod==2);
    assert(terminal_key('?',&k)&&k.usage==56&&k.mod==2);
    for(unsigned c=32;c<=126;c++)assert(terminal_key(c,&k));
    assert(!terminal_key(0x1d,&k));
    assert(!terminal_key(27,&k));
    assert(!terminal_key(0x80,&k));
    return 0;
}
