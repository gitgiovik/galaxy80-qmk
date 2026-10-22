// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Galaxy80 LED engine: CT16B1 timer ISR driving the LED matrix, with the key scan inside the ISR.
//
// Modelled line by line on the stock CT16B1 ISR (@0x51C8, init @0x6226):
//   CT16B1 at PCLK 48 MHz / (PRE 0x17 + 1) = 2 MHz, MR24 = 0xFF with IRQ + RESET + STOP
//   -> every "phase" is 256 ticks = 128 us, then the timer stops and the ISR runs.
//   21 phases = 7 LED rows x R,G,B in the stock order (R rows 0-6, G rows 0-6, B rows 0-6).
//   17 PWM channels (PWMENB = PWMIOENB = PFPA = 0x9FFFB0) drive the 17 LED column lines,
//   which are also the key-matrix columns. One active-high select line per phase.
//   Two key columns are read per phase, with every select LOW and the PWM detached from
//   the pins (the electrical sequence of the polled scan in matrix.c, verified on the keyboard).
//
// The only intentional electrical difference from stock: PWM mode 2 with MR = 256 - duty
// (stock: mode 1, MR = duty). Same waveform per LED (LOW = LED on for `duty` ticks), but the
// LOW part is at the end of the phase. When the timer stops at the end of a phase, or when
// the CPU cannot serve the IRQ (flash erase stall, masked IRQs, fault), every column is
// already HIGH = all LEDs dark in hardware.
//
// The ISR body uses direct register access only (like stock) and never feeds the watchdog.
// It runs at the lowest NVIC priority, wrapped in OSAL_IRQ_PROLOGUE/EPILOGUE: SysTick (ST,
// prio 0) and USB (prio 1) can nest inside it, and their own epilogue does not reschedule
// when nested, so this ISR's epilogue is what wakes a thread they made ready.

#include "quantum.h"
#include "rgb_matrix.h"
#include "wait.h"
#include "timer.h"
#include "g80_rgb.h"
#include <string.h>
#include <hal.h>

/* G80_DUTY_MAX (per-phase duty cap) is defined in config.h. */
_Static_assert(G80_DUTY_MAX >= 1 && G80_DUTY_MAX <= 252, "G80_DUTY_MAX must be 1..252 (mode 2 needs MR >= 4)");

#ifndef G80_COLS_PER_PHASE
#    define G80_COLS_PER_PHASE 2 /* stock: 2 key columns per CT16B1 phase */
#endif

#ifndef G80_CT16B1_PRE
#    define G80_CT16B1_PRE 0x17 /* stock @0x6226: 48 MHz / 24 = 2 MHz */
#endif

/* Same electrical timing as the polled scan in matrix.c: G80_COL_SETTLE_CYCLES and
 * G80_ROW_RELEASE_TRIES are defined once, in config.h, for both files. Every row wait is
 * always the full G80_ROW_RELEASE_TRIES. After the first timeout of an ISR
 * (a row that never discharges = hardware fault; 0 timeouts in 5.6 M passes on the real
 * keyboard) no further column is read in that ISR: the pass continues in the next phase
 * after a fresh full wait, so a slow row can never leak into the next column.
 * Every scanning ISR still reads at least one column, so a stuck row cannot stop the scan.
 * A timeout also makes the next G80_SCAN_BACKOFF_ISRS ISRs skip the scan (LEDs only): with a
 * row stuck for good the full waits then cost < 20 % CPU (the pass just takes ~4x longer;
 * not for the first pass, which matrix_init_custom() waits for). */
#ifndef G80_SCAN_BACKOFF_ISRS
#    define G80_SCAN_BACKOFF_ISRS 3
#endif
_Static_assert(G80_COL_SETTLE_CYCLES >= 1 && G80_COL_SETTLE_CYCLES <= 135, "wait_cpuclock() supports only 1..135 cycles");
_Static_assert(G80_ROW_RELEASE_TRIES >= 1 && G80_ROW_RELEASE_TRIES <= 255, "G80_ROW_RELEASE_TRIES must fit a uint8_t");
_Static_assert(RGB_MATRIX_LED_COUNT == G80_LED_COUNT, "keyboard.json rgb_matrix layout must have G80_LED_COUNT LEDs");

/* ------------------------------------------------------------------------- */
/* Pins                                                                       */
/* ------------------------------------------------------------------------- */
#define G80_SEL_MASK_P0 0xFF00u /* P0.8-15 */
#define G80_SEL_MASK_P1 0x4FDFu /* P1.0-4, P1.6-11, P1.14 */
#define G80_SEL_MASK_P3 0x0008u /* P3.3 */
#define G80_COL_MASK_P2 0x9FFFu /* P2.0-12, P2.15 */
#define G80_COL_MASK_P1 0xB000u /* P1.12, P1.13, P1.15 */
#define G80_ROW_MASK_P3 0x0FC0u /* P3.6-11 */
#define G80_ROW_BIT(row) (1u << (11 - (row)))

#define G80_PWM_MASK 0x009FFFB0u /* PWM04, 05, 07..20, 23 */
#define G80_PFPA_VALUE 0x009FFFB0u
#define G80_CT16B1_CLKEN (1u << 7)
#define G80_TMR_CEN 0x1u
#define G80_TMR_CRST 0x2u
#define G80_MCTRL3_MR24 0x7000u /* MR24IE | MR24RST | MR24STOP */
#define G80_IC_ALL 0x1FFFFFFu

#define G80_MR(d) (256u - (uint32_t)(d)) /* PWM mode 2: HIGH while TC < MR, MR = 256 -> always HIGH */
#define G80_MR_OFF 256u

/* LED column (= key column) -> CT16B1 match/PWM channel (per the stock firmware v1.10). */
const uint8_t g80_col_chan[G80_LED_COLS] = {20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8, 23, 7, 5, 4};

/* Phase -> select line, decoded from the stock switches @0x4D8C / @0x4E80.
 * Encoding: bits 7..4 = port (0, 1 or 3), bits 3..0 = pin. */
#define SEL(port, pin) (uint8_t)(((port) << 4) | (pin))
static const uint8_t g80_phase_sel[G80_PHASES] = {
    /* R rows 0-6 */ SEL(0, 8), SEL(0, 11), SEL(0, 14), SEL(1, 1), SEL(1, 4), SEL(3, 3), SEL(1, 6),
    /* G rows 0-6 */ SEL(0, 10), SEL(0, 13), SEL(1, 0), SEL(1, 3), SEL(1, 8), SEL(1, 10), SEL(1, 9),
    /* B rows 0-6 */ SEL(0, 9), SEL(0, 12), SEL(0, 15), SEL(1, 2), SEL(1, 7), SEL(1, 14), SEL(1, 11),
};
#undef SEL

static const pin_t g80_col_pins[MATRIX_COLS] = MATRIX_COL_PINS;

/* ------------------------------------------------------------------------- */
/* State                                                                      */
/* ------------------------------------------------------------------------- */
/* The only LED buffer: rgb_matrix writes go straight here, mapped and capped (357 B). */
uint8_t g80_frame[G80_PHASES][G80_LED_COLS];

/* rgb_matrix LED index -> (LED row << 5) | LED column. Rows 0-5 follow g_led_config.matrix_co; row 6
 * holds the side bar at columns 12-16 (stock slots 138-142). */
#define POS(r, c) (uint8_t)(((r) << 5) | (c))
// clang-format off
const uint8_t g80_led_pos[G80_LED_COUNT] = {
    POS(0, 0), POS(0, 1), POS(0, 2), POS(0, 3), POS(0, 4), POS(0, 5), POS(0, 6), POS(0, 7), POS(0, 8),
    POS(0, 9), POS(0, 10), POS(0, 11), POS(0, 12), POS(0, 13), POS(0, 14), POS(0, 15), POS(0, 16),
    POS(1, 0), POS(1, 1), POS(1, 2), POS(1, 3), POS(1, 4), POS(1, 5), POS(1, 6), POS(1, 7), POS(1, 8),
    POS(1, 9), POS(1, 10), POS(1, 11), POS(1, 12), POS(1, 13), POS(1, 14), POS(1, 15), POS(1, 16),
    POS(2, 0), POS(2, 1), POS(2, 2), POS(2, 3), POS(2, 4), POS(2, 5), POS(2, 6), POS(2, 7), POS(2, 8),
    POS(2, 9), POS(2, 10), POS(2, 11), POS(2, 12), POS(2, 13), POS(2, 14), POS(2, 15), POS(2, 16),
    POS(3, 0), POS(3, 1), POS(3, 2), POS(3, 3), POS(3, 4), POS(3, 5), POS(3, 6), POS(3, 7), POS(3, 8),
    POS(3, 9), POS(3, 10), POS(3, 11), POS(3, 13),
    POS(4, 0), POS(4, 2), POS(4, 3), POS(4, 4), POS(4, 5), POS(4, 6), POS(4, 7), POS(4, 8), POS(4, 9),
    POS(4, 10), POS(4, 11), POS(4, 13), POS(4, 15),
    POS(5, 0), POS(5, 1), POS(5, 2), POS(5, 6), POS(5, 10), POS(5, 11), POS(5, 12), POS(5, 13), POS(5, 14),
    POS(5, 15), POS(5, 16),
    POS(6, 12), POS(6, 13), POS(6, 14), POS(6, 15), POS(6, 16),
};
// clang-format on
#undef POS

volatile bool g80_mask_keys;
volatile bool g80_overlay;

volatile uint32_t g80_isr_steps;
volatile uint32_t g80_scan_passes;
volatile uint32_t g80_scan_enter_timeouts;
volatile uint32_t g80_scan_release_timeouts;
volatile uint32_t g80_crst_timeouts;
volatile uint16_t g80_isr_max_ticks;
volatile bool     g80_pwm_ok;

static volatile bool g80_running;
/* Set for good by g80_led_shutdown(): once the LEDs have been shut down (fallback to the
 * polled scan, bootloader jump, reset) g80_engine_start() never restarts them, whoever calls
 * it (rgb_matrix_init() runs after matrix_init()). */
static volatile bool g80_engine_dead;
static uint8_t       g80_phase;

static volatile bool g80_scan_ready;          /* a full pass is waiting for the consumer */
static uint8_t       g80_scan_col;            /* next column of the pass in progress */
static uint8_t       g80_scan_backoff;        /* ISRs still to skip after a row timeout */
static matrix_row_t  g80_scan_work[MATRIX_ROWS];

/* ------------------------------------------------------------------------- */
/* Key scan helpers (ISR context). Same sequence as matrix.c.                */
/* ------------------------------------------------------------------------- */
static inline __attribute__((always_inline)) void g80_cols_idle_high(void) {
    SN_GPIO2->BSET = G80_COL_MASK_P2;
    SN_GPIO1->BSET = G80_COL_MASK_P1;
}

static inline __attribute__((always_inline)) void g80_cols_all_low(void) {
    SN_GPIO2->BCLR = G80_COL_MASK_P2;
    SN_GPIO1->BCLR = G80_COL_MASK_P1;
}

static inline __attribute__((always_inline)) bool g80_rows_wait_low(void) {
    for (uint8_t i = 0; i < G80_ROW_RELEASE_TRIES; i++) {
        if ((SN_GPIO3->DATA & G80_ROW_MASK_P3) == 0) {
            return true;
        }
        wait_cpuclock(G80_ROW_POLL_CYCLES);
    }
    return (SN_GPIO3->DATA & G80_ROW_MASK_P3) == 0;
}

static inline __attribute__((always_inline)) void g80_isr_scan(void) {
    /* not before the first pass: matrix_init_custom() waits only 10 ms for it */
    if (g80_scan_backoff && g80_scan_passes) {
        g80_scan_backoff--; /* columns untouched: PWM-detached GPIO latch HIGH = LED-off idle */
        return;
    }
    uint8_t col = g80_scan_col;
    if (col == 0) {
        for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
            g80_scan_work[r] = 0;
        }
    }
    /* columns this ISR may read: after an entry timeout only one, like the polled scan (which reads
     * on after a full timed-out wait) */
    uint8_t budget = G80_COLS_PER_PHASE;
    /* stock read state: all 17 columns LOW, rows discharged through the external pull-downs */
    g80_cols_all_low();
    if (!g80_rows_wait_low()) {
        g80_scan_enter_timeouts++;
        g80_scan_backoff = G80_SCAN_BACKOFF_ISRS;
        budget           = 1;
    }
    for (uint8_t k = 0; k < budget && col < MATRIX_COLS; k++) {
        gpio_write_pin_high(g80_col_pins[col]);
        wait_cpuclock(G80_COL_SETTLE_CYCLES);
        uint32_t rows = SN_GPIO3->DATA;
        gpio_write_pin_low(g80_col_pins[col]);
        for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
            if (rows & G80_ROW_BIT(r)) {
                g80_scan_work[r] |= (MATRIX_ROW_SHIFTER << col);
            }
        }
        col++;
        if (!g80_rows_wait_low()) {
            g80_scan_release_timeouts++;
            g80_scan_backoff = G80_SCAN_BACKOFF_ISRS;
            break; /* never read another column after a timed-out release */
        }
    }
    /* back to the LED-off idle: every column HIGH */
    g80_cols_idle_high();
    if (col >= MATRIX_COLS) {
        col = 0;
        g80_scan_passes++;
        g80_scan_ready = true;
    }
    g80_scan_col = col;
}

/* ------------------------------------------------------------------------- */
/* CT16B1 ISR (IRQ16, vector 0x80). Entered with the timer stopped at TC = 0. */
/* ------------------------------------------------------------------------- */
OSAL_IRQ_HANDLER(SN32_CT16B1_HANDLER) {
    OSAL_IRQ_PROLOGUE();
    const uint16_t t0 = (uint16_t)SN_CT16B0->TC;

    /* 1. all 21 select lines LOW */
    SN_GPIO0->BCLR = G80_SEL_MASK_P0;
    SN_GPIO1->BCLR = G80_SEL_MASK_P1;
    SN_GPIO3->BCLR = G80_SEL_MASK_P3;
    /* 2. PWM detached: the 17 pins fall back to GPIO output, latch HIGH (idle) */
    SN_CT16B1->PWMIOENB = 0;
    SN_CT16B1->PWMENB   = 0;
    SN_CT16B1->IC       = G80_IC_ALL;

    /* 3. two key columns, only while the previous pass has been consumed (stock RAM flag @0x200000FC) */
    if (!g80_scan_ready) {
        g80_isr_scan();
    }

    /* 4. duties of this phase -> 17 match registers */
    const uint8_t      phase = g80_phase;
    const uint8_t     *duty  = g80_frame[phase];
    volatile uint32_t *mr    = &SN_CT16B1->MR0;
    uint32_t           any   = 0;
    for (uint8_t c = 0; c < G80_LED_COLS; c++) {
        uint32_t d = duty[c];
        if (d > G80_DUTY_MAX) {
            d = G80_DUTY_MAX;
        }
        any |= d;
        mr[g80_col_chan[c]] = G80_MR(d);
    }

    /* 5. counter reset (bounded wait, CRST self-clears) */
    SN_CT16B1->TMRCTRL = 0;
    SN_CT16B1->TMRCTRL = G80_TMR_CRST;
    for (uint8_t i = 0; SN_CT16B1->TMRCTRL & G80_TMR_CRST; i++) {
        if (i >= 32) {
            g80_crst_timeouts++;
            break;
        }
    }

    if (g80_pwm_ok) {
        /* 6. PWM back on the pins: with the timer stopped at TC = 0 every output is HIGH (mode 2) */
        SN_CT16B1->PWMENB   = G80_PWM_MASK;
        SN_CT16B1->PWMIOENB = G80_PWM_MASK;
        /* 7. one select line, raised while every column is still HIGH */
        if (any) {
            const uint8_t s = g80_phase_sel[phase];
            const uint32_t bit = 1u << (s & 0x0F);
            switch (s >> 4) {
                case 0:
                    SN_GPIO0->BSET = bit;
                    break;
                case 1:
                    SN_GPIO1->BSET = bit;
                    break;
                default:
                    SN_GPIO3->BSET = bit;
                    break;
            }
        }
    }

    /* 8. start the 128 us phase */
    SN_CT16B1->TMRCTRL = G80_TMR_CEN;

    g80_phase = (phase + 1 >= G80_PHASES) ? 0 : (uint8_t)(phase + 1);
    g80_isr_steps++;

    const uint16_t dt = (uint16_t)((uint16_t)SN_CT16B0->TC - t0);
    if (dt > g80_isr_max_ticks) {
        g80_isr_max_ticks = dt;
    }

    /* After CEN and the select raise: nothing electrical moves here. Reschedules if SysTick
     * or USB, nested inside this ISR, made a thread ready (they skip it when nested). */
    OSAL_IRQ_EPILOGUE();
}

/* ------------------------------------------------------------------------- */
/* Engine control (thread context)                                            */
/* ------------------------------------------------------------------------- */
void g80_engine_start(void) {
    if (g80_running || g80_engine_dead) {
        return;
    }
    NVIC_DisableIRQ(CT16B1_IRQn);

    memset(g80_frame, 0, sizeof(g80_frame));
    g80_scan_ready   = false;
    g80_scan_col     = 0;
    g80_scan_backoff = 0;
    g80_phase        = 0;

    SN_SYS1->AHBCLKEN |= G80_CT16B1_CLKEN;
    (void)SN_SYS1->AHBCLKEN;

    SN_CT16B1->TMRCTRL  = 0;
    SN_CT16B1->PWMIOENB = 0;
    SN_CT16B1->PWMENB   = 0;
    SN_CT16B1->MCTRL    = 0;
    SN_CT16B1->MCTRL2   = 0;
    SN_CT16B1->MCTRL3   = G80_MCTRL3_MR24;
    SN_CT16B1->PRE      = G80_CT16B1_PRE;
    SN_CT16B1->MR24     = 0xFF;
    volatile uint32_t *mr = &SN_CT16B1->MR0;
    for (uint8_t ch = 0; ch < 24; ch++) {
        mr[ch] = G80_MR_OFF;
    }

    uint32_t pwmctrl = 0, pwmctrl2 = 0;
    for (uint8_t c = 0; c < G80_LED_COLS; c++) {
        const uint8_t ch = g80_col_chan[c];
        if (ch < 16) {
            pwmctrl |= 1u << (2 * ch); /* 01b = PWM mode 2 */
        } else {
            pwmctrl2 |= 1u << (2 * (ch - 16));
        }
    }
    SN_CT16B1->PWMCTRL  = pwmctrl;
    SN_CT16B1->PWMCTRL2 = pwmctrl2;

    /* PWM channels to their "B" pins = the 17 column lines. With PFPA = 0 the same channels
     * would drive the "A" pins, i.e. the LED select lines: PWM is attached to the pins only
     * if every value reads back exactly. */
    SN_PFPA->CT16B1 = G80_PFPA_VALUE;
    g80_pwm_ok      = (SN_PFPA->CT16B1 == G80_PFPA_VALUE) && (SN_CT16B1->PWMCTRL == pwmctrl) && (SN_CT16B1->PWMCTRL2 == pwmctrl2);

    SN_CT16B1->IC = G80_IC_ALL;
    NVIC_ClearPendingIRQ(CT16B1_IRQn);
    NVIC_SetPriority(CT16B1_IRQn, 3); /* lowest */
    g80_running = true;
    NVIC_EnableIRQ(CT16B1_IRQn);

    SN_CT16B1->TMRCTRL = G80_TMR_CRST;
    for (uint8_t i = 0; (SN_CT16B1->TMRCTRL & G80_TMR_CRST) && i < 32; i++) {
    }
    SN_CT16B1->TMRCTRL = G80_TMR_CEN; /* first phase: all MR off, ISR after 128 us */
}

bool g80_engine_wait_first_pass(uint32_t timeout_ms) {
    const uint32_t t0 = timer_read32();
    while (!g80_scan_ready) {
        if (!g80_running || timer_elapsed32(t0) > timeout_ms) {
            return false;
        }
        wait_us(50);
    }
    return true;
}

bool g80_scan_take(matrix_row_t rows[MATRIX_ROWS]) {
    if (!g80_scan_ready) {
        return false;
    }
    __DMB();
    for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
        rows[r] = g80_scan_work[r];
    }
    __DMB();
    g80_scan_ready = false; /* the ISR starts the next pass */
    return true;
}

void g80_led_shutdown(void) {
    g80_engine_dead = true; /* permanent until reset: g80_engine_start() is a no-op from now on */
    NVIC_DisableIRQ(CT16B1_IRQn);
    __DSB();
    __ISB();
    /* selects LOW first (same order as the fault path), then timer and PWM off */
    SN_GPIO0->BCLR = G80_SEL_MASK_P0;
    SN_GPIO1->BCLR = G80_SEL_MASK_P1;
    SN_GPIO3->BCLR = G80_SEL_MASK_P3;
    if (SN_SYS1->AHBCLKEN & G80_CT16B1_CLKEN) {
        SN_CT16B1->TMRCTRL  = 0;
        SN_CT16B1->PWMIOENB = 0;
        SN_CT16B1->PWMENB   = 0;
    }
    g80_cols_idle_high();
    g80_running = false;
}

/* Deep sleep (g80_lpm.c): the same shutdown, plus the CT16B1 clock gated, but not permanent:
 * g80_engine_start() brings everything back (unless g80_led_shutdown() had run before). */
void g80_engine_stop(void) {
    const bool dead = g80_engine_dead;
    g80_led_shutdown();
    g80_engine_dead = dead;
    SN_SYS1->AHBCLKEN &= ~G80_CT16B1_CLKEN;
}

/* Debug keymap only: stop serving IRQ16 for a while (LEDs dark, pins back to GPIO, columns HIGH)
 * so that a caller can drive the columns itself; g80_engine_resume() restarts the ISR, which
 * is pending since the timer stopped at the end of its phase. */
void g80_engine_pause(void) {
    NVIC_DisableIRQ(CT16B1_IRQn);
    __DSB();
    __ISB();
    SN_GPIO0->BCLR = G80_SEL_MASK_P0;
    SN_GPIO1->BCLR = G80_SEL_MASK_P1;
    SN_GPIO3->BCLR = G80_SEL_MASK_P3;
    if (SN_SYS1->AHBCLKEN & G80_CT16B1_CLKEN) {
        SN_CT16B1->PWMIOENB = 0;
        SN_CT16B1->PWMENB   = 0;
    }
    g80_cols_idle_high();
}

void g80_engine_resume(void) {
    if (g80_running) {
        NVIC_EnableIRQ(CT16B1_IRQn);
    }
}

/* ------------------------------------------------------------------------- */
/* rgb_matrix custom driver                                                   */
/* ------------------------------------------------------------------------- */
static void g80_drv_init(void) {
    /* Nothing to do: matrix_init_custom() starts the engine before rgb_matrix_init(), and once
     * it has fallen back to the polled scan the LEDs must stay off. */
}

static inline uint8_t g80_cap(uint8_t v) {
    return v > G80_DUTY_MAX ? (uint8_t)G80_DUTY_MAX : v;
}

/* Brightness factor q / 256 while the battery cap is active (0 = none), g80_battery.c. */
extern uint8_t g80_bright_q8;

static void g80_put(uint8_t index, uint8_t r, uint8_t g, uint8_t b) {
    if (g80_mask_keys && !g80_overlay && index < G80_LED_KEYS_COUNT) {
        r = g = b = 0; /* key backlight off: only the indicator pass may light key LEDs */
    }
    if (g80_bright_q8) {
        r = (uint8_t)((uint16_t)r * g80_bright_q8 >> 8);
        g = (uint8_t)((uint16_t)g * g80_bright_q8 >> 8);
        b = (uint8_t)((uint16_t)b * g80_bright_q8 >> 8);
    }
    const uint8_t pos = g80_led_pos[index];
    const uint8_t row = pos >> 5;
    const uint8_t col = pos & 0x1F;
    if (index >= G80_LED_KEYS_COUNT) {
        /* Side bar LEDs have R and G swapped on the PCB (verified on hardware 2026-10-18:
         * solid red -> keys red, bar green). */
        const uint8_t t = r;
        r               = g;
        g               = t;
    }
    g80_frame[row][col]                    = g80_cap(r);
    g80_frame[G80_LED_ROWS + row][col]     = g80_cap(g);
    g80_frame[2 * G80_LED_ROWS + row][col] = g80_cap(b);
}

/* Single buffer: the ISR shows a value as soon as it is written. An indicator that overrides
 * an effect colour is written microseconds after it (same rgb_matrix task iteration); the
 * key-backlight-off mask is applied here, so masked keys never light at all. */
static void g80_drv_set_color(int index, uint8_t r, uint8_t g, uint8_t b) {
    if (index >= 0 && index < G80_LED_COUNT) {
        g80_put((uint8_t)index, r, g, b);
    }
}

static void g80_drv_set_color_all(uint8_t r, uint8_t g, uint8_t b) {
    for (uint8_t i = 0; i < G80_LED_COUNT; i++) {
        g80_put(i, r, g, b);
    }
}

static void g80_drv_flush(void) {}

const rgb_matrix_driver_t rgb_matrix_driver = {
    .init          = g80_drv_init,
    .flush         = g80_drv_flush,
    .set_color     = g80_drv_set_color,
    .set_color_all = g80_drv_set_color_all,
};
