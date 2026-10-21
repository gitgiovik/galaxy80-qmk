// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Custom "lite" matrix scan, electrically identical to the stock firmware (0x51C8/0x3A16):
//   - the 17 column lines (P2.12..P2.0, P2.15, P1.15, P1.13, P1.12) are also the CT16B1
//     PWM outputs of the LED column drivers. Stock runs them in PWM mode 1 with MR=0 when
//     the LEDs are off, i.e. the lines sit continuously HIGH (= LED column driver OFF);
//     they are pulled into "all LOW except the one being read, which is HIGH" only for the
//     few microseconds of a column read (2 columns per CT16B1 ISR, ~1 full scan per ms).
//     This file reproduces that: columns IDLE HIGH, a scan pass enters the stock read state,
//     and the pass is throttled to G80_SCAN_INTERVAL_US (stock ~1.15 ms per full scan).
//   - the 6 row lines P3.11..P3.6 are inputs without internal pull (external pull-downs)
//     and read active-high (pressed = 1).
// The rows are never configured as outputs and never get an internal pull-up: P3.7 is the
// chip's external RESET pin function and P3.6 is SWCLK (disabled in board_init()).
// Debounce is done by QMK (sym_defer_pk, 5 ms ~ stock default 5 full scans).
//
// With RGB_MATRIX_ENABLE the reads move into the CT16B1 LED ISR (g80_rgb.c, 2 columns
// per 128 us phase, same electrical sequence as below), and this file only takes the
// completed passes. If the ISR heartbeat stops, the LEDs are shut down and the scan falls
// back to the polled scan below. Without RGB_MATRIX_ENABLE this file compiles to exactly the
// polled scan.

#include "quantum.h"
#include "matrix.h"
#include "g80_test_hooks.h"
#include "wait.h"
#include <hal.h>
#ifdef RGB_MATRIX_ENABLE
#    include "timer.h"
#    include "g80_rgb.h"
#endif

static const pin_t col_pins[MATRIX_COLS] = MATRIX_COL_PINS;

#define COL_MASK_P2 0x9FFFu /* P2.0-12, P2.15 (same as galaxy80.c) */
#define COL_MASK_P1 0xB000u /* P1.12, P1.13, P1.15 */
#define ROW_MASK_P3 0x0FC0u /* P3.6-11 */
#define ROW_BIT(row) (1u << (11 - (row)))

/* 48 MHz core clock: wait_cpuclock(48) ~ 1 us. Stock waits ~1.5-3 us (@0x6318(5) + 6 reads).
 * wait_cpuclock() (platforms/chibios/_wait.c) only supports n = 1..135: larger values
 * silently compile to ZERO nops (the n/8 switch has no case above 16). 96 = ~2 us. */
/* G80_COL_SETTLE_CYCLES and G80_ROW_RELEASE_TRIES (x ~1 us, bounded wait for the rows to fall
 * back low) are defined in config.h, shared with the ISR scan in g80_rgb.c. */
_Static_assert(G80_COL_SETTLE_CYCLES >= 1 && G80_COL_SETTLE_CYCLES <= 135, "wait_cpuclock() supports only 1..135 cycles");
/* Minimum time between two full scan passes. Stock: ~1.15 ms (9 CT16B1 ISRs of ~128 us).
 * 0 = scan on every matrix_scan() call (old behaviour, columns still idle HIGH). */
#ifndef G80_SCAN_INTERVAL_US
#    define G80_SCAN_INTERVAL_US 1000
#endif

#ifdef G80_MATRIX_STATS
/* Readable from a debug keymap / console: how often the analog assumptions are violated. */
volatile uint32_t g80_stat_passes;          /* full scan passes */
volatile uint32_t g80_stat_enter_timeouts;  /* rows still high G80_ROW_RELEASE_TRIES us after all cols LOW */
volatile uint32_t g80_stat_release_timeouts;/* rows still high after a column went LOW again */
#    define G80_STAT(x) ((x)++)
#else
#    define G80_STAT(x) ((void)0)
#endif

static inline void cols_idle_high(void) {
    SN_GPIO2->BSET = COL_MASK_P2;
    SN_GPIO1->BSET = COL_MASK_P1;
}

static inline void cols_all_low(void) {
    SN_GPIO2->BCLR = COL_MASK_P2;
    SN_GPIO1->BCLR = COL_MASK_P1;
}

/* Bounded wait until all six rows read LOW (they discharge only through the external
 * pull-downs). Returns false on timeout. */
static inline bool rows_wait_low(void) {
    for (uint8_t i = 0; i < G80_ROW_RELEASE_TRIES; i++) {
        if ((palReadPort(GPIOD) & ROW_MASK_P3) == 0) {
            return true;
        }
        wait_cpuclock(G80_ROW_POLL_CYCLES);
    }
    return (palReadPort(GPIOD) & ROW_MASK_P3) == 0;
}

/* Polled scan (verified on the keyboard). */
static inline __attribute__((always_inline)) bool g80_polled_scan(matrix_row_t current_matrix[]) {
#if G80_SCAN_INTERVAL_US > 0
    static bool      scanned_once = false;
    static systime_t last_pass    = 0;
    const systime_t  now          = chVTGetSystemTimeX();
    if (scanned_once && chTimeDiffX(last_pass, now) < TIME_US2I(G80_SCAN_INTERVAL_US)) {
        return false; /* keep the previous raw matrix; debounce keeps running */
    }
    scanned_once = true;
    last_pass    = now;
#endif

    matrix_row_t next[MATRIX_ROWS] = {0};

    /* Enter the stock read state (0x39E4): all 17 columns LOW. While the columns idled HIGH,
     * every row with a pressed key was charged high through its diode: let it discharge. */
    cols_all_low();
    if (!rows_wait_low()) {
        G80_STAT(g80_stat_enter_timeouts);
    }

    for (uint8_t col = 0; col < MATRIX_COLS; col++) {
        gpio_write_pin_high(col_pins[col]);
        wait_cpuclock(G80_COL_SETTLE_CYCLES);
        uint32_t rows = palReadPort(GPIOD);
        gpio_write_pin_low(col_pins[col]);

        for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
            if (rows & ROW_BIT(row)) {
                next[row] |= (MATRIX_ROW_SHIFTER << col);
            }
        }

        if (!rows_wait_low()) {
            G80_STAT(g80_stat_release_timeouts);
        }
    }

    /* Back to the stock LED-off idle: all column lines HIGH (LED column drivers off). */
    cols_idle_high();
    G80_STAT(g80_stat_passes);

    bool changed = false;
    for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
        if (current_matrix[row] != next[row]) {
            current_matrix[row] = next[row];
            changed             = true;
        }
    }
    return changed;
}

#ifndef RGB_MATRIX_ENABLE

void matrix_init_custom(void) {
    /* Pins were configured in board_init() (columns output HIGH = stock LED-off idle);
     * re-assert the idle state anyway. */
    cols_idle_high();
}

bool matrix_scan_custom(matrix_row_t current_matrix[]) {
    return g80_polled_scan(current_matrix);
}

#else /* RGB_MATRIX_ENABLE: scan inside the CT16B1 ISR */

#    ifndef G80_HEARTBEAT_TIMEOUT_MS
#        define G80_HEARTBEAT_TIMEOUT_MS 50
#    endif
#    ifndef G80_FIRST_PASS_TIMEOUT_MS
#        define G80_FIRST_PASS_TIMEOUT_MS 10
#    endif

/* true once the ISR is considered dead: polled scan, LEDs off for good */
volatile bool   g80_scan_fallback = false;
static uint32_t g80_hb_steps;
static uint32_t g80_hb_time;
static uint8_t  g80_hb_misses;

static void g80_enter_fallback(void) {
    g80_led_shutdown();
    g80_scan_fallback = true;
}

void matrix_init_custom(void) {
    cols_idle_high();
    g80_engine_start();
    /* Wait for the first complete pass so that the first matrix_scan() of bootmagic already
     * sees Esc (with the "defer" debounce an empty first scan would lose it). */
    if (!g80_engine_wait_first_pass(G80_FIRST_PASS_TIMEOUT_MS)) {
        g80_enter_fallback();
    }
    g80_hb_steps = g80_isr_steps;
    g80_hb_time  = timer_read32();
}

bool matrix_scan_custom(matrix_row_t current_matrix[]) {
    if (g80_scan_fallback) {
        return g80_polled_scan(current_matrix);
    }

    /* Heartbeat: the ISR must keep stepping. >= 3 consecutive calls without progress
     * spanning >= G80_HEARTBEAT_TIMEOUT_MS -> LEDs off, polled scan. */
    const uint32_t steps = g80_isr_steps;
    const uint32_t now   = timer_read32();
    if (steps != g80_hb_steps) {
        g80_hb_steps  = steps;
        g80_hb_time   = now;
        g80_hb_misses = 0;
    } else {
        if (g80_hb_misses < 255) {
            g80_hb_misses++;
        }
        if (g80_hb_misses >= 3 && TIMER_DIFF_32(now, g80_hb_time) >= G80_HEARTBEAT_TIMEOUT_MS) {
            g80_enter_fallback();
            return g80_polled_scan(current_matrix);
        }
    }

    matrix_row_t next[MATRIX_ROWS];
    if (!g80_scan_take(next)) {
        return false; /* no new pass yet: debounce keeps running on the previous raw matrix */
    }
    bool changed = false;
    for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
        if (current_matrix[row] != next[row]) {
            current_matrix[row] = next[row];
            changed             = true;
        }
    }
    return changed;
}

#endif /* RGB_MATRIX_ENABLE */
