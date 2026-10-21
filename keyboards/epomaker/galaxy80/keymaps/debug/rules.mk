# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later

# Galaxy80 DEBUG image: same layers as "default" (transient EEPROM, no flash writes)
# plus the QMK console HID interface (usage page 0xFF31, usage 0x74) on endpoint 3.
# SN32F240B USB: 4 endpoints, 256 B PMA -> EP0 64 + kbd 8 + shared 32 + console 32 = 136 B.
CONSOLE_ENABLE = yes

# matrix.c polled-scan counters (used only if the LED ISR heartbeat fails)
OPT_DEFS += -DG80_MATRIX_STATS
