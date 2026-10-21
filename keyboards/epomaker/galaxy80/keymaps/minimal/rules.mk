# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Galaxy80 "minimal" image: the safety fallback. USB keyboard with the polled scan, all LEDs held
# off, no RGB / feature / radio code. It keeps every boot-entry and watchdog layer of galaxy80.c.
RGB_MATRIX_ENABLE = no
OPT_DEFS += -DG80_MINIMAL
