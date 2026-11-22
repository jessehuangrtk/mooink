#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define DISPLAY_MAX_IMAGE (16u*1024u*1024u)
typedef struct {
    uint64_t identity, size, mtime, mtime_ns, ctime, ctime_ns;
    uint32_t type;
} display_version;
bool display_version_equal(const display_version *a,const display_version *b);
bool display_png_header(const uint8_t *p,size_t n,uint32_t width,uint32_t height);
typedef struct {bool valid;uint8_t digest[32];} display_seen;
/* Observation only: never a successful MTP-delivery cache. */
bool display_observe(display_seen *seen,const uint8_t digest[32]);
