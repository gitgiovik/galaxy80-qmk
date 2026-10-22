// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Wireless deep-sleep countdown and deep sleep (stock firmware v1.10 behaviour) and the LVD reset
// level. The LED-off timeout (LEDs dark, key scan running, wired and wireless) is g80_battery.c;
// the deep sleep turns the LEDs off too, so a deep-sleep time at or below the LED-off time needs
// nothing more.
//
// Countdown (100 ms ticks, stock): reloaded by every debounced matrix change (also the keys the
// on-board editor takes), every mode change and 5B 32 (linked) to the "deep sleep after" time
// (5 / 10 / 30 / 60 min, 0 = never), or to 15 s while not linked; 5B 33 and 5B 22 -> 15 s. It runs
// down only in wireless mode, with the option not "never", no key down and no pairing in
// progress. At zero the keyboard enters deep sleep if every guard holds (slider ON,
// USB stopped, radio TX idle, matrix released, no settings save pending unless saves are inhibited
// at critical battery, not pairing, no factory reset running); otherwise it retries 1 s later.
// A key held with no other key event for 10 min no longer blocks sleep: the host gets an empty
// report first and the held keys stay masked until they are released.
//
// Deep sleep: LEDs and LED engine off, watchdog and SysTick stopped, wake on any edge of the rows
// P3.6-11 (and of the slider P0.7), system clock on ILRC, PMU deep sleep + WFI. On wake the clock
// goes back to IHRC 48 MHz, the watchdog is restarted first, then SysTick and the LED engine. Every
// wait for a clock is bounded: on timeout the MCU resets (AIRCR SYSRESETREQ). Nothing touches the
// flash on this path. UART bytes that arrive during sleep are lost (as on stock); the charge pins
// do not wake the keyboard (as on stock).
#pragma once

#include "galaxy80.h"

#ifdef G80_FEATURES

#    define G80_LPM_TICK_MS 100u

/* Statistics (emulator tests, debug console). */
typedef struct {
    uint16_t sleeps;  /* WFI executed */
    uint16_t aborts;  /* wake sources did not read back armed just before WFI: not slept */
    uint16_t blocked; /* countdown reached zero but a guard failed */
    uint8_t  guard;   /* first failed guard of the last attempt (enum in g80_lpm.c) */
} g80_lpm_stats_t;

/* Ticks left, 0 = not counting. Also set directly, e.g. 30 at the end of the pairing window and 5
 * at the end of the reconnect indication (stock). The option is read from g80_ucfg.deep_sleep at
 * every reload: a VIA change (wired only) applies from the next key or mode change. */
extern uint16_t        g80_lpm_countdown;
extern g80_lpm_stats_t g80_lpm_stats;

/* Hooks called from galaxy80.c. */
void g80_lpm_task(void);        /* housekeeping, after the radio task */
void g80_lpm_matrix_scan(void); /* matrix_scan_kb */

/* Called by the radio frame parser for every valid 5B frame (link and battery state). */
void g80_lpm_radio_status(uint8_t code);

#endif /* G80_FEATURES */
