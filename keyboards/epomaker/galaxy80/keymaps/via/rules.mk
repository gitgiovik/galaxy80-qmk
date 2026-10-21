# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later

VIA_ENABLE = yes
# Persistent EEPROM for VIA: wear-leveling on internal flash, 0xF7C0..0xFFBF only.
# Own backing store (g80_wear_leveling.c, erased flash reads as 0) instead of the stock
# embedded_flash backend, which rewrote the whole area at every boot.
EEPROM_DRIVER = vendor
WEAR_LEVELING_DRIVER = custom
OPT_DEFS += -DHAL_USE_EFL
SRC += g80_wear_leveling.c
# Key overrides: Shift+Backspace = Delete (keymap.c)
KEY_OVERRIDE_ENABLE = yes
