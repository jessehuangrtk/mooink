#include <assert.h>
#include "key_command.h"

int main(void){
    key_command_t k;
    assert(parse_key_command("KEY 0 4",&k)==KEY_PARSE_OK);
    assert(k.mod==0&&k.usage==4&&k.hold==60&&!k.extended);
    assert(parse_key_command("KEY 0 44",&k)==KEY_PARSE_OK&&k.usage==44);
    assert(parse_key_command("KEY 0 69",&k)==KEY_PARSE_OK&&k.usage==69);
    assert(parse_key_command("KEYH 0 44 500",&k)==KEY_PARSE_OK&&k.extended&&k.hold==500);
    assert(parse_key_command("KEYH 255 231 2000",&k)==KEY_PARSE_OK);
    assert(parse_key_command("KEY  2  4  ",&k)==KEY_PARSE_OK&&k.mod==2);
    const char *bad[]={"KEY","KEY 0","KEY 0 3","KEY 256 4","KEY 0 232","KEY 0 4 x",
                      "KEY 0 4 500","KEY -1 4","KEY +1 4","KEY 0x0 4",
                      "KEY 99999999999999999999999 4","KEYH 0 44","KEYH 0 44 19",
                      "KEYH 0 44 2001","KEYH 0 44 500 junk"};
    for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);++i)
        assert(parse_key_command(bad[i],&k)==KEY_PARSE_INVALID);
    assert(parse_key_command("STATUS",&k)==KEY_PARSE_OTHER);
    assert(parse_key_command("KEYBOARD",&k)==KEY_PARSE_OTHER);
    return 0;
}
