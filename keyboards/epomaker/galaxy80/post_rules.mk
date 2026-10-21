# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Evaluated after the keymap rules.mk, so the "minimal" keymap (RGB_MATRIX_ENABLE = no) builds
# without any of the RGB / feature / radio sources.
ifeq ($(strip $(RGB_MATRIX_ENABLE)), yes)
    # CT16B1 LED engine + key scan in its ISR, the stock functions (Fn layer, side bar, indicators,
    # VIA menu, per-key colours, settings) and the radio link on UART2 (BT + 2.4G)
    SRC += g80_rgb.c g80_settings.c g80_perkey.c g80_bar.c g80_gaming.c g80_fn.c g80_via.c g80_indicators.c g80_radio.c g80_lpm.c g80_battery.c
    # stock effects without a QMK equivalent + "Per-key" (rgb_matrix_kb.inc)
    RGB_MATRIX_CUSTOM_KB = yes
    # debounce time/mode settable at runtime (VIA)
    DEBOUNCE_TYPE := custom
    SRC += g80_debounce.c
    # Caps Word (Fn+C), Layer Lock (Fn+F13) and Repeat Key, assignable from VIA (Mouse Keys come
    # from keyboard.json). Not built, for RAM (free RAM must stay >= 512 B, galaxy80_guard.ld): Key
    # Lock, Dynamic Macros, Combos, Tap Dance, Leader.
    CAPS_WORD_ENABLE := yes
    REPEAT_KEY_ENABLE := yes
    LAYER_LOCK_ENABLE := yes
    # Stacks were oversized: process 568 / 1536 B, exceptions 208 / 768 B measured in use. 1280 +
    # 640 B keep a wide margin and the free RAM above 512 B (galaxy80_guard.ld). The minimal image
    # keeps the defaults.
    USE_PROCESS_STACKSIZE = 0x500
    USE_EXCEPTIONS_STACKSIZE = 0x280
endif
