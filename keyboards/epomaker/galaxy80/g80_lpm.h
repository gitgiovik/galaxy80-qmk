// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Wireless idle countdown and deep sleep (stock firmware v1.10 behaviour), the LVD reset level and
// the G80_SLEEP "sleep now" key.
//
// Countdown (100 ms ticks, stock): reloaded by every key event and every mode change (not linked:
// 15 s; linked: 1 / 5 / 30 min by the "sleep after" option, 0 = never); 5B 32 -> 5 min, 5B 33 and
// 5B 22 -> 15 s. It runs down only in wireless mode, with the option not "never", no key down and
// no pairing in progress. At zero the keyboard enters deep sleep if every guard holds (slider ON,
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

/* "Sleep after" option (eeconfig_user sleep_opt): stock combobox values. */
enum g80_sleep_opt {
    G80_SLEEP_NEVER = 0,
    G80_SLEEP_1MIN,
    G80_SLEEP_5MIN,
    G80_SLEEP_30MIN,
};

/* Statistics (emulator tests, debug console). */
typedef struct {
    uint16_t sleeps;  /* WFI executed */
    uint16_t aborts;  /* wake sources did not read back armed just before WFI: not slept */
    uint16_t blocked; /* countdown reached zero but a guard failed */
    uint8_t  guard;   /* first failed guard of the last attempt (enum in g80_lpm.c) */
} g80_lpm_stats_t;

extern uint16_t        g80_lpm_countdown; /* ticks left, 0 = not counting */
extern uint8_t         g80_lpm_opt;       /* enum g80_sleep_opt */
extern g80_lpm_stats_t g80_lpm_stats;

/* Hooks called from galaxy80.c. */
void g80_lpm_task(void);                                         /* housekeeping, after the radio task */
void g80_lpm_matrix_scan(void);                                  /* matrix_scan_kb */
bool g80_lpm_process_record(uint16_t keycode, keyrecord_t *record); /* first, for every key event */

/* Called by the radio frame parser for every valid 5B frame (link and battery state). */
void g80_lpm_radio_status(uint8_t code);

/* Option set from the settings / VIA menu (0..3); reloads the countdown. */
void    g80_lpm_set_option(uint8_t opt);
uint8_t g80_lpm_option(void);

/* Sets the countdown (ticks of 100 ms), e.g. 30 at the end of the pairing window and 5 at the end of
 * the reconnect indication (stock). */
void g80_lpm_countdown_set(uint16_t ticks);

#endif /* G80_FEATURES */
