// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Galaxy80 keyboard-specific effect: the per-key colour table (stock "user lighting"; editor in
// g80_perkey.c). Effect ids are VIA dropdown values (via_json/galaxy80.json, g80_via.c).

RGB_MATRIX_EFFECT(G80_PER_KEY)

#ifdef RGB_MATRIX_CUSTOM_EFFECT_IMPLS

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
