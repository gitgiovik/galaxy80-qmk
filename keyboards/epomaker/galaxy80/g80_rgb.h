// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Galaxy80 LED engine: CT16B1 timer ISR for the LED matrix + key scan inside the ISR.
// Only built when RGB_MATRIX_ENABLE = yes.
#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "matrix.h"

#define G80_LED_COUNT 93  /* 88 keys + 5 side-bar LEDs */
#define G80_LED_ROWS 7    /* LED rows 0..5 = key rows, 6 = side bar */
#define G80_LED_COLS 17   /* = matrix columns = PWM columns */
#define G80_PHASES 21     /* 7 LED rows x R,G,B (stock order: R rows 0-6, G rows 0-6, B rows 0-6) */
#define G80_NO_LED 0xFF
#define G80_LED_KEYS_COUNT 88

/* Duty values the ISR uses, one byte per (phase, LED column), already mapped and capped.
 * Written by the rgb_matrix driver set_color() in thread context, read by the ISR. */
extern uint8_t g80_frame[G80_PHASES][G80_LED_COLS];
/* Key backlight off (G80_BLTG): key LEDs 0..87 are written black unless g80_overlay is set
 * (the indicator pass sets it while it draws). */
extern volatile bool g80_mask_keys;
extern volatile bool g80_overlay;

/* Hardware constants (verified against the stock firmware tables in the emulator). */
extern const uint8_t g80_col_chan[G80_LED_COLS];              /* CT16B1 MR channel per column */
extern const uint8_t g80_led_pos[G80_LED_COUNT];               /* LED index -> (row << 5) | col */

/* Engine control */
void g80_engine_start(void);    /* idempotent: CT16B1 + IRQ16 on, key scan in the ISR */
bool g80_engine_wait_first_pass(uint32_t timeout_ms);
/* Copies a complete scan pass (6 rows) if one is ready; returns false otherwise. */
bool g80_scan_take(matrix_row_t rows[MATRIX_ROWS]);
/* LEDs off in hardware and ISR stopped. Safe to call at any time, from any state. */
void g80_led_shutdown(void);
/* Deep sleep: LEDs off, ISR, timer and CT16B1 clock stopped; g80_engine_start() restarts it. */
void g80_engine_stop(void);
/* Debug keymap only: hold / restart the ISR around code that drives the columns itself. */
void g80_engine_pause(void);
void g80_engine_resume(void);

/* Statistics (always present, a few words of RAM) */
extern volatile uint32_t g80_isr_steps;           /* ISR heartbeat: one per phase */
extern volatile uint32_t g80_scan_passes;         /* complete 17-column passes */
extern volatile uint32_t g80_scan_enter_timeouts; /* rows still high after all columns LOW */
extern volatile uint32_t g80_scan_release_timeouts;
extern volatile uint32_t g80_crst_timeouts;       /* CT16B1 CRST did not self-clear */
extern volatile uint16_t g80_isr_max_ticks;       /* longest ISR, CT16B0 ticks (187.5 kHz = 5.33 us) */
extern volatile bool     g80_pwm_ok;              /* PFPA/PWMCTRL read back as expected */
