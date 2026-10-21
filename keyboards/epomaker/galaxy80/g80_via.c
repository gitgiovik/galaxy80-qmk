// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// VIA custom channel 0 ("Galaxy80" menu): value ids, the key painter (id 1), and the pre-hook that
// keeps every flash write out of the RAW HID handler (I-V2):
//   - custom values and the lighting channel change RAM only and arm the deferred save
//     (g80_settings.c);
//   - the commands that write EEPROM directly in QMK (keymap / macro / layout-option writes and
//     resets) are copied here and executed from housekeeping by g80_via_task(), in bounded steps.
//     Their answer is sent when the write is complete, so VIA (one command in flight, it waits for
//     each answer) never shows a change as saved before it is in flash. A step that will fill the
//     wear-leveling log (and so trigger a consolidation: ~2 KB erase + rewrite) waits for the write
//     gate, so a consolidation never runs while keys are being typed. A packet that arrives while a
//     deferred write is still open (a host that does not wait for answers) is answered
//     id_unhandled and ignored.

#include "g80_via.h"

#if defined(G80_FEATURES) && defined(VIA_ENABLE)

#    include "g80_bar.h"
#    include "g80_fn.h"
#    include "g80_gaming.h"
#    include "g80_lpm.h"
#    include "g80_perkey.h"
#    include "g80_settings.h"
#    include "g80_test_hooks.h"
#    include "rgb_matrix.h"
#    include <string.h>
#    include "via.h"
#    include "raw_hid.h"
#    include "dynamic_keymap.h"
#    include "keymap_introspection.h"
#    include "g80_version.h"
/* The reproducible VIA EEPROM magic needs the keyboard's version.h shadow ahead of the generated one
 * on the include path that quantum/via.c also uses (angle include: not this file's directory). */
#    include <version.h>
#    if !defined(G80_BUILD_DATE) || !defined(QMK_BUILDDATE)
#        error "version.h shadow not in effect: the VIA EEPROM magic would follow the build date"
#    endif

/* V-7: QMK's raw_hid_task drains the whole OUT queue in one call; the queue holds RAW_OUT_CAPACITY
 * packets (config.h), which bounds a burst. Keep it small. */
_Static_assert(RAW_OUT_CAPACITY <= 4, "V-7: at most 4 VIA packets per raw_hid_task call");
/* Firmware version item (read-only VIA dropdown): "d.d.d" -> one byte, 100 * major + 10 * minor
 * + patch. The json lists the matching option. */
_Static_assert(sizeof(G80_FW_VERSION) == 6, "G80_FW_VERSION must be d.d.d");
#    define G80_FW_VIA_CODE ((G80_FW_VERSION[0] - '0') * 100 + (G80_FW_VERSION[2] - '0') * 10 + (G80_FW_VERSION[4] - '0'))

/* the VIA key painter switches keys off instead of painting them (value id 21) */
static bool g80_pk_paint_off;

/* The VIA lighting menu (via_json/galaxy80.json) lists the effects by enum value: keep in sync. */
_Static_assert(RGB_MATRIX_SOLID_COLOR == 1, "via_json effect list out of sync: SOLID_COLOR");
_Static_assert(RGB_MATRIX_BREATHING == 2, "via_json effect list out of sync: BREATHING");
_Static_assert(RGB_MATRIX_CYCLE_ALL == 3, "via_json effect list out of sync: CYCLE_ALL");
_Static_assert(RGB_MATRIX_CYCLE_LEFT_RIGHT == 4, "via_json effect list out of sync: CYCLE_LEFT_RIGHT");
_Static_assert(RGB_MATRIX_CYCLE_UP_DOWN == 5, "via_json effect list out of sync: CYCLE_UP_DOWN");
_Static_assert(RGB_MATRIX_SOLID_REACTIVE_SIMPLE == 6, "via_json effect list out of sync: SOLID_REACTIVE_SIMPLE");
_Static_assert(RGB_MATRIX_CUSTOM_G80_SINGLE_OFF == 7, "via_json effect list out of sync: G80_SINGLE_OFF");
_Static_assert(RGB_MATRIX_CUSTOM_G80_COLOURFUL == 8, "via_json effect list out of sync: G80_COLOURFUL");
_Static_assert(RGB_MATRIX_CUSTOM_G80_FLOWING == 9, "via_json effect list out of sync: G80_FLOWING");
_Static_assert(RGB_MATRIX_CUSTOM_G80_SHUTTLE == 10, "via_json effect list out of sync: G80_SHUTTLE");
_Static_assert(RGB_MATRIX_CUSTOM_G80_PER_KEY == 11, "via_json effect list out of sync: G80_PER_KEY (also the showIf of Speed/Color)");
_Static_assert(RGB_MATRIX_EFFECT_MAX == 12, "via_json effect list out of sync: count");

/* value ids of via_json/galaxy80.json, channel 0 (id_custom_channel). Id 1 is the VIA key-painter
 * protocol (fixed by the VIA app: channel 0, id 1, [index, count, hue, sat]...). The json and the
 * image ship together. */
enum g80_via_value {
    G80_VIA_PK_PAINT = 1, /* 1  VIA key painter (color-palette control + "li" on every key) */
    G80_VIA_WIN_LOCK,     /* 2  toggles */
    G80_VIA_ALT_TAB,      /* 3 */
    G80_VIA_ALT_F4,       /* 4 */
    G80_VIA_WASD,         /* 5 */
    G80_VIA_KEYS_OFF,     /* 6 */
    G80_VIA_SOCD,         /* 7  toggle */
    G80_VIA_SOCD_AD,      /* 8  toggle, 1 = pair enabled */
    G80_VIA_SOCD_WS,      /* 9 */
    G80_VIA_SOCD_LR,      /* 10 */
    G80_VIA_SOCD_UD,      /* 11 */
    G80_VIA_DEBOUNCE,     /* 12 range 1..10 ms */
    G80_VIA_DEB_EAGER,    /* 13 toggle */
    G80_VIA_GAME,         /* 14 toggle */
    G80_VIA_BAR_IND,      /* 15 toggle, 1 = overlay on */
    G80_VIA_PK_EDIT,      /* 16 toggle: editor on/off */
    G80_VIA_PK_BRUSH,     /* 17 color: editor brush (also recolours the last edited key) */
    G80_VIA_PK_RESET,     /* 18 button: per-key colours back to the defaults */
    G80_VIA_BAR_MODE,     /* 19 dropdown: side-bar mode */
    G80_VIA_PK_PALETTE,   /* 20 color-palette control (VIA keeps the colour itself) */
    G80_VIA_PK_PAINT_OFF, /* 21 toggle: the VIA painter switches keys off */
    G80_VIA_GRAD_START,   /* 22 color: side-bar gradient start (bar entry 1) */
    G80_VIA_GRAD_END,     /* 23 color: side-bar gradient end (bar entry 5) */
    G80_VIA_PK_APPLY,     /* 24 button: store the painted colours (Apply) */
    G80_VIA_PK_DISCARD,   /* 25 button: back to the stored colours (Discard) */
    G80_VIA_SLEEP,        /* 26 dropdown: wireless sleep after never / 1 / 5 / 30 min */
    G80_VIA_WL_NKRO,      /* 27 toggle: hybrid NKRO over wireless */
    G80_VIA_SNAP_MODE,    /* 28 dropdown: snap click mode */
    G80_VIA_LOW_BATT,     /* 29 dropdown: low-battery warning style */
    G80_VIA_BATT_CAP,     /* 30 dropdown: brightness cap on battery */
    G80_VIA_CHG_WIRED,    /* 31 toggle: charge LED also while wired */
    G80_VIA_IDLE_LED,     /* 32 dropdown: wired idle LED-off timeout */
    G80_VIA_FW_VERSION,   /* 33 dropdown, read-only: firmware version */
    G80_VIA_PK_UNSAVED,   /* 34 dropdown, read-only: painted colours not applied yet */
};

/* SOCD pair bit (socd_pair_off) of the VIA ids 8..11 */
static const uint8_t g80_via_pair_bit[4] = {1u << 1, 1u << 0, 1u << 3, 1u << 2};

/* VIA ids 26..32 -> fields of g80_ucfg (g80_settings.h): first bit, number of values, stored
 * inverted (options whose default is "on" are stored as "off" bits, so zero = defaults). A value
 * out of range selects 0 (off). */
static const uint8_t g80_ucfg_map[][3] = {
    {0, 4, 0},                 /* 26 sleep_opt */
    {4, 2, 1},                 /* 27 nkro_off */
    {5, G80_SNAP_MODES, 0},    /* 28 snap_mode */
    {3, 2, 0},                 /* 29 low_batt_5x */
    {8, 4, 0},                 /* 30 batt_cap */
    {2, 2, 1},                 /* 31 chg_wired_off */
    {10, G80_IDLE_LED_OPTS, 0} /* 32 idle_led */
};

static void g80_via_ucfg(uint8_t id, uint8_t *v, bool set) {
    const uint8_t *f    = g80_ucfg_map[id - G80_VIA_SLEEP];
    const uint32_t mask = f[1] <= 2 ? 1u : (f[1] <= 4 ? 3u : 7u);
    if (set) {
        uint8_t x = *v < f[1] ? *v : 0;
        if (f[2]) {
            x = x == 0;
        }
        g80_ucfg.raw = (g80_ucfg.raw & ~(mask << f[0])) | ((uint32_t)x << f[0]);
        if (id == G80_VIA_SLEEP) {
            g80_lpm_set_option(x); /* the idle countdown follows the new option at once */
        }
    } else {
        const uint8_t x = (uint8_t)((g80_ucfg.raw >> f[0]) & mask);
        *v              = f[2] ? x == 0 : x;
    }
}

/* VIA key painter: data = [cmd, channel, 1, first LED, count, hue, sat, hue, sat...] */
static void g80_via_paint(uint8_t *data, uint8_t length, bool set) {
    const uint8_t first = data[3];
    const uint8_t count = data[4];
    for (uint8_t k = 0; k < count && 6u + 2u * k < length; k++) {
        const uint8_t led = (uint8_t)(first + k);
        uint8_t      *hs  = &data[5u + 2u * k];
        if (set) {
            if (led >= G80_LED_COUNT) {
                break;
            }
            if (!g80_pk_editing) {
                g80_pk_take_over(); /* first paint: the other keys keep what they show now */
            }
            g80_pk_mark(led);
            g80_pk_put(led, hs[0], g80_s8_to_s4(hs[1]), g80_pk_paint_off ? 0 : G80_PK_VMAX);
        } else if (led < G80_LED_COUNT) {
            hs[0] = g80_pk[2u * led];
            hs[1] = g80_s4_to_s8(g80_pk[2u * led + 1u] >> 4);
        } else {
            hs[0] = hs[1] = 0;
        }
    }
}

void via_custom_value_command_kb(uint8_t *data, uint8_t length) {
    uint8_t *command_id = &(data[0]);
    uint8_t *channel_id = &(data[1]);
    uint8_t *value_id   = &(data[2]);
    uint8_t *value      = &(data[3]);
    if (*channel_id != id_custom_channel || length < 7) {
        *command_id = id_unhandled;
        return;
    }
    switch (*command_id) {
        case id_custom_set_value:
            switch (*value_id) {
                case G80_VIA_PK_PAINT:
                    g80_via_paint(data, length, true);
                    break;
                case G80_VIA_BAR_MODE:
                    g80_config.bar_mode = value[0] < G80_BAR_MODES ? value[0] : G80_BAR_FOLLOW;
                    break;
                case G80_VIA_WIN_LOCK:
                    g80_config.win_lock = value[0] != 0;
                    g80_apply_win_lock();
                    break;
                case G80_VIA_ALT_TAB:
                    g80_config.block_alt_tab = value[0] != 0;
                    break;
                case G80_VIA_ALT_F4:
                    g80_config.block_alt_f4 = value[0] != 0;
                    break;
                case G80_VIA_WASD:
                    g80_config.wasd_swap = value[0] != 0;
                    break;
                case G80_VIA_KEYS_OFF:
                    g80_config.keys_off = value[0] != 0;
                    g80_mask_keys       = g80_config.keys_off;
                    break;
                case G80_VIA_SOCD:
                    g80_config.socd_on = value[0] != 0;
                    break;
                case G80_VIA_SOCD_AD ... G80_VIA_SOCD_UD: {
                    const uint8_t bit = g80_via_pair_bit[*value_id - G80_VIA_SOCD_AD];
                    g80_config.socd_pair_off = value[0] ? (g80_config.socd_pair_off & ~bit) : (g80_config.socd_pair_off | bit);
                    break;
                }
                case G80_VIA_DEBOUNCE:
                    g80_config.debounce = (value[0] >= 1 && value[0] <= G80_DEBOUNCE_MAX) ? value[0] : 0;
                    g80_apply_debounce();
                    break;
                case G80_VIA_DEB_EAGER:
                    g80_config.deb_eager = value[0] != 0;
                    g80_apply_debounce();
                    break;
                case G80_VIA_GAME:
                    g80_config.game_mode = value[0] != 0;
                    g80_apply_win_lock();
                    break;
                case G80_VIA_BAR_IND:
                    g80_config.bar_ind_off = value[0] == 0;
                    break;
                case G80_VIA_PK_EDIT:
                    if (value[0] && !g80_pk_editing && !g80_frst_running) {
                        g80_pk_enter();
                    } else if (!value[0] && g80_pk_editing) {
                        g80_pk_exit();
                    }
                    break;
                case G80_VIA_PK_BRUSH:
                    g80_pk_brush_h  = value[0];
                    g80_pk_brush_s4 = g80_s8_to_s4(value[1]);
                    g80_pk_brush_on = true;
                    if (g80_pk_editing && g80_pk_cur < G80_LED_COUNT) {
                        g80_pk_mark(g80_pk_cur);
                        g80_pk_put(g80_pk_cur, g80_pk_brush_h, g80_pk_brush_s4, G80_PK_VMAX);
                    }
                    break;
                case G80_VIA_PK_RESET:
                    g80_pk_defaults(); /* unsaved until Apply */
                    g80_pk_dirty = true;
                    break;
                case G80_VIA_PK_APPLY:
                    g80_pk_apply();
                    break;
                case G80_VIA_PK_DISCARD:
                    g80_pk_discard();
                    break;
                case G80_VIA_SLEEP ... G80_VIA_IDLE_LED:
                    g80_via_ucfg(*value_id, &value[0], true);
                    break;
                case G80_VIA_PK_PALETTE:
                case G80_VIA_FW_VERSION: /* read-only */
                case G80_VIA_PK_UNSAVED:
                    break;
                case G80_VIA_PK_PAINT_OFF:
                    g80_pk_paint_off = value[0] != 0;
                    break;
                case G80_VIA_GRAD_START:
                case G80_VIA_GRAD_END: {
                    const uint8_t *e  = &g80_pk[2u * (*value_id == G80_VIA_GRAD_START ? G80_LED_BAR0 : G80_LED_BAR0 + G80_LED_BAR_COUNT - 1)];
                    uint8_t        h0 = e[0], s0 = (uint8_t)(e[1] >> 4);
                    const uint8_t *o  = &g80_pk[2u * (*value_id == G80_VIA_GRAD_START ? G80_LED_BAR0 + G80_LED_BAR_COUNT - 1 : G80_LED_BAR0)];
                    uint8_t        h1 = o[0], s1 = (uint8_t)(o[1] >> 4);
                    h0 = value[0];
                    s0 = g80_s8_to_s4(value[1]);
                    if (*value_id == G80_VIA_GRAD_START) {
                        g80_bar_gradient(h0, s0, h1, s1);
                    } else {
                        g80_bar_gradient(h1, s1, h0, s0);
                    }
                    break;
                }
                default:
                    *command_id = id_unhandled;
                    break;
            }
            if (*command_id != id_unhandled) {
                g80_config_save(); /* deferred, also when VIA sends no "save" (buttons) */
            }
            break;
        case id_custom_get_value:
            switch (*value_id) {
                case G80_VIA_PK_PAINT:
                    g80_via_paint(data, length, false);
                    break;
                case G80_VIA_BAR_MODE:
                    value[0] = g80_config.bar_mode;
                    break;
                case G80_VIA_WIN_LOCK:
                    value[0] = g80_config.win_lock;
                    break;
                case G80_VIA_ALT_TAB:
                    value[0] = g80_config.block_alt_tab;
                    break;
                case G80_VIA_ALT_F4:
                    value[0] = g80_config.block_alt_f4;
                    break;
                case G80_VIA_WASD:
                    value[0] = g80_config.wasd_swap;
                    break;
                case G80_VIA_KEYS_OFF:
                    value[0] = g80_config.keys_off;
                    break;
                case G80_VIA_SOCD:
                    value[0] = g80_config.socd_on;
                    break;
                case G80_VIA_SOCD_AD ... G80_VIA_SOCD_UD:
                    value[0] = (g80_config.socd_pair_off & g80_via_pair_bit[*value_id - G80_VIA_SOCD_AD]) == 0;
                    break;
                case G80_VIA_DEBOUNCE:
                    value[0] = g80_debounce_ms;
                    break;
                case G80_VIA_DEB_EAGER:
                    value[0] = g80_config.deb_eager;
                    break;
                case G80_VIA_GAME:
                    value[0] = g80_config.game_mode;
                    break;
                case G80_VIA_BAR_IND:
                    value[0] = !g80_config.bar_ind_off;
                    break;
                case G80_VIA_PK_EDIT:
                    value[0] = g80_pk_editing;
                    break;
                case G80_VIA_PK_BRUSH:
                    value[0] = g80_pk_brush_h;
                    value[1] = g80_s4_to_s8(g80_pk_brush_s4);
                    break;
                case G80_VIA_PK_RESET:
                case G80_VIA_PK_PALETTE:
                case G80_VIA_PK_APPLY:
                case G80_VIA_PK_DISCARD:
                    value[0] = 0;
                    value[1] = 0;
                    break;
                case G80_VIA_SLEEP ... G80_VIA_IDLE_LED:
                    g80_via_ucfg(*value_id, &value[0], false);
                    break;
                case G80_VIA_FW_VERSION:
                    value[0] = G80_FW_VIA_CODE;
                    break;
                case G80_VIA_PK_UNSAVED:
                    value[0] = g80_pk_dirty;
                    break;
                case G80_VIA_PK_PAINT_OFF:
                    value[0] = g80_pk_paint_off;
                    break;
                case G80_VIA_GRAD_START:
                case G80_VIA_GRAD_END: {
                    const uint8_t *e = &g80_pk[2u * (*value_id == G80_VIA_GRAD_START ? G80_LED_BAR0 : G80_LED_BAR0 + G80_LED_BAR_COUNT - 1)];
                    value[0]         = e[0];
                    value[1]         = g80_s4_to_s8(e[1] >> 4);
                    break;
                }
                default:
                    *command_id = id_unhandled;
                    break;
            }
            break;
        case id_custom_save:
            /* Always deferred. VIA sends set + save for every slider step, colour-picker mouse move
             * and painted key; writing flash for each would stall the CPU, the LED ISR and the key
             * scan for every erase. One write G80_SAVE_DELAY_MS after the last change instead. */
            g80_config_save();
            break;
        default:
            *command_id = id_unhandled;
            break;
    }
}

/* ------------------------------------------------------------------------- */
/* Deferred VIA EEPROM writes                                                 */
/* ------------------------------------------------------------------------- */
#    define G80_VIA_PKT 32 /* VIA packet = RAW HID report size (RAW_EPSIZE) */
#    define G80_VQ_STEP_BYTES 2u /* EEPROM bytes written per step (one keycode) */
#    define G80_VQ_KEYS (DYNAMIC_KEYMAP_LAYER_COUNT * MATRIX_ROWS * MATRIX_COLS)
/* Log bytes one step can append: QMK writes these bytes one by one (eeprom_update_byte: one 4 B
 * log entry per changed byte), layout options are 4 bytes. */
#    define G80_VQ_STEP_LOG (4u * BACKING_STORE_WRITE_SIZE)

static uint8_t  g80_vq[G80_VIA_PKT]; /* the pending command, also its answer */
static bool     g80_vq_busy;
static uint16_t g80_vq_pos; /* bytes / keys done */

bool g80_via_write_pending(void) {
    return g80_vq_busy;
}

/* One bounded step (at most G80_VQ_STEP_BYTES bytes, so the key scan in the main loop keeps
 * running between the flash programs); true when the command is complete. Same effect as
 * quantum/via.c. */
static bool g80_vq_step(void) {
    const uint8_t *d   = g80_vq;
    const uint16_t off = (uint16_t)((d[1] << 8) | d[2]);
    switch (d[0]) {
        case id_dynamic_keymap_set_keycode:
            dynamic_keymap_set_keycode(d[1], d[2], d[3], (uint16_t)((d[4] << 8) | d[5]));
            return true;
        case id_dynamic_keymap_set_buffer:
        case id_dynamic_keymap_macro_set_buffer:
        case id_dynamic_keymap_macro_reset: { /* reset = zeros over the whole macro buffer */
            static uint8_t zero[G80_VQ_STEP_BYTES];
            const bool     rst  = d[0] == id_dynamic_keymap_macro_reset;
            const uint16_t size = rst ? dynamic_keymap_macro_get_buffer_size() : (d[3] <= G80_VIA_PKT - 4 ? d[3] : G80_VIA_PKT - 4);
            const uint16_t n    = size - g80_vq_pos < G80_VQ_STEP_BYTES ? size - g80_vq_pos : G80_VQ_STEP_BYTES;
            uint8_t       *src  = rst ? zero : &g80_vq[4 + g80_vq_pos];
            if (d[0] == id_dynamic_keymap_set_buffer) {
                dynamic_keymap_set_buffer(off + g80_vq_pos, n, src);
            } else {
                dynamic_keymap_macro_set_buffer((rst ? 0 : off) + g80_vq_pos, n, src);
            }
            g80_vq_pos += n;
            return g80_vq_pos >= size;
        }
        case id_dynamic_keymap_reset: {
            const uint8_t layer = (uint8_t)(g80_vq_pos / (MATRIX_ROWS * MATRIX_COLS));
            const uint8_t row   = (uint8_t)(g80_vq_pos / MATRIX_COLS % MATRIX_ROWS);
            const uint8_t col   = (uint8_t)(g80_vq_pos % MATRIX_COLS);
            dynamic_keymap_set_keycode(layer, row, col, keycode_at_keymap_location_raw(layer, row, col));
            return ++g80_vq_pos >= G80_VQ_KEYS;
        }
        default: /* id_set_keyboard_value / id_layout_options */
            via_set_layout_options(((uint32_t)d[2] << 24) | ((uint32_t)d[3] << 16) | ((uint32_t)d[4] << 8) | d[5]);
            return true;
    }
}

void g80_via_task(void) {
    if (!g80_vq_busy) {
        return;
    }
    /* a step that fills the log consolidates: only behind the write gate (no typing) */
    if (G80_VQ_STEP_LOG >= g80_wl_log_free() && !g80_save_gate_open()) {
        return;
    }
    if (g80_vq_step()) {
        g80_vq_busy = false;
        raw_hid_send(g80_vq, G80_VIA_PKT); /* the same buffer back, as quantum/via.c answers */
    }
}

static bool g80_vq_defer(const uint8_t *data, uint8_t length) {
    switch (data[0]) {
        case id_set_keyboard_value:
            if (data[1] != id_layout_options) {
                return false; /* device indication: RAM only */
            }
            break;
        case id_dynamic_keymap_set_keycode:
        case id_dynamic_keymap_reset:
        case id_dynamic_keymap_macro_set_buffer:
        case id_dynamic_keymap_macro_reset:
        case id_dynamic_keymap_set_buffer:
            break;
        default:
            return false;
    }
    memcpy(g80_vq, data, length < G80_VIA_PKT ? length : G80_VIA_PKT);
    g80_vq_pos  = 0;
    g80_vq_busy = true;
    return true;
}

/* Called first for every VIA packet (via_command_kb, galaxy80.c). The lighting channel's "save"
 * (QMK writes rgb_matrix_config at once) becomes a deferred save; EEPROM writes are deferred
 * (above). Returns true when the packet has been taken here. */
bool g80_via_command(uint8_t *data, uint8_t length) {
    if (length < G80_VIA_PKT) {
        return false;
    }
    if (g80_vq_busy) {
        /* the previous write has not been answered yet: busy */
        data[0] = id_unhandled;
        raw_hid_send(data, length);
        return true;
    }
    if (data[0] == id_custom_save && data[1] == id_qmk_rgb_matrix_channel) {
        g80_save_rgb = true;
        g80_config_save();
        raw_hid_send(data, length);
        return true;
    }
    return g80_vq_defer(data, length);
}

#endif /* G80_FEATURES && VIA_ENABLE */
