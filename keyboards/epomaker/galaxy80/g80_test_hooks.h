// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Symbols the emulator test suite (tests/emulator) looks up by name in the firmware images (nm),
// to call, hook or read them. Renaming, inlining away, or making any of them `static` (for the
// "extern" ones) silently breaks tests, so they are listed here. Keep the names and the layouts.
//
// 1. External call-ins (the emulator sets pc to them or breaks on them; must stay global,
//    out-of-line functions):
//      early_hardware_init_pre, board_init, keyboard_post_init_kb, housekeeping_task_kb,
//      process_record_kb, post_process_record_kb, suspend_power_down_kb, via_command_kb,
//      via_custom_value_command_kb, via_init_kb, matrix_scan_custom, mcu_reset, chSysHalt,
//      g80_lpm_sleep (local, noinline: the deep-sleep sequence, its WFI is recognised by name),
//      bootmagic_reset_eeprom, eeconfig_update_kb, g80_wdt_kick, g80_pk_rgb.
//    The option getters g80_opt_* (g80_battery.c, noinline) are patched by the battery suites.
//    The VIA image additionally relies on the QMK entry points raw_hid_receive and
//    via_eeprom_is_valid.
//
// 2. Data read or written from the emulator (global; the layout of the structs and tables is part
//    of the contract):
//      g80_config (g80_config_t, galaxy80.h)           g80_pk[G80_PK_BYTES]    g80_pk_editing
//      g80_frame[G80_PHASES][G80_LED_COLS]             g80_led_pos, g80_col_chan
//      g80_isr_steps (g80_rgb.c)
//      g80_scan_fallback (matrix.c)                    g80_debounce_ms (g80_debounce.c)
//      g80_radio_stats (g80_radio_stats_t)             g80_pk_snap (g80_perkey.c)
//      g80_pk_dirty (g80_perkey.c: "unsaved")          g80_ucfg (g80_user_config_t, eeconfig_user)
//      g80_lpm_countdown, g80_lpm_stats (g80_lpm.h, deep-sleep tests)
//      g80_save_keymap, g80_save_rgb (g80_settings.h)   g80_frst_running (g80_fn.c: factory reset)
//      g80_save_deflayer (g80_settings.h)
//    Called by the emulator: g80_pk_store (g80_perkey.c: the per-key flash-write site, Apply and
//    factory reset), g80_wl_log_free (g80_wear_leveling.c).
//
// 3. Symbols read as local (static) symbols of the ELF: the name must survive and the object must
//    not be optimised away, so keep them referenced (and, for functions, noinline):
//      g80_config_save_now   (g80_settings.c, noinline: the single flash-write site of a deferred save)
//      g80_pk_activity       (g80_perkey.c: last editor key, ms; idle exit after G80_PK_IDLE_MS)
//      g80_pk_apply_req      (g80_perkey.c: Apply requested, waiting for the write gate)
//      g80_vq_busy           (g80_via.c: a deferred VIA EEPROM write has not been answered yet)
//      g80_save_dirty        (g80_settings.c: a deferred settings save is pending)
//      g80_fl                (g80_fn.c: flash state; layout target, led, r, g, b, left, on, half_ms, next)
//      g80_dir_cnt           (g80_gaming.c: 8 counters of held direction keys)
//      g80_wl_on             (g80_radio.c: wireless mode active)
//
// 4. Static audit (emulator test S4-WDT-sites): g80_wdt_kick() may be called only from
//    g80_pk_store, g80_config_save_now and, inside a wear-leveling
//    consolidation, backing_store_erase / backing_store_write_bulk (g80_wear_leveling.c, bounded
//    loops of 32 sectors / 258 lines); no other function may contain a watchdog feed besides the
//    feeders listed in that test (FEEDERS_OK) (housekeeping_task_kb, process_record_kb,
//    post_process_record_kb, via_command_kb, suspend_power_down_kb, g80_wdt_kick, g80_wdt_start).
#pragma once

#include "galaxy80.h"

#ifdef G80_FEATURES
// Including the headers that declare the externals above makes a stray `static` on a definition a
// compile error in the defining file.
#    include "g80_perkey.h"
#    include "g80_radio.h"
#    include "g80_rgb.h"
#    include "g80_settings.h"
#    include "g80_lpm.h"

extern uint8_t g80_debounce_ms; // g80_debounce.c
#    ifdef RGB_MATRIX_ENABLE
extern volatile bool g80_scan_fallback; // matrix.c
#    endif
#endif
