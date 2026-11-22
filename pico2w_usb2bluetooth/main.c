/* USB CDC is always a plain ASCII -> Bluetooth HID keyboard.
 * No text commands, mode switches or automatic keystrokes. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pico/stdlib.h"
#include "pico/cyw43_arch.h"
#include "pico/stdio.h"
#include "btstack.h"
#include "btstack_tlv.h"
#include "terminal_keys.h"

#define NAME "mooInk Pico2W Keyboard"
#define PEER_TAG 0x4d4b4254u
#define RAW_QUEUE_SIZE 32
static const uint8_t descriptor[]={
  0x05,0x01,0x09,0x06,0xa1,0x01,0x85,0x01,
  0x05,0x07,0x19,0xe0,0x29,0xe7,0x15,0x00,0x25,0x01,
  0x75,0x01,0x95,0x08,0x81,0x02,
  0x95,0x01,0x75,0x08,0x81,0x03,
  0x95,0x05,0x75,0x01,0x05,0x08,0x19,0x01,0x29,0x05,0x91,0x02,
  0x95,0x01,0x75,0x03,0x91,0x03,
  0x95,0x06,0x75,0x08,0x15,0x00,0x26,0xff,0x00,
  0x05,0x07,0x19,0x00,0x2a,0xff,0x00,0x81,0x00,0xc0
};

static uint8_t service[400];
static btstack_packet_callback_registration_t hci_cb;
static btstack_timer_source_t timer;
static const btstack_tlv_t *tlv;
static void *tlv_ctx;
static bd_addr_t peer,pending_peer;
static bool ready,paired,pairing,connected,connecting,raw_cr;
static uint16_t cid,pending_cid;
static uint32_t pair_end,connect_end,reconnect_at,key_end,release_at,escape_end;
static unsigned pairing_request,pin_len,compare_number;
static char pin[17];
static enum {IDLE,DOWN,HOLD,UP} key_state;
static uint8_t key_mod,key_usage,escape_state;
static terminal_key_t raw_queue[RAW_QUEUE_SIZE];
static unsigned raw_head,raw_count;

static uint32_t ms(void){return to_ms_since_boot(get_absolute_time());}
static bool expired(uint32_t at){return (int32_t)(ms()-at)>=0;}
static bool allowed(const bd_addr_t a){return paired?memcmp(peer,a,6)==0:pairing;}
static void notice(const char *s){printf("\n%s\n",s);}
static void clear_queue(void){raw_head=raw_count=0;}
static void clear_pin(void){memset(pin,0,sizeof(pin));pin_len=0;pairing_request=0;}
static void stop_pair(void){
  pairing=false;gap_discoverable_control(0);gap_set_bondable_mode(0);
  gap_connectable_control(paired?1:0);
  if(pairing_request==1)gap_pin_code_negative(pending_peer);
  if(pairing_request==2)gap_ssp_passkey_negative(pending_peer);
  if(pairing_request==3)gap_ssp_confirmation_negative(pending_peer);
  clear_pin();
}
static void begin_pair(void){
  pairing=true;pair_end=ms()+120000;
  gap_set_bondable_mode(1);gap_connectable_control(1);gap_discoverable_control(1);
  notice("Pair from mooInk Bluetooth settings (120 seconds).");
}
static void key_fail(const char *why){
  if(key_state!=IDLE||raw_count)notice(why);
  key_state=IDLE;clear_queue();
}
static void reconnect(void){
  if(!ready||!paired||connected||connecting)return;
  if(!expired(reconnect_at))return;
  reconnect_at=ms()+5000;
  connecting=true;connect_end=ms()+20000;
  uint8_t rc=hid_device_connect(peer,&pending_cid);
  if(rc){connecting=false;pending_cid=0;}
}
static void raw_next(void){
  if(!connected||!paired||key_state!=IDLE||!raw_count)return;
  terminal_key_t k=raw_queue[raw_head];
  raw_head=(raw_head+1)%RAW_QUEUE_SIZE;--raw_count;
  key_mod=k.mod;key_usage=k.usage;key_state=DOWN;key_end=ms()+5000;
  hid_device_request_can_send_now_event(cid);
}
static void pairing_input(uint8_t ch){
  // Pairing challenge only; input is never forwarded as a keyboard report.
  if(ch==8||ch==127){if(pin_len)pin[--pin_len]=0;return;}
  if(ch>='0'&&ch<='9'){
    if(pin_len<16){pin[pin_len++]=(char)ch;pin[pin_len]=0;}
    return;
  }
  if(ch!='\r'&&ch!='\n')return;
  raw_cr=ch=='\r';
  if(!pin_len)return;
  unsigned req=pairing_request;
  if(!allowed(pending_peer)){clear_pin();return;}
  if(req==1){
    pairing_request=0;gap_pin_code_response(pending_peer,pin);
  }else if(req==2&&pin_len==6){
    pairing_request=0;gap_ssp_passkey_response(pending_peer,(uint32_t)strtoul(pin,NULL,10));
  }else if(req==3&&pin_len==6&&(unsigned)strtoul(pin,NULL,10)==compare_number){
    pairing_request=0;gap_ssp_confirmation_response(pending_peer);
  }else{
    memset(pin,0,sizeof(pin));pin_len=0;
    notice("Incorrect pairing code; try again.");return;
  }
  clear_pin();notice("Pairing response sent.");
}
static void raw_input(uint8_t ch){
  if(ch=='\n'&&raw_cr){raw_cr=false;return;}
  raw_cr=ch=='\r';
  if(pairing_request){pairing_input(ch);return;}
  // Do not turn terminal escape sequences into stray printable keys.
  if(escape_state&&expired(escape_end))escape_state=0;
  if(escape_state==1){escape_state=(ch=='['||ch=='O')?2:0;return;}
  if(escape_state==2){if(ch>=0x40&&ch<=0x7e)escape_state=0;return;}
  if(ch==27){escape_state=1;escape_end=ms()+250;return;}
  terminal_key_t k;
  if(!terminal_key(ch,&k))return;
  // Plain echo, including spaces: no per-key logs or command grammar.
  if(k.usage==40){putchar_raw('\r');putchar_raw('\n');}
  else if(k.usage==42){putchar_raw('\b');putchar_raw(' ');putchar_raw('\b');}
  else putchar_raw(ch);
  if(!paired||!connected){
    notice("Bluetooth not connected; key not sent.");return;
  }
  if(raw_count==RAW_QUEUE_SIZE){notice("Keyboard queue full; key not sent.");return;}
  raw_queue[(raw_head+raw_count)%RAW_QUEUE_SIZE]=k;++raw_count;
  raw_next();
}
static void report(uint8_t mod,uint8_t usage){
  uint8_t data[]={0xa1,1,mod,0,usage,0,0,0,0,0};
  hid_device_send_interrupt_message(cid,data,sizeof(data));
}
static void tick(btstack_timer_source_t *t){
  for(unsigned i=0;i<64;++i){
    int ch=getchar_timeout_us(0);
    if(ch<0)break;
    raw_input((uint8_t)ch);
  }
  if(pairing&&expired(pair_end)){stop_pair();notice("Pairing window ended; reconnect Pico to try again.");}
  if(connecting&&expired(connect_end)){
    // Wait for close/failure before reconnecting, no delayed keystrokes.
    connecting=false;
    if(pending_cid){hid_device_disconnect(pending_cid);pending_cid=0;}
    reconnect_at=ms()+5000;
  }
  reconnect();
  if(key_state!=IDLE&&expired(key_end)){
    key_fail("Bluetooth key timed out.");if(connected)hid_device_disconnect(cid);
  }else if(key_state==HOLD&&expired(release_at)){
    key_state=UP;hid_device_request_can_send_now_event(cid);
  }
  raw_next();
  btstack_run_loop_set_timer(t,20);btstack_run_loop_add_timer(t);
}
static void packet(uint8_t type,uint16_t channel,uint8_t *p,uint16_t size){
  (void)channel;(void)size;if(type!=HCI_EVENT_PACKET)return;bd_addr_t a;
  switch(hci_event_packet_get_type(p)){
  case BTSTACK_EVENT_STATE:
    if(btstack_event_state_get_state(p)!=HCI_STATE_WORKING||ready)return;
    btstack_tlv_get_instance(&tlv,&tlv_ctx);
    paired=tlv&&tlv->get_tag(tlv_ctx,PEER_TAG,peer,6)==6;
    ready=true;
    if(paired){stop_pair();reconnect_at=ms();}
    else begin_pair();
    return;
  case HCI_EVENT_PIN_CODE_REQUEST:
    hci_event_pin_code_request_get_bd_addr(p,a);
    if(!allowed(a)){gap_pin_code_negative(a);return;}
    clear_pin();memcpy(pending_peer,a,6);pairing_request=1;
    notice("Type the mooInk pairing PIN, then Enter.");return;
  case HCI_EVENT_USER_PASSKEY_REQUEST:
    hci_event_user_passkey_request_get_bd_addr(p,a);
    if(!allowed(a)){gap_ssp_passkey_negative(a);return;}
    clear_pin();memcpy(pending_peer,a,6);pairing_request=2;
    notice("Type the six-digit mooInk pairing code, then Enter.");return;
  case HCI_EVENT_USER_CONFIRMATION_REQUEST:
    hci_event_user_confirmation_request_get_bd_addr(p,a);
    if(!allowed(a)){gap_ssp_confirmation_negative(a);return;}
    clear_pin();memcpy(pending_peer,a,6);pairing_request=3;
    compare_number=(unsigned)hci_event_user_confirmation_request_get_numeric_value(p);
    printf("\nConfirm code %06u matches mooInk: type the same six digits, then Enter.\n",compare_number);return;
  case HCI_EVENT_HID_META:
    switch(hci_event_hid_meta_get_subevent_code(p)){
    case HID_SUBEVENT_CONNECTION_OPENED:{
      uint16_t incoming=hid_subevent_connection_opened_get_hid_cid(p);
      if(hid_subevent_connection_opened_get_status(p)){
        connecting=false;pending_cid=0;clear_pin();return;
      }
      hid_subevent_connection_opened_get_bd_addr(p,a);
      if(!allowed(a)||connected){hid_device_disconnect(incoming);return;}
      if(!paired){
        if(!tlv||tlv->store_tag(tlv_ctx,PEER_TAG,a,6)!=0){
          hid_device_disconnect(incoming);notice("Pairing save failed.");return;
        }
        memcpy(peer,a,6);paired=true;
      }
      cid=incoming;connected=true;connecting=false;pending_cid=0;
      clear_pin();stop_pair();notice("Bluetooth connected.");return;
    }
    case HID_SUBEVENT_CONNECTION_CLOSED:
      if(hid_subevent_connection_closed_get_hid_cid(p)==cid){
        key_fail("Bluetooth disconnected; pending keys discarded.");
        connected=false;cid=0;reconnect_at=ms()+5000;
      }
      return;
    case HID_SUBEVENT_CAN_SEND_NOW:
      if(!connected||hid_subevent_can_send_now_get_hid_cid(p)!=cid)return;
      if(key_state==DOWN){report(key_mod,key_usage);key_state=HOLD;release_at=ms()+60;}
      else if(key_state==UP){report(0,0);key_state=IDLE;}
      return;
    default:return;
    }
  default:return;
  }
}
int main(void){
  stdio_init_all();if(cyw43_arch_init())panic("CYW43 init failed");
  l2cap_init();sm_init();sdp_init();
  gap_set_local_name(NAME);gap_set_class_of_device(0x2540);
  gap_set_default_link_policy_settings(LM_LINK_POLICY_ENABLE_ROLE_SWITCH|LM_LINK_POLICY_ENABLE_SNIFF_MODE);
  gap_set_allow_role_switch(true);
  gap_ssp_set_io_capability(SSP_IO_CAPABILITY_KEYBOARD_ONLY);
  gap_ssp_set_authentication_requirement(SSP_IO_AUTHREQ_MITM_PROTECTION_REQUIRED_GENERAL_BONDING);
  gap_ssp_set_auto_accept(false);gap_set_bondable_mode(0);
  gap_discoverable_control(0);gap_connectable_control(0);
  hid_sdp_record_t params={
    .hid_device_subclass=0x2540,.hid_country_code=33,.hid_virtual_cable=1,
    .hid_remote_wake=1,.hid_reconnect_initiate=1,.hid_normally_connectable=true,
    .hid_boot_device=false,.hid_ssr_host_max_latency=1600,.hid_ssr_host_min_timeout=3200,
    .hid_supervision_timeout=3200,.hid_descriptor=descriptor,
    .hid_descriptor_size=sizeof(descriptor),.device_name=NAME
  };
  hid_create_sdp_record(service,sdp_create_service_record_handle(),&params);
  sdp_register_service(service);
  hid_device_init(false,sizeof(descriptor),descriptor);
  hci_cb.callback=packet;hci_add_event_handler(&hci_cb);hid_device_register_packet_handler(packet);
  btstack_run_loop_set_timer_handler(&timer,tick);
  btstack_run_loop_set_timer(&timer,20);btstack_run_loop_add_timer(&timer);
  hci_power_control(HCI_POWER_ON);btstack_run_loop_execute();return 0;
}
