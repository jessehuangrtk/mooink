#include "display_update.h"
#include <string.h>
static bool image_valid(const update_image *a){
    return a->size>0&&a->size<=UPDATE_MAX_BYTES;
}
bool update_image_equal(const update_image *a,const update_image *b){
    return a&&b&&a->size==b->size&&!memcmp(a->sha,b->sha,32);
}
static bool valid_id(uint32_t id){return id!=0&&id!=UINT32_MAX;}
static bool target_valid(const update_target *a){
    return a->safe_target&&a->generation&&valid_id(a->storage)&&valid_id(a->parent)&&
           (a->handle?(valid_id(a->handle)&&image_valid(&a->image)):a->image.size==0);
}
static bool location_equal(const update_target *a,const update_target *b){
    return target_valid(a)&&target_valid(b)&&a->generation==b->generation&&
           a->storage==b->storage&&a->parent==b->parent;
}
static bool target_equal(const update_target *a,const update_target *b){
    return location_equal(a,b)&&a->handle==b->handle&&
           (!a->handle||update_image_equal(&a->image,&b->image));
}
static bool can_update(const update_target *a,const update_blob *b){
    /* Conservative: enough free space without relying on reclaiming old file. */
    return b->id&&image_valid(&b->image)&&a->writable&&a->can_create&&
           (!a->handle||a->can_delete)&&a->free_bytes>=b->image.size;
}
static bool save(const update_ops *o,void *ctx,update_record *r,update_phase p){
    r->phase=p;return o->persist(ctx,r);
}
update_result update_run(const update_ops *o,void *ctx,bool enable_write){
    if(!o||!o->journal_clean||!o->discover||!o->stage||!o->verify_blob)return UPDATE_BLOCKED;
    if(!o->journal_clean(ctx))return UPDATE_RECOVERY_REQUIRED;
    update_record r={0};
    update_target current={0},after={0};
    if(!o->discover(ctx,&r.old)||!target_valid(&r.old))return UPDATE_BLOCKED;
    if(!o->stage(ctx,&r.staged)||!r.staged.id||!image_valid(&r.staged.image)||
       !o->verify_blob(ctx,&r.staged))return UPDATE_FAILED_SAFE;
    if(!o->discover(ctx,&current)||!target_equal(&r.old,&current))return UPDATE_FAILED_SAFE;
    if(current.handle&&update_image_equal(&current.image,&r.staged.image))return UPDATE_UNCHANGED;
    if(!enable_write)return UPDATE_DRY_RUN;
    if(!o->persist||!o->upload||!o->wake||
       (current.handle&&(!o->backup||!o->remove_old))||!can_update(&current,&r.staged))return UPDATE_BLOCKED;
    if(current.handle){
        if(!o->backup(ctx,&current,&r.backup)||!r.backup.id||r.backup.id==r.staged.id||
           !update_image_equal(&current.image,&r.backup.image)||!o->verify_blob(ctx,&r.backup))
            return UPDATE_FAILED_SAFE;
    }
    if(!o->verify_blob(ctx,&r.staged)||
       !o->discover(ctx,&current)||!target_equal(&r.old,&current)||
       !can_update(&current,&r.staged))return UPDATE_FAILED_SAFE;
    /* Once a journal write is attempted, failure may leave a pending journal.
     * Do not automatically clear it, even if no mutation has been attempted. */
    if(!save(o,ctx,&r,UPDATE_J_PREPARED))return UPDATE_RECOVERY_REQUIRED;
    if(!save(o,ctx,&r,UPDATE_J_MUTATING))return UPDATE_RECOVERY_REQUIRED;
    if(current.handle&&!o->remove_old(ctx,&current))return UPDATE_RECOVERY_REQUIRED;
    if(!o->discover(ctx,&after)||!location_equal(&current,&after)||after.handle||
       !can_update(&after,&r.staged)||!o->verify_blob(ctx,&r.staged))return UPDATE_RECOVERY_REQUIRED;
    if(!o->upload(ctx,&after,&r.staged))return UPDATE_RECOVERY_REQUIRED;
    if(!o->discover(ctx,&after)||!location_equal(&current,&after)||!after.handle||
       !update_image_equal(&after.image,&r.staged.image))return UPDATE_RECOVERY_REQUIRED;
    if(!save(o,ctx,&r,UPDATE_J_VERIFIED))return UPDATE_RECOVERY_REQUIRED;
    /* Persist attempted before key-down: a reset cannot replay a wake token.
     * Recheck image/session after the durable marker, before attempting Space. */
    if(!save(o,ctx,&r,UPDATE_J_WAKE_ATTEMPTED))return UPDATE_RECOVERY_REQUIRED;
    update_target check={0};
    if(!o->discover(ctx,&check)||!target_equal(&after,&check))return UPDATE_RECOVERY_REQUIRED;
    if(!o->wake(ctx,&check))return UPDATE_WAKE_UNCONFIRMED;
    if(!save(o,ctx,&r,UPDATE_J_COMPLETE))return UPDATE_WAKE_UNCONFIRMED;
    return UPDATE_SENT;
}
