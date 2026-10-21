// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Radio module (BT + 2.4G) on UART2 and the wireless mode (g80_radio.h). Register-level, no ChibiOS
// serial driver: the IRQ moves RX bytes into a ring and feeds TX one byte per THRE; framing, ACKs,
// retries and the mode logic run in the main loop (g80_radio_task, from housekeeping_task_kb).
// Protocol facts: docs/protocol/radio-uart.md.
//
// Hardware rules found on the real keyboard:
//  - UART2 TX takes ONE byte per THRE (holding register empty), like the stock firmware v1.10
//    (@0x650A + ISR @0x6454). Three bytes written back to back into the "FIFO" reached the module as
//    garbage, never answered.
//  - the P0.1 GPIO latch is set to 1 (the stock firmware gets it through read-modify-write on P0 DATA).
// Safety rules:
//  - A6 commands: only the codes the stock firmware sends (whitelist in g80_radio_request);
//  - data frames: A1 keyboard, A2 NKRO bitmap (16 B), A3 consumer, A4 system control, A8 mouse. No
//    2.4G configuration tunnel (81 frames are dropped, never answered), no A9 name, no AB events
//    (in particular never AB A6 FF 01 after a wake);
//  - every frame, the 16-byte A2 included, leaves one byte per THRE: there is no other TX path;
//  - the IRQ handler is bounded (at most 32 RX bytes, one TX byte per entry) and touches no OS object.
#include <string.h>
#include "quantum.h"
#include "host.h"
#include "usb_main.h"
#include "galaxy80.h"
#include "g80_radio.h"
#include "g80_battery.h"
#include "g80_settings.h"
#include "g80_lpm.h"

#define G80_UART_LS_RDR (1u << 0)
#define G80_UART_LS_ERR ((1u << 1) | (1u << 2) | (1u << 3) | (1u << 4)) /* OE, PE, FE, BI */
#define G80_UART_LS_THRE (1u << 5)
#define G80_UART_LS_TEMT (1u << 6)
#define G80_UART_IE_RDA (1u << 0)
#define G80_UART_IE_THRE (1u << 1)
#define G80_UART_IE_RLS (1u << 2)
#define G80_UART2_CLK_BIT (1u << 18) /* SYS1 AHBCLKEN */
#define G80_UART_DIV 26              /* 48 MHz / 16 / 26 = 115385 baud */

#define G80_RX_RING 64u /* power of two */
#define G80_RX_GAP_MS 3 /* a partial frame older than this is dropped (stock: ~1 ms of silence) */
#define G80_TX_GAP_MS 2 /* idle time between two frames (stock: 2 SysTicks after TEMT) */
#define G80_RETRY_MS 5  /* stock: data frames resent every 5 ms ... */
#define G80_RETRIES 3   /* ... at most 3 times, until the module answers 61 0D 0A */
#define G80_BAT_POLL_MS 60000u /* battery request while linked (stock: 5 s; the level changes slowly) */
#define G80_PAIR_HOLD_MS 3000 /* stock: Fn+Q/W/E/R held 3 s -> A6 51, 3 times 0.5 s apart */
#define G80_TXQ 6             /* frames waiting behind the one in flight */

#define G80_FRAME_MAX 16 /* longest frame sent: A2 (NKRO bitmap) */
#define G80_A2_BYTES 14  /* A2 carries the usages 0x00..0x6F */
#define G80_A1_KEYS 5    /* hybrid NKRO: A1 holds 5 keys in press order, the rest goes to A2 */
#define G80_PAIR_WIN_MS 20000u /* stock: the pairing indication runs for 20 s */
#define G80_LOST_WIN_MS 10000u /* stock: the reconnect indication runs for 10 s */

/* Frame kinds. A report carries the whole state, so a queued one of the same kind is replaced. */
enum { G80_F_CMD = 0, G80_F_KBD, G80_F_EXTRA, G80_F_MOUSE, G80_F_NKRO, G80_F_SYS };

typedef struct {
    uint8_t type, len;
    uint8_t retried; /* a keyboard / NKRO report given up once is queued again once (no stuck key) */
    uint8_t b[G80_FRAME_MAX];
} g80_frame_t;

g80_radio_stats_t g80_radio_stats;

static volatile uint8_t g80_rx_ring[G80_RX_RING];
static volatile uint8_t g80_rx_head; /* written by the IRQ only */
static volatile uint8_t g80_rx_tail; /* written by the main loop only */
static volatile uint8_t g80_tx_buf[G80_FRAME_MAX], g80_tx_n, g80_tx_i;

static uint8_t  g80_fb[34]; /* frame being assembled */
static uint8_t  g80_rfl, g80_need; /* RX frame fill / length wanted (named apart from g80_fn.c's g80_fl, which the emulator looks up) */
static uint16_t g80_fb_time;

static g80_frame_t g80_q[G80_TXQ];
static uint8_t     g80_q_len;
static g80_frame_t g80_cur;       /* frame in flight (retried until ACK) */
static bool        g80_cur_valid, g80_cur_first;
static uint8_t     g80_cur_tries;
static uint16_t    g80_cur_time, g80_tx_time;
static uint32_t    g80_bat_time;    /* 32 bit: the 60 s poll period is close to the 16 bit timer wrap */
static bool        g80_bat_wake;    /* a wake asked for a fresh battery level as soon as linked */
static bool        g80_ack_pending; /* 61 0D 0A owed to the module */
static bool        g80_started;

/* wireless mode */
static bool           g80_wl_on;      /* keys go to the radio, USB stopped */
static host_driver_t *g80_usb_driver; /* QMK's USB driver, restored on the way back */
static uint8_t        g80_leds;       /* host LEDs from 5A */
static uint8_t        g80_pair_col;   /* matrix column of the held Fn+Q/W/E/R, 0 = none */
static uint16_t       g80_pair_t0;
static uint8_t        g80_pair_sent;
static uint32_t       g80_link_t;     /* when the link code last changed */
static uint8_t        g80_susp_on;    /* suspend-loop samples with the slider ON */
static uint32_t       g80_win_t;      /* g80_link_t of the window whose end was handled (once) */
static uint8_t        g80_a1k[6]; /* hybrid NKRO: keys in the A1 slots, press order, 0 = free */
static uint8_t        g80_a2[G80_A2_BYTES]; /* hybrid NKRO: extra keys, the A2 bitmap */
static bool           g80_a2_chg;     /* g80_a2 changed since the last A2 was queued */

static void g80_log(char dir, const uint8_t *f) {
    uint8_t *e = g80_radio_stats.log[g80_radio_stats.log_head];
    e[0]       = (uint8_t)dir;
    e[1]       = f[0];
    e[2]       = f[1];
    e[3]       = f[2];
    g80_radio_stats.log_head = (uint8_t)((g80_radio_stats.log_head + 1) % G80_RADIO_LOG);
}

/* UART2 IRQ (vector 0x78, IRQ14). Not kernel-aware: no OS call, so no OSAL prologue/epilogue. */
void SN32_UART2_HANDLER(void) {
    for (uint8_t n = 0; n < 32; n++) {
        const uint32_t ls = SN_UART2->LS;
        if (ls & G80_UART_LS_ERR) {
            g80_radio_stats.rx_overrun++;
        }
        if (!(ls & G80_UART_LS_RDR)) {
            break;
        }
        const uint8_t b  = (uint8_t)SN_UART2->RB;
        const uint8_t h  = g80_rx_head;
        const uint8_t nh = (uint8_t)((h + 1u) & (G80_RX_RING - 1u));
        if (nh == g80_rx_tail) {
            g80_radio_stats.rx_overrun++; /* ring full: byte dropped */
        } else {
            g80_rx_ring[h] = b;
            g80_rx_head    = nh;
        }
    }
    if ((SN_UART2->IE & G80_UART_IE_THRE) && (SN_UART2->LS & G80_UART_LS_THRE)) {
        if (g80_tx_i < g80_tx_n) {
            SN_UART2->TH = g80_tx_buf[g80_tx_i++]; /* one byte per THRE */
        }
        if (g80_tx_i >= g80_tx_n) {
            SN_UART2->IE &= ~G80_UART_IE_THRE;
        }
    }
    (void)SN_UART2->II;
}

void g80_radio_init(void) {
    SN_SYS1->AHBCLKEN |= G80_UART2_CLK_BIT; /* UART2 clock (stock @0x64AC) */
    SN_GPIO0->BSET = 3u;           /* P0.1 latch = 1 (see the top), P0.0 harmless */
    /* stock @0x64BA..0x64D8: 115385 baud, 8N1, FIFO on, RX trigger 1 byte */
    SN_UART2->LC       = 0x83; /* DLAB */
    SN_UART2->FD       = 0;
    SN_UART2->DLM      = 0;
    SN_UART2->DLL      = G80_UART_DIV;
    SN_UART2->LC       = 0x03;
    SN_UART2->FIFOCTRL = 0x07;
    SN_UART2->IE       = G80_UART_IE_RDA | G80_UART_IE_RLS; /* THRE only while a frame goes out */
    SN_UART2->CTRL     = 0xC1;                              /* UART + RX + TX enable */
    g80_rx_head = g80_rx_tail = 0;
    g80_tx_n = g80_tx_i       = 0;
    g80_radio_stats.battery   = 0xFF;
    NVIC_ClearPendingIRQ(UART2_IRQn);
    NVIC_SetPriority(UART2_IRQn, 2); /* above the LED ISR (3), below USB / SysTick */
    NVIC_EnableIRQ(UART2_IRQn);
    g80_started  = false;
    g80_tx_time  = timer_read();
    g80_bat_time = timer_read32();
    g80_q_len    = 0;
}

static bool g80_enqueue(uint8_t type, const uint8_t *b, uint8_t len) {
    if (type != G80_F_CMD) {
        /* reports carry the whole state: a newer one replaces the queued one of the same kind */
        for (uint8_t i = 0; i < g80_q_len; i++) {
            if (g80_q[i].type == type) {
                memcpy(g80_q[i].b, b, len);
                g80_q[i].len = len;
                return true;
            }
        }
    }
    if (g80_q_len >= G80_TXQ) {
        g80_radio_stats.tx_dropped++;
        return false;
    }
    g80_q[g80_q_len].type    = type;
    g80_q[g80_q_len].len     = len;
    g80_q[g80_q_len].retried = 0;
    memcpy(g80_q[g80_q_len].b, b, len);
    g80_q_len++;
    return true;
}

static uint8_t g80_sum(const uint8_t *b, uint8_t n) {
    uint8_t s = 0;
    for (uint8_t i = 0; i < n; i++) {
        s += b[i];
    }
    return s;
}

/* Queue an A6 command. Whitelist = the A6 codes the stock firmware sends (enum g80_cmd): wake-up,
 * USB, 2.4G, BT1-3, pairing, battery. 52 (overwritten in the stock firmware), 34/35 (unreachable
 * slots), 55-58 / 62-66 / 70 (never sent by the stock firmware) stay refused. */
bool g80_radio_request(uint8_t c) {
    switch (c) {
        case G80_CMD_WAKE:
        case G80_CMD_USB:
        case G80_CMD_24G:
        case G80_CMD_BT1:
        case G80_CMD_BT2:
        case G80_CMD_BT3:
        case G80_CMD_PAIR:
        case G80_CMD_BATTERY:
            break;
        default:
            return false;
    }
    const uint8_t f[3] = {G80_FT_CMD, c, (uint8_t)(G80_FT_CMD + c)};
    return g80_enqueue(G80_F_CMD, f, 3);
}

/* like the stock (0x650A + ISR): byte 0 written here (transmitter idle, checked by the caller),
 * the others by the IRQ, one per THRE */
static void g80_tx(const uint8_t *f, uint8_t n) {
    chSysLock();
    for (uint8_t i = 0; i < n; i++) {
        g80_tx_buf[i] = f[i];
    }
    g80_tx_n     = n;
    g80_tx_i     = 1;
    SN_UART2->TH = f[0];
    if (n > 1) {
        SN_UART2->IE |= G80_UART_IE_THRE;
    }
    chSysUnlock();
    g80_tx_time = timer_read();
    g80_radio_stats.tx_frames++;
    g80_log('T', f);
}

static void g80_frame_done(void) {
    g80_radio_stats.rx_frames++;
    g80_log('R', g80_fb);
    if (g80_fb[0] == 0x81) {
        return; /* 2.4G configuration tunnel (34 B): not served, no ACK (stock answers with 91) */
    }
    if (g80_fb[0] == 0x61) {
        if (g80_fb[1] == 0x0D && g80_fb[2] == 0x0A) {
            g80_radio_stats.acks_rx++;
            if (!g80_cur_first) {
                g80_cur_valid = false; /* the frame in flight was received */
            } /* else: the ACK of the A6 00 sent just before it, the frame itself is still to go */
        } else {
            g80_radio_stats.rx_bad++;
        }
        return;
    }
    if ((uint8_t)(g80_fb[0] + g80_fb[1]) != g80_fb[2]) {
        g80_radio_stats.rx_bad++;
        return;
    }
    if (g80_fb[0] == 0x5B && (g80_fb[1] == 0x42 || g80_fb[1] == 0x43)) {
        return; /* stock: module ready / UART closing, no ACK, no state */
    }
    g80_ack_pending = true; /* stock: every other valid 3-byte frame is acknowledged */
    switch (g80_fb[0]) {
        case 0x5A:
            if (g80_fb[1] < 8) {
                g80_radio_stats.host_leds = g80_leds = g80_fb[1];
            }
            g80_radio_stats.link = G80_LINK_CONNECTED; /* stock: LED report = connected */
            break;
        case 0x5B:
            if (g80_fb[1] == G80_LINK_PAIRING || g80_fb[1] == G80_LINK_CONNECTED || g80_fb[1] == G80_LINK_LOST) {
                if (g80_radio_stats.link != g80_fb[1]) {
                    g80_link_t = timer_read32();
                }
                g80_radio_stats.link = g80_fb[1];
                if (g80_fb[1] != G80_LINK_CONNECTED) {
                    /* stock: pairing and link lost clear the host LEDs (no stuck Caps LED) */
                    g80_radio_stats.host_leds = g80_leds = 0;
                }
            }
            if (g80_fb[1] >= 0x21 && g80_fb[1] <= 0x23) {
                g80_battery_rx(0x5B, g80_fb[1]);
            }
            g80_lpm_radio_status(g80_fb[1]); /* idle countdown: 5B 22 / 32 / 33 */
            break;
        case 0x5C:
            g80_radio_stats.battery = g80_fb[1];
            g80_battery_rx(0x5C, g80_fb[1]);
            break;
        default:
            break;
    }
}

static void g80_rx_task(void) {
    while (g80_rx_tail != g80_rx_head) {
        const uint8_t b = g80_rx_ring[g80_rx_tail];
        g80_rx_tail     = (uint8_t)((g80_rx_tail + 1u) & (G80_RX_RING - 1u));
        g80_radio_stats.rx_bytes++;
        if (g80_rfl == 0) {
            if (b == 0x81) {
                g80_need = 34;
            } else if ((b >= 0x5A && b <= 0x5D) || b == 0x60 || b == 0x61) {
                g80_need = 3;
            } else {
                g80_radio_stats.rx_bad++; /* not a frame start: resynchronise */
                continue;
            }
        }
        g80_fb[g80_rfl++] = b;
        g80_fb_time      = timer_read();
        if (g80_rfl >= g80_need) {
            g80_frame_done();
            g80_rfl = 0;
        }
    }
    if (g80_rfl && timer_elapsed(g80_fb_time) > G80_RX_GAP_MS) {
        g80_radio_stats.rx_bad++; /* truncated frame */
        g80_rfl = 0;
    }
}

static void g80_tx_task(void) {
    if (g80_tx_i < g80_tx_n) {
        if (timer_elapsed(g80_tx_time) > 10) { /* 10 bytes need < 1 ms: the THRE IRQ is stuck */
            chSysLock();
            SN_UART2->IE &= ~G80_UART_IE_THRE;
            g80_tx_n = g80_tx_i = 0;
            chSysUnlock();
            g80_radio_stats.tx_stuck++;
        }
        return; /* a frame is still going out */
    }
    if (!(SN_UART2->LS & G80_UART_LS_TEMT) || timer_elapsed(g80_tx_time) < G80_TX_GAP_MS) {
        return;
    }
    if (g80_ack_pending) {
        static const uint8_t ack[3] = {0x61, 0x0D, 0x0A};
        g80_ack_pending             = false;
        g80_radio_stats.acks_tx++;
        g80_tx(ack, 3);
        return;
    }
    if (g80_cur_valid && (g80_cur_first || timer_elapsed(g80_cur_time) >= G80_RETRY_MS)) {
        if (g80_cur_tries >= G80_RETRIES) {
            g80_cur_valid = false; /* no ACK after 3 tries: dropped, like the stock firmware */
            g80_radio_stats.tx_lost++;
            bool newer = false;
            for (uint8_t i = 0; i < g80_q_len; i++) {
                newer |= g80_q[i].type == g80_cur.type;
            }
            if ((g80_cur.type == G80_F_KBD || g80_cur.type == G80_F_NKRO) && !g80_cur.retried && !newer && g80_q_len < G80_TXQ) {
                /* the last key state (A1 / A2) must reach the host (a lost release = a stuck key): once more,
                 * in front of the queue */
                for (uint8_t i = g80_q_len; i > 0; i--) {
                    g80_q[i] = g80_q[i - 1];
                }
                g80_q[0]         = g80_cur;
                g80_q[0].retried = 1;
                g80_q_len++;
            }
        } else {
            g80_cur_tries++;
            g80_cur_first = false;
            g80_cur_time  = timer_read();
            g80_tx(g80_cur.b, g80_cur.len);
            return;
        }
    }
    if (!g80_cur_valid && g80_q_len) {
        g80_cur = g80_q[0];
        for (uint8_t i = 1; i < g80_q_len; i++) {
            g80_q[i - 1] = g80_q[i];
        }
        g80_q_len--;
        /* stock: a data frame while the link is down is preceded by A6 00 (wake / reconnect), which
         * is never retried; A6 00 itself is sent once */
        if (g80_cur.type != G80_F_CMD && g80_radio_stats.link != G80_LINK_CONNECTED) {
            static const uint8_t wake[3] = {G80_FT_CMD, G80_CMD_WAKE, G80_FT_CMD};
            g80_tx(wake, 3);
        }
        g80_cur_valid = true;
        g80_cur_first = true;
        g80_cur_tries = (g80_cur.type == G80_F_CMD && g80_cur.b[1] == G80_CMD_WAKE) ? G80_RETRIES - 1 : 0;
        g80_cur_time  = timer_read();
    }
}

/* ------------------------------------------------------------------------- */
/* QMK host driver for the wireless mode                                      */
/* ------------------------------------------------------------------------- */
static uint8_t g80_wl_leds(void) {
    return g80_leds;
}

static void g80_wl_send_keys(uint8_t mods, const uint8_t *keys) {
    uint8_t f[10] = {G80_FT_KBD, mods, 0};
    memcpy(&f[3], keys, 6);
    f[9] = g80_sum(f, 9);
    g80_enqueue(G80_F_KBD, f, 10);
}

/* 6-key report. (Leaving NKRO clears the keyboard first, so no extra key can be left in A2.) */
static void g80_wl_send_keyboard(report_keyboard_t *r) {
    g80_wl_send_keys(r->mods, r->keys);
}

/* VIA "Wireless NKRO" option (default on). Kept out of line: suite_radio patches this body to flip the
 * option at runtime. */
__attribute__((noinline)) bool g80_wl_nkro_enabled(void) {
    return g80_cfg_wl_nkro();
}

#define G80_NK_HAS(bits, u) (((bits)[(u) >> 3] >> ((u) & 7)) & 1)

/* NKRO as the stock firmware does it (FUN_1E98): A1 = modifiers + the first 5 keys in press order
 * (k6 = 0); from the 6th key on, the usages below 0x70 go to the A2 bitmap, which is sent only when
 * it changes (also the all-zero one after the last extra key). Usages from 0x70 up travel in A1 only
 * and are dropped when it is full. Option off: A1 carries the 6 lowest usages, no A2. */
static void g80_wl_send_nkro(report_nkro_t *r) {
    uint8_t bits[NKRO_REPORT_BITS]; /* keys not placed yet */
    memcpy(bits, r->bits, sizeof bits);
    const bool hybrid = g80_wl_nkro_enabled();
    uint8_t    n      = 0;
    if (!hybrid) {
        memset(g80_a1k, 0, sizeof g80_a1k);
    }
    for (uint8_t i = 0; i < G80_A1_KEYS; i++) { /* A1 keys still held keep their slot, in order */
        const uint8_t k = g80_a1k[i];
        if (k && G80_NK_HAS(bits, k)) {
            g80_a1k[n++] = k;
            bits[k >> 3] &= (uint8_t)~(1u << (k & 7));
        }
    }
    memset(&g80_a1k[n], 0, sizeof g80_a1k - n);
    for (uint8_t i = 0; i < G80_A2_BYTES; i++) { /* extra keys still held stay in A2 */
        const uint8_t keep = hybrid ? g80_a2[i] & bits[i] : 0;
        g80_a2_chg |= keep != g80_a2[i];
        g80_a2[i] = keep;
        bits[i] &= (uint8_t)~keep;
    }
    bits[0] &= ~1u; /* usage 0 is "no key" */
    for (uint8_t u = 1; u < NKRO_REPORT_BITS * 8; u++) { /* new keys, ascending usage */
        if (!G80_NK_HAS(bits, u)) {
            continue;
        }
        if (n < (hybrid ? G80_A1_KEYS : 6)) {
            g80_a1k[n++] = u;
        } else if (hybrid && u < G80_A2_BYTES * 8) {
            g80_a2[u >> 3] |= (uint8_t)(1u << (u & 7));
            g80_a2_chg = true;
        }
    }
    g80_wl_send_keys(r->mods, g80_a1k);
    if (g80_a2_chg) {
        g80_a2_chg    = false;
        uint8_t f[16] = {G80_FT_NKRO};
        memcpy(&f[1], g80_a2, G80_A2_BYTES);
        f[15] = g80_sum(f, 15);
        g80_enqueue(G80_F_NKRO, f, 16);
    }
}

static void g80_wl_send_mouse(report_mouse_t *r) {
    /* A8 b1..b5: buttons, x, y, wheel, pan [I] (stock source 0x20000158[1..5]) */
    uint8_t f[7] = {G80_FT_MOUSE, r->buttons, (uint8_t)r->x, (uint8_t)r->y, (uint8_t)r->v, (uint8_t)r->h, 0};
    f[6]         = g80_sum(f, 6);
    g80_enqueue(G80_F_MOUSE, f, 7);
}

static void g80_wl_send_extra(report_extra_t *r) {
    uint8_t f[4];
    uint8_t type = G80_F_EXTRA, n = 4;
    if (r->report_id == REPORT_ID_CONSUMER) {
        f[0] = G80_FT_CONSUMER;
        f[1] = (uint8_t)r->usage;
        f[2] = (uint8_t)(r->usage >> 8);
    } else if (r->report_id == REPORT_ID_SYSTEM) {
        /* A4 u cs: u = bit 0 Power / 1 Sleep / 2 Wake (usages 0x81..0x83, like the stock report ID 2),
         * 0 = released [I: bit order from the stock HID descriptor, hardware check H-W6] */
        f[0] = G80_FT_SYSTEM;
        f[1] = (uint8_t)(r->usage - 0x81u < 3u ? 1u << (r->usage - 0x81u) : 0);
        type = G80_F_SYS;
        n    = 3;
    } else {
        return;
    }
    f[n - 1] = g80_sum(f, n - 1);
    g80_enqueue(type, f, n);
}

#ifdef RAW_ENABLE
static void g80_wl_send_raw(uint8_t *data, uint8_t length) {
    (void)data;
    (void)length; /* VIA only over the cable (USB is off in wireless, like the stock) */
}
#endif

static host_driver_t g80_wl_driver = {
    g80_wl_leds, g80_wl_send_keyboard, g80_wl_send_nkro, g80_wl_send_mouse, g80_wl_send_extra,
#ifdef RAW_ENABLE
    g80_wl_send_raw,
#endif
};

/* mode code for A6: 0..2 = BT1..3 (31..33), 3 = 2.4G (30) */
static uint8_t g80_wl_code(void) {
    const uint8_t m = g80_wl_mode_get();
    return m == 3 ? G80_CMD_24G : (uint8_t)(G80_CMD_BT1 + m);
}

static void g80_wl_enter(void) {
    clear_keyboard(); /* nothing stays pressed on the USB side */
    if (host_get_driver() != &g80_wl_driver) {
        g80_usb_driver = host_get_driver();
    }
    /* stock FUN_95C: USB off in wireless, even with the cable in (it only charges). Also keeps QMK's
     * USB suspend loop (PC asleep on the cable) from stopping the keyboard. */
    if (USBD1.state != USB_STOP) {
        usbDisconnectBus(&USBD1);
        usbStop(&USBD1);
    }
    g80_leds     = 0;
    g80_pair_col = g80_pair_sent = 0;
    host_set_driver(&g80_wl_driver);
    g80_wl_on = true;
    g80_radio_stats.link = 0;
    g80_link_t           = timer_read32(); /* the indication window runs from here */
    rgb_matrix_set_suspend_state(false); /* in case the USB suspend path had darkened the LEDs */
    g80_radio_request(g80_wl_code());
    g80_bat_time = timer_read32();
    g80_bat_wake = false;
}

static void g80_wl_leave(void) {
    clear_keyboard(); /* empty report over the radio: nothing stays pressed on the host */
    g80_wl_on    = false;
    g80_pair_col = g80_pair_sent = 0; /* no pairing in USB mode */
    g80_radio_request(G80_CMD_USB); /* stock @0x1B1E */
    if (g80_usb_driver) {
        host_set_driver(g80_usb_driver);
    }
    restart_usb_driver(&USBD1); /* stock FUN_8CE: USB re-init (blocks ~50 ms) */
}

bool g80_wl_active(void) {
    return g80_wl_on;
}

/* QMK's USB suspend loop does not run housekeeping: if the slider goes ON while the PC sleeps on the
 * cable, stop USB here so the loop ends and the main loop switches to wireless (~50 ms of samples). */
void g80_wl_suspend_check(void) {
    if (!g80_wl_on && g80_raw_slider_now()) {
        if (++g80_susp_on >= 3 && USBD1.state == USB_SUSPENDED) {
            usbDisconnectBus(&USBD1);
            usbStop(&USBD1);
        }
    } else {
        g80_susp_on = 0;
    }
}

/* Fn+Q/W/E (BT1-3) and Fn+R (2.4G), stock 0x2C28: only with the slider ON. Held 3 s: pairing. */
bool g80_wl_process_record(keyrecord_t *record) {
    const uint8_t r = record->event.key.row, c = record->event.key.col;
    if (r != 2 || c < 1 || c > 4) {
        return true;
    }
    if (!record->event.pressed) {
        if (c == g80_pair_col && g80_pair_sent == 0) {
            g80_pair_col = 0; /* released before 3 s: no pairing (once started, all 3 go out, stock) */
        }
        return true; /* the layer gives KC_NO here: nothing to release */
    }
    if (!g80_wl_on || !IS_LAYER_ON(1)) {
        return true;
    }
    g80_wl_mode_set(c == 4 ? 3 : (uint8_t)(c - 1));
    g80_radio_request(g80_wl_code());
    g80_radio_stats.link = 0;
    g80_link_t           = timer_read32();
    g80_pair_col         = G80_LAYER_LOCKED() ? 0 : c; /* channel switch only: pairing needs Fn held */
    g80_pair_t0          = timer_read();
    g80_pair_sent        = 0;
    return false;
}

/* Channel key (Q/W/E/R), state for the renderer (g80_indicators.c):
 *  - pairing: fast blink for the 20 s pairing window, then off
 *  - link down or just switched: slow blink (256 ms phases) for 10 s, then off
 *  - connected: steady for 3 s from the link coming up, from a channel switch or from a wake
 * 2.4G green, Bluetooth blue. */
bool g80_wl_indicator(uint8_t *led, uint8_t *r, uint8_t *g, uint8_t *b) {
    if (!g80_wl_on) {
        return false;
    }
    static const uint8_t keys[4] = {35, 36, 37, 38}; /* Q W E R */
    const uint8_t        m       = g80_wl_mode_get();
    const uint32_t       t       = timer_read32();
    const uint32_t       age     = timer_elapsed32(g80_link_t);
    bool                 on;
    switch (g80_radio_stats.link) {
        case G80_LINK_PAIRING:
            if (age >= G80_PAIR_WIN_MS) {
                return false;
            }
            on = (t & 0x80) != 0;
            break;
        case G80_LINK_CONNECTED:
            on = age < 3000;
            if (!on) {
                return false;
            }
            break;
        default:
            if (age >= G80_LOST_WIN_MS) {
                return false;
            }
            on = (t & 0x100) != 0;
            break;
    }
    *led = keys[m];
    *r   = 0;
    *g   = on && m == 3 ? 255 : 0; /* 2.4G green, BT blue */
    *b   = on && m != 3 ? 255 : 0;
    return true;
}

/* ------------------------------------------------------------------------- */
/* API for the sleep, indicator and settings code                             */
/* ------------------------------------------------------------------------- */
bool g80_radio_idle(void) {
    return g80_q_len == 0 && !g80_cur_valid && !g80_ack_pending && g80_tx_i >= g80_tx_n && (SN_UART2->LS & G80_UART_LS_TEMT);
}

bool g80_radio_pairing(void) {
    return g80_radio_stats.link == G80_LINK_PAIRING && timer_elapsed32(g80_link_t) < G80_PAIR_WIN_MS;
}

/* After a deep sleep (called by g80_lpm.c with interrupts masked, right after the clock is back):
 * bytes still in the UART FIFO and in the ring, and a partial frame, are from before or during the
 * sleep and are dropped; a fresh battery level is wanted as soon as the link is up, and the channel
 * key shows the link for 3 s. */
void g80_radio_on_wake(void) {
    for (uint8_t n = 0; n < 32 && (SN_UART2->LS & G80_UART_LS_RDR); n++) {
        (void)SN_UART2->RB;
    }
    g80_rx_tail = g80_rx_head;
    g80_rfl     = 0;
    g80_bat_wake = true;
    g80_link_t   = timer_read32();
}

void g80_radio_task(void) {
    g80_rx_task();
    if (!g80_started && timer_elapsed(g80_tx_time) >= 500) {
        g80_started = true;
        if (!g80_raw_slider_now()) {
            g80_radio_request(G80_CMD_USB); /* stock @0x0566: boot on the cable -> "USB mode" */
        }
    }
    const bool on = g80_slider_wireless();
    if (on && !g80_wl_on) {
        g80_wl_enter();
    } else if (!on && g80_wl_on) {
        g80_wl_leave();
    } else if (!on && USBD1.state == USB_STOP) {
        /* the suspend loop stopped USB on a short ON blip that the debounced slider never
         * confirmed: wired again, so USB must come back */
        restart_usb_driver(&USBD1);
    }
    /* End of the pairing window (20 s) / of the reconnect indication (10 s) without a link: sleep
     * 3 s / 0.5 s later (stock), once per window; the sleep option still applies. A failed pairing
     * must not keep the keyboard awake (the idle code treats an expired window as not pairing). */
    if (g80_wl_on && g80_win_t != g80_link_t) {
        const uint8_t  link = g80_radio_stats.link;
        const uint32_t age  = timer_elapsed32(g80_link_t);
        if ((link == G80_LINK_PAIRING && age >= G80_PAIR_WIN_MS) || (link == G80_LINK_LOST && age >= G80_LOST_WIN_MS)) {
            g80_win_t = g80_link_t;
            g80_lpm_countdown_set(link == G80_LINK_PAIRING ? 30u : 5u);
        }
    }
    if (g80_wl_on && g80_radio_stats.link == G80_LINK_CONNECTED && (g80_bat_wake || timer_elapsed32(g80_bat_time) >= G80_BAT_POLL_MS)) {
        g80_bat_time = timer_read32();
        g80_bat_wake = false;
        g80_radio_request(G80_CMD_BATTERY); /* battery level: every 60 s while linked and on wake */
    }
    if (g80_wl_on && g80_pair_col && g80_pair_sent < 3 && timer_elapsed(g80_pair_t0) >= G80_PAIR_HOLD_MS + 500u * g80_pair_sent) {
        g80_radio_request(G80_CMD_PAIR);
        if (++g80_pair_sent >= 3) {
            g80_pair_col = 0;
        }
    }
    g80_tx_task();
}
