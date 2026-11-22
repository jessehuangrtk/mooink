#include "display_update.h"
#include <assert.h>
#include <string.h>
/* Callback failure may happen after side effects. Fake models a pinned
 * device, a durable journal and sealed storage, not real USB or SMB. */
typedef struct {
    update_target target;
    update_blob source;
    update_phase journal;
    unsigned calls,fail_at,removes,uploads,wakes,discovers;
    unsigned change_at;
    bool bad_backup,bad_readback,unwritable;
} fake;
static bool pass(fake *f){return ++f->calls!=f->fail_at;}
static bool clean(void *v){fake *f=v;return pass(f)&&(!f->journal||f->journal==UPDATE_J_COMPLETE);}
static bool discover(void *v,update_target *out){
    fake *f=v;
    if(!pass(f))return false;
    if(++f->discovers==f->change_at)f->target.generation++;
    *out=f->target;return true;
}
static bool stage(void *v,update_blob *out){fake *f=v;*out=f->source;return pass(f);}
static bool verify(void *v,const update_blob *b){fake *f=v;return b->id&&pass(f);}
static bool backup(void *v,const update_target *t,update_blob *out){
    fake *f=v;out->id=2;out->image=t->image;
    if(f->bad_backup)out->image.sha[0]^=1;
    return pass(f);
}
static bool persist(void *v,const update_record *r){
    fake *f=v;f->journal=r->phase;
    return pass(f);
}
static bool remove_old(void *v,const update_target *t){
    fake *f=v;
    assert(f->journal==UPDATE_J_MUTATING&&t->handle==f->target.handle);
    ++f->removes;f->target.handle=0;memset(&f->target.image,0,sizeof(f->target.image));
    return pass(f);
}
static bool upload(void *v,const update_target *t,const update_blob *b){
    fake *f=v;
    assert(f->journal==UPDATE_J_MUTATING&&!t->handle&&!f->target.handle);
    ++f->uploads;f->target.handle=99;f->target.image=b->image;
    if(f->bad_readback)f->target.image.sha[0]^=1;
    return pass(f);
}
static bool wake(void *v,const update_target *t){
    fake *f=v;
    assert(f->journal==UPDATE_J_WAKE_ATTEMPTED&&t->handle==f->target.handle);
    assert(update_image_equal(&t->image,&f->source.image));
    ++f->wakes;return pass(f);
}
static const update_ops ops={clean,discover,stage,verify,backup,persist,remove_old,upload,wake};
static fake fixture(void){
    fake f={0};
    f.target.generation=1;f.target.storage=1;f.target.parent=2;f.target.handle=3;
    f.target.safe_target=f.target.writable=f.target.can_create=f.target.can_delete=true;
    f.target.free_bytes=1000;f.target.image.size=10;f.target.image.sha[0]=1;
    f.source.id=1;f.source.image.size=20;f.source.image.sha[0]=2;
    return f;
}
int main(void){
    fake f=fixture();
    assert(update_run(&ops,&f,false)==UPDATE_DRY_RUN&&!f.removes&&!f.uploads&&!f.wakes&&!f.journal);
    f=fixture();f.source.image=f.target.image;
    assert(update_run(&ops,&f,true)==UPDATE_UNCHANGED&&!f.removes&&!f.uploads&&!f.wakes);
    f=fixture();f.target.free_bytes=0;
    assert(update_run(&ops,&f,true)==UPDATE_BLOCKED&&!f.removes);
    f=fixture();f.bad_backup=true;
    assert(update_run(&ops,&f,true)==UPDATE_FAILED_SAFE&&!f.removes&&!f.wakes);
    f=fixture();f.bad_readback=true;
    assert(update_run(&ops,&f,true)==UPDATE_RECOVERY_REQUIRED&&f.uploads==1&&!f.wakes);
    f=fixture();f.change_at=3;
    assert(update_run(&ops,&f,true)==UPDATE_FAILED_SAFE&&!f.removes);
    f=fixture();f.change_at=4;
    assert(update_run(&ops,&f,true)==UPDATE_RECOVERY_REQUIRED&&f.removes==1&&!f.uploads&&!f.wakes);
    f=fixture();f.change_at=6;
    assert(update_run(&ops,&f,true)==UPDATE_RECOVERY_REQUIRED&&f.uploads==1&&!f.wakes);
    f=fixture();f.target.handle=0;memset(&f.target.image,0,sizeof(f.target.image));
    assert(update_run(&ops,&f,true)==UPDATE_SENT&&!f.removes&&f.uploads==1&&f.wakes==1);
    f=fixture();
    assert(update_run(&ops,&f,true)==UPDATE_SENT&&f.removes==1&&f.uploads==1&&f.wakes==1);
    unsigned count=f.calls;
    assert(update_run(&ops,&f,true)==UPDATE_UNCHANGED&&f.wakes==1);
    for(unsigned i=1;i<=count;i++){
        f=fixture();f.fail_at=i;
        update_result result=update_run(&ops,&f,true);
        assert(result!=UPDATE_SENT);
        assert(f.removes<=1&&f.uploads<=1&&f.wakes<=1);
        if(f.wakes)assert(result==UPDATE_WAKE_UNCONFIRMED);
        if(f.journal&&f.journal!=UPDATE_J_COMPLETE){
            unsigned d=f.removes,u=f.uploads,w=f.wakes;
            f.fail_at=0;
            assert(update_run(&ops,&f,true)==UPDATE_RECOVERY_REQUIRED);
            assert(f.removes==d&&f.uploads==u&&f.wakes==w);
        }
    }
    return 0;
}
