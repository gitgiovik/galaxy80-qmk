// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

/* Home-row mods for MT()/LT() keys assigned from VIA ("Any"). Both options only act on tap-hold
 * keys, so they are inert while the keymap has none:
 *  - Chordal Hold: a tap-hold key becomes a hold only together with a key of the other hand
 *    (hand map: chordal_hold_handedness() in g80_fn.c);
 *  - Flow Tap: a tap-hold key pressed within FLOW_TAP_TERM ms of the previous key is a tap, so fast
 *    typing on a home-row mod does not misfire. */
#define CHORDAL_HOLD
#define FLOW_TAP_TERM 150
