#ifndef DISPLAY_UPDATE_H
#define DISPLAY_UPDATE_H
#include <stdbool.h>
#include <stdint.h>
#define UPDATE_MAX_BYTES (16u*1024u*1024u)
typedef struct {uint32_t size;uint8_t sha[32];} update_image;
/* id identifies a sealed, immutable stage or backup owned by this attempt. */
typedef struct {uint64_t id;update_image image;} update_blob;
typedef struct {
    uint64_t generation; /* changes on detach/rebind; never reused within boot */
    uint32_t storage,parent,handle; /* zero handle means absent, not ambiguous */
    uint64_t free_bytes;
    bool safe_target, writable, can_create, can_delete;
    update_image image; /* full readback digest, not metadata alone */
} update_target;
typedef enum {
    UPDATE_J_PREPARED=1,UPDATE_J_MUTATING,UPDATE_J_VERIFIED,
    UPDATE_J_WAKE_ATTEMPTED,UPDATE_J_COMPLETE
} update_phase;
typedef struct {
    update_phase phase;
    update_target old;
    update_blob staged,backup;
} update_record;
typedef enum {
    UPDATE_BLOCKED,UPDATE_DRY_RUN,UPDATE_UNCHANGED,
    UPDATE_FAILED_SAFE,UPDATE_RECOVERY_REQUIRED,
    UPDATE_WAKE_UNCONFIRMED,UPDATE_SENT
} update_result;
/* Single owner, synchronous, bounded operations. The adapter must keep the
 * same device/session pinned and validate identity inside delete/upload,
 * not merely trust the most recent discover. Only reserved sleep filename
 * is allowed. False from a mutator may mean it changed storage.
 * journal_clean: false on pending/invalid/unreadable durable journal.
 * persist: true only when record is durably saved and readback verified.
 * verify_blob: full hash/size validation of an immutable stage or backup.
 * wake: one Space down/up attempt; true means local stack accepted only.
 * Neither callbacks nor caller may retry mutations or wake automatically. */
typedef struct {
    bool (*journal_clean)(void *);
    bool (*discover)(void *,update_target *);
    bool (*stage)(void *,update_blob *);
    bool (*verify_blob)(void *,const update_blob *);
    bool (*backup)(void *,const update_target *,update_blob *);
    bool (*persist)(void *,const update_record *);
    bool (*remove_old)(void *,const update_target *);
    bool (*upload)(void *,const update_target *,const update_blob *);
    bool (*wake)(void *,const update_target *);
} update_ops;
bool update_image_equal(const update_image *a,const update_image *b);
update_result update_run(const update_ops *ops,void *ctx,bool enable_write);
#endif
