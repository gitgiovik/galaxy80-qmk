// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Side bar (the 5 LEDs after the keys): the stock palette, the render of every bar mode and the
// gradient between the first and the last bar entry of the per-key table.

#include "g80_bar.h"

#ifdef G80_FEATURES

#    include "g80_battery.h"
#    include "g80_gaming.h"
#    include "g80_perkey.h"
#    include "rgb_matrix.h"
#    include "timer.h"
#    include <lib/lib8tion/lib8tion.h>

/* Palette (stock 0x6CFD) */
const uint8_t g80_palette_rgb[8][3] = {
    {0xFF, 0x00, 0x00}, {0xFF, 0x80, 0x00}, {0xFF, 0xFF, 0x00}, {0x00, 0xFF, 0x00},
    {0x00, 0xFF, 0xFF}, {0x00, 0x00, 0xFF}, {0xFF, 0x00, 0x80}, {0xFF, 0xFF, 0xFF},
};
/* same colours as QMK hue/sat (white = sat 0) */
const uint8_t g80_palette_hs[8][2] = {
    {0, 255}, {21, 255}, {43, 255}, {85, 255}, {128, 255}, {170, 255}, {234, 255}, {0, 0},
};

/* none, red (layer 1), green (layer 2), blue (layer 3) */
const uint8_t g80_layer_rgb[4][3] = {{0, 0, 0}, {255, 0, 0}, {0, 255, 0}, {0, 0, 255}};

/* The 5 bar entries of the table from (h0, s0) to (h1, s1), hue along the shorter way round the
 * wheel. Rendered by the bar modes "Gradient" / "Gradient Flow" and, as the bar part of the table,
 * by the "Per-key" effect. */
void g80_bar_gradient(uint8_t h0, uint8_t s0_4, uint8_t h1, uint8_t s1_4) {
    const int16_t dh = (int8_t)(uint8_t)(h1 - h0);
    for (uint8_t i = 0; i < G80_LED_BAR_COUNT; i++) {
        const uint8_t h  = (uint8_t)(h0 + dh * i / (G80_LED_BAR_COUNT - 1));
        const uint8_t s4 = (uint8_t)(s0_4 + ((int16_t)s1_4 - s0_4) * i / (G80_LED_BAR_COUNT - 1));
        g80_pk_put(G80_LED_BAR0 + i, h, s4, G80_PK_VMAX);
    }
}

/* a * b / 256 with 255 -> a (the library scale8() would turn 255 into 254) */
static uint8_t g80_sc(uint8_t a, uint8_t b) {
    return (uint8_t)(((uint16_t)a * (b + 1u)) >> 8);
}

void g80_bar_gauge(uint8_t led_min, uint8_t led_max, uint8_t pct, uint8_t flags) {
    const uint32_t t = timer_read32();
    uint8_t        k = 255; /* brightness of every lit LED */
    if (flags & G80_GAUGE_CHARGING) {
        const uint8_t ph = (uint8_t)(t >> 3); /* triangle, about 2 s per breath */
        k                = (uint8_t)(64 + g80_sc((uint8_t)((uint8_t)(ph << 1) ^ ((ph & 128) ? 255 : 0)), 191));
    }
    if (flags & G80_GAUGE_STALE) {
        k >>= 2;
    }
    const uint8_t n  = (uint8_t)((pct + 19) / 20); /* LEDs lit: 1..5 (0 and 0xFF: LED 1 blinks) */
    const uint8_t cr = pct < 50 ? 255 : 0;
    const uint8_t cg = pct < 20 ? 0 : pct < 50 ? 170 : 255;
    for (uint8_t i = 0; i < G80_LED_BAR_COUNT; i++) {
        const uint8_t led = G80_LED_BAR0 + i;
        if (led < led_min || led >= led_max) {
            continue;
        }
        uint8_t r = 0, g = 0, b = 0;
        if (i >= flags >> G80_GAUGE_VIS_SHIFT) {
            /* growing animation: not reached yet, dark */
        } else if (pct == 0 || pct == 0xFF) { /* empty: red blink (no underflow as in the stock gauge); unknown: slow white */
            if (i == 0 && (t & (pct ? 2 * G80_BAR_BLINK_MS : G80_BAR_BLINK_MS))) {
                r = k;
                g = b = pct ? k : 0;
            }
        } else if (i < n) {
            /* the top LED shows the remainder r = pct - 20 * (n - 1), 1..20, as r / 20 of full */
            const uint8_t v = g80_sc(i == n - 1 ? (uint8_t)((pct - 20 * (n - 1)) * 51 >> 2) : 255, k);
            r               = g80_sc(cr, v);
            g               = g80_sc(cg, v);
        }
        rgb_matrix_set_color(led, r, g, b);
    }
    /* number-row mirror: keys 1..0 = 10 % each, key n lit while pct > 10 (n - 1), in the bar
     * colour; it grows with the bar (two keys per bar LED) */
    for (uint8_t i = 0; i < 10; i++) {
        const uint8_t led = G80_LED_NUM1 + i;
        if (led < led_min || led >= led_max) {
            continue;
        }
        const uint8_t v = (pct <= 100 && i * 10 < pct && i < 2 * (flags >> G80_GAUGE_VIS_SHIFT)) ? k : 0;
        rgb_matrix_set_color(led, g80_sc(cr, v), g80_sc(cg, v), 0); /* (g80_sc(x, 0) = 0) */
    }
}

void g80_bar_render(uint8_t led_min, uint8_t led_max) {
    const uint8_t mode = g80_config.bar_mode;
    if (mode == G80_BAR_FOLLOW) {
        return;
    }
    const uint8_t val   = rgb_matrix_get_val();
    const uint8_t speed = rgb_matrix_get_speed();
    for (uint8_t i = 0; i < G80_LED_BAR_COUNT; i++) {
        const uint8_t led = G80_LED_BAR0 + i;
        if (led < led_min || led >= led_max) {
            continue;
        }
        rgb_t rgb = {0, 0, 0};
        switch (mode) {
            case G80_BAR_GRADIENT:
                rgb = g80_pk_rgb(led, val);
                break;
            case G80_BAR_GRAD_FLOW: {
                /* position along the ring of the 5 entries (last -> first closes it), 256 per step */
                const uint8_t  t = scale16by8(g_rgb_timer, qadd8(speed / 8, 1));
                const uint16_t p = (uint16_t)(((uint16_t)i * 256u + (uint16_t)t * G80_LED_BAR_COUNT) % (256u * G80_LED_BAR_COUNT));
                const uint8_t  a = (uint8_t)(p >> 8), f = (uint8_t)p;
                const rgb_t    c0 = g80_pk_rgb(G80_LED_BAR0 + a, val);
                const rgb_t    c1 = g80_pk_rgb(G80_LED_BAR0 + (a + 1) % G80_LED_BAR_COUNT, val);
                rgb.r = (uint8_t)(((uint16_t)c0.r * (255 - f) + (uint16_t)c1.r * f) / 255);
                rgb.g = (uint8_t)(((uint16_t)c0.g * (255 - f) + (uint16_t)c1.g * f) / 255);
                rgb.b = (uint8_t)(((uint16_t)c0.b * (255 - f) + (uint16_t)c1.b * f) / 255);
                break;
            }
            case G80_BAR_WAVE:
            case G80_BAR_SPECTRUM: {
                uint8_t t   = scale16by8(g_rgb_timer, qadd8(speed / 4, 1));
                hsv_t   hsv = {(uint8_t)(t + (mode == G80_BAR_WAVE ? i * G80_HUE_STEP : 0)), 255, val};
                rgb         = hsv_to_rgb(hsv);
                break;
            }
            case G80_BAR_BREATH: {
                uint32_t ph  = ((uint32_t)g_rgb_timer * qadd8(speed / 8, 1)) >> 4;
                uint8_t  p   = (uint8_t)ph;
                uint8_t  tri = p < 128 ? (uint8_t)(p * 2) : (uint8_t)((255 - p) * 2);
                uint8_t  c   = (uint8_t)((ph >> 8) % 7);
                uint8_t  v   = scale8(tri, val);
                rgb.r        = scale8(g80_palette_rgb[c][0], v);
                rgb.g        = scale8(g80_palette_rgb[c][1], v);
                rgb.b        = scale8(g80_palette_rgb[c][2], v);
                break;
            }
            case G80_BAR_RED ... G80_BAR_PINK: {
                const uint8_t c = mode - G80_BAR_RED;
                rgb.r           = scale8(g80_palette_rgb[c][0], val);
                rgb.g           = scale8(g80_palette_rgb[c][1], val);
                rgb.b           = scale8(g80_palette_rgb[c][2], val);
                break;
            }
            case G80_BAR_STATUS: {
                bool on = false;
                switch (i) {
                    case 0:
                        on = host_keyboard_led_state().caps_lock;
                        rgb = (rgb_t){on ? 255 : 0, on ? 255 : 0, on ? 255 : 0};
                        break;
                    case 1:
                        on = g80_eff_win_lock();
                        rgb = (rgb_t){on ? 255 : 0, on ? 255 : 0, on ? 255 : 0};
                        break;
                    case 2:
                        rgb = (rgb_t){0, keymap_config.nkro ? 255 : 0, 0};
                        break;
                    case 3:
                        rgb = g80_is_mac() ? (rgb_t){0, 0, 255} : (rgb_t){0, 255, 0};
                        break;
                    default: {
                        uint8_t l = get_highest_layer(layer_state) & 3;
                        rgb = (rgb_t){g80_layer_rgb[l][0], g80_layer_rgb[l][1], g80_layer_rgb[l][2]};
                        break;
                    }
                }
                break;
            }
            default: /* G80_BAR_OFF */
                break;
        }
        rgb_matrix_set_color(led, rgb.r, rgb.g, rgb.b);
    }
}

#endif /* G80_FEATURES */
