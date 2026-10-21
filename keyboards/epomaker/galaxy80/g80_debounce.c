// Copyright 2017 Alex Ong<the.onga@gmail.com>
// Copyright 2020 Andrei Purdea<andrei@purdea.ro>
// Copyright 2021 Simon Arlott
// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Galaxy80 per-key debounce with the time set at runtime (VIA, eeconfig_kb).
// DEBOUNCE_TYPE = custom (post_rules.mk, RGB images only; the minimal image keeps sym_defer_pk).
//   g80_debounce_eager == false: quantum/debounce/sym_defer_pk.c, unchanged algorithm
//                                (a change is accepted after g80_debounce_ms ms without bounce)
//   g80_debounce_eager == true : quantum/debounce/asym_eager_defer_pk.c (press reported at once,
//                                then ignored for g80_debounce_ms; release deferred)
// Default 5 ms deferred = the behaviour verified on the keyboard.

#include "debounce.h"
#include "g80_test_hooks.h"
#include "matrix.h"
#include "timer.h"
#include "util.h"

#ifndef G80_DEBOUNCE_DEFAULT
#    define G80_DEBOUNCE_DEFAULT 5
#endif

#define G80_DEB_ELAPSED 0

/* written by g80_settings.c (thread context, same thread as debounce()) */
uint8_t g80_debounce_ms    = G80_DEBOUNCE_DEFAULT;
bool    g80_debounce_eager = false;

typedef struct {
    bool    pressed : 1;
    uint8_t time : 7;
} g80_deb_counter_t;

static g80_deb_counter_t g80_deb_counters[MATRIX_ROWS * MATRIX_COLS];
static bool              g80_counters_need_update;
static bool              g80_matrix_need_update;
static bool              g80_cooked_changed;

void debounce_init(void) {}

static void g80_deb_update(matrix_row_t raw[], matrix_row_t cooked[], uint8_t elapsed) {
    g80_counters_need_update = false;
    g80_matrix_need_update   = false;
    for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
        for (uint8_t col = 0; col < MATRIX_COLS; col++) {
            g80_deb_counter_t *c = &g80_deb_counters[row * MATRIX_COLS + col];
            if (c->time == G80_DEB_ELAPSED) {
                continue;
            }
            if (c->time <= elapsed) {
                c->time = G80_DEB_ELAPSED;
                if (g80_debounce_eager && c->pressed) {
                    g80_matrix_need_update = true; /* lock-out over: look at the raw state again */
                } else {
                    const matrix_row_t mask = MATRIX_ROW_SHIFTER << col;
                    const matrix_row_t next = (cooked[row] & ~mask) | (raw[row] & mask);
                    g80_cooked_changed |= (next ^ cooked[row]) != 0;
                    cooked[row] = next;
                }
            } else {
                c->time -= elapsed;
                g80_counters_need_update = true;
            }
        }
    }
}

static void g80_deb_start(matrix_row_t raw[], matrix_row_t cooked[]) {
    g80_matrix_need_update = false;
    uint8_t ms             = g80_debounce_ms;
    if (ms < 1) {
        ms = 1;
    } else if (ms > 127) {
        ms = 127;
    }
    for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
        const matrix_row_t delta = raw[row] ^ cooked[row];
        for (uint8_t col = 0; col < MATRIX_COLS; col++) {
            g80_deb_counter_t *c    = &g80_deb_counters[row * MATRIX_COLS + col];
            const matrix_row_t mask = MATRIX_ROW_SHIFTER << col;
            if (delta & mask) {
                if (c->time == G80_DEB_ELAPSED) {
                    c->pressed               = (raw[row] & mask) != 0;
                    c->time                  = ms;
                    g80_counters_need_update = true;
                    if (g80_debounce_eager && c->pressed) {
                        cooked[row] ^= mask; /* key-down: eager */
                        g80_cooked_changed = true;
                    }
                }
            } else if (c->time != G80_DEB_ELAPSED && !(g80_debounce_eager && c->pressed)) {
                c->time = G80_DEB_ELAPSED; /* bounced back: deferred change cancelled */
            }
        }
    }
}

bool debounce(matrix_row_t raw[], matrix_row_t cooked[], bool changed) {
    static fast_timer_t last_time;
    bool                updated_last = false;
    g80_cooked_changed               = false;

    if (g80_counters_need_update) {
        const fast_timer_t now     = timer_read_fast();
        const fast_timer_t elapsed = TIMER_DIFF_FAST(now, last_time);
        last_time                  = now;
        updated_last               = true;
        if (elapsed > 0) {
            g80_deb_update(raw, cooked, (uint8_t)MIN(elapsed, 127));
        }
    }

    if (changed || g80_matrix_need_update) {
        if (!updated_last) {
            last_time = timer_read_fast();
        }
        g80_deb_start(raw, cooked);
    }

    return g80_cooked_changed;
}
