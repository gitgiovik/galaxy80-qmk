// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Gaming and lock functions: SOCD / Snap Click, WASD <-> arrows, game mode, Win-lock, LAlt+Tab and
// LAlt+F4 blocking.
#pragma once

#include "galaxy80.h"

#ifdef G80_FEATURES

/* Effective switches (game mode = Win-lock + both Alt blocks + SOCD). */
static inline bool g80_eff_win_lock(void) {
    return g80_config.win_lock || g80_config.game_mode;
}
static inline bool g80_eff_alt_tab(void) {
    return g80_config.block_alt_tab || g80_config.game_mode;
}
static inline bool g80_eff_alt_f4(void) {
    return g80_config.block_alt_f4 || g80_config.game_mode;
}

/* Snap Click mode: how two opposite direction keys (W/S, A/D, Up/Down, Left/Right) resolve when both
 * are held. Stored as g80_user_config_t.snap_mode (eeconfig_user, 3 bits); 0 = Off after a factory
 * reset. Off keeps the original behaviour: the pair is resolved as "Last" only while the SOCD
 * switch (Fn+S) or game mode is on. Any other mode switches the resolution on by itself, for the
 * pairs that are enabled in the per-pair mask. */
enum { G80_SC_OFF, G80_SC_LAST, G80_SC_FIRST, G80_SC_SECOND, G80_SC_NEUTRAL, G80_SC_MODES };
/* "First" / "Second" = the first / second key of the pair (W, A, Up, Left / S, D, Down, Right). */

/* The mode is g80_cfg_snap_mode() (g80_settings.h); values >= G80_SC_MODES are treated as Off. */

/* True when direction pairs are resolved at all (SOCD switch, game mode or a Snap Click mode). */
bool g80_eff_socd(void);

/* Result of g80_gaming_key(). */
enum { G80_KEY_PASS, G80_KEY_SWALLOW, G80_KEY_QMK };

/* Win-lock, LAlt+Tab / LAlt+F4 blocking, WASD swap and SOCD for one key event:
 * G80_KEY_PASS = not ours, G80_KEY_SWALLOW = fully handled, G80_KEY_QMK = continue in the normal path. */
uint8_t g80_gaming_key(uint16_t keycode, bool pressed);

/* Applies the effective Win-lock to the GUI mask. */
void g80_apply_win_lock(void);

/* Forgets all held direction keys (after clear_keyboard()). */
void g80_dir_reset(void);

#endif /* G80_FEATURES */
