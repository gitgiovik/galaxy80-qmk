// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Fn-layer actions of the stock firmware (v1.10) and the LED flashes that confirm them:
//   Win-lock, key backlight off, next palette colour, side-bar mode, lock screen / show desktop,
//   WASD swap, LAlt+Tab / LAlt+F4 blocking, SOCD, game mode, per-key editor, wireless keys,
//   brightness (down to off, up back on) / speed limit flashes and the factory reset (Fn+Backspace
//   held 3 s, or EE_CLR). QMK magic keycodes (swaps, NKRO) without the EEPROM write inside the key
//   event.

#include "g80_fn.h"

#ifdef G80_FEATURES

#    include "g80_bar.h"
#    include "g80_gaming.h"
#    include "g80_indicators.h"
#    include "g80_perkey.h"
#    include "g80_radio.h"
#    include "g80_settings.h"
#    include "rgb_matrix.h"
#    include "eeconfig.h"
#    include "keycode_config.h"
#    include "timer.h"

#    ifdef DYNAMIC_KEYMAP_ENABLE
#        define G80_KEYMAP_LAYERS DYNAMIC_KEYMAP_LAYER_COUNT
#    else
#        include "keymap_introspection.h"
#        define G80_KEYMAP_LAYERS keymap_layer_count()
#    endif

#    define G80_FRST_HOLD_MS 3000

/* ------------------------------------------------------------------------- */
/* LED flashes (stock FUN_d4fa / FUN_d518 style)                              */
/* ------------------------------------------------------------------------- */
static struct {
    uint8_t  target;
    uint8_t  led;
    uint8_t  r, g, b;
    uint8_t  left; /* half periods still to show */
    bool     on;
    uint16_t half_ms;
    uint32_t next;
} g80_fl;

void g80_flash(uint8_t target, uint8_t led, uint8_t r, uint8_t g, uint8_t b, uint8_t halves, uint16_t half_ms) {
    g80_fl.target  = target;
    g80_fl.led     = led;
    g80_fl.r       = r;
    g80_fl.g       = g;
    g80_fl.b       = b;
    g80_fl.left    = halves;
    g80_fl.on      = true;
    g80_fl.half_ms = half_ms;
    g80_fl.next    = timer_read32() + half_ms;
}

void g80_flash_task(void) {
    if (g80_fl.target == G80_FL_NONE) {
        return;
    }
    if (timer_expired32(timer_read32(), g80_fl.next)) {
        if (g80_fl.left <= 1) {
            g80_fl.target = G80_FL_NONE;
            return;
        }
        g80_fl.left--;
        g80_fl.on = !g80_fl.on;
        g80_fl.next += g80_fl.half_ms;
    }
}

static bool g80_flash_busy(void) {
    return g80_fl.target != G80_FL_NONE;
}

void g80_flash_render(uint8_t led_min, uint8_t led_max) {
    if (g80_fl.target == G80_FL_NONE) {
        return;
    }
    const uint8_t r = g80_fl.on ? g80_fl.r : 0;
    const uint8_t g = g80_fl.on ? g80_fl.g : 0;
    const uint8_t b = g80_fl.on ? g80_fl.b : 0;
    switch (g80_fl.target) {
        case G80_FL_KEYS:
            for (uint8_t i = led_min; i < led_max && i < G80_LED_KEYS; i++) {
                rgb_matrix_set_color(i, r, g, b);
            }
            break;
        case G80_FL_ARROWS:
            G80_SET(G80_LED_UP, r, g, b);
            G80_SET(G80_LED_LEFT, r, g, b);
            G80_SET(G80_LED_DOWN, r, g, b);
            G80_SET(G80_LED_RIGHT, r, g, b);
            break;
        default:
            G80_SET(g80_fl.led, r, g, b);
            break;
    }
}

static void g80_confirm(uint8_t led, bool on) {
    g80_flash(G80_FL_ONE, led, on ? 0 : 255, on ? 255 : 0, 0, 3, 240);
}

static void g80_limit_flash(void) {
    g80_flash(G80_FL_ARROWS, 0, 255, 255, 255, 7, 240); /* stock FUN_d518 */
}

/* ------------------------------------------------------------------------- */
/* Factory reset (stock D2: Fn+Backspace held 3 s)                            */
/* ------------------------------------------------------------------------- */
static bool     g80_frst_held;
bool            g80_frst_running;
static uint32_t g80_frst_t0;
static void     g80_frst_start(void);

void g80_frst_task(void) {
    if (g80_frst_running) {
        if (g80_save_inhibited()) {
            /* 5B 22 arrived during the white flashes (I-F5): refuse now, before any write */
            g80_frst_running = false;
            g80_flash(G80_FL_KEYS, 0, 255, 0, 0, 5, 240);
            return;
        }
        if (!g80_flash_busy()) {
            /* same as QK_CLEAR_EEPROM: invalidate, then everything is re-initialised on boot.
             * VIA image: wear-leveling area 0xF6C0..0xFFBF only. Default image: RAM only. */
            eeconfig_disable();
            soft_reset_keyboard(); /* -> mcu_reset(): LEDs off, WDT stop, SYSRESETREQ */
        }
        return;
    }
    if (g80_frst_held && timer_elapsed32(g80_frst_t0) >= G80_FRST_HOLD_MS) {
        g80_frst_held = false;
        g80_frst_start();
    }
}

/* Fn+Backspace after 3 s, and QK_CLEAR_EEPROM (EE_CLR) from VIA at once. */
static void g80_frst_start(void) {
    if (g80_save_inhibited()) {
        /* critical battery (I-F5): the reset rewrites the whole EEPROM band, refused */
        g80_flash(G80_FL_KEYS, 0, 255, 0, 0, 5, 240); /* 3 red flashes */
        return;
    }
    g80_frst_running = true;
    g80_pk_editing   = false; /* the flashes must be visible; nothing is saved any more */
    g80_pk_snap      = G80_SNAP_IDLE;
    g80_flash(G80_FL_KEYS, 0, 255, 255, 255, 13, 120); /* 7 white flashes */
}

/* ------------------------------------------------------------------------- */
/* Keys                                                                       */
/* ------------------------------------------------------------------------- */
static uint8_t g80_color_idx = 7; /* index in the stock palette (g80_bar.c) */

/* RGB keys. QMK's own handlers (process_rgb_matrix / process_underglow) use the EEPROM variants:
 * they mark rgb_matrix_config dirty and rgb_matrix_task writes it on its next pass, outside the
 * write gate (V-3) and the critical-battery inhibit (I-F5), possibly as a wear-leveling
 * consolidation while keys are down. Every RGB keycode (RM_* and the shared UG_* ones) is therefore
 * handled here with the _noeeprom variants, on press, and stored by the deferred, gated settings
 * save. Shift reverses the direction (as QMK). */
static void g80_rgb_changed(void) {
    g80_save_rgb = true;
    g80_config_save();
}

typedef void (*g80_rgb_fn)(void);
/* {forward, backward} per RM_* pair, in keycode order from RM_NEXT */
static const g80_rgb_fn g80_rgb_pair[6][2] = {
    {rgb_matrix_step_noeeprom, rgb_matrix_step_reverse_noeeprom},
    {rgb_matrix_increase_hue_noeeprom, rgb_matrix_decrease_hue_noeeprom},
    {rgb_matrix_increase_sat_noeeprom, rgb_matrix_decrease_sat_noeeprom},
    {rgb_matrix_increase_val_noeeprom, rgb_matrix_decrease_val_noeeprom},
    {rgb_matrix_increase_speed_noeeprom, rgb_matrix_decrease_speed_noeeprom},
    {rgb_matrix_flags_step_noeeprom, rgb_matrix_flags_step_reverse_noeeprom},
};

/* kc: QK_RGB_MATRIX_ON..QK_RGB_MATRIX_FLAG_PREVIOUS */
static void g80_rgb_key(uint16_t kc) {
    const uint8_t i = (uint8_t)(kc - QK_RGB_MATRIX_ON);
    if (i < 3) { /* on / off / toggle */
        if (i == 0) {
            rgb_matrix_enable_noeeprom();
        } else if (i == 1) {
            rgb_matrix_disable_noeeprom();
        } else {
            rgb_matrix_toggle_noeeprom();
        }
    } else {
        const uint8_t p    = (uint8_t)((i - 3) >> 1);
        const bool    back = (((i - 3) & 1) != 0) != ((get_mods() & MOD_MASK_SHIFT) != 0);
        const uint8_t v    = rgb_matrix_get_val();
        if (p == 3) {
            /* Brightness: 255 / 191 / 127 / 63 / 0 (= off, stock levels). Up from the off state
             * switches the LEDs back on, also when they were turned off with the all-LEDs-off key.
             * At the ends the 4 arrows flash white (stock FUN_d518). */
            if (!back && !rgb_matrix_is_enabled()) {
                rgb_matrix_enable_noeeprom();
                if (v == 0) {
                    rgb_matrix_increase_val_noeeprom();
                }
                g80_rgb_changed();
                return;
            }
            if (back ? (!rgb_matrix_is_enabled() || v == 0) : v >= RGB_MATRIX_MAXIMUM_BRIGHTNESS) {
                g80_limit_flash();
                return;
            }
        } else if (p == 4 && rgb_matrix_get_speed() == (back ? 0 : 255)) {
            g80_limit_flash(); /* speed at its end: flash, nothing changes */
        }
        g80_rgb_pair[p][back]();
    }
    g80_rgb_changed();
}

/* QMK magic keycodes (QK_MAGIC_*: Ctrl/Caps, Alt/GUI, Ctrl/GUI swaps, GUI off, Grave/Esc,
 * Backslash/Backspace, Esc/Caps, NKRO). QMK's process_magic() reloads keymap_config from EEPROM and
 * writes it back inside the key event (no write gate, no critical-battery inhibit). Here the same
 * RAM change is made (RAM is authoritative: no reload, which would also undo the Mac Alt/GUI swap of
 * the switch) and stored by the deferred, gated save. Per keycode from QK_MAGIC: the keymap_config
 * bits (low 12 bits) and the operation (top 2 bits): set, clear, or toggle (all bits take the
 * inverse of the lowest one, as QMK's Alt/GUI and Ctrl/GUI toggles). EE_HANDS_LEFT/RIGHT (split
 * keyboards only) do nothing. */
#    define G80_MG_SET 0x0000u
#    define G80_MG_CLR 0x4000u
#    define G80_MG_TOG 0x8000u
#    define G80_MG_NONE 0xC000u
static const uint16_t g80_magic[QK_MAGIC_TOGGLE_ESCAPE_CAPS_LOCK - QK_MAGIC + 1] = {
    G80_MG_SET | 0x001, G80_MG_CLR | 0x001, G80_MG_TOG | 0x001, /* CL_SWAP / CL_NORM / CL_TOGG */
    G80_MG_CLR | 0x002, G80_MG_SET | 0x002,                     /* CL_CAPS / CL_CTRL */
    G80_MG_SET | 0x004, G80_MG_CLR | 0x004,                     /* AG_LSWP / AG_LNRM */
    G80_MG_SET | 0x008, G80_MG_CLR | 0x008,                     /* AG_RSWP / AG_RNRM */
    G80_MG_CLR | 0x010, G80_MG_SET | 0x010, G80_MG_TOG | 0x010, /* GU_ON / GU_OFF / GU_TOGG */
    G80_MG_SET | 0x020, G80_MG_CLR | 0x020,                     /* GE_SWAP / GE_NORM */
    G80_MG_SET | 0x040, G80_MG_CLR | 0x040, G80_MG_TOG | 0x040, /* BS_SWAP / BS_NORM / BS_TOGG */
    G80_MG_SET | 0x080, G80_MG_CLR | 0x080, G80_MG_TOG | 0x080, /* NK_ON / NK_OFF / NK_TOGG */
    G80_MG_SET | 0x00C, G80_MG_CLR | 0x00C, G80_MG_TOG | 0x00C, /* AG_SWAP / AG_NORM / AG_TOGG */
    G80_MG_SET | 0x100, G80_MG_CLR | 0x100,                     /* CG_LSWP / CG_LNRM */
    G80_MG_SET | 0x200, G80_MG_CLR | 0x200,                     /* CG_RSWP / CG_RNRM */
    G80_MG_SET | 0x300, G80_MG_CLR | 0x300, G80_MG_TOG | 0x300, /* CG_SWAP / CG_NORM / CG_TOGG */
    G80_MG_NONE, G80_MG_NONE,                                   /* EH_LEFT / EH_RGHT */
    G80_MG_SET | 0x800, G80_MG_CLR | 0x800, G80_MG_TOG | 0x800, /* EC_SWAP / EC_NORM / EC_TOGG */
};

static void g80_magic_key(uint16_t keycode) {
    const uint16_t e    = g80_magic[keycode - QK_MAGIC]; /* IS_MAGIC_KEYCODE() = the table range */
    const uint16_t bits = e & 0x0FFFu;
    uint16_t       op   = e & G80_MG_NONE;
    if (op == G80_MG_NONE) {
        return;
    }
    if (op == G80_MG_TOG) {
        op = (keymap_config.raw & bits & -bits) ? G80_MG_CLR : G80_MG_SET;
    }
    clear_keyboard(); /* no stuck key across the change (NKRO: the report format changes) */
    keymap_config.raw = op == G80_MG_SET ? (uint16_t)(keymap_config.raw | bits) : (uint16_t)(keymap_config.raw & ~bits);
    clear_keyboard();
    g80_save_keymap = true;
    g80_config_save();
}

#    ifndef NO_ACTION_ONESHOT
/* OS_TOGG / OS_ON / OS_OFF: QMK's oneshot_set() writes keymap_config to the EEPROM inside the key
 * event. Same RAM effect as oneshot_set() (the bit, then the one-shot layer release), stored by the
 * deferred, gated save. */
static void g80_oneshot_key(uint16_t keycode) {
    const bool active = keycode == QK_ONE_SHOT_TOGGLE ? !keymap_config.oneshot_enable : keycode == QK_ONE_SHOT_ON;
    if (keymap_config.oneshot_enable != active) {
        keymap_config.oneshot_enable = active;
        clear_oneshot_layer_state(ONESHOT_OTHER_KEY_PRESSED);
        g80_save_keymap = true;
        g80_config_save();
    }
}
#    endif

/* returns false when the key has been fully handled */
bool g80_features_process_record(uint16_t keycode, keyrecord_t *record) {
    const bool pressed = record->event.pressed;

    /* editor: also catches what tap-hold keys resolve to later (LT/MT taps) */
    if (g80_pk_editing && (!g80_pk_passthrough(keycode) || (IS_QK_LAYER_TAP(keycode) && record->tap.count))) {
        return false;
    }
    if (!g80_wl_process_record(record)) {
        return false; /* Fn+Q/W/E/R in wireless */
    }
    switch (g80_gaming_key(keycode, pressed)) {
        case G80_KEY_SWALLOW:
            return false;
        case G80_KEY_QMK:
            return true;
        default:
            break;
    }

    if (keycode >= QK_UNDERGLOW_TOGGLE && keycode <= QK_UNDERGLOW_SPEED_DOWN) {
        keycode += QK_RGB_MATRIX_TOGGLE - QK_UNDERGLOW_TOGGLE; /* UG_* = the RM_* of the same order */
    }
    if (keycode >= QK_RGB_MATRIX_ON && keycode <= QK_RGB_MATRIX_FLAG_PREVIOUS) {
        if (pressed) {
            g80_rgb_key(keycode);
        }
        return false; /* never reaches QMK's EEPROM-writing handlers */
    }

    if (IS_MAGIC_KEYCODE(keycode)) {
        if (pressed) {
            g80_magic_key(keycode);
        }
        return false; /* never reaches QMK's EEPROM-writing handler */
    }
#    ifndef NO_ACTION_ONESHOT
    if (keycode >= QK_ONE_SHOT_ON && keycode <= QK_ONE_SHOT_TOGGLE) { /* OS_ON, OS_OFF, OS_TOGG */
        if (pressed) {
            g80_oneshot_key(keycode);
        }
        return false; /* never reaches QMK's EEPROM-writing process_oneshot() */
    }
#    endif
    if (IS_QK_PERSISTENT_DEF_LAYER(keycode)) {
        /* PDF(n): QMK's set_single_persistent_default_layer() writes the EEPROM in the key event. Same
         * RAM change on the release (as QMK), stored by the deferred, gated save. A layer the keymap
         * does not have (PDF(3..31) from VIA "Any") is ignored: it would leave a dead default layer. */
        if (!pressed && QK_PERSISTENT_DEF_LAYER_GET_LAYER(keycode) < G80_KEYMAP_LAYERS) {
            default_layer_set((layer_state_t)1 << QK_PERSISTENT_DEF_LAYER_GET_LAYER(keycode));
            g80_save_deflayer = true;
            g80_config_save();
        }
        return false;
    }

    switch (keycode) {
        case QK_CLEAR_EEPROM: /* EE_CLR from VIA: the factory reset (honours the critical-battery refusal) */
            if (pressed && !g80_frst_running) {
                g80_frst_start();
            }
            return false;
        case G80_WLCK:
            if (pressed) {
                g80_config.win_lock = !g80_config.win_lock;
                g80_apply_win_lock();
                g80_config_save();
            }
            return false;
        case G80_FRST:
            if (pressed) {
                g80_frst_held = true;
                g80_frst_t0   = timer_read32();
            } else {
                g80_frst_held = false;
            }
            return false;
        case G80_COLOR:
            if (pressed) {
                g80_color_idx = (uint8_t)((g80_color_idx + 1) & 7);
                rgb_matrix_sethsv_noeeprom(g80_palette_hs[g80_color_idx][0], g80_palette_hs[g80_color_idx][1], rgb_matrix_get_val());
                g80_rgb_changed();
            }
            return false;
        case G80_BLTG:
            if (pressed) {
                g80_config.keys_off = !g80_config.keys_off;
                g80_mask_keys       = g80_config.keys_off;
                g80_config_save();
            }
            return false;
        case G80_BAR:
            /* Also a Right Shift: Fn+RShift+Esc keeps working in any pressing order. */
            if (pressed) {
                register_code(KC_RSFT);
                g80_config.bar_mode = (uint8_t)((g80_config.bar_mode + 1) % G80_BAR_MODES);
                g80_config_save();
            } else {
                unregister_code(KC_RSFT);
            }
            return false;
        case G80_LOCK:
            if (pressed) {
                tap_code16(g80_is_mac() ? LCTL(LGUI(KC_Q)) : LGUI(KC_L));
            }
            return false;
        case G80_DESK:
            if (pressed) {
                tap_code16(g80_is_mac() ? KC_F11 : LGUI(KC_D));
            }
            return false;
        case G80_WASD:
            if (pressed) {
                g80_config.wasd_swap = !g80_config.wasd_swap;
                g80_config_save();
                g80_confirm(G80_LED_G, g80_config.wasd_swap);
            }
            return false;
        case G80_ATAB:
            if (pressed) {
                g80_config.block_alt_tab = !g80_config.block_alt_tab;
                g80_config_save();
                g80_confirm(G80_LED_TAB, g80_config.block_alt_tab);
            }
            return false;
        case G80_AF4:
            if (pressed) {
                g80_config.block_alt_f4 = !g80_config.block_alt_f4;
                g80_config_save();
                g80_confirm(G80_LED_CAPS, g80_config.block_alt_f4);
            }
            return false;
        case G80_SOCD:
            if (pressed) {
                g80_config.socd_on = !g80_config.socd_on;
                g80_config_save();
                g80_confirm(G80_LED_S, g80_config.socd_on);
            }
            return false;
        case G80_GAME:
            if (pressed) {
                g80_config.game_mode = !g80_config.game_mode;
                g80_apply_win_lock();
                g80_config_save();
                g80_confirm(G80_LED_PAUSE, g80_config.game_mode);
            }
            return false;
        case G80_EDIT:
            if (pressed) {
                if (g80_pk_editing) {
                    g80_pk_exit();
                } else if (!g80_frst_running) {
                    g80_pk_enter();
                }
            }
            return false;

        default:
            return true;
    }
}

#endif /* G80_FEATURES */
