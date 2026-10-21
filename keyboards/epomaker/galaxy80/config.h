// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

/* Hardware watchdog: timeout = (TC+1) * 128 * 2^PRE / 32 kHz (ILRC).
 * PRE=3 (/8), TC=255 -> 256 * 32 ms = ~8.2 s nominal.
 * If the prescaler change is not applied (datasheet 3.4.2 asks for a SYS1_PRST peripheral
 * reset that is not documented), PRE stays /1: 256 * 4 ms = ~1.0 s, still safe. */
#define G80_WDT_PRE 3
#define G80_WDT_TC 255

/* EEPROM emulation (used only when EEPROM_DRIVER = vendor, i.e. the VIA keymap).
 * wear_leveling/embedded_flash allocates WEAR_LEVELING_BACKING_SIZE bytes counting
 * backwards from the end of flash, skipping WEAR_LEVELING_EFL_OMIT_LAST_SECTOR_COUNT
 * 64-byte sectors: 2048 B -> sectors 991..1022 = 0xF7C0..0xFFBF.
 * Sector 1023 (0xFFC0..0xFFFF, boot flag 0xAAAA5555 at 0xFFFC) is never touched.
 * The link-time guard galaxy80_guard.ld fails the build if code reaches 0xF7C0. */
#define WEAR_LEVELING_BACKING_SIZE 2048
#define WEAR_LEVELING_LOGICAL_SIZE 1024
#define WEAR_LEVELING_EFL_OMIT_LAST_SECTOR_COUNT 1
/* custom backing store (g80_wear_leveling.c, VIA image): 4-byte flash lines */
#define BACKING_STORE_WRITE_SIZE 4

/* Key scan timing, shared by the polled scan (matrix.c) and the ISR scan (g80_rgb.c).
 * 48 MHz core: wait_cpuclock(96) ~ 2 us column settle (wait_cpuclock() supports only 1..135
 * cycles); rows waited for up to 20 x ~1 us to discharge. Verified on the keyboard. */
#ifndef G80_COL_SETTLE_CYCLES
#    define G80_COL_SETTLE_CYCLES 96
#endif
#ifndef G80_ROW_RELEASE_TRIES
#    define G80_ROW_RELEASE_TRIES 20
#endif
/* One poll step of the row-release wait: wait_cpuclock(48) = ~1 us at 48 MHz. */
#define G80_ROW_POLL_CYCLES 48

#ifndef G80_MINIMAL
/* LED engine (g80_rgb.c). Per-phase duty cap, <= stock maximum (255 minus the ~2-3 ticks before the
 * select line rises). */
#    ifndef G80_DUTY_MAX
#        define G80_DUTY_MAX 252
#    endif

/* Win/Mac switch: Mac when P0.2 reads LOW (stock flashes blue = Mac with P0.2 low). */
#    ifndef G80_MAC_WHEN_P02_LOW
#        define G80_MAC_WHEN_P02_LOW 1
#    endif

/* rgb_matrix: the cap lives in the driver, so indicators are capped too. Fn brightness
 * levels 255/191/127/63/0 ~ stock LUT 0xFF/0xC0/0x80/0x40/0 (0x7B8C). */
#    define RGB_MATRIX_VAL_STEP 64
#    define RGB_MATRIX_SPD_STEP 51
#    define RGB_MATRIX_LED_FLUSH_LIMIT 16
/* Every RGB keycode (RM_* and UG_*) is handled in g80_fn.c without an EEPROM write (the change goes
 * through the deferred, gated save); QMK's shared UG_* -> rgb_matrix mapping is not needed. */
#    define RGB_MATRIX_DISABLE_SHARED_KEYCODES

/* NKRO on by default (shared endpoint, no new endpoint). */
#    define NKRO_DEFAULT_ON true

/* Debounce default (g80_debounce.c, VIA 1..10 ms), verified on hardware. */
#    define G80_DEBOUNCE_DEFAULT 5
/* EEPROM layout (VIA image, 1024 B logical): eeconfig 37 + VIA magic/layout 4 + custom config 187
 * (1 layout-version byte + 93 LEDs x 2 B per-key colours: hue, saturation, relative value) +
 * keymaps 3 layers x 102 x 2 = 612 -> 840 B; VIA macros get the remaining 184 B (QMK requires
 * >= 100). Four layers would leave 1024 - 41 - 816 - 187 < 0, so base + Fn + 1 free layer are
 * editable in VIA. Only the VIA image has persistent EEPROM. */
#    ifdef VIA_ENABLE
#        define VIA_EEPROM_CUSTOM_CONFIG_SIZE 187
#        define DYNAMIC_KEYMAP_LAYER_COUNT 3
/* VIA raw HID queues: VIA sends one command and waits for its answer, so 2 buffers per direction
 * are enough (QMK default 4 x 36 B each way). Frees 144 B of RAM for the 2-byte colour table. */
#        define RAW_IN_CAPACITY 2
#        define RAW_OUT_CAPACITY 2
#    endif
/* MT()/LT() keys assigned from VIA "Any": 200 ms with permissive hold (a nested tap = hold). */
#    define TAPPING_TERM 200
#    define PERMISSIVE_HOLD
#endif
