// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Indicator pass of the RGB matrix (rgb_matrix_indicators_advanced_kb).
#pragma once

#include "galaxy80.h"

#ifdef G80_FEATURES

/* Sets LED i only if it belongs to the chunk led_min..led_max-1 being drawn. Needs led_min and
 * led_max in scope. */
#    define G80_SET(i, r, g, b)                           \
        do {                                              \
            if ((i) >= led_min && (i) < led_max) {        \
                rgb_matrix_set_color((i), (r), (g), (b)); \
            }                                             \
        } while (0)

/* The active wireless channel key (Q / W / E / R) is lit steady for G80_CHANNEL_SHOW_MS. Started
 * here on a channel switch and on entering wireless mode; the sleep code calls it after a wake-up. */
#    define G80_CHANNEL_SHOW_MS 3000u
void g80_ind_channel_show(void);

#endif /* G80_FEATURES */
