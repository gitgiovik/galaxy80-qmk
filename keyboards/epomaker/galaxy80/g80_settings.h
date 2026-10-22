// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Persistent settings (eeconfig_kb + eeconfig_user), the deferred save and its write gate, the
// slow hardware inputs (Win/Mac switch, wireless slider, charger) and the entry points called
// from galaxy80.c.
#pragma once

#include "galaxy80.h"
#include <hal.h>

#ifdef G80_FEATURES

#    define G80_DEBOUNCE_MAX 10

/* Deferred settings save (V-1). A change only marks the settings dirty. The write becomes
 * eligible G80_SAVE_DELAY_MS after the LAST change, or G80_SAVE_MAX_LATENCY_MS after the FIRST
 * unsaved change (a slider dragged for minutes is still saved), and never sooner than
 * G80_SAVE_MIN_INTERVAL_MS after the previous settings write. An eligible save then starts in the
 * first pass where the write gate is open. A value toggled back is not written at all
 * (eeprom_update_* compares first). The per-key table is NOT part of it (Apply only, P0-9).
 * 1 s: a change is on flash ~1.25 s after the last one (the magenta
 * "unsaved" bar blink stops then), so an early unplug loses less; a VIA slider drag costs at most
 * one 4-line save per second. */
#    ifndef G80_SAVE_DELAY_MS
#        define G80_SAVE_DELAY_MS 1000u
#    endif
#    ifndef G80_SAVE_MAX_LATENCY_MS
#        define G80_SAVE_MAX_LATENCY_MS 30000u
#    endif
#    ifndef G80_SAVE_MIN_INTERVAL_MS
#        define G80_SAVE_MIN_INTERVAL_MS 1000u
#    endif

/* Write gate (V-3): a deferred save, an Apply flush or a VIA write that will consolidate the
 * wear-leveling log starts only while no key is down, no key changed in the last
 * G80_WRITE_GATE_MS, nothing is in flight to the radio module and the battery is not critical.
 * A key held without any matrix change for G80_GATE_HELD_MS no longer blocks it (an object
 * resting on a key must not block saving forever). */
#    ifndef G80_WRITE_GATE_MS
#        define G80_WRITE_GATE_MS 250u
#    endif
#    ifndef G80_GATE_HELD_MS
#        define G80_GATE_HELD_MS 600000u
#    endif

/* Called from galaxy80.c. */
void g80_features_init(void);
void g80_features_task(void);

/* The next deferred save also stores rgb_matrix_config (VIA lighting "save"). */
extern bool g80_save_rgb;
/* The next deferred save also stores keymap_config (NKRO keys, g80_fn.c). */
extern bool g80_save_keymap;
/* The next deferred save also stores default_layer_state (PDF(n) keys, g80_fn.c). */
extern bool g80_save_deflayer;

/* Marks the settings dirty (deferred save, see above). */
void g80_config_save(void);

/* Applies g80_config.debounce / deb_eager to the debounce engine. */
void g80_apply_debounce(void);

/* Write gate (see above). */
bool g80_save_gate_open(void);
/* I-F5: true from the critical battery message (5B 22) until 5B 23 or a charge edge, and never
 * while a charging cable is connected (g80_battery.c). While set no deferred save, Apply flush,
 * consolidating VIA write or factory reset starts; pending data stays in RAM. */
void g80_save_inhibit(bool inhibit);
bool g80_save_inhibited(void);
/* A settings save, an Apply flush or a VIA write is waiting or running (blocks deep sleep unless
 * the saves are inhibited, I-P1). */
bool g80_save_pending(void);

/* Wireless channel stored in the settings (0..2 = BT1..3, 3 = 2.4G). */
static inline uint8_t g80_wl_mode_get(void) {
    return (uint8_t)g80_config.wl_mode;
}
void g80_wl_mode_set(uint8_t m); /* deferred save */
/* Slider P0.7, raw (not debounced); high = ON (wireless position). */
static inline bool g80_raw_slider_now(void) {
    return (SN_GPIO0->DATA & (1u << 7)) != 0;
}

// ---------------------------------------------------------------------------------------------
// Second settings word (eeconfig_user, 32 bits). All zero = the factory defaults, so an EEPROM
// that never stored it (or a factory reset) reads as defaults; options that default to "on" are
// therefore stored inverted. Set from the VIA menu (g80_via.c). Fields are only ever appended
// (G80_EE_LAYOUT changes when one moves).
// ---------------------------------------------------------------------------------------------
typedef union {
    uint32_t raw;
    struct {
        uint32_t reserved0 : 2;     // bits 0..1: unused (keeps the positions of the fields below)
        uint32_t chg_wired_off : 1; // bit 2: 1 = no charge LED while wired (default: shown)
        uint32_t low_batt_5x : 1;   // bit 3: low battery: 0 continuous Fn blink (stock), 1 five blinks every 30 s
        uint32_t nkro_off : 1;      // bit 4: 1 = no hybrid NKRO over wireless (default: on)
        uint32_t snap_mode : 3;     // bits 5..7: snap click: 0 off, 1 last, 2 first, 3 second, 4 neutral
        uint32_t batt_cap : 2;      // bits 8..9: brightness cap on battery: 0 none, 1 / 2 / 3 = 75 / 50 / 25 %
        uint32_t led_off : 3;       // bits 10..12: LEDs off after (wired and wireless): 0 = 5 min, 1 never, 2 / 3 / 4 = 1 / 10 / 30 min
        uint32_t deep_sleep : 3;    // bits 13..15: wireless deep sleep after: 0 never, 1..4 = 5 / 10 / 30 / 60 min
        uint32_t reserved : 16;
    };
} g80_user_config_t;
_Static_assert(sizeof(g80_user_config_t) == 4, "g80_user_config_t must stay 32 bits (eeconfig_user)");

#    define G80_SNAP_MODES 5
#    define G80_LED_OFF_OPTS 5
#    define G80_DEEP_SLEEP_OPTS 5

extern g80_user_config_t g80_ucfg;

/* Charge state on the Fn LED also while wired (default on). */
static inline bool g80_cfg_charge_led_wired(void) {
    return !g80_ucfg.chg_wired_off;
}
/* Low-battery warning: false = continuous Fn blink (stock), true = 5 blinks every 30 s. */
static inline bool g80_cfg_low_batt_5x(void) {
    return g80_ucfg.low_batt_5x;
}
/* Hybrid NKRO (A1 + A2) over wireless (default on). */
static inline bool g80_cfg_wl_nkro(void) {
    return !g80_ucfg.nkro_off;
}
/* Snap click mode 0..4 (0 = off, the factory default). */
static inline uint8_t g80_cfg_snap_mode(void) {
    return (uint8_t)g80_ucfg.snap_mode;
}

#endif /* G80_FEATURES */
