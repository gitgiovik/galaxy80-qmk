// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Galaxy80 default keymap: RGB (CT16B1 engine), every stock Fn function, NKRO.
// EEPROM is "transient" (keyboard rules.mk): no runtime flash writes at all, the lighting
// and toggles go back to their defaults at every power-on.
// Layers and the full Fn list: g80_layers.h. Fn + RShift + Esc = ROM ISP bootloader.
#include QMK_KEYBOARD_H
#include "galaxy80.h"
#include "g80_layers.h"

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    [0] = G80_LAYER_BASE,
    [1] = G80_LAYER_FN(G80_LLCK, _______),
};
