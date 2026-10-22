// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Battery state of the radio module: low / critical events, the Fn+Space gauge request, the
// brightness cap on battery and the LED-off timeout. Rendering lives in g80_indicators.c and
// g80_bar.c; this file only holds state and timing.
#pragma once

#include "galaxy80.h"

#ifdef G80_FEATURES

/* Fn LED low-battery blink: half period in ms (stock: 200 ticks of 2 ms). */
#    define G80_LOWBAT_HALF_MS 400u
/* Option "five blinks": one burst every G80_LOWBAT_GAP_MS at most, started by key activity; each
 * blink is G80_LOWBAT_BLINK_MS on and the same off. */
#    define G80_LOWBAT_BLINKS 5u
#    define G80_LOWBAT_BLINK_MS 250u
#    define G80_LOWBAT_GAP_MS 30000u

/* Fn+Space gauge: the module is asked again every G80_GAUGE_REQ_MS while the keys stay down; no
 * answer within G80_GAUGE_REPLY_MS makes the cached value stale. */
#    define G80_GAUGE_REQ_MS 2000u
#    define G80_GAUGE_REPLY_MS 300u

/* Gauge flags passed to the renderer. */
#    define G80_GAUGE_CHARGING 1u /* the lit LEDs breathe */
#    define G80_GAUGE_STALE 2u    /* cached value, no answer: shown at 25 % */

/* Matrix position of Space (the Fn key is whatever turns layer 1 on). */
#    define G80_GAUGE_ROW 5
#    define G80_GAUGE_COL 6

/* From g80_features_task() (main loop). */
void g80_battery_task(void);

/* Activity that is not a key (a VIA change, g80_via.c): restarts the LED-off timeout and lights the
 * LEDs again. Keys (any debounced matrix change), a wired <-> wireless change and a charger edge
 * count by themselves. */
void g80_led_activity(void);

/* Called by the radio driver for every valid module frame: 0x5B <code> (code 0x21 low, 0x22
 * critical, 0x23 normal; other codes are ignored) and 0x5C <percent>. */
void g80_battery_rx(uint8_t frame, uint8_t value);

/* Fn LED low-battery warning: -1 = not running (draw nothing), 0 = dark phase, 1 = red phase. */
int8_t g80_batt_fn_state(void);
/* Fn+Space is held (outside the on-board editor): *pct = cached percent (0xFF = unknown), *flags =
 * G80_GAUGE_*. */
bool g80_batt_gauge(uint8_t *pct, uint8_t *flags);

/* Brightness cap on battery: factor q / 256 (1..255) applied by the LED driver to every colour;
 * 0 = no cap. Read by g80_rgb.c. */
extern uint8_t g80_bright_q8;

// ---------------------------------------------------------------------------------------------
// Options read from g80_ucfg (g80_settings.h); defined in g80_battery.c.
// ---------------------------------------------------------------------------------------------
uint8_t g80_opt_low_batt_mode(void);  /* 0 = continuous blink (stock), 1 = five blinks per 30 s */
bool    g80_opt_charge_wired(void);   /* charge LED also while wired; default on (stock) */
uint8_t g80_opt_batt_cap(void);       /* 0 = off, 1 / 2 / 3 = at most 75 / 50 / 25 % brightness */
uint8_t g80_opt_led_off_min(void);    /* LEDs off after this many minutes without activity; 0 = never */

#endif /* G80_FEATURES */
