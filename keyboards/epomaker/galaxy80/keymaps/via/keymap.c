// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Galaxy80 VIA keymap: same layers as "default" (g80_layers.h) plus one empty layer (3 layers,
// DYNAMIC_KEYMAP_LAYER_COUNT in config.h, to make room for the 2-byte per-key colours),
// all editable from VIA; VIA lighting menu (effects, brightness, speed, colour) and a
// "Galaxy80" menu (side bar, Win-lock, LAlt+Tab/F4 blocking, WASD swap, key backlight).
// EEPROM: wear-leveling in 0xF7C0..0xFFBF plus its aux sectors 0xF6C0..0xF7BF only (keymap rules.mk, keyboard config.h).
// Fn + RShift + Esc = ROM ISP bootloader. Key override: Shift+Backspace = Delete.
#include QMK_KEYBOARD_H
#include "galaxy80.h"
#include "g80_layers.h"

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    [0] = G80_LAYER_BASE,
    [1] = G80_LAYER_FN,
    [2] = G80_LAYER_EMPTY,
};
_Static_assert(sizeof(keymaps) / sizeof(keymaps[0]) == DYNAMIC_KEYMAP_LAYER_COUNT, "VIA keymap: one entry per dynamic layer");

/* Shift+Backspace sends Delete (Shift is not sent with it); Fn+Backspace stays the factory reset. */
const key_override_t  g80_shift_bspc_del = ko_make_basic(MOD_MASK_SHIFT, KC_BSPC, KC_DEL);
const key_override_t *key_overrides[]    = {&g80_shift_bspc_del};
