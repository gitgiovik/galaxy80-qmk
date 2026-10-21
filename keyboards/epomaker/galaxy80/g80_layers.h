// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Galaxy80 layers shared by the default, via and debug keymaps.
//   Layer 0 = stock default HID table (@0x6B61 in the stock firmware v1.10), with the three extra
//             ISO/JIS positions the stock firmware also maps (NUBS r4c1, NUHS r3c12, Ro r4c12).
//   Layer 1 = stock Fn table:
//     Fn+Esc (+RShift held)  ROM ISP bootloader          Fn+F1..F12   stock media keys
//     Fn+LWin                Win-lock (GUI + App)        Fn+Bksp 3 s  factory reset
//     Fn+Enter               next effect (+Shift: prev)  Fn+\         next stock colour
//     Fn+Up / Fn+Down        brightness                  Fn+Right/Left effect speed
//     Fn+T                   key backlight off/on        Fn+RShift    next side-bar mode
//     Fn+Ins                 all LEDs off/on (RM_TOGG)   Fn+N         NKRO on/off
//     Fn+L / Fn+D            lock screen / show desktop  Fn+G         WASD <-> arrows
//     Fn+Tab / Fn+Caps       block LAlt+Tab / LAlt+F4    Fn+Q/W/E/R/Space  reserved (wireless)
//   Additions of this firmware:
//     Fn+S                   SOCD / snap tap on/off      Fn+K         per-key colour editor
//     Fn+Pause               game mode on/off            Fn+C         Caps Word
//     Fn+F13                 Layer Lock: the Fn layer stays on (Fn-lock) until Fn or F13 is pressed
// `extra0`/`extra1` = Fn+F13 / Fn+PrtSc, keymap slots (Layer Lock / transparent in all keymaps).
#pragma once

/* Caps Word only where it is built (RGB images, post_rules.mk) */
#ifdef CAPS_WORD_ENABLE
#    define G80_CAPW CW_TOGG
#else
#    define G80_CAPW _______
#endif
/* Layer Lock likewise (RGB images, post_rules.mk) */
#ifdef LAYER_LOCK_ENABLE
#    define G80_LLCK QK_LLCK
#else
#    define G80_LLCK _______
#endif

// clang-format off
#define G80_LAYER_BASE LAYOUT_all( \
    KC_ESC,  KC_F1,   KC_F2,   KC_F3,   KC_F4,   KC_F5,   KC_F6,   KC_F7,   KC_F8,   KC_F9,   KC_F10,  KC_F11,  KC_F12,  KC_F13,  KC_PSCR, KC_SCRL, KC_PAUS, \
    KC_GRV,  KC_1,    KC_2,    KC_3,    KC_4,    KC_5,    KC_6,    KC_7,    KC_8,    KC_9,    KC_0,    KC_MINS, KC_EQL,  KC_BSPC,          KC_INS,  KC_HOME, KC_PGUP, \
    KC_TAB,  KC_Q,    KC_W,    KC_E,    KC_R,    KC_T,    KC_Y,    KC_U,    KC_I,    KC_O,    KC_P,    KC_LBRC, KC_RBRC, KC_BSLS,          KC_DEL,  KC_END,  KC_PGDN, \
    KC_CAPS, KC_A,    KC_S,    KC_D,    KC_F,    KC_G,    KC_H,    KC_J,    KC_K,    KC_L,    KC_SCLN, KC_QUOT, KC_NUHS, KC_ENT,                                       \
    KC_LSFT, KC_NUBS, KC_Z,    KC_X,    KC_C,    KC_V,    KC_B,    KC_N,    KC_M,    KC_COMM, KC_DOT,  KC_SLSH, KC_INT1, KC_RSFT,                   KC_UP,             \
    KC_LCTL, KC_LGUI, KC_LALT,                            KC_SPC,                    KC_RALT, MO(1),   KC_APP,  KC_RCTL,          KC_LEFT, KC_DOWN, KC_RGHT  \
)

#define G80_LAYER_FN(extra0, extra1) LAYOUT_all( \
    G80_SBOOT, KC_MYCM, KC_WHOM, KC_CALC, KC_MAIL, KC_MSEL, KC_MSTP, KC_MPRV, KC_MPLY, KC_MNXT, KC_MUTE, KC_VOLD, KC_VOLU, extra0, extra1, _______, G80_GAME, \
    _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, G80_FRST,         RM_TOGG, _______, _______, \
    G80_ATAB, KC_NO,  KC_NO,   KC_NO,   KC_NO,   G80_BLTG, _______, _______, _______, _______, _______, _______, _______, G80_COLOR,       _______, _______, _______, \
    G80_AF4, _______, G80_SOCD, G80_DESK, _______, G80_WASD, _______, _______, G80_EDIT, G80_LOCK, _______, _______, _______, RM_NEXT,                                    \
    _______, _______, _______, _______, G80_CAPW, _______, _______, NK_TOGG, _______, _______, _______, _______, _______, G80_BAR,                   RM_VALU,           \
    _______, G80_WLCK, _______,                           KC_NO,                     _______, _______, _______, _______,          RM_SPDD, RM_VALD, RM_SPDU  \
)

#define G80_LAYER_EMPTY LAYOUT_all( \
    _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, \
    _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______,          _______, _______, _______, \
    _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______,          _______, _______, _______, \
    _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______,                                     \
    _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______,                   _______,           \
    _______, _______, _______,                            _______,                   _______, _______, _______, _______,          _______, _______, _______  \
)
// clang-format on
