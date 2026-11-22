/* Pico 2 W USB CDC to Bluetooth Classic HID keyboard. No automatic keystrokes. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pico/stdlib.h"
#include "pico/cyw43_arch.h"
#include "btstack.h"
#include "btstack_stdin.h"
#include "btstack_tlv.h"

#define NAME "mooInk Pico2W Keyboard"
#define PEER_TAG 0x4d4b4254u
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
static bd_addr_t peer, pending_peer;
static bool ready, paired, pairing, connected, connecting;
static uint16_t cid, pending_cid;
static uint32_t pair_end,key_end,release_at,connect_end;
static unsigned pairing_request;
static enum {IDLE,DOWN,HOLD,UP} key_state;
static uint8_t key_usage,key_mod;
static unsigned key_hold_ms=60;
static bool key_extended;
static char line[100];
static unsigned line_len;
static bool overflow;

static uint32_t ms(void){return to_ms_since_boot(get_absolute_time());}
static bool expired(uint32_t at){return (int32_t)(ms()-at)>=0;}
static bool allowed(const bd_addr_t a){return paired?memcmp(peer,a,6)==0:pairing;}
static void stop_pair(void){
  pairing=false;
  gap_discoverable_control(0);gap_set_bondable_mode(0);
  gap_connectable_control(paired?1:0);
  if(pairing_request==1)gap_pin_code_negative(pending_peer);
  if(pairing_request==2)gap_ssp_passkey_negative(pending_peer);
  if(pairing_request==3)gap_ssp_confirmation_negative(pending_peer);
  pairing_request=0;
}
static void key_fail(const char *why){
  if(key_state!=IDLE){key_state=IDLE;printf("ERR %s %s\n",key_extended?"KEYH":"KEY",why);}
}
static void report(uint8_t mod,uint8_t usage){
  uint8_t data[]={0xa1,1,mod,0,usage,0,0,0,0,0};
  hid_device_send_interrupt_message(cid,data,sizeof(data));
}
static void command(char *s){
  unsigned mod,usage,hold;char tail;
  if(!strcmp(s,"HELLO")){puts("OK HELLO MOOINK_PICO2W_BT/1");return;}
  if(!strcmp(s,"STATUS")){
    printf("OK STATUS ready=%u paired=%u connected=%u pairing=%u connecting=%u peer=%s\n",
        ready,paired,connected,pairing,connecting,paired?bd_addr_to_str(peer):"-");return;
  }
  if(!ready){puts("ERR NOT_READY");return;}
  if(!strcmp(s,"PAIR")){
    if(paired||connected||connecting||pairing){puts("ERR PAIR FORGET_OR_CANCEL_FIRST");return;}
    pairing=true;pairing_request=0;pair_end=ms()+120000;
    gap_set_bondable_mode(1);gap_connectable_control(1);gap_discoverable_control(1);
    puts("OK PAIR 120");return;
  }
  if(!strcmp(s,"CANCEL")){stop_pair();puts("OK CANCEL");return;}
  if(!strcmp(s,"FORGET")){
    if(connected||connecting||pairing){puts("ERR FORGET DISCONNECT_OR_CANCEL_FIRST");return;}
    if(paired){gap_drop_link_key_for_bd_addr(peer);tlv->delete_tag(tlv_ctx,PEER_TAG);}
    paired=false;memset(peer,0,6);stop_pair();puts("OK FORGET");return;
  }
  if(!strcmp(s,"CONNECT")){
    if(connected||connecting){puts("OK CONNECT");return;}
    if(!paired){puts("ERR CONNECT NOT_PAIRED");return;}
    connecting=true;connect_end=ms()+20000;
    uint8_t rc=hid_device_connect(peer,&pending_cid);
    if(rc){connecting=false;printf("ERR CONNECT %u\n",rc);return;}
    puts("OK CONNECT");return;
  }
  if(!strcmp(s,"DISCONNECT")){
    if(key_state!=IDLE||connecting){puts("ERR DISCONNECT BUSY");return;}
    if(connected)hid_device_disconnect(cid);
    puts("OK DISCONNECT");return;
  }
  if(!strcmp(s,"CONFIRM")){
    if(pairing_request!=3||!allowed(pending_peer)){puts("ERR CONFIRM NO_REQUEST");return;}
    pairing_request=0;gap_ssp_confirmation_response(pending_peer);puts("OK CONFIRM");return;
  }
  if(!strncmp(s,"PIN ",4)){
    const char *pin=s+4;size_t n=strlen(pin);
    if(!pairing_request||!allowed(pending_peer)||n<1||n>16||strspn(pin,"0123456789")!=n){
      puts("ERR PIN INVALID_OR_NO_REQUEST");return;
    }
    if(pairing_request==2&&n==6){pairing_request=0;gap_ssp_passkey_response(pending_peer,(uint32_t)strtoul(pin,NULL,10));}
    else if(pairing_request==1){pairing_request=0;gap_pin_code_response(pending_peer,pin);}
    else {puts("ERR PIN EXPECTED_SIX_DIGITS_OR_CONFIRM");return;}
    puts("OK PIN");return;
  }
  // KEY retains its original 60 ms semantics. KEYH is opt-in test only.
  hold=60;
  bool is_key=sscanf(s,"KEY %u %u %c",&mod,&usage,&tail)==2;
  bool is_hold=false;
  if(!is_key){
    is_hold=sscanf(s,"KEYH %u %u %u %c",&mod,&usage,&hold,&tail)==3;
  }
  if(is_key||is_hold){
    if(!connected||!paired){puts("ERR KEY NOT_CONNECTED");return;}
    if(key_state!=IDLE){puts("ERR KEY BUSY");return;}
    if(mod>255||usage<4||usage>231||hold<20||hold>2000){puts("ERR KEY INVALID_USAGE_OR_HOLD");return;}
    key_hold_ms=hold;key_extended=is_hold;
    key_mod=(uint8_t)mod;key_usage=(uint8_t)usage;key_end=ms()+hold+5000;key_state=DOWN;
    hid_device_request_can_send_now_event(cid);return;
  }
  puts("ERR UNKNOWN_COMMAND");
}
static void serial_char(char c){
  if(c=='\r'||c=='\n'){
    if(overflow)puts("ERR LINE_TOO_LONG");
    else if(line_len){line[line_len]=0;command(line);}
    line_len=0;overflow=false;return;
  }
  if(c<32||c>126||line_len>=sizeof(line)-1){overflow=true;return;}
  if(!overflow)line[line_len++]=c;
}
static void tick(btstack_timer_source_t *t){
  if(pairing&&expired(pair_end)){stop_pair();puts("EVENT PAIR_TIMEOUT");}
  if(connecting&&expired(connect_end)){
    connecting=false;if(pending_cid)hid_device_disconnect(pending_cid);
    puts("EVENT CONNECT_TIMEOUT");
  }
  if(key_state!=IDLE&&expired(key_end)){
    key_fail("SEND_TIMEOUT");if(connected)hid_device_disconnect(cid);
  }else if(key_state==HOLD&&expired(release_at)){
    key_state=UP;hid_device_request_can_send_now_event(cid);
  }
  btstack_run_loop_set_timer(t,20);btstack_run_loop_add_timer(t);
}
static void packet(uint8_t type,uint16_t channel,uint8_t *p,uint16_t size){
  (void)channel;(void)size;if(type!=HCI_EVENT_PACKET)return;bd_addr_t a;
  switch(hci_event_packet_get_type(p)){
  case BTSTACK_EVENT_STATE:
    if(btstack_event_state_get_state(p)!=HCI_STATE_WORKING)return;
    btstack_tlv_get_instance(&tlv,&tlv_ctx);
    paired=tlv&&tlv->get_tag(tlv_ctx,PEER_TAG,peer,6)==6;
    ready=true;stop_pair();puts("EVENT READY MOOINK_PICO2W_BT/1");return;
  case HCI_EVENT_PIN_CODE_REQUEST:
    hci_event_pin_code_request_get_bd_addr(p,a);
    if(!allowed(a)){gap_pin_code_negative(a);return;}
    memcpy(pending_peer,a,6);pairing_request=1;puts("EVENT PIN_REQUIRED LEGACY");return;
  case HCI_EVENT_USER_PASSKEY_REQUEST:
    hci_event_user_passkey_request_get_bd_addr(p,a);
    if(!allowed(a)){gap_ssp_passkey_negative(a);return;}
    memcpy(pending_peer,a,6);pairing_request=2;puts("EVENT PIN_REQUIRED SSP_6_DIGITS");return;
  case HCI_EVENT_USER_CONFIRMATION_REQUEST:
    hci_event_user_confirmation_request_get_bd_addr(p,a);
    if(!allowed(a)){gap_ssp_confirmation_negative(a);return;}
    memcpy(pending_peer,a,6);pairing_request=3;
    printf("EVENT CONFIRM_REQUIRED %06u\n",(unsigned)hci_event_user_confirmation_request_get_numeric_value(p));return;
  case HCI_EVENT_HID_META:
    switch(hci_event_hid_meta_get_subevent_code(p)){
    case HID_SUBEVENT_CONNECTION_OPENED:{
      uint16_t incoming=hid_subevent_connection_opened_get_hid_cid(p);
      if(hid_subevent_connection_opened_get_status(p)){
        connecting=false;pending_cid=0;puts("EVENT CONNECT_FAILED");return;
      }
      hid_subevent_connection_opened_get_bd_addr(p,a);
      if(!allowed(a)||connected){hid_device_disconnect(incoming);puts("EVENT HOST_REJECTED");return;}
      if(!paired){
        if(!tlv||tlv->store_tag(tlv_ctx,PEER_TAG,a,6)!=0){
          hid_device_disconnect(incoming);puts("EVENT PAIR_SAVE_FAILED");return;
        }
        memcpy(peer,a,6);paired=true;
      }
      cid=incoming;connected=true;connecting=false;pending_cid=0;
      pairing_request=0;stop_pair();
      printf("EVENT CONNECTED %s\n",bd_addr_to_str(peer));return;
    }
    case HID_SUBEVENT_CONNECTION_CLOSED:
      if(hid_subevent_connection_closed_get_hid_cid(p)==cid){
        key_fail("DISCONNECTED");connected=false;cid=0;puts("EVENT DISCONNECTED");
      }
      return;
    case HID_SUBEVENT_CAN_SEND_NOW:
      if(!connected||hid_subevent_can_send_now_get_hid_cid(p)!=cid)return;
      if(key_state==DOWN){
        report(key_mod,key_usage);key_state=HOLD;release_at=ms()+key_hold_ms;
        if(key_extended)printf("EVENT KEY DOWN mod=%u usage=%u hold_ms=%u\n",key_mod,key_usage,key_hold_ms);
      }else if(key_state==UP){
        report(0,0);key_state=IDLE;
        if(key_extended){puts("EVENT KEY UP");puts("OK KEYH SENT");}
        else puts("OK KEY SENT");
      }
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
  btstack_stdin_setup(serial_char);
  btstack_run_loop_set_timer_handler(&timer,tick);
  btstack_run_loop_set_timer(&timer,20);btstack_run_loop_add_timer(&timer);
  hci_power_control(HCI_POWER_ON);btstack_run_loop_execute();return 0;
}
