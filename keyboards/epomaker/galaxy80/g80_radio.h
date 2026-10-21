// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Radio module link (BT + 2.4G) on UART2: P0.0 RX, P0.1 TX, 115200 8N1, protocol of the stock
// firmware (docs/protocol/radio-uart.md), and the wireless mode.
// Slider ON -> keys over the radio (A1 / A3 / A8 frames), USB stopped like the stock firmware;
// Fn+Q/W/E = BT1-3, Fn+R = 2.4G, held 3 s = pairing; slider OFF -> A6 11 and USB again.
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "action.h"
#include "galaxy80.h"

#define G80_RADIO_LOG 8 /* last frames kept for the debug console */

/* First byte of a frame to the module. */
enum g80_frame_type {
    G80_FT_KBD      = 0xA1, /* modifiers + keys */
    G80_FT_NKRO     = 0xA2,
    G80_FT_CONSUMER = 0xA3,
    G80_FT_SYSTEM   = 0xA4,
    G80_FT_CMD      = 0xA6, /* command, second byte = enum g80_cmd */
    G80_FT_MOUSE    = 0xA8,
};

/* A6 commands (the codes the stock firmware sends). */
enum g80_cmd {
    G80_CMD_WAKE    = 0x00, /* wake the module / reconnect */
    G80_CMD_USB     = 0x11, /* USB mode */
    G80_CMD_24G     = 0x30,
    G80_CMD_BT1     = 0x31,
    G80_CMD_BT2     = 0x32,
    G80_CMD_BT3     = 0x33,
    G80_CMD_PAIR    = 0x51,
    G80_CMD_BATTERY = 0x53, /* request the charge level */
};

/* Link state reported by the module (g80_radio_stats.link, frame 5B). */
enum g80_link {
    G80_LINK_PAIRING   = 0x31,
    G80_LINK_CONNECTED = 0x32,
    G80_LINK_LOST      = 0x33,
};

typedef struct {
    uint32_t rx_bytes, rx_frames, rx_bad, rx_overrun, tx_frames, acks_rx, acks_tx;
    uint8_t  battery;     /* 5C p, 0xFF = never received */
    uint8_t  host_leds;   /* 5A v */
    uint8_t  link;        /* 31 pairing, 32 connected, 33 disconnected, 0 = unknown / just switched */
    uint8_t  log_head;    /* next slot of log[] */
    uint8_t  log[G80_RADIO_LOG][4]; /* [0] = 'R'/'T', [1..3] = frame (first 3 bytes) */
    uint16_t tx_dropped;  /* frames refused, queue full */
    uint16_t tx_lost;     /* frames given up after 3 tries without ACK */
    uint16_t tx_stuck;    /* TX aborted by the THRE timeout */
} g80_radio_stats_t;

extern g80_radio_stats_t g80_radio_stats;

void g80_radio_init(void);
void g80_radio_task(void);
bool g80_radio_request(uint8_t code); /* queue A6 <code>; false if not a stock code or queue full */

/* True when nothing is queued or in flight on the UART (no frame, no owed ACK) and the last byte has
 * left the transmitter (TEMT). Deep sleep may start only then. */
bool g80_radio_idle(void);

/* Pairing in progress: 5B 31 and still inside the 20 s pairing window. */
bool g80_radio_pairing(void);

/* The deep-sleep code calls this once it is awake again: flushes the half-received frame, asks for a
 * fresh battery level once linked, and restarts the 3 s steady channel indication. */
void g80_radio_on_wake(void);

/* Runtime option "Wireless NKRO" (true = hybrid A1 + A2 bitmap, false = the 6-key A1 fold); reads
 * g80_cfg_wl_nkro(). */
bool g80_wl_nkro_enabled(void);

bool g80_wl_active(void);                          /* keys go to the radio */
void g80_wl_suspend_check(void);                   /* from the USB suspend loop */
bool g80_wl_process_record(keyrecord_t *record);   /* Fn+Q/W/E/R */
bool g80_wl_indicator(uint8_t *led, uint8_t *r, uint8_t *g, uint8_t *b); /* channel key colour */
