// Copyright 2022 Nick Brassel (@tzarc), 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Wear-leveling backing store for the VIA image (WEAR_LEVELING_DRIVER = custom).
// Based on platforms/chibios/drivers/wear_leveling/wear_leveling_efl.c, with these differences:
//  - values are stored complemented: the SN32F240B EFL descriptor does not report
//    FLASH_ATTR_ERASED_IS_ONE, so the stock backend handed erased cells (0xFF) to the
//    wear-leveling layer as log data. That layer expects erased = 0: the log never looked
//    empty and the whole area was erased and rewritten at every boot.
//  - the area is fixed: sectors 991..1022 = 0xF7C0..0xFFBF (config.h). Sector 1023 with the
//    boot flag at 0xFFFC is outside it.
//  - the consolidation is power-fail safe (2026-10-21, "settings sometimes all reset on unplug").
//    QMK's wear_leveling_consolidate_force() erases the whole area (the only consolidated copy and
//    the log) and then rewrites it: a power cut in that ~1.2 s window left a hash mismatch and the
//    boot reset everything to defaults. Here backing_store_erase() only records the request and the
//    consolidation runs sector by sector (below), with 4 auxiliary sectors below the area
//    (G80_WL_AUX_BASE = 0xF6C0, config.h) as copy buffer and state log. A cut at any point boots to
//    the data of main + intact log, never to zeros. QMK core is unchanged.
//  - watchdog feeds: one per erased sector and one every 16 programmed lines. All loops are
//    bounded; a flash controller stuck busy is still caught (no feed inside the busy waits).
//  - the write-log fill level (g80_wl_log_free), taken from the addresses the wear-leveling layer
//    reads during playback and writes afterwards, so that a write which will trigger a
//    consolidation can wait for the write gate (g80_settings.h).
//
// Safe consolidation (backing_store_write_bulk(0, cache) right after the deferred erase):
//   state log ST = 32 raw words (2 sectors, erased = 0xFFFFFFFF), one 4 B program each:
//     BEGIN, then per changed logical sector i: COPY(i, crc20), finally PHASE2.
//   0. no recovery pending: erase ST if it holds anything; main with an invalid hash (blank or
//      corrupt area, QMK used zeros as the base) is erased; program BEGIN.
//   1. for each logical sector i (64 B) that differs from the cache: erase scratch SCR[i & 1],
//      program the cache sector into it, program COPY(i, crc of the scratch), then erase main i and
//      program it. The log is untouched and its last entry per byte is the cached value, so
//      main + log == cache at every instant. (Exception: the tail of the write that filled the log,
//      not in the log: those bytes come back new in the sectors already rewritten, old in the
//      others, like a write cut half-way.)
//   2. program PHASE2, erase hash + log (sectors 16..31). QMK then writes the hash; the program of
//      its second word erases ST.
// Boot (backing_store_init, before QMK reads anything), ST active (word 0 == BEGIN):
//   - FNV(main) == stored hash: cut before the first main erase or after the hash: erase ST.
//   - PHASE2 present: main is final: erase hash + log, program the hash, erase ST.
//   - else phase 1: the last COPY(i) whose scratch matches its crc and differs from main i is
//     copied back to main i (idempotent), then main is trusted: reads of the hash return FNV(main),
//     QMK plays the (still full or still invalid) log over it and consolidates again at once, which
//     continues the procedure (more COPY words). Fewer than 2 free ST words (>= 14 nested power
//     cuts inside one consolidation) -> the old erase-all consolidation (not power-fail safe).
// Format (wear_leveling_erase(): the deferred erase followed by anything else than
//   write_bulk(0)): ST first, then hash + log, then main (a cut leaves the old data or the defaults).
#include <stdbool.h>
#include <hal.h>
#include "fnv.h"
#include "wear_leveling.h"
#include "wear_leveling_internal.h"

void     g80_wdt_kick(void);     /* galaxy80.c: watchdog feed, no-op before the watchdog runs */
uint32_t g80_wl_log_free(void);  /* g80_via.h */

#define G80_WL_BASE 0xF7C0u
#define G80_WL_HASH (G80_WL_BASE + (WEAR_LEVELING_LOGICAL_SIZE)) /* hash + log: sectors 16..31 */
#define G80_SCR (G80_WL_AUX_BASE)                                 /* 2 scratch sectors, stored like main */
#define G80_ST (G80_WL_AUX_BASE + 128u)                           /* 2 state sectors = 32 raw words */
#define G80_ST_WORDS 32u
#define G80_LINES 16u /* lines per sector */

#define G80_ST_BEGIN 0x5A17C0DEu
#define G80_ST_PHASE2 0xA5E81F21u
#define G80_ST_COPY 0xC6u /* tag (top byte): COPY(i, crc20) = 0xC6 << 24 | i << 20 | crc20 */

_Static_assert(SN32_FLASH_SECTOR_SIZE == 64, "SN32F240B sector size");
_Static_assert(SN32_FLASH_BASE == 0, "flash mapped at 0");
_Static_assert(BACKING_STORE_WRITE_SIZE == SN32_FLASH_LINE_SIZE, "write size = flash line");
_Static_assert(G80_WL_BASE + (WEAR_LEVELING_BACKING_SIZE) == 0xFFC0u, "area must end below the boot-flag sector");
_Static_assert(G80_WL_AUX_BASE + 256u == G80_WL_BASE, "aux sectors just below the area");
_Static_assert((WEAR_LEVELING_LOGICAL_SIZE) == 1024 && (WEAR_LEVELING_BACKING_SIZE) == 2048, "16 data sectors, hash at 1024, 16 hash + log sectors");

/* The write log starts after the consolidated copy and its 8 B hash. */
#define G80_WL_LOG_START ((WEAR_LEVELING_LOGICAL_SIZE) + 8u)
#define G80_FL ((BaseFlash *)&EFLD1)
#define G80_RD(a) (*(const volatile uint32_t *)(a))

/* First free log address; the end of the area (= full, a consolidation is due) until the boot
 * playback has seen the real end. */
static uint32_t g80_wl_log_end = (WEAR_LEVELING_BACKING_SIZE);
static bool     g80_wl_erase_pending; /* backing_store_erase() called, not yet resolved */
static bool     g80_wl_trust_main;    /* phase-1 recovery: main is the base, the hash reads are faked */
static uint32_t g80_wl_fake_hash[2];

uint32_t g80_wl_log_free(void) {
    return (WEAR_LEVELING_BACKING_SIZE) - g80_wl_log_end;
}

/* n sectors from a, one watchdog feed per sector. No status: a failed erase shows as a failed
 * verify or as data QMK rejects (hash), as with QMK's own backend. */
static void g80_erase(uint32_t a, uint32_t n) {
    for (; n; --n, a += SN32_FLASH_SECTOR_SIZE) {
        g80_wdt_kick();
        flashStartEraseSector(G80_FL, a / SN32_FLASH_SECTOR_SIZE);
        flashWaitErase(G80_FL);
    }
}

/* n lines src[k] ^ x at a (x = ~0: logical -> stored complement), each one read back; one feed
 * every 16 lines */
static bool g80_prog(uint32_t a, const uint32_t *src, uint32_t n, uint32_t x) {
    for (uint32_t k = 0; k < n; ++k, a += 4u) {
        if (!(k % G80_LINES)) {
            g80_wdt_kick();
        }
        uint32_t v = src[k] ^ x;
        if (flashProgram(G80_FL, a, 4u, (const uint8_t *)&v) != FLASH_NO_ERROR || G80_RD(a) != v) {
            return false;
        }
    }
    return true;
}

/* sector at a == src ^ x */
static bool g80_eq(uint32_t a, const uint32_t *src, uint32_t x) {
    for (uint32_t k = 0; k < G80_LINES; ++k) {
        if (G80_RD(a + 4u * k) != (src[k] ^ x)) {
            return false;
        }
    }
    return true;
}

/* FNV-1a over the logical bytes of n stored lines */
static __attribute__((noinline)) uint64_t g80_fnv(uint32_t a, uint32_t n, uint64_t h) {
    for (; n; --n, a += 4u) {
        uint32_t v = ~G80_RD(a);
        h          = fnv_64a_buf(&v, 4, h);
    }
    return h;
}

static uint64_t g80_main_fnv(void) {
    return g80_fnv(G80_WL_BASE, (WEAR_LEVELING_LOGICAL_SIZE) / 4u, FNV1A_64_INIT);
}

/* h == the stored hash of the consolidated data */
static bool g80_hash_ok(uint64_t h) {
    return ~(uint32_t)h == G80_RD(G80_WL_HASH) && ~(uint32_t)(h >> 32) == G80_RD(G80_WL_HASH + 4u);
}

/* COPY word of logical sector i: crc over its scratch sector, seeded with i */
static uint32_t g80_copy_word(uint32_t i) {
    return G80_ST_COPY << 24 | i << 20 | ((uint32_t)g80_fnv(G80_SCR + 64u * (i & 1u), G80_LINES, FNV1A_64_INIT ^ i) & 0xFFFFFu);
}

/* next state word: after the last programmed one (0 = the state log is erased) */
static uint32_t g80_st_next(void) {
    uint32_t n = 0;
    for (uint32_t k = 0; k < G80_ST_WORDS; ++k) {
        if (G80_RD(G80_ST + 4u * k) != 0xFFFFFFFFu) {
            n = k + 1;
        }
    }
    return n;
}

/* appends w; false when fewer than `need` words are free */
static bool g80_st_add(uint32_t w, uint32_t need) {
    uint32_t k = g80_st_next();
    return k + need <= G80_ST_WORDS && g80_prog(G80_ST + 4u * k, &w, 1, 0);
}

static void g80_st_clear(void) {
    g80_wl_trust_main = false;
    g80_erase(G80_ST, 2);
}

/* the plain erase (format, fallback): state first, then hash + log, then main */
static void g80_erase_all(void) {
    g80_wl_erase_pending = false;
    g80_wl_log_end       = G80_WL_LOG_START;
    g80_st_clear();
    g80_erase(G80_WL_HASH, 16);
    g80_erase(G80_WL_BASE, 16);
}

/* the deferred erase + write_bulk(0, cache): sector by sector, see the header. A false return
 * (a program verify failed, or the state log is full) leaves the old log in place: the later log
 * writes fail their read-back and nothing more persists until the next boot, whose phase-1
 * recovery completes the consolidation. Only on a flash error; no stored data is lost. */
static bool g80_consolidate(const uint32_t *v) {
    g80_wl_erase_pending = false;
    if (!g80_wl_trust_main) {
        if (g80_st_next()) {
            g80_st_clear();
        }
        if (!g80_hash_ok(g80_main_fnv())) {
            for (uint32_t a = G80_WL_BASE; a < G80_WL_HASH; a += 4u) {
                if (G80_RD(a) != 0xFFFFFFFFu) {
                    g80_erase(a & ~(SN32_FLASH_SECTOR_SIZE - 1u), 1);
                }
            }
        }
        if (!g80_st_add(G80_ST_BEGIN, 2)) {
            return false;
        }
    }
    for (uint32_t i = 0; i < (WEAR_LEVELING_LOGICAL_SIZE) / 64u; ++i) {
        const uint32_t *s    = v + i * G80_LINES;
        const uint32_t  main = G80_WL_BASE + 64u * i;
        const uint32_t  scr  = G80_SCR + 64u * (i & 1u);
        if (g80_eq(main, s, ~0u)) {
            continue;
        }
        g80_erase(scr, 1);
        if (!g80_prog(scr, s, G80_LINES, ~0u)) {
            return false;
        }
        if (!g80_st_add(g80_copy_word(i), 2)) {
            /* state log exhausted (>= 14 nested power cuts): the erase-all consolidation */
            g80_erase_all();
            return g80_prog(G80_WL_BASE, v, (WEAR_LEVELING_LOGICAL_SIZE) / 4u, ~0u);
        }
        g80_erase(main, 1);
        if (!g80_prog(main, s, G80_LINES, ~0u)) {
            return false;
        }
    }
    if (!g80_st_add(G80_ST_PHASE2, 1)) {
        return false;
    }
    g80_wl_log_end = G80_WL_LOG_START;
    g80_erase(G80_WL_HASH, 16);
    return true;
}

/* boot recovery of an interrupted consolidation, before QMK reads the area */
static void g80_recover(void) {
    if (G80_RD(G80_ST) != G80_ST_BEGIN) {
        return; /* erased, or a torn state erase: cleared by the next consolidation */
    }
    backing_store_unlock();
    uint64_t h    = g80_main_fnv();
    uint32_t last = 0;
    if (!g80_hash_ok(h)) {
        bool ph2 = false;
        for (uint32_t k = 1; k < G80_ST_WORDS; ++k) {
            uint32_t w = G80_RD(G80_ST + 4u * k);
            ph2 |= w == G80_ST_PHASE2;
            if (w >> 24 == G80_ST_COPY) {
                last = w;
            }
        }
        if (ph2) {
            /* main is final: finish phase 2 */
            uint32_t hw[2] = {(uint32_t)h, (uint32_t)(h >> 32)};
            g80_erase(G80_WL_HASH, 16);
            last = !g80_prog(G80_WL_HASH, hw, 2, ~0u); /* 1: keep the state, retried at the next boot */
        } else {
            /* phase 1: complete the sector cut while main i was being erased / programmed */
            uint32_t        i    = last >> 20 & 15u;
            uint32_t        main = G80_WL_BASE + 64u * i;
            const uint32_t *scr  = (const uint32_t *)(G80_SCR + 64u * (i & 1u));
            if (last && g80_copy_word(i) == last && !g80_eq(main, scr, 0)) {
                g80_erase(main, 1);
                g80_prog(main, scr, G80_LINES, 0);
            }
            h                   = g80_main_fnv();
            g80_wl_fake_hash[0] = (uint32_t)h;
            g80_wl_fake_hash[1] = (uint32_t)(h >> 32);
            g80_wl_trust_main   = true;
            last                = 1;
        }
    }
    if (!last) {
        g80_st_clear();
    }
    backing_store_lock();
}

bool backing_store_init(void) {
    // Re-lock the EFL: after the bootloader it may still be unlocked.
    backing_store_lock();
    g80_recover();
    return true;
}

bool backing_store_unlock(void) {
    return eflStart(&EFLD1, NULL) == HAL_RET_SUCCESS;
}

/* Deferred: the next call tells a consolidation (write_bulk(0)) from a format (anything else). */
bool backing_store_erase(void) {
    g80_wl_erase_pending = true;
    return true;
}

bool backing_store_write(uint32_t address, backing_store_int_t value) {
    if (g80_wl_erase_pending) {
        g80_erase_all();
    }
    if (address >= (WEAR_LEVELING_BACKING_SIZE)) {
        return false;
    }
    if (address >= G80_WL_LOG_START) {
        g80_wl_log_end = address + BACKING_STORE_WRITE_SIZE;
    }
    bool ok = g80_prog(G80_WL_BASE + address, &value, 1, ~0u);
    /* second hash word after a safe consolidation: done, clear the state */
    if (address == (WEAR_LEVELING_LOGICAL_SIZE) + 4u && G80_RD(G80_ST) == G80_ST_BEGIN) {
        g80_st_clear();
    }
    return ok;
}

bool backing_store_write_bulk(uint32_t address, backing_store_int_t *values, size_t item_count) {
    if (address == 0 && g80_wl_erase_pending) {
        return g80_consolidate(values);
    }
    for (size_t i = 0; i < item_count; ++i) {
        if (!backing_store_write(address + (i * BACKING_STORE_WRITE_SIZE), values[i])) {
            return false;
        }
    }
    return true;
}

bool backing_store_lock(void) {
    if (g80_wl_erase_pending) {
        g80_erase_all(); /* wear_leveling_erase(): format */
    }
    eflStop(&EFLD1);
    return true;
}

bool backing_store_read(uint32_t address, backing_store_int_t *value) {
    if (address >= (WEAR_LEVELING_BACKING_SIZE)) {
        *value = 0;
        return false;
    }
    uint32_t v = ~G80_RD(G80_WL_BASE + address);
    if (g80_wl_trust_main && (address & ~4u) == (WEAR_LEVELING_LOGICAL_SIZE)) {
        v = g80_wl_fake_hash[(address >> 2) & 1u];
    }
    /* playback reads the log in order and stops at the first empty entry: the last log read
     * gives the end */
    if (address >= G80_WL_LOG_START) {
        g80_wl_log_end = v ? address + BACKING_STORE_WRITE_SIZE : address;
    }
    *value = v;
    return true;
}
