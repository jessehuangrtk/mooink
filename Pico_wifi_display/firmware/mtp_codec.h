#ifndef MTP_CODEC_H
#define MTP_CODEC_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
enum { MTP_COMMAND=1,MTP_DATA=2,MTP_RESPONSE=3,MTP_OK=0x2001 };
enum { MTP_DEVICE_INFO=0x1001,MTP_OPEN=0x1002,MTP_CLOSE=0x1003,
       MTP_STORAGE_IDS=0x1004,MTP_HANDLES=0x1007,MTP_OBJECT_INFO=0x1008,
       MTP_GET_OBJECT=0x1009 };
typedef struct {uint32_t length,transaction; uint16_t type,code;} mtp_header;
uint16_t mtp_u16(const uint8_t *p);
uint32_t mtp_u32(const uint8_t *p);
size_t mtp_command(uint8_t *dst,size_t cap,uint16_t op,uint32_t tid,const uint32_t *params,size_t n);
bool mtp_decode_header(const uint8_t *p,size_t n,mtp_header *out);
bool mtp_array32(const uint8_t *p,size_t n,uint32_t *out,size_t cap,size_t *count);
bool mtp_object_info(const uint8_t *p,size_t n,uint16_t *format,uint32_t *parent,char *name,size_t cap);
#endif
