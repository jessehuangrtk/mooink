#include <assert.h>
#include <string.h>
#include "smb_snapshot.h"
int main(void){
    display_version a={1,100,2,3,4,5,0},b=a;
    assert(display_version_equal(&a,&b));
    b.mtime_ns++;assert(!display_version_equal(&a,&b));
    b=a;b.identity++;assert(!display_version_equal(&a,&b));
    b=a;b.size++;assert(!display_version_equal(&a,&b));
    uint8_t png[33]={137,80,78,71,13,10,26,10,0,0,0,13,'I','H','D','R',
                      0,0,5,124,0,0,7,80,8,0,0,0,0};
    assert(display_png_header(png,sizeof(png),1404,1872));
    assert(!display_png_header(png,32,1404,1872));
    assert(!display_png_header(png,sizeof(png),1650,2200));
    png[25]=2;assert(!display_png_header(png,sizeof(png),1404,1872));
    display_seen seen={0};uint8_t hash[32]={0};
    assert(display_observe(&seen,hash));
    assert(!display_observe(&seen,hash));
    hash[31]=1;assert(display_observe(&seen,hash));
    return 0;
}
