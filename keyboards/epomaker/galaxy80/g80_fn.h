// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Fn-layer actions, LED flashes and the factory reset.
#pragma once

#include "galaxy80.h"

#ifdef G80_FEATURES

/* Flash targets: all key LEDs, the four arrow keys, one LED. */
enum { G80_FL_NONE, G80_FL_KEYS, G80_FL_ARROWS, G80_FL_ONE };

/* Starts a flash: `halves` half periods of `half_ms`, colour r/g/b, starting "on". */
void g80_flash(uint8_t target, uint8_t led, uint8_t r, uint8_t g, uint8_t b, uint8_t halves, uint16_t half_ms);
void g80_flash_task(void);
/* Draws the active flash over LEDs led_min..led_max-1 (last step of the indicator pass). */
void g80_flash_render(uint8_t led_min, uint8_t led_max);

/* Factory reset (Fn+Backspace held 3 s) is in progress: the settings are no longer saved. */
extern bool g80_frst_running;
void        g80_frst_task(void);

/* Called by galaxy80.c first for every key event; returns false when the key has been handled. */
bool g80_features_process_record(uint16_t keycode, keyrecord_t *record);

#endif /* G80_FEATURES */
