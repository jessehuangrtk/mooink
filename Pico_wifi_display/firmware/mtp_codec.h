#ifndef MTP_CODEC_H
#define MTP_CODEC_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
enum { MTP_COMMAND=1,MTP_DATA=2,MTP_RESPONSE=3,MTP_OK=0x2001 };
enum { MTP_DEVICE_INFO=0x1001,MTP_OPEN=0x1002,MTP_CLOSE=0x1003,
       MTP_STORAGE_IDS=0x1004,MTP_STORAGE_INFO=0x1005,MTP_HANDLES=0x1007,
       MTP_OBJECT_INFO=0x1008,MTP_GET_OBJECT=0x1009 };
enum { MTP_FORMAT_UNDEFINED=0x3000,MTP_FORMAT_ASSOCIATION=0x3001,MTP_FORMAT_PNG=0x380b };
typedef struct {uint32_t length,transaction; uint16_t type,code;} mtp_header;
typedef struct {
    uint32_t storage,size,parent;
    uint16_t format,protection,association;
    uint16_t name[254];
    size_t name_len;
} mtp_object;
typedef struct {
    uint16_t storage_type,filesystem,access;
    uint64_t capacity,free_bytes;
    uint32_t free_objects;
} mtp_storage;
typedef struct {
    uint32_t operations;
    uint32_t event_count,format_count;
    bool png;
} mtp_capabilities;
uint16_t mtp_u16(const uint8_t *p);
uint32_t mtp_u32(const uint8_t *p);
size_t mtp_command(uint8_t *dst,size_t cap,uint16_t op,uint32_t tid,const uint32_t *params,size_t n);
bool mtp_decode_header(const uint8_t *p,size_t n,mtp_header *out);
bool mtp_array32(const uint8_t *p,size_t n,uint32_t *out,size_t cap,size_t *count);
bool mtp_unique_ids(const uint32_t *ids,size_t n);
bool mtp_object_info(const uint8_t *p,size_t n,mtp_object *out);
/* ASCII comparison against exact UTF-16 code units; fold only ASCII A-Z. */
bool mtp_name_is(const mtp_object *obj,const char *ascii,bool fold_ascii);
/* Lossy display only; never use this output as a file identity. */
void mtp_name_display(const mtp_object *obj,char *dst,size_t cap);
bool mtp_storage_info(const uint8_t *p,size_t n,mtp_storage *out);
bool mtp_device_info(const uint8_t *p,size_t n,mtp_capabilities *out);
bool mtp_supports(const mtp_capabilities *caps,uint16_t opcode);
const char *mtp_response_name(uint16_t code);
#endif
