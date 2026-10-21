// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Galaxy80 keyboard-specific effects: the stock modes without a close QMK equivalent, and the
// per-key colour table (stock "user lighting"; editor in g80_perkey.c).
// Effect ids are VIA dropdown values (via_json/galaxy80.json): only append.

#ifdef RGB_MATRIX_KEYREACTIVE_ENABLED
RGB_MATRIX_EFFECT(G80_SINGLE_OFF)
#endif
RGB_MATRIX_EFFECT(G80_COLOURFUL)
RGB_MATRIX_EFFECT(G80_FLOWING)
RGB_MATRIX_EFFECT(G80_SHUTTLE)
RGB_MATRIX_EFFECT(G80_PER_KEY)

#ifdef RGB_MATRIX_CUSTOM_EFFECT_IMPLS

#    include "g80_rgb.h"

#    define G80_FX_KEYS 88 /* LEDs 0..87 = keys, 88..92 = side bar */

#    ifdef RGB_MATRIX_KEYREACTIVE_ENABLED
/* stock SingleOff (@0xb2ea): everything lit, the pressed key goes dark and comes back */
static hsv_t G80_SINGLE_OFF_math(hsv_t hsv, uint16_t offset) {
    if (offset > 255) {
        offset = 255;
    }
    hsv.v = scale8((uint8_t)offset, hsv.v);
    return hsv;
}

static bool G80_SINGLE_OFF(effect_params_t *params) {
    return effect_runner_reactive(params, &G80_SINGLE_OFF_math);
}
#    endif

/* stock Colourful (@0xb318): every key cycles the hue wheel, phase (index mod 6) * 32 */
static bool G80_COLOURFUL(effect_params_t *params) {
    RGB_MATRIX_USE_LIMITS(led_min, led_max);
    uint8_t time = scale16by8(g_rgb_timer, qadd8(rgb_matrix_config.speed / 4, 1));
    for (uint8_t i = led_min; i < led_max; i++) {
        RGB_MATRIX_TEST_LED_FLAGS();
        hsv_t hsv = {(uint8_t)(time + (i % 6) * 32), rgb_matrix_config.hsv.s, rgb_matrix_config.hsv.v};
        rgb_t rgb = rgb_matrix_hsv_to_rgb(hsv);
        rgb_matrix_set_color(i, rgb.r, rgb.g, rgb.b);
    }
    return rgb_matrix_check_finished_leds(led_max);
}

/* stock Flowing (@0xbdba): one dot runs along the key list back and forth with a trail */
static bool G80_FLOWING(effect_params_t *params) {
    RGB_MATRIX_USE_LIMITS(led_min, led_max);
    const uint16_t span = 2 * (G80_FX_KEYS - 1);
    uint16_t       ph   = (uint16_t)((((uint32_t)g_rgb_timer * qadd8(rgb_matrix_config.speed / 16, 1)) >> 7) % span);
    int16_t        pos  = ph < (G80_FX_KEYS - 1) ? (int16_t)ph : (int16_t)(span - ph);
    for (uint8_t i = led_min; i < led_max; i++) {
        RGB_MATRIX_TEST_LED_FLAGS();
        uint8_t v = 0;
        if (i < G80_FX_KEYS) {
            int16_t d = (int16_t)i - pos;
            if (d < 0) {
                d = -d;
            }
            if (d < 6) {
                v = scale8(rgb_matrix_config.hsv.v, (uint8_t)(255 - d * 42));
            }
        }
        hsv_t hsv = {rgb_matrix_config.hsv.h, rgb_matrix_config.hsv.s, v};
        rgb_t rgb = rgb_matrix_hsv_to_rgb(hsv);
        rgb_matrix_set_color(i, rgb.r, rgb.g, rgb.b);
    }
    return rgb_matrix_check_finished_leds(led_max);
}

/* stock Shuttle (@0xaf3a): rows 0/2/4 lit at column x, rows 1/3/5 at column 16 - x */
static bool G80_SHUTTLE(effect_params_t *params) {
    RGB_MATRIX_USE_LIMITS(led_min, led_max);
    const uint8_t ph = (uint8_t)((((uint32_t)g_rgb_timer * qadd8(rgb_matrix_config.speed / 16, 1)) >> 8) % 32);
    const int8_t  x  = ph <= 16 ? (int8_t)ph : (int8_t)(32 - ph);
    for (uint8_t r = 0; r < 6; r++) {
        const int8_t target = (r & 1) ? (int8_t)(16 - x) : x;
        for (uint8_t c = 0; c < G80_LED_COLS; c++) {
            const uint8_t i = g80_led_map[r][c];
            if (i == G80_NO_LED || i < led_min || i >= led_max) {
                continue;
            }
            if (!HAS_ANY_FLAGS(g_led_config.flags[i], params->flags)) {
                continue;
            }
            int8_t d = (int8_t)c - target;
            if (d < 0) {
                d = (int8_t)-d;
            }
            uint8_t v = d == 0 ? rgb_matrix_config.hsv.v : (d == 1 ? rgb_matrix_config.hsv.v / 3 : 0);
            hsv_t   hsv = {rgb_matrix_config.hsv.h, rgb_matrix_config.hsv.s, v};
            rgb_t   rgb = rgb_matrix_hsv_to_rgb(hsv);
            rgb_matrix_set_color(i, rgb.r, rgb.g, rgb.b);
        }
    }
    for (uint8_t i = (led_min > G80_FX_KEYS ? led_min : G80_FX_KEYS); i < led_max; i++) {
        rgb_matrix_set_color(i, 0, 0, 0);
    }
    return rgb_matrix_check_finished_leds(led_max);
}

/* per-key colours (g80_pk: hue + saturation + relative value per LED incl. the side bar,
 * g80_perkey.c) scaled by the global brightness */
rgb_t g80_pk_rgb(uint8_t led, uint8_t val);

static bool G80_PER_KEY(effect_params_t *params) {
    RGB_MATRIX_USE_LIMITS(led_min, led_max);
    for (uint8_t i = led_min; i < led_max; i++) {
        RGB_MATRIX_TEST_LED_FLAGS();
        const rgb_t rgb = g80_pk_rgb(i, rgb_matrix_config.hsv.v);
        rgb_matrix_set_color(i, rgb.r, rgb.g, rgb.b);
    }
    return rgb_matrix_check_finished_leds(led_max);
}

#endif /* RGB_MATRIX_CUSTOM_EFFECT_IMPLS */
