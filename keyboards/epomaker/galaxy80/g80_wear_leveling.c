// Copyright 2022 Nick Brassel (@tzarc), 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Wear-leveling backing store for the VIA image (WEAR_LEVELING_DRIVER = custom).
// Same as platforms/chibios/drivers/wear_leveling/wear_leveling_efl.c, with two differences:
//  - values are stored complemented: the SN32F240B EFL descriptor does not report
//    FLASH_ATTR_ERASED_IS_ONE, so the stock backend handed erased cells (0xFF) to the
//    wear-leveling layer as log data. That layer expects erased = 0: the log never looked
//    empty and the whole area was erased and rewritten at every boot.
//  - the area is fixed: sectors 991..1022 = 0xF7C0..0xFFBF (config.h). Sector 1023 with the
//    boot flag at 0xFFFC is outside it.
// Hooks around the unchanged erase / program sequence (no flash operation added, removed or
// reordered):
//  - watchdog feeds inside a consolidation (32 sector erases + 258 line programs, > 1 s with slow
//    sectors): one feed per erased sector and every G80_WL_FEED_LINES programmed lines. Both loops
//    are bounded; a flash controller stuck busy is still caught (no feed inside the busy waits).
//  - the write-log fill level (g80_wl_log_free), taken from the addresses the wear-leveling layer
//    reads during playback and writes afterwards, so that a write which will trigger a
//    consolidation can wait for the write gate (g80_settings.h).
#include <stdbool.h>
#include <hal.h>
#include "wear_leveling.h"
#include "wear_leveling_internal.h"

void     g80_wdt_kick(void);     /* galaxy80.c: watchdog feed, no-op before the watchdog runs */
uint32_t g80_wl_log_free(void);  /* g80_via.h */

#define G80_WL_BASE 0xF7C0u
#define G80_WL_FIRST_SECTOR (G80_WL_BASE / SN32_FLASH_SECTOR_SIZE)
#define G80_WL_SECTORS ((WEAR_LEVELING_BACKING_SIZE) / SN32_FLASH_SECTOR_SIZE)

_Static_assert(SN32_FLASH_SECTOR_SIZE == 64, "SN32F240B sector size");
_Static_assert(BACKING_STORE_WRITE_SIZE == SN32_FLASH_LINE_SIZE, "write size = flash line");
_Static_assert(G80_WL_BASE % SN32_FLASH_SECTOR_SIZE == 0, "area must start on a sector");
_Static_assert(G80_WL_BASE + (WEAR_LEVELING_BACKING_SIZE) == 0xFFC0u, "area must end below the boot-flag sector");

/* The write log starts after the consolidated copy and its 8 B hash. */
#define G80_WL_LOG_START ((WEAR_LEVELING_LOGICAL_SIZE) + 8u)
#define G80_WL_FEED_LINES 16u

/* First free log address; the end of the area (= full, a consolidation is due) until the boot
 * playback has seen the real end. */
static uint32_t g80_wl_log_end = (WEAR_LEVELING_BACKING_SIZE);

uint32_t g80_wl_log_free(void) {
    return (WEAR_LEVELING_BACKING_SIZE) - g80_wl_log_end;
}

static BaseFlash *flash;

bool backing_store_init(void) {
    flash = (BaseFlash *)&EFLD1;
    // Re-lock the EFL: after the bootloader it may still be unlocked.
    backing_store_lock();
    return true;
}

bool backing_store_unlock(void) {
    return eflStart(&EFLD1, NULL) == HAL_RET_SUCCESS;
}

bool backing_store_erase(void) {
    bool ret       = true;
    g80_wl_log_end = G80_WL_LOG_START;
    for (flash_sector_t i = 0; i < G80_WL_SECTORS; ++i) {
        g80_wdt_kick();
        flash_error_t status = flashStartEraseSector(flash, G80_WL_FIRST_SECTOR + i);
        if (status != FLASH_NO_ERROR && status != FLASH_BUSY_ERASING) {
            ret = false;
        }
        status = flashWaitErase(flash);
        if (status != FLASH_NO_ERROR && status != FLASH_BUSY_ERASING) {
            ret = false;
        }
    }
    return ret;
}

bool backing_store_write(uint32_t address, backing_store_int_t value) {
    if (address >= (WEAR_LEVELING_BACKING_SIZE)) {
        return false;
    }
    if (address >= G80_WL_LOG_START) {
        g80_wl_log_end = address + BACKING_STORE_WRITE_SIZE;
    }
    value = ~value;
    return flashProgram(flash, G80_WL_BASE + address, sizeof(value), (const uint8_t *)&value) == FLASH_NO_ERROR;
}

/* Same as the weak default (one backing_store_write per line, in order), plus the watchdog feed:
 * this writes the whole consolidated copy. */
bool backing_store_write_bulk(uint32_t address, backing_store_int_t *values, size_t item_count) {
    for (size_t i = 0; i < item_count; ++i) {
        if (i % G80_WL_FEED_LINES == 0) {
            g80_wdt_kick();
        }
        if (!backing_store_write(address + (i * BACKING_STORE_WRITE_SIZE), values[i])) {
            return false;
        }
    }
    return true;
}

bool backing_store_lock(void) {
    eflStop(&EFLD1);
    return true;
}

bool backing_store_read(uint32_t address, backing_store_int_t *value) {
    if (address >= (WEAR_LEVELING_BACKING_SIZE)) {
        *value = 0;
        return false;
    }
    *value = ~*(const volatile backing_store_int_t *)(SN32_FLASH_BASE + G80_WL_BASE + address);
    /* playback reads the log in order and stops at the first empty entry: the last log read
     * gives the end */
    if (address >= G80_WL_LOG_START) {
        g80_wl_log_end = *value ? address + BACKING_STORE_WRITE_SIZE : address;
    }
    return true;
}
