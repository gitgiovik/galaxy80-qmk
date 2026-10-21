// Copyright 2026 gitgiovik
// Copyright 2024 @ Keychron (https://www.keychron.com)
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Modified for Galaxy80: the Snap Click modes (Last / First / Second / Neutral) follow the state
// machine of Keychron's common/snap_click/snap_click.c (keychron_qmk commit 666862cb): both keys of a
// pair held -> the mode picks the key; one released -> the other comes back. Here it works on the
// output keys with per-key hold counters, so it composes with the WASD <-> arrows swap and with
// 6KRO / NKRO. See NOTICE.md.
//
// Gaming and lock functions:
//   - Snap Click / SOCD ("last input wins" and the other modes) and the WASD <-> arrows swap, on the
//     output keys so that the two compose;
//   - Win-lock (GUI + App), LAlt+Tab and LAlt+F4 blocking (left Alt only, like the stock firmware);
//   - game mode = Win-lock + both Alt blocks + SOCD (the effective switches in g80_gaming.h).

#include "g80_gaming.h"

#ifdef G80_FEATURES

#    include "g80_settings.h"
#    include "keycode_config.h"
#    include <string.h>
#    ifdef CAPS_WORD_ENABLE
#        include "caps_word.h"
#    endif

void g80_apply_win_lock(void) {
    keymap_config.no_gui = g80_eff_win_lock();
    if (g80_eff_win_lock()) {
        del_mods(MOD_MASK_GUI);
        del_weak_mods(MOD_MASK_GUI);
        unregister_code(KC_APP);
        send_keyboard_report();
    }
}

/* Index = source key = output key: 0 W, 1 A, 2 S, 3 D, 4 Up, 5 Left, 6 Down, 7 Right.
 * WASD swap: output = source ^ 4. Opposite of an output: ^ 2 (W/S, A/D, Up/Down, Left/Right).
 * Pair (bit in socd_pair_off): 0 W/S, 1 A/D, 2 Up/Down, 3 Left/Right; its keys are outputs
 * lo = ((pair >> 1) << 2) | (pair & 1) and hi = lo | 2.
 * The pair logic works on the OUTPUT keys, so it follows the swap; the reports are built
 * with add_key()/del_key(), so it is the same for 6KRO and NKRO, and a direction change is
 * one single report (old key out, new key in). Presses that need neither the swap nor a pair
 * rule go through the normal QMK path (Caps Word etc. unchanged). */
static const uint8_t g80_dir_kc[8] = {KC_W, KC_A, KC_S, KC_D, KC_UP, KC_LEFT, KC_DOWN, KC_RGHT};
static uint8_t       g80_dir_cnt[8]; /* physical keys currently holding each output */
static uint8_t       g80_dir_reg;    /* bit: output currently in the report */
static uint8_t       g80_dir_swp;    /* bit: source pressed while swapped */
static uint8_t       g80_dir_own;    /* bit: source handled here (not by the QMK path) */
static uint8_t       g80_dir_last;   /* bit per pair: the hi key was pressed last */

static uint8_t g80_snap_mode(void) {
    const uint8_t m = g80_cfg_snap_mode(); /* VIA "Snap Tap Mode", eeconfig_user */
    return m < G80_SC_MODES ? m : G80_SC_OFF;
}

bool g80_eff_socd(void) {
    return g80_config.socd_on || g80_config.game_mode || g80_snap_mode() != G80_SC_OFF;
}

static bool g80_socd_pair_on(uint8_t out) {
    const uint8_t pair = (uint8_t)(((out >> 2) << 1) | (out & 1));
    return g80_eff_socd() && !(g80_config.socd_pair_off & (1u << pair));
}

void g80_dir_reset(void) {
    memset(g80_dir_cnt, 0, sizeof(g80_dir_cnt));
    g80_dir_reg = g80_dir_swp = g80_dir_own = g80_dir_last = 0;
}

static void g80_dir_add(uint8_t out) {
#    ifdef CAPS_WORD_ENABLE
    if (is_caps_word_on() && out < 4) {
        add_weak_mods(MOD_BIT(KC_LSFT)); /* what Caps Word does for a letter */
    }
#    endif
    add_key(g80_dir_kc[out]);
    g80_dir_reg |= (uint8_t)(1u << out);
}

static void g80_dir_del(uint8_t out) {
    del_key(g80_dir_kc[out]);
    g80_dir_reg &= (uint8_t)~(1u << out);
}

/* Puts one output of a pair into the wanted state (no report is sent here). */
static void g80_dir_sync(uint8_t out, bool want) {
    const bool in_report = (g80_dir_reg & (1u << out)) != 0;
    if (want && !in_report) {
        g80_dir_add(out);
    } else if (!want && in_report) {
        g80_dir_del(out);
    }
}

/* Applies the Snap Click rule to a pair from the hold counters: one key held = that key; both held =
 * Last: the one pressed last, First / Second: the pair's first / second key, Neutral: neither.
 * "Off" (only reachable with the SOCD switch or game mode on) resolves like Last. */
static void g80_pair_resolve(uint8_t pair) {
    const uint8_t lo = (uint8_t)(((pair >> 1) << 2) | (pair & 1)), hi = (uint8_t)(lo | 2);
    bool          w0 = g80_dir_cnt[lo] != 0, w1 = g80_dir_cnt[hi] != 0;
    if (w0 && w1) {
        switch (g80_snap_mode()) {
            case G80_SC_FIRST:
                w1 = false;
                break;
            case G80_SC_SECOND:
                w0 = false;
                break;
            case G80_SC_NEUTRAL:
                w0 = w1 = false;
                break;
            default: /* Off, Last */
                if (g80_dir_last & (1u << pair)) {
                    w0 = false;
                } else {
                    w1 = false;
                }
                break;
        }
    }
    /* the key that goes out first: the report never holds both, not even between the two updates */
    if (w0) {
        g80_dir_sync(hi, w1);
        g80_dir_sync(lo, w0);
    } else {
        g80_dir_sync(lo, w0);
        g80_dir_sync(hi, w1);
    }
}

static inline uint8_t g80_out_pair(uint8_t out) {
    return (uint8_t)(((out >> 2) << 1) | (out & 1));
}

/* returns false when the event has been fully handled here */
static bool g80_dir_event(uint8_t src, bool pressed) {
    const uint8_t sbit = (uint8_t)(1u << src);
    if (pressed) {
        const bool    swap = g80_config.wasd_swap;
        const uint8_t out  = swap ? (uint8_t)(src ^ 4) : src;
        const uint8_t pair = g80_out_pair(out);
        if (g80_dir_cnt[out] < 255) {
            g80_dir_cnt[out]++;
        }
        if (out & 2) {
            g80_dir_last |= (uint8_t)(1u << pair);
        } else {
            g80_dir_last &= (uint8_t)~(1u << pair);
        }
        if (swap) {
            g80_dir_swp |= sbit;
        } else {
            g80_dir_swp &= (uint8_t)~sbit;
        }
        const bool socd = g80_socd_pair_on(out);
        if (!swap && !socd) {
            g80_dir_own &= (uint8_t)~sbit;
            g80_dir_reg |= (uint8_t)(1u << out); /* registered by the normal path */
            return true;
        }
        g80_dir_own |= sbit;
        if (socd) {
            g80_pair_resolve(pair);
        } else {
            g80_dir_add(out);
        }
        send_keyboard_report();
        return false;
    }
    const uint8_t out  = (g80_dir_swp & sbit) ? (uint8_t)(src ^ 4) : src;
    const bool    mine = (g80_dir_own & sbit) != 0;
    g80_dir_swp &= (uint8_t)~sbit;
    g80_dir_own &= (uint8_t)~sbit;
    if (g80_dir_cnt[out]) {
        g80_dir_cnt[out]--;
    }
    if (g80_dir_cnt[out] == 0) {
        if (!mine) {
            g80_dir_reg &= (uint8_t)~(1u << out); /* the normal path removes it */
        }
        if (g80_socd_pair_on(out)) {
            /* the other key of the pair, if held, comes back (or stays out, depending on the mode) */
            g80_pair_resolve(g80_out_pair(out));
        } else if (mine) {
            g80_dir_del(out);
        }
    }
    if (mine) {
        send_keyboard_report();
        return false;
    }
    return true; /* the normal path's report carries a re-added key */
}

uint8_t g80_gaming_key(uint16_t keycode, bool pressed) {
    /* Win-lock: App blocked like stock (GUI is blocked by keymap_config.no_gui) */
    if (keycode == KC_APP && pressed && g80_eff_win_lock()) {
        return G80_KEY_SWALLOW;
    }
    /* LAlt+Tab / LAlt+F4 blocking (left Alt only, stock 0x3196-0x31CE) */
    if (pressed && (get_mods() & MOD_BIT(KC_LALT))) {
        if ((keycode == KC_TAB && g80_eff_alt_tab()) || (keycode == KC_F4 && g80_eff_alt_f4())) {
            return G80_KEY_SWALLOW;
        }
    }
    /* WASD <-> arrows, SOCD */
    for (uint8_t i = 0; i < 8; i++) {
        if (keycode == g80_dir_kc[i]) {
            return g80_dir_event(i, pressed) ? G80_KEY_QMK : G80_KEY_SWALLOW;
        }
    }
    return G80_KEY_PASS;
}

#endif /* G80_FEATURES */
