// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Battery state reported by the radio module (frames 5B 21 / 22 / 23 and 5C pp), derived UI state
// and the small power-related LED policies:
//   - low battery: Fn key blink (stock: continuous; option: five blinks per 30 s on key activity);
//   - critical battery: flash writes are inhibited (g80_save_inhibit) until the module reports
//     normal again or the charger is plugged in (never while a cable delivers power);
//   - Fn+Space gauge: asks the module for the level on press and every 2 s while held;
//   - brightness cap on battery (factor for the LED driver);
//   - LED-off timeout (wired and wireless): the LEDs go dark after N minutes without activity, the
//     key scan keeps running; the deep sleep (wireless) is g80_lpm.c.
// No flash is ever written from here.

#include "g80_battery.h"

#ifdef G80_FEATURES

#    include "g80_perkey.h"
#    include "g80_radio.h"
#    include "g80_settings.h"
#    include "matrix.h"
#    include "rgb_matrix.h"
#    include "timer.h"

#    define G80_BAT_CRITICAL 0x22
#    define G80_BAT_NORMAL 0x23

/* ------------------------------------------------------------------------- */
/* Options (g80_ucfg, VIA Power menu). Out of line (noinline): the emulator    */
/* suites patch these bodies to force an option.                              */
/* ------------------------------------------------------------------------- */
__attribute__((noinline)) uint8_t g80_opt_low_batt_mode(void) {
    return g80_cfg_low_batt_5x() ? 1u : 0u;
}
__attribute__((noinline)) bool g80_opt_charge_wired(void) {
    return g80_cfg_charge_led_wired();
}
__attribute__((noinline)) uint8_t g80_opt_batt_cap(void) {
    return (uint8_t)g80_ucfg.batt_cap;
}
__attribute__((noinline)) uint8_t g80_opt_led_off_min(void) {
    /* code 0 = 5 min, the default (all-zero settings word); out of range reads as code 0 */
    static const uint8_t min[G80_LED_OFF_OPTS] = {5, 0, 1, 10, 30};
    return min[g80_ucfg.led_off < G80_LED_OFF_OPTS ? g80_ucfg.led_off : 0];
}

uint8_t g80_bright_q8; /* 0 = no cap */

/* ------------------------------------------------------------------------- */
/* State                                                                      */
/* ------------------------------------------------------------------------- */
static struct {
    uint32_t t0;          /* start of the continuous blink */
    uint32_t req_t;       /* last Fn+Space request */
    uint32_t act_prev;    /* last_matrix_activity_elapsed() of the previous pass */
    uint32_t touch_t;     /* last activity that is not a key (g80_led_activity) */
    bool     low, crit;
    bool     ext;         /* charging or charged: a cable delivers power */
    bool     held, req_valid, answered, stale;
    bool     idle_dark;
    bool     prev_wl;
} g80_bat;
static uint32_t g80_burst_t = (uint32_t)-G80_LOWBAT_GAP_MS; /* start of the last five-blink burst */

void g80_led_activity(void) {
    g80_bat.touch_t = timer_read32();
}

/* Critical battery: no flash write may start (I-F5). The state is set by 5B 22 and ended by 5B 23
 * or a charger edge; the inhibit follows the LEVEL "critical and no cable": a 5B 22 that arrives
 * while a cable already delivers power (no edge left to end it) does not hold the saves back, and
 * they are held back again if the cable goes while the module still reports critical. */
static void g80_inhibit_update(void) {
    const bool inh = g80_bat.crit && !g80_bat.ext;
    if (inh != g80_save_inhibited()) {
        g80_save_inhibit(inh);
    }
}

static void g80_crit_set(bool crit) {
    g80_bat.crit = crit;
    g80_inhibit_update();
}

void g80_battery_rx(uint8_t frame, uint8_t value) {
    if (frame == 0x5C) {
        g80_bat.answered = g80_bat.req_valid;
    } else if (value == G80_BAT_NORMAL) {
        g80_bat.low = false;
        g80_crit_set(false);
    } else {
        if (!g80_bat.low) {
            g80_bat.low = true;
            g80_bat.t0  = timer_read32();
        }
        if (value == G80_BAT_CRITICAL) {
            g80_crit_set(true); /* critical is also low: the warning keeps blinking */
        }
    }
}

int8_t g80_batt_fn_state(void) {
    if (!g80_bat.low) {
        return -1;
    }
    uint32_t e = timer_read32();
    uint16_t half;
    if (g80_opt_low_batt_mode() == 0) {
        e -= g80_bat.t0;
        half = G80_LOWBAT_HALF_MS;
    } else {
        e -= g80_burst_t;
        if (e >= 2u * G80_LOWBAT_BLINKS * G80_LOWBAT_BLINK_MS) {
            return -1;
        }
        half = G80_LOWBAT_BLINK_MS;
    }
    return ((e / half) & 1) == 0;
}

bool g80_batt_gauge(uint8_t *pct, uint8_t *flags) {
    *pct   = g80_radio_stats.battery;
    *flags = (uint8_t)((g80_charge_state() == 1 ? G80_GAUGE_CHARGING : 0) | (g80_bat.stale ? G80_GAUGE_STALE : 0));
    return g80_bat.held;
}

void g80_battery_task(void) {
    const uint32_t now     = timer_read32();
    const uint32_t act     = last_matrix_activity_elapsed(); /* ms since the last key event */
    const bool     key_new = act < g80_bat.act_prev;
    g80_bat.act_prev           = act;

    /* charger plugged in: the battery is not the problem any more */
    const bool ext = g80_in_chg.stable || g80_in_full.stable;
    const bool wl  = g80_wl_active();
    /* wired <-> wireless: the LEDs show it. Charger edges do not count: the CHG/FULL pins can
     * toggle at charge termination or on a charger fault. */
    if (wl != g80_bat.prev_wl) {
        g80_led_activity();
    }
    if (ext && !g80_bat.ext) {
        g80_bat.low  = false;
        g80_bat.crit = false;
    }
    g80_bat.ext     = ext;
    g80_bat.prev_wl = wl;
    g80_inhibit_update();

    /* five-blink option: one burst per 30 s, started by a key */
    if (g80_bat.low && key_new && g80_opt_low_batt_mode() == 1 && now - g80_burst_t >= G80_LOWBAT_GAP_MS) {
        g80_burst_t = now;
    }

    /* Fn+Space (Space on the Fn layer is KC_NO): ask for the level on press, again every 2 s */
    const bool held = !g80_pk_editing && layer_state_is(1) && matrix_is_on(G80_GAUGE_ROW, G80_GAUGE_COL);
    g80_bat.held = held;
    if (!held) {
        g80_bat.req_valid = false;
    } else if ((!g80_bat.req_valid || now - g80_bat.req_t >= G80_GAUGE_REQ_MS) && g80_radio_request(G80_CMD_BATTERY)) {
        g80_bat.req_valid = true;
        g80_bat.answered  = false;
        g80_bat.req_t     = now;
    }
    g80_bat.stale = !wl && !g80_bat.answered && now - g80_bat.req_t >= G80_GAUGE_REPLY_MS;

    /* Brightness cap: at most 75 / 50 / 25 % while running from the battery. The driver multiplies
     * every colour by g80_bright_q8 / 256, so the user's own setting is never changed or saved.
     * Not while charging, and not while the editor / snapshot reads the real colours back. */
    uint8_t       q   = 0;
    const uint8_t cap = g80_opt_batt_cap();
    if (cap && wl && !g80_bat.ext && !g80_pk_editing && g80_pk_snap == G80_SNAP_IDLE) {
        const uint8_t lim = (uint8_t)(256 - 64 * cap); /* 192 / 128 / 64 */
        const uint8_t v   = rgb_matrix_get_val();
        if (v > lim) {
            q = (uint8_t)(lim * 256u / v);
        }
    }
    g80_bright_q8 = q;

    /* LED-off timeout (wired and wireless): dark after N minutes without activity, lit again by the
     * next key or other activity. Never while the on-board editor runs (it ends itself when idle). */
    const uint32_t touch = now - g80_bat.touch_t;
    const uint32_t idle  = act < touch ? act : touch;
    const uint8_t  mins  = g80_opt_led_off_min();
    const bool     dark  = mins && !g80_pk_editing && idle >= (uint32_t)mins * 60000u;
    if (dark != g80_bat.idle_dark || (dark && !rgb_matrix_get_suspend_state())) {
        g80_bat.idle_dark = dark; /* (a USB wake-up may have lit them again: dark is applied again) */
        rgb_matrix_set_suspend_state(dark);
    }
}

#endif /* G80_FEATURES */
