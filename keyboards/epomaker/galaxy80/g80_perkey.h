// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Per-key colour table: storage, paint session (Apply / Discard), on-board editor, snapshot of the
// running effect.
// The table itself (g80_pk, g80_pk_editing, g80_pk_rgb) is declared in galaxy80.h.
#pragma once

#include "galaxy80.h"

#ifdef G80_FEATURES

/* First byte of the VIA custom-config block = EEPROM layout version (3 layers, 2 B per LED, effect
 * ids of the trimmed effect list). Any other value: via_init_kb() resets every VIA block (keymaps,
 * macros, colours), because their addresses or meanings moved. */
#    define G80_EE_LAYOUT 0xA7

/* 8-bit saturation <-> the 4-bit nibble the table stores. */
static inline uint8_t g80_s8_to_s4(uint8_t s) {
    return (uint8_t)(((uint16_t)s * G80_PK_VMAX + 127) / 255);
}
static inline uint8_t g80_s4_to_s8(uint8_t s4) {
    return (uint8_t)(s4 * 17);
}

/* Paint session (P0-9): every stroke (on-board editor, VIA painter and brush, bar gradient, Reset
 * Key Colors, the snapshot of the running effect) changes only the RAM table and sets g80_pk_dirty
 * ("unsaved"). Nothing is written until Apply: one write of the table + rgb_matrix_config behind
 * the write gate. Discard reloads the stored table. Leaving the editor (also by its idle timeout)
 * keeps the changes unsaved in RAM; a reset or power-off loses them. */
extern bool g80_pk_dirty;       /* the table differs from what was last stored ("unsaved") */
void        g80_pk_apply(void); /* request the Apply write (deferred, write gate) */
void        g80_pk_discard(void);
bool        g80_pk_apply_pending(void);
/* Unsaved indicator: bar LED 5 slow magenta blink while unsaved and the editor is off. Drawn
 * right after the bar mode, so every other bar overlay wins. */
void g80_pk_unsaved_render(uint8_t led_min, uint8_t led_max);

/* Editor / VIA brush state. */
extern uint8_t g80_pk_cur; /* last edited LED */
extern uint8_t g80_pk_brush_h;
extern uint8_t g80_pk_brush_s4;
extern bool    g80_pk_brush_on;

/* Snapshot of the running effect: ARMED = the next indicator pass clears the key-backlight-off
 * mask, WAIT = waiting for the first chunk of a frame, RUN = capturing one full frame. */
enum { G80_SNAP_IDLE, G80_SNAP_ARMED, G80_SNAP_WAIT, G80_SNAP_RUN };
extern uint8_t g80_pk_snap;

/* Table access. */
void g80_pk_put(uint8_t led, uint8_t h, uint8_t s4, uint8_t v4);
void g80_pk_mark(uint8_t led); /* the LED was edited: a running snapshot must not overwrite it */
void g80_pk_defaults(void);
void g80_pk_load(void);
void g80_pk_store(void);

/* Editor and snapshot. */
void g80_pk_take_over(void);
void g80_pk_enter(void);
void g80_pk_exit(void);
void g80_pk_task(void);
bool g80_pk_passthrough(uint16_t keycode);
void g80_pk_snap_step(uint8_t led_min, uint8_t led_max);
void g80_pk_render(uint8_t led_min, uint8_t led_max);

#endif /* G80_FEATURES */
