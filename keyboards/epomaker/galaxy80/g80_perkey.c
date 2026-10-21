// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Per-key colour table (effect "Per-key"): 2 bytes per LED (hue, saturation nibble << 4 | value
// nibble) for the 88 keys and the 5 side-bar LEDs, kept in RAM and stored in the VIA custom-config
// block. Also the paint session (Apply / Discard), the on-board editor (Fn+K), the snapshot of the
// running effect the editor and the VIA painter start from, and the editor's key handling.

#include "g80_perkey.h"

#ifdef G80_FEATURES

#    include "g80_bar.h"
#    include "g80_fn.h"
#    include "g80_gaming.h"
#    include "g80_indicators.h"
#    include "g80_settings.h"
#    include "keymap_introspection.h"
#    include "rgb_matrix.h"
#    include "timer.h"
#    include <string.h>
#    include <lib/lib8tion/lib8tion.h>
#    ifdef VIA_ENABLE
#        include "via.h"
#    endif

/* the editor leaves by itself (without saving) after this long without an editor key */
#    ifndef G80_PK_IDLE_MS
#        define G80_PK_IDLE_MS 120000u
#    endif
/* editor / painter: give up the snapshot of the running effect after this long (no rendering) */
#    define G80_PK_SNAP_TIMEOUT_MS 500u

/* g80_pk[2n] = hue, g80_pk[2n+1] = saturation nibble << 4 | value nibble (galaxy80.h) */
uint8_t g80_pk[G80_PK_BYTES];
bool    g80_pk_editing;
bool    g80_pk_dirty;

/* editor presets 1..14: red, red-orange, orange (stock), amber, yellow (stock), lime, green (stock),
 * spring, cyan (stock), azure, blue (stock), violet, magenta, pink (stock); 0 off, 15 white */
static const uint8_t g80_pk_hue[14] = {0, 11, 21, 32, 43, 64, 85, 106, 128, 149, 170, 191, 213, 234};

void g80_pk_put(uint8_t led, uint8_t h, uint8_t s4, uint8_t v4) {
    if (led >= G80_LED_COUNT) {
        return;
    }
    const uint8_t sv = (uint8_t)(((s4 & 0x0F) << 4) | (v4 & 0x0F));
    if (g80_pk[2u * led] != h || g80_pk[2u * led + 1u] != sv) {
        g80_pk[2u * led]      = h;
        g80_pk[2u * led + 1u] = sv;
        g80_pk_dirty          = true;
    }
}

rgb_t g80_pk_rgb(uint8_t led, uint8_t val) {
    if (led >= G80_LED_COUNT) {
        return (rgb_t){0, 0, 0};
    }
    const uint8_t sv = g80_pk[2u * led + 1u];
    const uint8_t v4 = sv & 0x0F;
    if (v4 == 0) {
        return (rgb_t){0, 0, 0};
    }
    const hsv_t hsv = {g80_pk[2u * led], g80_s4_to_s8(sv >> 4), v4 == G80_PK_VMAX ? val : (uint8_t)(((uint16_t)val * v4 + 7) / G80_PK_VMAX)};
    return hsv_to_rgb(hsv);
}

static void g80_pk_preset(uint8_t led, uint8_t code) {
    if (code == G80_PK_OFF) {
        g80_pk_put(led, 0, 0, 0);
    } else if (code >= G80_PK_WHITE) {
        g80_pk_put(led, 0, 0, G80_PK_VMAX);
    } else {
        g80_pk_put(led, g80_pk_hue[code - 1], G80_PK_VMAX, G80_PK_VMAX);
    }
}

/* hue/saturation -> nearest editor preset (low saturation = white) */
static uint8_t g80_hs_to_code(uint8_t h, uint8_t s) {
    if (s < 64) {
        return G80_PK_WHITE;
    }
    uint8_t best = 1, dmin = 255;
    for (uint8_t i = 0; i < sizeof(g80_pk_hue); i++) {
        uint8_t d = (uint8_t)(h - g80_pk_hue[i]);
        if ((uint8_t)(256 - d) < d) {
            d = (uint8_t)(256 - d);
        }
        if (d < dmin) {
            dmin = d;
            best = (uint8_t)(i + 1);
        }
    }
    return best;
}

/* preset a LED is at (or the nearest one, for colours from the painter or a snapshot) */
static uint8_t g80_pk_code(uint8_t led) {
    const uint8_t sv = g80_pk[2u * led + 1u];
    if ((sv & 0x0F) == 0) {
        return G80_PK_OFF;
    }
    return g80_hs_to_code(g80_pk[2u * led], g80_s4_to_s8(sv >> 4));
}

/* displayed colour (frame duties, R/G of the bar swapped back) -> table entry, relative to the
 * brightness `ref` the effect rendered with. ref 0 = nothing can be learnt: entry kept. */
static void g80_pk_from_rgb(uint8_t led, uint8_t r, uint8_t g, uint8_t b, uint8_t ref) {
    const uint8_t mx = r > g ? (r > b ? r : b) : (g > b ? g : b);
    const uint8_t mn = r < g ? (r < b ? r : b) : (g < b ? g : b);
    if (mx == 0) {
        g80_pk_put(led, 0, 0, 0);
        return;
    }
    if (ref == 0) {
        return;
    }
    const uint8_t d = (uint8_t)(mx - mn);
    int16_t       h = 0;
    if (d) {
        if (mx == r) {
            h = (int16_t)(43 * ((int16_t)g - (int16_t)b) / d);
        } else if (mx == g) {
            h = (int16_t)(85 + 43 * ((int16_t)b - (int16_t)r) / d);
        } else {
            h = (int16_t)(171 + 43 * ((int16_t)r - (int16_t)g) / d);
        }
    }
    uint16_t v = ((uint16_t)mx * G80_PK_VMAX + ref / 2) / ref;
    if (v < 1) {
        v = 1;
    } else if (v > G80_PK_VMAX) {
        v = G80_PK_VMAX;
    }
    g80_pk_put(led, (uint8_t)(h & 0xFF), g80_s8_to_s4((uint8_t)((uint16_t)d * 255 / mx)), (uint8_t)v);
}

void g80_pk_defaults(void) {
    /* white keys, red Esc + WASD + arrows, gradient on the bar */
    static const uint8_t red[] = {0, 36, 52, 53, 54, G80_LED_UP, G80_LED_LEFT, G80_LED_DOWN, G80_LED_RIGHT};
    for (uint8_t i = 0; i < G80_LED_COUNT; i++) {
        g80_pk_preset(i, G80_PK_WHITE);
    }
    for (uint8_t i = 0; i < sizeof(red); i++) {
        g80_pk_preset(red[i], 1);
    }
    g80_bar_gradient(213, G80_PK_VMAX, 128, G80_PK_VMAX); /* side bar: magenta -> cyan */
}

void g80_pk_load(void) {
#    ifdef VIA_ENABLE
    uint8_t ver = 0;
    via_read_custom_config(&ver, 0, 1);
    if (ver == G80_EE_LAYOUT) {
        via_read_custom_config(g80_pk, 1, G80_PK_BYTES);
        g80_pk_dirty = false;
        return;
    }
#    endif
    g80_pk_defaults();
    g80_pk_dirty = false;
}

/* Writes only the runs of changed bytes (wear-leveling log economy), then the layout byte (an
 * interrupted write leaves the old layout byte = defaults at the next boot). The watchdog is fed
 * before each run (a run that fills the log triggers a consolidation: erase + rewrite). Bounded
 * (no feed inside a loop that could spin forever): i only grows, at most G80_PK_BYTES / 2
 * + 1 runs, each run one QMK EEPROM call. */
void g80_pk_store(void) {
    g80_pk_dirty = false;
#    ifdef VIA_ENABLE
    uint8_t old[G80_PK_BYTES];
    via_read_custom_config(old, 1, G80_PK_BYTES);
    uint8_t i = 0;
    while (i < G80_PK_BYTES) {
        if (old[i] == g80_pk[i]) {
            i++;
            continue;
        }
        uint8_t j = (uint8_t)(i + 1);
        while (j < G80_PK_BYTES && old[j] != g80_pk[j]) {
            j++;
        }
        g80_wdt_kick();
        via_update_custom_config(&g80_pk[i], 1u + i, (uint32_t)(j - i));
        i = j;
    }
    const uint8_t ver = G80_EE_LAYOUT;
    g80_wdt_kick();
    via_update_custom_config(&ver, 0, 1);
#    endif
}

/* ------------------------------------------------------------------------- */
/* On-board per-key colour editor (stock action 0xD0 equivalent)              */
/* ------------------------------------------------------------------------- */
uint8_t         g80_pk_cur = G80_NO_LED; /* last edited LED (the VIA brush colour also lands here) */
static uint8_t  g80_pk_tap = G80_NO_LED; /* Shift/Fn pressed alone: edited on release */
static uint32_t g80_pk_activity;
uint8_t         g80_pk_brush_h; /* VIA "brush" colour: editor keys paint it instead of the presets */
uint8_t         g80_pk_brush_s4;
bool            g80_pk_brush_on;

/* Snapshot of the running effect: the editor and the VIA painter start from the colours that are
 * on the keys. LEDs edited meanwhile are marked in g80_pk_keep and not overwritten. */
uint8_t         g80_pk_snap;
static uint32_t g80_pk_snap_t0;
static uint8_t  g80_pk_keep[(G80_LED_COUNT + 7) / 8];

void g80_pk_mark(uint8_t led) {
    if (led < G80_LED_COUNT) {
        g80_pk_keep[led >> 3] |= (uint8_t)(1u << (led & 7));
    }
}

/* "Per-key" becomes the effect (lit), in RAM; Apply stores it with the table */
static void g80_pk_show(void) {
    if (!rgb_matrix_is_enabled()) {
        rgb_matrix_enable_noeeprom();
    }
    if (rgb_matrix_get_mode() != RGB_MATRIX_CUSTOM_G80_PER_KEY) {
        rgb_matrix_mode_noeeprom(RGB_MATRIX_CUSTOM_G80_PER_KEY);
    }
}

/* Editor entry / first VIA paint: if another effect is on the keys, copy what it shows into the
 * table first (asynchronous, one frame); otherwise switch to "Per-key" right away. */
void g80_pk_take_over(void) {
    if (g80_pk_snap != G80_SNAP_IDLE) {
        return;
    }
    const uint8_t mode = rgb_matrix_get_mode();
    if (rgb_matrix_is_enabled() && mode != RGB_MATRIX_NONE && mode != RGB_MATRIX_CUSTOM_G80_PER_KEY) {
        memset(g80_pk_keep, 0, sizeof(g80_pk_keep));
        g80_pk_snap    = G80_SNAP_ARMED;
        g80_pk_snap_t0 = timer_read32();
    } else {
        g80_pk_show();
    }
}

/* called first in every indicator pass (g80_frame holds what the effect just drew for LEDs
 * led_min..led_max-1, before any indicator) */
void g80_pk_snap_step(uint8_t led_min, uint8_t led_max) {
    if (g80_pk_snap == G80_SNAP_ARMED) {
        g80_pk_snap = G80_SNAP_WAIT; /* the chunks drawn from now on are unmasked */
        return;
    }
    if (g80_pk_snap == G80_SNAP_WAIT && led_min == 0) {
        g80_pk_snap = G80_SNAP_RUN;
    }
    if (g80_pk_snap != G80_SNAP_RUN) {
        return;
    }
    uint8_t ref = rgb_matrix_get_val();
    if (ref > G80_DUTY_MAX) {
        ref = G80_DUTY_MAX; /* the frame holds capped duties */
    }
    for (uint8_t i = led_min; i < led_max && i < G80_LED_COUNT; i++) {
        if (g80_pk_keep[i >> 3] & (1u << (i & 7))) {
            continue;
        }
        const uint8_t pos = g80_led_pos[i];
        const uint8_t row = pos >> 5, col = pos & 0x1F;
        uint8_t       r = g80_frame[row][col], g = g80_frame[G80_LED_ROWS + row][col], b = g80_frame[2 * G80_LED_ROWS + row][col];
        if (i >= G80_LED_KEYS_COUNT) {
            const uint8_t t = r; /* side bar: R and G swapped on the PCB (g80_put) */
            r               = g;
            g               = t;
        }
        g80_pk_from_rgb(i, r, g, b, ref);
    }
    if (led_max >= G80_LED_COUNT) {
        g80_pk_snap = G80_SNAP_IDLE;
        g80_pk_show();
        g80_pk_dirty = true; /* unsaved until Apply */
    }
}

/* one edit of the on-board editor: VIA brush colour, else the next / previous preset */
static void g80_pk_edit_led(uint8_t led, bool back) {
    if (led >= G80_LED_COUNT) {
        return;
    }
    g80_pk_mark(led);
    if (g80_pk_brush_on && !back) {
        g80_pk_put(led, g80_pk_brush_h, g80_pk_brush_s4, G80_PK_VMAX);
    } else {
        g80_pk_preset(led, (uint8_t)((g80_pk_code(led) + (back ? G80_PK_PRESETS - 1 : 1)) % G80_PK_PRESETS));
    }
    g80_pk_cur = led;
}

void g80_pk_enter(void) {
    clear_keyboard(); /* nothing stays pressed on the host while keys are swallowed */
    g80_dir_reset();
    g80_pk_editing  = true;
    g80_pk_cur      = G80_NO_LED;
    g80_pk_tap      = G80_NO_LED;
    g80_pk_brush_on = false;
    g80_pk_activity = timer_read32();
    g80_pk_take_over();
}

/* Leaving the editor never writes: unsaved changes stay in RAM (unsaved indicator). */
void g80_pk_exit(void) {
    g80_pk_editing = false;
    g80_pk_tap     = G80_NO_LED;
}

/* ------------------------------------------------------------------------- */
/* Paint session: Apply / Discard                                             */
/* ------------------------------------------------------------------------- */
static bool g80_pk_apply_req;

void g80_pk_apply(void) {
    g80_pk_apply_req = true;
}

bool g80_pk_apply_pending(void) {
    return g80_pk_apply_req;
}

/* Back to the stored table (the wear-leveling cache), no flash write; a running snapshot is dropped
 * so that it cannot overwrite the reloaded table. */
void g80_pk_discard(void) {
    g80_pk_apply_req = false;
    g80_pk_snap      = G80_SNAP_IDLE;
    g80_pk_load();
    g80_flash(G80_FL_KEYS, 0, 255, 0, 0, 4, 150); /* 2 red flashes */
}

void g80_pk_task(void) {
    if (g80_pk_editing && timer_elapsed32(g80_pk_activity) >= G80_PK_IDLE_MS) {
        g80_pk_exit(); /* safety net: two minutes without any key; changes stay unsaved */
    }
    if (g80_pk_snap != G80_SNAP_IDLE && timer_elapsed32(g80_pk_snap_t0) >= G80_PK_SNAP_TIMEOUT_MS) {
        g80_pk_snap = G80_SNAP_IDLE; /* nothing rendered (suspend...): keep the stored table */
        g80_pk_show();
    }
    /* Apply: one write of the table and of rgb_matrix_config ("Per-key"), once a running snapshot
     * has finished and only behind the write gate (a request made while typing waits) */
    if (g80_pk_apply_req && g80_pk_snap == G80_SNAP_IDLE && g80_save_gate_open()) {
        g80_pk_apply_req = false;
        g80_pk_store(); /* feeds the watchdog before each run */
        eeconfig_update_rgb_matrix(&rgb_matrix_config);
        g80_flash(G80_FL_KEYS, 0, 0, 255, 0, 4, 150); /* 2 green flashes */
    }
}

void g80_pk_unsaved_render(uint8_t led_min, uint8_t led_max) {
    if (g80_pk_dirty && !g80_pk_editing && (timer_read32() & (2 * G80_BAR_BLINK_MS))) {
        G80_SET(G80_LED_BAR0 + G80_LED_BAR_COUNT - 1, 255, 0, 255);
    }
}

/* keys that keep working in the editor: layer keys (Fn), Shift (backwards + Fn+RShift+Esc),
 * the editor key itself (the only way out, with the VIA switch) and the safety / Fn functions
 * bootloader, factory reset, side bar */
bool g80_pk_passthrough(uint16_t keycode) {
    return (keycode >= QK_LAYER_TAP && keycode <= QK_LAYER_TAP_TOGGLE_MAX) || keycode == KC_LSFT || keycode == KC_RSFT || keycode == G80_SBOOT || keycode == G80_FRST || keycode == G80_BAR || keycode == G80_EDIT;
}

/* Editor key handling, before combos and tap-hold (pre_process_record_kb). Returns false
 * for every swallowed event: nothing of it reaches the host. Esc is an ordinary key here: it
 * gets its colour like every other key; only Fn+K / VIA leave the editor. */
static bool g80_pk_key(uint16_t keycode, keyrecord_t *record) {
    const keypos_t pos     = record->event.key;
    const bool     pressed = record->event.pressed;
    if (pos.row >= MATRIX_ROWS || pos.col >= MATRIX_COLS) {
        return true; /* not a matrix event (combo, encoder...) */
    }
    uint8_t led = g_led_config.matrix_co[pos.row][pos.col];
    if (g80_pk_passthrough(keycode)) {
        /* Shift or Fn tapped alone: that key's own LED */
        if (keycode == KC_LSFT || keycode == KC_RSFT || IS_QK_MOMENTARY(keycode)) {
            if (pressed) {
                g80_pk_tap = led;
            } else {
                if (g80_pk_tap == led && led != NO_LED) {
                    g80_pk_activity = timer_read32();
                    /* a Shift tapped alone goes backwards only if the OTHER Shift is held */
                    const uint8_t self = keycode == KC_LSFT ? MOD_BIT(KC_LSFT) : (keycode == KC_RSFT ? MOD_BIT(KC_RSFT) : 0);
                    g80_pk_edit_led(led, (get_mods() & MOD_MASK_SHIFT & (uint8_t)~self) != 0);
                }
                g80_pk_tap = G80_NO_LED;
            }
        } else if (pressed) {
            g80_pk_tap = G80_NO_LED;
        }
        return true;
    }
    if (!pressed) {
        return false;
    }
    g80_pk_tap      = G80_NO_LED;
    g80_pk_activity = timer_read32();
    if (IS_LAYER_ON(1)) {
        /* Fn + the base-layer Enter / Delete key: Apply / Discard (outside the editor Fn+Enter keeps
         * its Fn-layer function). Fn + 1..5: the 5 side-bar LEDs; every other Fn combination does
         * nothing. */
        const uint16_t base = keycode_at_keymap_location(0, pos.row, pos.col);
        if (base == KC_ENT) {
            g80_pk_apply();
            return false;
        }
        if (base == KC_DEL) {
            g80_pk_discard();
            return false;
        }
        led = (pos.row == 1 && pos.col >= 1 && pos.col <= G80_LED_BAR_COUNT) ? (uint8_t)(G80_LED_BAR0 + pos.col - 1) : G80_NO_LED;
    }
    g80_pk_edit_led(led, (get_mods() & MOD_MASK_SHIFT) != 0);
    return false;
}

bool pre_process_record_kb(uint16_t keycode, keyrecord_t *record) {
    if (g80_pk_editing && !g80_pk_key(keycode, record)) {
        return false;
    }
    return pre_process_record_user(keycode, record);
}

/* Editor: the whole table on every LED. Input is suppressed while it is on, so the side bar shows it
 * unmistakably: every G80_BAR_BLINK_MS the 5 bar LEDs alternate between their own table colour (an
 * "off" bar LED shows dim white) and solid magenta. */
void g80_pk_render(uint8_t led_min, uint8_t led_max) {
    uint8_t val = rgb_matrix_get_val();
    if (val < 64) {
        val = 64; /* always visible while editing */
    }
    const bool magenta = (timer_read32() & G80_BAR_BLINK_MS) != 0;
    for (uint8_t i = led_min; i < led_max && i < G80_LED_COUNT; i++) {
        rgb_t c = g80_pk_rgb(i, val);
        if (i >= G80_LED_BAR0) {
            if (magenta) {
                c = (rgb_t){255, 0, 255};
            } else if ((g80_pk[2u * i + 1u] & 0x0F) == 0) {
                c = (rgb_t){val / 2, val / 2, val / 2};
            }
        }
        rgb_matrix_set_color(i, c.r, c.g, c.b);
    }
}

#endif /* G80_FEATURES */
