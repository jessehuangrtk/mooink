#ifndef DISPLAY_MENU_H
#define DISPLAY_MENU_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    char ssid[33], wifi_password[65];
    char smb_server[128], smb_share[81], smb_user[81], smb_password[129];
    char bt_peer[18];
    unsigned poll_seconds;
    unsigned display_size; /* 10 or 13 */
    bool debug_mode;
} display_settings;

typedef void (*menu_write)(void *, const char *);
typedef struct {
    display_settings draft;
    menu_write write;
    void *ctx;
    char input[129];
    size_t len;
    unsigned page, field;
    bool active, cr, overflow;
} display_menu;

void display_menu_init(display_menu *m, menu_write write, void *ctx);
void display_menu_feed(display_menu *m, uint8_t ch);
#endif
