// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Persistent settings and the slow hardware inputs.
//   - g80_config (eeconfig_kb), g80_ucfg (eeconfig_user) and their deferred save: a change only
//     marks the settings dirty, the write happens from housekeeping behind the write gate.
//   - EEPROM layout check (via_init_kb) and factory-reset defaults (eeconfig_init_kb).
//   - Win/Mac switch P0.2, wireless slider P0.7 and charger pins P3.4 / P3.5, each debounced with
//     the stock timing (2 ms poll, 20 stable samples).
//   - g80_features_init() / g80_features_task(), called from galaxy80.c.

#include "g80_settings.h"

#ifdef G80_FEATURES

#    include "g80_bar.h"
#    include "g80_battery.h"
#    include "g80_fn.h"
#    include "g80_gaming.h"
#    include "g80_perkey.h"
#    include "g80_radio.h"
#    include "g80_test_hooks.h"
#    include "rgb_matrix.h"
#    include "eeconfig.h"
#    include "keycode_config.h"
#    include "timer.h"
#    include <hal.h>
#    include "matrix.h"
#    ifdef VIA_ENABLE
#        include "via.h"
#        include "g80_via.h"
#    endif

#    define G80_SLOW_PERIOD_MS 2     /* stock: slow pins polled every 2 ms */
#    define G80_SLOW_DEBOUNCE 20     /* stock: 20 stable samples (~40 ms) */
#    define G80_FULL_GREEN_MS 60000u /* stock: green for 60 s at end of charge */

/* debounce engine, g80_debounce.c */
extern bool g80_debounce_eager;

g80_config_t g80_config;

/* ------------------------------------------------------------------------- */
/* Slow inputs                                                                */
/* ------------------------------------------------------------------------- */
g80_deb_t        g80_in_mac, g80_in_slider;
g80_deb_t        g80_in_chg, g80_in_full;
static uint16_t  g80_slow_timer;
static bool      g80_inputs_ready;
static uint32_t  g80_full_since;
bool             g80_full_window;

static bool g80_deb_update(g80_deb_t *d, bool raw) {
    if (raw != d->last) {
        d->last  = raw;
        d->count = 0;
        return false;
    }
    if (d->count < G80_SLOW_DEBOUNCE) {
        d->count++;
        if (d->count == G80_SLOW_DEBOUNCE && raw != d->stable) {
            d->stable = raw;
            return true;
        }
    }
    return false;
}

static void g80_deb_init(g80_deb_t *d, bool raw) {
    d->stable = raw;
    d->last   = raw;
    d->count  = G80_SLOW_DEBOUNCE;
}

static inline bool g80_raw_mac(void) {
    const bool low = (SN_GPIO0->DATA & (1u << 2)) == 0;
    return G80_MAC_WHEN_P02_LOW ? low : !low;
}
static inline bool g80_raw_charging(void) {
    return (SN_GPIO3->DATA & (1u << 5)) == 0; /* P3.5 low = charging */
}
static inline bool g80_raw_full(void) {
    return (SN_GPIO3->DATA & (1u << 4)) != 0; /* P3.4 high = charge complete */
}

/* ------------------------------------------------------------------------- */
/* Settings and the deferred save                                             */
/* ------------------------------------------------------------------------- */
g80_user_config_t g80_ucfg;
bool              g80_save_rgb;
bool              g80_save_keymap;
bool              g80_save_deflayer;
static bool       g80_save_dirty;
static bool       g80_save_off; /* I-F5: critical battery */
static uint32_t   g80_save_first_change, g80_save_last_change, g80_save_last_write;

void g80_save_inhibit(bool inhibit) {
    g80_save_off = inhibit;
}
bool g80_save_inhibited(void) {
    return g80_save_off;
}

bool g80_save_gate_open(void) {
    if (g80_save_off || !g80_radio_idle()) {
        return false;
    }
    const uint32_t quiet = last_matrix_activity_elapsed();
    if (quiet < G80_WRITE_GATE_MS) {
        return false;
    }
    if (quiet < G80_GATE_HELD_MS) {
        for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
            if (matrix_get_row(r)) {
                return false;
            }
        }
    }
    return true;
}

bool g80_save_pending(void) {
    return g80_save_dirty || g80_pk_apply_pending()
#    ifdef VIA_ENABLE
           || g80_via_write_pending()
#    endif
        ;
}

/* The write itself, from the deferred task only (right after the main-loop watchdog feed).
 * noinline: keeps its watchdog feed a separate, auditable call site. */
static __attribute__((noinline)) void g80_config_save_now(void) {
    g80_save_dirty      = false;
    g80_save_last_write = timer_read32();
    eeconfig_update_kb(g80_config.raw);
    eeconfig_update_user(g80_ucfg.raw);
    if (g80_save_rgb) {
        g80_save_rgb = false;
        g80_wdt_kick();
        eeconfig_update_rgb_matrix(&rgb_matrix_config);
    }
    if (g80_save_keymap) {
        g80_save_keymap = false;
        g80_wdt_kick();
        /* in Mac mode this also stores the switch's Alt/GUI swap bits: harmless, g80_apply_mac() sets
         * them again from the switch at every boot */
        eeconfig_update_keymap(&keymap_config);
    }
    if (g80_save_deflayer) {
        g80_save_deflayer = false;
        g80_wdt_kick();
        eeconfig_update_default_layer(default_layer_state);
    }
}

void g80_config_save(void) {
    const uint32_t now = timer_read32();
    if (!g80_save_dirty) {
        g80_save_dirty        = true;
        g80_save_first_change = now;
    }
    g80_save_last_change = now;
}

void g80_wl_mode_set(uint8_t m) {
    if (g80_config.wl_mode != (m & 3u)) {
        g80_config.wl_mode = m & 3u;
        g80_config_save(); /* deferred */
    }
}

/* called from g80_features_task() right after the watchdog feed of housekeeping_task_kb() */
static void g80_save_task(void) {
    if (!g80_save_dirty || timer_elapsed32(g80_save_last_write) < G80_SAVE_MIN_INTERVAL_MS) {
        return;
    }
    if (timer_elapsed32(g80_save_last_change) < G80_SAVE_DELAY_MS && timer_elapsed32(g80_save_first_change) < G80_SAVE_MAX_LATENCY_MS) {
        return;
    }
    if (g80_save_gate_open()) {
        g80_config_save_now();
    }
}

/* Factory reset / first boot (eeconfig_init(), after eeconfig_init_via()): both settings words
 * zero (= defaults: sleep never, snap off, NKRO on, charge LED wired on, no caps) and, VIA image,
 * the per-key defaults stored with the current layout byte. */
void eeconfig_init_kb(void) {
    eeconfig_update_kb(0);
    eeconfig_update_user(0);
#    ifdef VIA_ENABLE
    g80_pk_defaults();
    g80_pk_store();
#    endif
    eeconfig_init_user();
}

#    ifdef VIA_ENABLE
/* VIA EEPROM layout check, before QMK validates its own magic. QMK's VIA magic only encodes the
 * build date, so an image with another EEPROM layout would read the old keymaps and colours at
 * the wrong addresses. Another layout byte -> VIA magic invalidated (via_init() then resets
 * keymaps, macros and layout options), the RGB settings reset (an older layout numbered the
 * effects differently, its stored effect id would select the wrong effect) and the per-key
 * defaults stored. Runs at boot, before the watchdog is started. */
void via_init_kb(void) {
    uint8_t ver = 0;
    via_read_custom_config(&ver, 0, 1);
    if (ver != G80_EE_LAYOUT) {
        via_eeprom_set_valid(false);
        eeconfig_update_rgb_matrix_default();
        eeconfig_update_user(0);
        g80_pk_defaults();
        g80_pk_store();
    }
}
#    endif

/* The Win/Mac switch owns the Alt/GUI swap bits: an AG_* magic key lasts only until the next boot
 * or switch move. */
static void g80_apply_mac(bool mac, bool runtime) {
    if (runtime) {
        clear_keyboard(); /* nothing stays pressed across the modifier swap */
    }
    keymap_config.swap_lalt_lgui = mac;
    keymap_config.swap_ralt_rgui = mac;
}

void g80_apply_debounce(void) {
    uint8_t ms = g80_config.debounce;
    if (ms == 0 || ms > G80_DEBOUNCE_MAX) {
        ms = G80_DEBOUNCE_DEFAULT;
    }
    g80_debounce_ms    = ms;
    g80_debounce_eager = g80_config.deb_eager;
}

void g80_features_init(void) {
    g80_config.raw = eeconfig_read_kb();
    g80_ucfg.raw   = eeconfig_read_user();
    if (g80_ucfg.snap_mode >= G80_SNAP_MODES) {
        g80_ucfg.snap_mode = 0;
    }
    if (g80_ucfg.idle_led >= G80_IDLE_LED_OPTS) {
        g80_ucfg.idle_led = 0;
    }
    if (g80_config.bar_mode >= G80_BAR_MODES) {
        g80_config.bar_mode = G80_BAR_FOLLOW;
    }
    if (g80_config.debounce > G80_DEBOUNCE_MAX) {
        g80_config.debounce = 0;
    }
    g80_apply_win_lock();
    g80_apply_debounce();
    g80_mask_keys = g80_config.keys_off;
    g80_pk_load();

    g80_deb_init(&g80_in_mac, g80_raw_mac());
    g80_deb_init(&g80_in_slider, g80_raw_slider_now());
    g80_deb_init(&g80_in_chg, g80_raw_charging());
    g80_deb_init(&g80_in_full, g80_raw_full());
    if (g80_in_full.stable) {
        g80_full_window = true;
        g80_full_since  = timer_read32();
    }
    g80_apply_mac(g80_in_mac.stable, false);
    g80_slow_timer   = timer_read();
    g80_inputs_ready = true;
}

/* ------------------------------------------------------------------------- */
/* Housekeeping (main loop)                                                   */
/* ------------------------------------------------------------------------- */
void g80_features_task(void) {
    if (!g80_inputs_ready) {
        return;
    }
    if (timer_elapsed(g80_slow_timer) >= G80_SLOW_PERIOD_MS) {
        g80_slow_timer = timer_read();
        if (g80_deb_update(&g80_in_mac, g80_raw_mac())) {
            g80_apply_mac(g80_in_mac.stable, true);
            g80_dir_reset(); /* clear_keyboard() above */
            if (g80_in_mac.stable) {
                g80_flash(G80_FL_KEYS, 0, 0, 0, 255, 5, 240); /* Mac: 3 blue flashes */
            } else {
                g80_flash(G80_FL_KEYS, 0, 0, 255, 0, 5, 240); /* Win: 3 green flashes */
            }
        }
        g80_deb_update(&g80_in_slider, g80_raw_slider_now());
        g80_deb_update(&g80_in_chg, g80_raw_charging());
        if (g80_deb_update(&g80_in_full, g80_raw_full()) && g80_in_full.stable) {
            g80_full_window = true;
            g80_full_since  = timer_read32();
        }
        if (g80_full_window && timer_elapsed32(g80_full_since) >= G80_FULL_GREEN_MS) {
            g80_full_window = false;
        }
    }
    g80_flash_task();
    g80_battery_task();
    if (!g80_frst_running) { /* never right before a factory reset (it invalidates eeconfig anyway) */
        g80_pk_task();
        g80_save_task();
#    ifdef VIA_ENABLE
        g80_via_task();
#    endif
    }
    g80_frst_task();
}

#endif /* G80_FEATURES */
