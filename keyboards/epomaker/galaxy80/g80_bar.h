// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Side bar: the 5 LEDs after the keys, their modes and the gradient.
#pragma once

#include "galaxy80.h"

#ifdef G80_FEATURES

/* Hue offset between neighbouring bar LEDs in the rainbow wave mode. */
#    define G80_HUE_STEP 13

/* Half period of the bar blink patterns in ms (a power of two: used as a timer bit mask). */
#    define G80_BAR_BLINK_MS 256u

/* Stock palette (8 colours) as RGB and as QMK hue / saturation (white = saturation 0). */
extern const uint8_t g80_palette_rgb[8][3];
extern const uint8_t g80_palette_hs[8][2];

/* Layer colours of the bar overlay and the status mode, indexed by layer (0..3). */
extern const uint8_t g80_layer_rgb[4][3];

/* Writes the bar LEDs led_min..led_max-1 of the active bar mode into the frame. */
void g80_bar_render(uint8_t led_min, uint8_t led_max);

/* Battery gauge on the whole bar (Fn+Space). LED 1 is bar index 0 (rgb_matrix LED 88, the first of
 * the 5 in keyboard.json: x grows 198..224 with the index; the layout has no vertical extent, so
 * "bottom" of a vertical bar is an assumption to confirm on the hardware, H-B1). pct 1..100: n =
 * ceil(pct / 20) LEDs lit, red below 20 %, yellow below 50 %, green otherwise; the top LED is
 * dimmed to r / 20 with r = pct - 20 * (n - 1). pct 0: LED 1 blinks red. pct 0xFF (unknown): LED 1
 * blinks white, slowly. flags: G80_GAUGE_CHARGING breathes the lit LEDs, G80_GAUGE_STALE shows
 * the value at 25 %, bits G80_GAUGE_VIS_SHIFT.. = LEDs the growing animation has reached (LED 1
 * first, one more every G80_GAUGE_STEP_MS from the press). Unlit bar LEDs are drawn black. The
 * number row mirrors it: keys 1..0 = 10 % each, lit in the bar colour, the others black. */
void g80_bar_gauge(uint8_t led_min, uint8_t led_max, uint8_t pct, uint8_t flags);

/* The 5 bar entries of the per-key table from (h0, s0) to (h1, s1); hue along the shorter way. */
void g80_bar_gradient(uint8_t h0, uint8_t s0_4, uint8_t h1, uint8_t s1_4);

#endif /* G80_FEATURES */
