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
 * (eeprom_update_* compares first). The per-key table is NOT part of it (Apply only, P0-9). */
#    ifndef G80_SAVE_DELAY_MS
#        define G80_SAVE_DELAY_MS 3000u
#    endif
#    ifndef G80_SAVE_MAX_LATENCY_MS
#        define G80_SAVE_MAX_LATENCY_MS 30000u
#    endif
#    ifndef G80_SAVE_MIN_INTERVAL_MS
#        define G80_SAVE_MIN_INTERVAL_MS 3000u
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
// therefore stored inverted. Set from the VIA menu (g80_via.c); everything else reads it only
// through the getters below. Fields are only ever appended.
// ---------------------------------------------------------------------------------------------
typedef union {
    uint32_t raw;
    struct {
        uint32_t sleep_opt : 2;     // bits 0..1: wireless idle sleep: 0 never, 1 / 2 / 3 = 1 / 5 / 30 min
        uint32_t chg_wired_off : 1; // bit 2: 1 = no charge LED while wired (default: shown)
        uint32_t low_batt_5x : 1;   // bit 3: low battery: 0 continuous Fn blink (stock), 1 five blinks every 30 s
        uint32_t nkro_off : 1;      // bit 4: 1 = no hybrid NKRO over wireless (default: on)
        uint32_t snap_mode : 3;     // bits 5..7: snap click: 0 off, 1 last, 2 first, 3 second, 4 neutral
        uint32_t batt_cap : 2;      // bits 8..9: brightness cap on battery: 0 none, 1 / 2 / 3 = 75 / 50 / 25 %
        uint32_t idle_led : 3;      // bits 10..12: wired idle LED off: 0 never, 1..4 = 1 / 5 / 10 / 30 min
        uint32_t reserved : 19;
    };
} g80_user_config_t;
_Static_assert(sizeof(g80_user_config_t) == 4, "g80_user_config_t must stay 32 bits (eeconfig_user)");

#    define G80_SNAP_MODES 5
#    define G80_IDLE_LED_OPTS 5

extern g80_user_config_t g80_ucfg;

/* Sleep option 0..3 (0 = never) and its countdown in ms (0 = never). */
static inline uint8_t g80_cfg_sleep_opt(void) {
    return (uint8_t)g80_ucfg.sleep_opt;
}
static inline uint32_t g80_cfg_sleep_ms(void) {
    switch (g80_ucfg.sleep_opt) {
        case 1:
            return 60000u;
        case 2:
            return 300000u;
        case 3:
            return 1800000u;
        default:
            return 0;
    }
}
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
/* Brightness cap on battery in percent (100 = no cap). */
static inline uint8_t g80_cfg_batt_cap_pct(void) {
    return (uint8_t)(100u - 25u * g80_ucfg.batt_cap);
}
/* Wired idle LED-off timeout in ms (0 = never, the default). */
static inline uint32_t g80_cfg_idle_led_ms(void) {
    switch (g80_ucfg.idle_led) {
        case 1:
            return 60000u;
        case 2:
            return 300000u;
        case 3:
            return 600000u;
        case 4:
            return 1800000u;
        default:
            return 0;
    }
}

#endif /* G80_FEATURES */
