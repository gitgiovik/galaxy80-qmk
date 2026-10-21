// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Indicator pass of the RGB matrix. Draw order, later steps win:
//   1. snapshot of the running effect (editor / VIA painter), before anything is drawn over it;
//   2. user hook, then either the editor view or, in this order [bar mode, "unsaved" blink (bar
//      LED 5), side-bar status overlay (layer on LED 5 wins over "unsaved"), slider reminder,
//      Caps Lock / Win-lock key LEDs, wireless channel key, Fn key (charging red > full green >
//      low battery)]; while Fn+Space is held the battery gauge replaces the whole bar section;
//   3. the active confirmation flash.

#include "g80_indicators.h"

#ifdef G80_FEATURES

#    include "g80_bar.h"
#    include "g80_battery.h"
#    include "g80_fn.h"
#    include "g80_gaming.h"
#    include "g80_perkey.h"
#    include "g80_radio.h"
#    include "g80_settings.h"
#    include "rgb_matrix.h"
#    include "timer.h"
#    ifdef CAPS_WORD_ENABLE
#        include "caps_word.h"
#    endif

/* Status overlay on top of every bar mode (VIA "Status Indicators", default on):
 *   bar LED 1 white = Caps Lock / orange = Caps Word, LED 2 red = game mode / cyan = SOCD on,
 *   LED 5 = active layer (1 red, 2 green, 3 blue) while a layer above 0 is on. */
static void g80_bar_overlay(uint8_t led_min, uint8_t led_max) {
    if (g80_config.bar_ind_off) {
        return;
    }
    if (host_keyboard_led_state().caps_lock) {
        G80_SET(G80_LED_BAR0, 255, 255, 255);
    }
#    ifdef CAPS_WORD_ENABLE
    else if (is_caps_word_on()) {
        G80_SET(G80_LED_BAR0, 255, 128, 0); /* Caps Word: orange */
    }
#    endif
    if (g80_config.game_mode) {
        G80_SET(G80_LED_BAR0 + 1, 255, 0, 0);
    } else if (g80_config.socd_on) {
        G80_SET(G80_LED_BAR0 + 1, 0, 255, 255);
    }
    const uint8_t l = get_highest_layer(layer_state) & 3;
    if (l) {
        G80_SET(G80_LED_BAR0 + 4, g80_layer_rgb[l][0], g80_layer_rgb[l][1], g80_layer_rgb[l][2]);
    }
}

/* Per-key paint changes not applied yet: bar LED 5 blinks magenta slowly. Lowest overlay priority
 * (drawn before the status overlay, so an active layer colour replaces it). */
/* Active channel key steady for 3 s after a switch / entering wireless / a wake-up. The radio
 * code draws the pairing and link-lost blinks and the first 3 s after a link change itself. */
static bool     g80_chan_on, g80_chan_wl;
static uint8_t  g80_chan_mode;
static uint32_t g80_chan_t0;

void g80_ind_channel_show(void) {
    g80_chan_on = true;
    g80_chan_t0 = timer_read32();
}

static void g80_ind_channel(uint8_t led_min, uint8_t led_max) {
    const bool    wl = g80_wl_active();
    const uint8_t m  = g80_wl_mode_get();
    if (wl && (!g80_chan_wl || m != g80_chan_mode)) {
        g80_ind_channel_show();
    }
    g80_chan_wl   = wl;
    g80_chan_mode = m;
    uint8_t li, lr, lg, lb;
    if (g80_wl_indicator(&li, &lr, &lg, &lb)) {
        G80_SET(li, lr, lg, lb);
    } else if (wl && g80_chan_on && timer_elapsed32(g80_chan_t0) < G80_CHANNEL_SHOW_MS) {
        G80_SET(35 + m, 0, m == 3 ? 255 : 0, m == 3 ? 0 : 255); /* Q W E R: 2.4G green, BT blue */
    }
}

bool rgb_matrix_indicators_advanced_kb(uint8_t led_min, uint8_t led_max) {
    /* key backlight off (stock 9C): the driver writes key LEDs black (g80_mask_keys) except
     * during this indicator pass, so the indicators below still light their keys */
    g80_pk_snap_step(led_min, led_max); /* before anything is drawn over the effect */
    /* (during a snapshot the effect is drawn unmasked so that its real colours are captured) */
    g80_mask_keys = g80_config.keys_off && g80_pk_snap == G80_SNAP_IDLE;
    g80_overlay   = true;
    if (!rgb_matrix_indicators_advanced_user(led_min, led_max)) {
        g80_overlay = false;
        return false;
    }
    if (g80_pk_editing && g80_pk_snap == G80_SNAP_IDLE) {
        g80_pk_render(led_min, led_max);
    } else {
        /* the Fn+Space gauge takes over the whole bar (and the number row): nothing of the bar section
         * is drawn then */
        uint8_t gp, gf;
        if (g80_batt_gauge(&gp, &gf)) {
            g80_bar_gauge(led_min, led_max, gp, gf);
        } else {
            g80_bar_render(led_min, led_max);
            g80_pk_unsaved_render(led_min, led_max); /* painted colours not applied: lowest bar overlay */
            g80_bar_overlay(led_min, led_max);
            /* slider ON (wireless position) while running from USB: bar LED 1 slow amber blink */
            if (g80_slider_wireless() && (timer_read32() & (2 * G80_BAR_BLINK_MS))) {
                G80_SET(G80_LED_BAR0, 255, 96, 0);
            }
        }
        if (host_keyboard_led_state().caps_lock) {
            G80_SET(G80_LED_CAPS, 255, 255, 255);
        }
        if (g80_eff_win_lock()) {
            G80_SET(G80_LED_LGUI, 255, 255, 255);
        }
        g80_ind_channel(led_min, led_max);
        /* Fn key: charge state (stock shows it in every mode; wired only with the VIA option on),
         * else the low-battery warning */
        const uint8_t cs = (g80_slider_wireless() || g80_opt_charge_wired()) ? g80_charge_state() : 0;
        const int8_t  lb = g80_batt_fn_state();
        if (cs == 1) {
            G80_SET(G80_LED_FN, 255, 0, 0);
        } else if (cs == 2) {
            G80_SET(G80_LED_FN, 0, 255, 0);
        } else if (lb >= 0) {
            G80_SET(G80_LED_FN, lb ? 255 : 0, 0, 0);
        }
    }

    g80_flash_render(led_min, led_max);
    g80_overlay = false;
    return true;
}

#endif /* G80_FEATURES */
