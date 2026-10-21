// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Galaxy80 DEBUG keymap: same layers as "default" (g80_layers.h), plus raw matrix, LED-engine,
// slow-pin and radio-link diagnostics on the QMK console (HID usage page 0xFF31 / usage 0x74).
// Nothing here writes flash: EEPROM stays "transient" (keyboard rules.mk), every safety layer
// of galaxy80.c is untouched.
//
// Console line formats (one line each, '\n' terminated, all numbers hex unless noted):
//   M <ms> <r0> <r1> <r2> <r3> <r4> <r5>  raw matrix (before debounce) changed; each row is a
//                                          5-hex-digit column bitmap, bit n = column n. <ms> decimal.
//   D <ms> <n>                             n (decimal) M lines suppressed by the rate limit.
//   N <ms> <scans> [r,c:count ...]         every 2 s: matrix_scan() calls (decimal) in the window,
//                                          and each (row,col) that toggled while the debounced
//                                          matrix was empty, with its toggle count (decimal, max 20
//                                          entries, highest counts first). r,c decimal.
//   S <ms> <passes> <enter_to> <release_to> every 2 s, decimal, cumulative: complete 17-column
//                                          scan passes and row-discharge timeouts (LED ISR scan; the
//                                          polled-scan counters once the fallback is active).
//   L <ms> k=v ...                         every 2 s, decimal: LED engine / slow pins, see dbg_l().
//                                          isr = ISR count in the last 2 s (expected ~13000-14000
//                                          = 6.5-7 kHz if CT16B1 really runs at 2 MHz).
//   R k=v ... <T|R>xxxxxx ...              every 2 s: radio link counters, then the last frames
//                                          (R = from the module, T = sent), see dbg_l().
//   K <r> <c> <1|0>                        debounced key event (decimal r, c).
//   B ...                                  boot register dump / electrical baseline, printed at
//                                          3 s and repeated at 20 s. See dbg_dump_boot().

#include QMK_KEYBOARD_H
#include "g80_radio.h"
#include "galaxy80.h"
#include "g80_layers.h"
#include "print.h"
#include "timer.h"
#include "wait.h"
#include <string.h>

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    [0] = G80_LAYER_BASE,
    [1] = G80_LAYER_FN(G80_LLCK, _______),
};

/* ------------------------------------------------------------------------- */
/* Diagnostics                                                                */
/* ------------------------------------------------------------------------- */
extern matrix_row_t  raw_matrix[MATRIX_ROWS]; /* quantum/matrix_common.c */
extern volatile bool g80_scan_fallback;       /* matrix.c */
extern volatile uint32_t g80_stat_passes, g80_stat_enter_timeouts, g80_stat_release_timeouts;

#define DBG_NOISE_PERIOD_MS 2000u
#define DBG_NOISE_MAX_ENTRIES 20u
#define DBG_M_BUDGET_PER_S 200u    /* max M lines per second (console bandwidth guard) */
#define DBG_DUMP1_MS 3000u
#define DBG_DUMP2_MS 20000u
#define DBG_REPS 4u
#define DBG_SETTLE_SHORT_CYCLES 96 /* same as matrix.c (~2 us at 48 MHz) */
#define DBG_SETTLE_LONG_US 50u

static const pin_t dbg_col_pins[MATRIX_COLS] = MATRIX_COL_PINS;

static matrix_row_t dbg_prev_raw[MATRIX_ROWS];
static matrix_row_t dbg_prev_deb[MATRIX_ROWS];
static uint16_t     dbg_noise[MATRIX_ROWS][MATRIX_COLS];
static uint32_t     dbg_scans;
static uint32_t     dbg_noise_t0;
static uint32_t     dbg_m_t0;
static uint16_t     dbg_m_count;
static uint16_t     dbg_m_dropped;
static uint8_t      dbg_dumps_done;
static uint32_t     dbg_last_steps;

/* P3.11..P3.6 -> bit r = row r (row 0 = P3.11). */
static uint8_t dbg_rows_from_p3(uint32_t p3) {
    uint8_t v = 0;
    for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
        if (p3 & (1u << (11 - r))) {
            v |= (uint8_t)(1u << r);
        }
    }
    return v;
}

static void dbg_settle(bool longwait) {
    if (longwait) {
        wait_us(DBG_SETTLE_LONG_US);
    } else {
        wait_cpuclock(DBG_SETTLE_SHORT_CYCLES);
    }
}

/* B sub-formats:
 *   B H <ms> <dump#> RSTST=<hex> SWDCTRL=<hex> EXRSTCTRL=<hex>
 *   B GPIO <n> MODE=<hex> CFG=<hex> DATA=<hex>             n = 0..3
 *   B AHBCLKEN=<hex>
 *   B PFPA=<hex>
 *   B CT16B1 TMRCTRL=<hex> PRE=<hex> MCTRL3=<hex> MR24=<hex> PWMCTRL=<hex> PWMCTRL2=<hex>
 *            PWMENB=<hex> PWMIOENB=<hex>                     (or "B CT16B1 off" if its clock is gated)
 *   B COL <c> <S|L> <v0> <v1> <v2> <v3> P2=<hex> P1=<hex>
 *       column c (decimal) driven HIGH alone, settle S (~2 us, as matrix.c) or L (50 us);
 *       v = 2-hex-digit row bitmap read (bit r = row r), 4 repetitions;
 *       P2/P1 = port DATA read back while the column is high (output check).
 *   B LOW <S|L> <v0> <v1> <v2> <v3> P3=<hex>   all columns LOW: row bitmaps (should be 00), raw P3.DATA
 *   B END <dump#>
 * The baseline only drives the 17 column outputs (already push-pull outputs) and only reads
 * rows; the LED ISR is held (g80_engine_pause: selects LOW, PWM detached) for its duration. */
static void dbg_dump_boot(uint8_t n) {
    uprintf("B H %lu %u RSTST=%02lX SWDCTRL=%lX EXRSTCTRL=%lX\n", (unsigned long)timer_read32(), (unsigned)n, (unsigned long)SN_SYS0->RSTST, (unsigned long)SN_SYS0->SWDCTRL, (unsigned long)SN_SYS0->EXRSTCTRL);
    uprintf("B GPIO 0 MODE=%04lX CFG=%08lX DATA=%04lX\n", (unsigned long)SN_GPIO0->MODE, (unsigned long)SN_GPIO0->CFG, (unsigned long)SN_GPIO0->DATA);
    uprintf("B GPIO 1 MODE=%04lX CFG=%08lX DATA=%04lX\n", (unsigned long)SN_GPIO1->MODE, (unsigned long)SN_GPIO1->CFG, (unsigned long)SN_GPIO1->DATA);
    uprintf("B GPIO 2 MODE=%04lX CFG=%08lX DATA=%04lX\n", (unsigned long)SN_GPIO2->MODE, (unsigned long)SN_GPIO2->CFG, (unsigned long)SN_GPIO2->DATA);
    uprintf("B GPIO 3 MODE=%04lX CFG=%08lX DATA=%04lX\n", (unsigned long)SN_GPIO3->MODE, (unsigned long)SN_GPIO3->CFG, (unsigned long)SN_GPIO3->DATA);
    uint32_t ahb = SN_SYS1->AHBCLKEN;
    uprintf("B AHBCLKEN=%08lX\n", (unsigned long)ahb);
    uprintf("B PFPA=%08lX\n", (unsigned long)SN_PFPA->CT16B1);
    if (ahb & (1u << 7)) { /* CT16B1CLKEN: read the timer only while its clock is on */
        uprintf("B CT16B1 TMRCTRL=%08lX PRE=%02lX MCTRL3=%04lX MR24=%04lX PWMCTRL=%08lX PWMCTRL2=%08lX PWMENB=%08lX PWMIOENB=%08lX\n", (unsigned long)SN_CT16B1->TMRCTRL, (unsigned long)SN_CT16B1->PRE, (unsigned long)SN_CT16B1->MCTRL3, (unsigned long)SN_CT16B1->MR24, (unsigned long)SN_CT16B1->PWMCTRL, (unsigned long)SN_CT16B1->PWMCTRL2, (unsigned long)SN_CT16B1->PWMENB, (unsigned long)SN_CT16B1->PWMIOENB);
    } else {
        uprintf("B CT16B1 off\n");
    }

    for (uint8_t l = 0; l < 2; l++) {
        bool longwait = (l == 1);
        char tag      = longwait ? 'L' : 'S';
        for (uint8_t c = 0; c < MATRIX_COLS; c++) {
            uint8_t  v[DBG_REPS];
            uint32_t p2 = 0, p1 = 0;
            g80_engine_pause();
            SN_GPIO2->BCLR = 0x9FFFu; /* stock read state: all columns LOW */
            SN_GPIO1->BCLR = 0xB000u;
            wait_us(DBG_SETTLE_LONG_US);
            for (uint8_t i = 0; i < DBG_REPS; i++) {
                gpio_write_pin_high(dbg_col_pins[c]);
                dbg_settle(longwait);
                uint32_t p3 = SN_GPIO3->DATA;
                p2          = SN_GPIO2->DATA;
                p1          = SN_GPIO1->DATA;
                gpio_write_pin_low(dbg_col_pins[c]);
                v[i] = dbg_rows_from_p3(p3);
                wait_us(DBG_SETTLE_LONG_US); /* let the rows discharge */
            }
            SN_GPIO2->BSET = 0x9FFFu; /* columns idle HIGH again */
            SN_GPIO1->BSET = 0xB000u;
            g80_engine_resume();
            uprintf("B COL %u %c %02X %02X %02X %02X P2=%04lX P1=%04lX\n", (unsigned)c, tag, v[0], v[1], v[2], v[3], (unsigned long)(p2 & 0xFFFFu), (unsigned long)(p1 & 0xFFFFu));
        }
        uint8_t  v[DBG_REPS];
        uint32_t p3 = 0;
        g80_engine_pause();
        SN_GPIO2->BCLR = 0x9FFFu;
        SN_GPIO1->BCLR = 0xB000u;
        for (uint8_t i = 0; i < DBG_REPS; i++) {
            wait_us(DBG_SETTLE_LONG_US);
            dbg_settle(longwait);
            p3   = SN_GPIO3->DATA;
            v[i] = dbg_rows_from_p3(p3);
        }
        SN_GPIO2->BSET = 0x9FFFu;
        SN_GPIO1->BSET = 0xB000u;
        g80_engine_resume();
        uprintf("B LOW %c %02X %02X %02X %02X P3=%04lX\n", tag, v[0], v[1], v[2], v[3], (unsigned long)(p3 & 0xFFFFu));
    }
    uprintf("B END %u\n", (unsigned)n);
}

static void dbg_l(uint32_t now) {
    const uint32_t steps = g80_isr_steps;
    const uint32_t p0 = SN_GPIO0->DATA, p3 = SN_GPIO3->DATA;
    uprintf("L %lu isr=%lu steps=%lu isr_max_ticks=%u crst_to=%lu pwm_ok=%u fallback=%u rgb=%u mode=%u val=%u bar=%u",
            (unsigned long)now, (unsigned long)(steps - dbg_last_steps), (unsigned long)steps, (unsigned)g80_isr_max_ticks,
            (unsigned long)g80_crst_timeouts, (unsigned)g80_pwm_ok, (unsigned)g80_scan_fallback, (unsigned)rgb_matrix_is_enabled(),
            (unsigned)rgb_matrix_get_mode(), (unsigned)rgb_matrix_get_val(), (unsigned)g80_config.bar_mode);
    uprintf(" mac=%u p02=%u slider=%u p07=%u chg=%u p34=%u p35=%u nkro=%u wlck=%u\n", (unsigned)g80_is_mac(),
            (unsigned)((p0 >> 2) & 1), (unsigned)g80_slider_wireless(), (unsigned)((p0 >> 7) & 1), (unsigned)g80_charge_state(),
            (unsigned)((p3 >> 4) & 1), (unsigned)((p3 >> 5) & 1), (unsigned)keymap_config.nkro, (unsigned)g80_config.win_lock);
    dbg_last_steps = steps;
    /* radio link: counters, then the last frames (R = from the module, T = sent) */
    const g80_radio_stats_t *r = &g80_radio_stats;
    uprintf("R rxb=%lu fr=%lu bad=%lu ovr=%lu tx=%lu ackr=%lu ackt=%lu bat=%u leds=%u link=%02X lc=%02lX ctrl=%02lX",
            (unsigned long)r->rx_bytes, (unsigned long)r->rx_frames, (unsigned long)r->rx_bad, (unsigned long)r->rx_overrun,
            (unsigned long)r->tx_frames, (unsigned long)r->acks_rx, (unsigned long)r->acks_tx, (unsigned)r->battery,
            (unsigned)r->host_leds, (unsigned)r->link, (unsigned long)SN_UART2->LC, (unsigned long)SN_UART2->CTRL);
    uprintf(" wl=%u drop=%u lost=%u stuck=%u", (unsigned)g80_wl_active(), (unsigned)r->tx_dropped, (unsigned)r->tx_lost, (unsigned)r->tx_stuck);
    /* (no LS / RB / DLL read here: LS clears the error flags and RB would steal a byte) */
    for (uint8_t i = 0; i < G80_RADIO_LOG; i++) {
        const uint8_t *e = r->log[(r->log_head + i) % G80_RADIO_LOG];
        if (e[0]) {
            uprintf(" %c%02X%02X%02X", (char)e[0], e[1], e[2], e[3]);
        }
    }
    uprintf("\n");
}

static void dbg_noise_report(uint32_t now) {
    if (g80_scan_fallback) {
        uprintf("S %lu %lu %lu %lu F\n", (unsigned long)now, (unsigned long)g80_stat_passes, (unsigned long)g80_stat_enter_timeouts, (unsigned long)g80_stat_release_timeouts);
    } else {
        uprintf("S %lu %lu %lu %lu\n", (unsigned long)now, (unsigned long)g80_scan_passes, (unsigned long)g80_scan_enter_timeouts, (unsigned long)g80_scan_release_timeouts);
    }
    dbg_l(now);
    if (dbg_m_dropped) {
        uprintf("D %lu %u\n", (unsigned long)now, (unsigned)dbg_m_dropped);
        dbg_m_dropped = 0;
    }
    uprintf("N %lu %lu", (unsigned long)now, (unsigned long)dbg_scans);
    /* print up to 20 entries, highest counts first (selection, destructive) */
    for (uint8_t e = 0; e < DBG_NOISE_MAX_ENTRIES; e++) {
        uint16_t best = 0;
        uint8_t  br = 0, bc = 0;
        for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
            for (uint8_t c = 0; c < MATRIX_COLS; c++) {
                if (dbg_noise[r][c] > best) {
                    best = dbg_noise[r][c];
                    br   = r;
                    bc   = c;
                }
            }
        }
        if (best == 0) {
            break;
        }
        uprintf(" %u,%u:%u", (unsigned)br, (unsigned)bc, (unsigned)best);
        dbg_noise[br][bc] = 0;
    }
    uprintf("\n");
    memset(dbg_noise, 0, sizeof(dbg_noise));
    dbg_scans = 0;
}

void matrix_scan_user(void) {
    uint32_t now = timer_read32();
    dbg_scans++;

    /* debounced matrix state before this scan (empty?) */
    bool deb_empty = true;
    for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
        if (dbg_prev_deb[r]) {
            deb_empty = false;
        }
    }

    /* raw matrix changes */
    bool raw_changed = false;
    for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
        matrix_row_t diff = raw_matrix[r] ^ dbg_prev_raw[r];
        if (diff) {
            raw_changed = true;
            if (deb_empty) {
                for (uint8_t c = 0; c < MATRIX_COLS; c++) {
                    if ((diff & (MATRIX_ROW_SHIFTER << c)) && dbg_noise[r][c] < 0xFFFFu) {
                        dbg_noise[r][c]++;
                    }
                }
            }
        }
    }
    if (raw_changed) {
        if ((uint32_t)(now - dbg_m_t0) >= 1000u) {
            dbg_m_t0    = now;
            dbg_m_count = 0;
        }
        if (dbg_m_count < DBG_M_BUDGET_PER_S) {
            dbg_m_count++;
            uprintf("M %lu", (unsigned long)now);
            for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
                uprintf(" %05lX", (unsigned long)(raw_matrix[r] & 0x1FFFFu));
            }
            uprintf("\n");
        } else if (dbg_m_dropped < 0xFFFFu) {
            dbg_m_dropped++;
        }
        for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
            dbg_prev_raw[r] = raw_matrix[r];
        }
    }

    /* debounced key events */
    for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
        matrix_row_t cur  = matrix_get_row(r);
        matrix_row_t diff = cur ^ dbg_prev_deb[r];
        if (diff) {
            for (uint8_t c = 0; c < MATRIX_COLS; c++) {
                if (diff & (MATRIX_ROW_SHIFTER << c)) {
                    uprintf("K %u %u %u\n", (unsigned)r, (unsigned)c, (cur & (MATRIX_ROW_SHIFTER << c)) ? 1u : 0u);
                }
            }
            dbg_prev_deb[r] = cur;
        }
    }

    /* periodic report */
    if ((uint32_t)(now - dbg_noise_t0) >= DBG_NOISE_PERIOD_MS) {
        dbg_noise_report(now);
        dbg_noise_t0 = now;
    }

    /* boot dump(s) */
    if ((dbg_dumps_done == 0 && now >= DBG_DUMP1_MS) || (dbg_dumps_done == 1 && now >= DBG_DUMP2_MS)) {
        dbg_dumps_done++;
        dbg_dump_boot(dbg_dumps_done);
    }
}
