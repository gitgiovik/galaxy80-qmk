// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Minimal rand()/srand() (xorshift32). Defining them here keeps newlib's rand(), which drags in
// the reentrancy layer, malloc and printf, out of the link. Not suitable for anything but effects.

#include <stdint.h>
#include <stdlib.h>

static uint32_t g80_rand_state = 1;

void srand(unsigned int seed) {
    g80_rand_state = seed ? seed : 1; /* xorshift32 must never hold 0 */
}

int rand(void) {
    uint32_t x = g80_rand_state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    g80_rand_state = x;
    return (int)(x & RAND_MAX);
}
