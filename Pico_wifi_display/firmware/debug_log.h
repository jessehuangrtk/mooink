#ifndef DISPLAY_DEBUG_LOG_H
#define DISPLAY_DEBUG_LOG_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define DISPLAY_LOG_CAPACITY 16384u
typedef struct {
    char text[DISPLAY_LOG_CAPACITY];
    size_t used;
    uint32_t dropped,sequence;
    bool enabled,dirty;
} display_debug_log;
/* Single owner task; only structured, non-secret diagnostic events. */
void display_log_init(display_debug_log *log,bool enabled);
void display_log_event(display_debug_log *log,uint64_t ms,const char *message);
#endif
