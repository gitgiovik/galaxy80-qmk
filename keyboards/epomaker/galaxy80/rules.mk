# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later

# Stock-equivalent matrix scan (columns driven high one at a time, rows read active-high)
SRC += matrix.c

# Safety: images without VIA perform NO runtime flash writes at all.
# The VIA keymap overrides this with EEPROM_DRIVER = vendor (wear-leveling, bounded
# to 0xF6C0..0xFFBF, see config.h).
EEPROM_DRIVER ?= transient

# Link-time guard: code must end below 0xF6C0 (wear-leveling aux sectors, then the EEPROM area at
# 0xF7C0) and the boot flag must be at 0xFFFC. Taken from this directory, so that a build-slot copy
# of the keyboard elsewhere in the QMK tree links its own copy.
G80_KB_DIR := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))
EXTRALDFLAGS += $(G80_KB_DIR)/galaxy80_guard.ld

# Features no keymap uses.
SPACE_CADET_ENABLE = no
GRAVE_ESC_ENABLE = no

# ChibiOS system halt handler: unreferenced by the firmware itself but kept as the target of the
# fault/halt paths (and of the emulator's halt-injection test).
LDFLAGS += -Wl,--undefined=chSysHalt

# Only the main thread calls the embedded-flash driver (wear-leveling for the VIA EEPROM, no other
# thread, never from an interrupt), so its mutex is dead weight.
OPT_DEFS += -DEFL_USE_MUTUAL_EXCLUSION=FALSE
