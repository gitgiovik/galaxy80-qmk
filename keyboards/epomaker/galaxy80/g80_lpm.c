// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Wireless deep-sleep countdown, deep sleep and LVD (see g80_lpm.h for the behaviour). The register
// sequence of the deep sleep follows the stock firmware v1.10 (sleep entry @0x0244..0x02F0, wake
// sources @0x0A04, clock to ILRC @0x0BA0, clock back to IHRC @0x662A, LVD @0x06C4); the waits are
// bounded here (stock waits forever) and the watchdog restarts first on wake.

#include "g80_lpm.h"

#ifdef G80_FEATURES

#    include <hal.h>
#    include "quantum.h"
#    include "timer.h"
#    include "usb_main.h"
#    include "g80_fn.h"
#    include "g80_indicators.h"
#    include "g80_radio.h"
#    include "g80_rgb.h"
#    include "g80_settings.h"

/* SYS0_LVDCTRL: LVDEN | LVDRSTEN | LVDRSTLVL = 2 (reset at 2.40 V), the stock value. LVD only
 * resets, it never enters ISP: the boot and recovery paths are unchanged. */
#    define G80_LVDCTRL_VALUE 0xC002u

/* Countdown reload values, 100 ms ticks (stock @0x422C). */
#    define G80_CD_UNLINKED 150u /* 15 s: wireless, not linked; also 5B 33 and 5B 22 */
#    define G80_CD_RETRY 10u     /* a guard failed at zero: try again in 1 s */
/* "deep sleep after" codes: never / 5 / 10 / 30 / 60 min */
static const uint16_t g80_cd_opt[G80_DEEP_SLEEP_OPTS] = {0, 3000, 6000, 18000, 36000};

/* A key held with no other key event for this long no longer keeps the keyboard awake. */
#    ifndef G80_LPM_HELD_MS
#        define G80_LPM_HELD_MS 600000u
#    endif

/* Upper bound of every wait for a clock; on timeout the MCU resets. At the ILRC clock (8 kHz HCLK,
 * ~7 cycles per iteration) the bound is about 57 s, at 48 MHz about 10 ms. */
#    define G80_LPM_WAIT_MAX 0xFFFFu

#    define G80_ROWS_P3 0x0FC0u        /* P3.6-11 */
#    define G80_SLIDER_P0 (1u << 7)    /* P0.7 */
#    define G80_USB_CLKEN (1u << 4)    /* SYS1 AHBCLKEN.USBCLKEN */
#    define G80_USB_CFG_PHY_ESD 0xB7FFFFFFu /* USB_CFG: PHY_EN (bit 30) and ESD_EN (bit 27) cleared */
#    define G80_SYSCLKST_MASK 0x70u        /* CLKCFG.SYSCLKST: 0 = IHRC, 1 = ILRC */
#    define G80_SYSCLKST_ILRC 0x10u
#    define G80_FLASH_LP(v) (0x5AFA0000u | (v))

enum { G80_G_OK = 0, G80_G_MODE, G80_G_USB, G80_G_KEYS, G80_G_PAIR, G80_G_FRST, G80_G_SAVE, G80_G_TX };

uint16_t        g80_lpm_countdown;
g80_lpm_stats_t g80_lpm_stats;

static bool     g80_lpm_ready;
static bool     g80_lpm_was_wl;
static bool     g80_lpm_keys;     /* a key is down in the debounced matrix */
static bool     g80_lpm_cleared;  /* held keys already released on the host */
static uint16_t g80_lpm_tick_t;
static uint32_t g80_lpm_key_t;    /* last matrix change */
static uint32_t g80_lpm_act;      /* last_matrix_activity_time() seen */

static bool g80_lpm_linked(void) {
    return g80_radio_stats.link == G80_LINK_CONNECTED;
}

/* Stock reload (@0x422C): not linked -> 15 s; linked -> the option time. */
static void g80_lpm_reload(void) {
    const uint8_t opt = (uint8_t)g80_ucfg.deep_sleep;
    g80_lpm_countdown = g80_lpm_linked() ? g80_cd_opt[opt < G80_DEEP_SLEEP_OPTS ? opt : 0] : G80_CD_UNLINKED;
}

void g80_lpm_radio_status(uint8_t code) {
    switch (code) {
        case 0x22: /* battery critical: sleep soon (the countdown still needs the option on) */
            g80_lpm_countdown = G80_CD_UNLINKED;
            break;
        case 0x32: /* linked (already set by the parser): the option time */
            g80_lpm_reload();
            break;
        case 0x33: /* link lost */
            g80_lpm_countdown = G80_CD_UNLINKED;
            break;
        default:
            break;
    }
}

void g80_lpm_matrix_scan(void) {
    bool any = false;
    for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
        any |= matrix_get_row(r) != 0;
    }
    g80_lpm_keys = any;
}

/* First failed guard, or G80_G_OK. `held`: the 10-min held-key case, the matrix guard is skipped. */
static uint8_t g80_lpm_guard(bool held) {
    if (!g80_wl_active() || !g80_slider_wireless() || !g80_raw_slider_now()) {
        return G80_G_MODE;
    }
    if (USBD1.state != USB_STOP) {
        return G80_G_USB;
    }
    if (g80_lpm_keys && !held) {
        return G80_G_KEYS;
    }
    if (g80_radio_pairing()) {
        return G80_G_PAIR;
    }
    if (g80_frst_running) {
        return G80_G_FRST;
    }
    /* a save that is only postponed by the critical-battery inhibit must not keep the keyboard awake
     * until the cell is flat; the data stays in RAM across the sleep. The inhibit (I-F5) is owned by
     * g80_battery.c: set by 5B 22, cleared by 5B 23 or the charger. */
    if (g80_save_pending() && !g80_save_inhibited()) {
        return G80_G_SAVE;
    }
    if (!g80_radio_idle()) {
        return G80_G_TX;
    }
    return G80_G_OK;
}

/* Wake-source interrupts: P3 (rows, IRQ28) and P0 (slider, IRQ31). The sleep path keeps PRIMASK
 * set from arming to disarming and clears both pending bits, so these are never taken. If one ever
 * is (an IRQ left enabled), the weak default handler would end in the fault path, i.e. in the ROM
 * ISP, a dead keyboard on battery until it is re-flashed. Instead both sources are disarmed and the
 * keyboard carries on (one handler for both vectors). */
void VectorB0(void) {
    SN_GPIO3->IE  = 0;
    SN_GPIO3->IC  = 0xFFFF;
    SN_GPIO0->IE  = 0;
    SN_GPIO0->IC  = 0xFFFF;
    NVIC->ICER[0] = (1u << P3_IRQn) | (1u << P0_IRQn);
}
void VectorBC(void) __attribute__((alias("VectorB0")));

/* A wait that did not end: reset (the boot path is unchanged, no flash write, no ISP magic). */
static __attribute__((noreturn, noinline)) void g80_lpm_fail(void) {
    NVIC_SystemReset();
    while (1) {
    }
}

/* Bounded wait until (*reg & mask) == want; on timeout the MCU resets. */
static __attribute__((noinline)) void g80_lpm_wait(const volatile uint32_t *reg, uint32_t mask, uint32_t want) {
    for (uint32_t n = 0; (*reg & mask) != want; n++) {
        if (n >= G80_LPM_WAIT_MAX) {
            g80_lpm_fail();
        }
    }
}

/* The deep sleep itself, interrupts masked from the first register write to the last. Returns after
 * the wake (or after an abort, with everything restored). */
static __attribute__((noinline)) void g80_lpm_sleep(void) {
    chSysLock();
    /* 1. LEDs off (selects LOW, columns HIGH), CT16B1 stopped and its clock gated */
    g80_engine_stop();
    /* 2. watchdog and SysTick stopped; a pending SysTick is kept for later (it would end the WFI) */
    g80_wdt_suspend();
    const uint32_t st_ctrl = SysTick->CTRL;
    SysTick->CTRL          = 0;
    const bool st_pend     = (SCB->ICSR & SCB_ICSR_PENDSTSET_Msk) != 0;
    SCB->ICSR              = SCB_ICSR_PENDSTCLR_Msk;
    /* only the wake sources may end the WFI */
    const uint32_t iser = NVIC->ISER[0];
    NVIC->ICER[0]       = iser;
    /* 3. wake sources: any edge on the rows P3.6-11 (stock @0x0A04) and on the slider P0.7 (stock
     * never enables P0 interrupts) */
    SN_GPIO3->IS  = 0;
    SN_GPIO3->IBS = G80_ROWS_P3;
    SN_GPIO3->IEV = 0;
    SN_GPIO3->IC  = 0xFFFF;
    SN_GPIO3->IE  = G80_ROWS_P3;
    NVIC_ClearPendingIRQ(P3_IRQn);
    NVIC_EnableIRQ(P3_IRQn);
    SN_GPIO0->IS &= ~G80_SLIDER_P0;
    SN_GPIO0->IBS |= G80_SLIDER_P0;
    SN_GPIO0->IC = G80_SLIDER_P0;
    SN_GPIO0->IE |= G80_SLIDER_P0;
    NVIC_ClearPendingIRQ(P0_IRQn);
    NVIC_EnableIRQ(P0_IRQn);
    /* 4. USB PHY off, then the system clock to ILRC (stock @0x0BA0); the USB registers need their
     * AHB clock, which is off while the radio is the transport (usbStop) */
    const uint32_t ahb = SN_SYS1->AHBCLKEN;
    SN_SYS1->AHBCLKEN  = ahb | G80_USB_CLKEN;
    SN_USB->CFG &= G80_USB_CFG_PHY_ESD;
    for (uint32_t i = 200u; i; i--) { /* ~20 us settle, as stock */
        __NOP();
    }
    SN_SYS0->CLKCFG = 1;
    g80_lpm_wait(&SN_SYS0->CLKCFG, G80_SYSCLKST_MASK, G80_SYSCLKST_ILRC);
    SN_SYS0->AHBCP    = 2;
    SN_FLASH->LPCTRL  = G80_FLASH_LP(2u);
    SN_SYS0->ANBCTRL  = 0; /* IHRC off */
    SN_USB->CFG       = 0;
    SN_SYS1->AHBCLKEN = ahb;
    /* 5. deep sleep, only if the wake sources read back armed (else nothing could wake us) */
    const bool armed = (NVIC->ISER[0] & (1u << P3_IRQn)) && SN_GPIO3->IE == G80_ROWS_P3 && (NVIC->ISER[0] & (1u << P0_IRQn)) && (SN_GPIO0->IE & G80_SLIDER_P0);
    if (armed) {
        SN_PMU->CTRL = 2;
        __WFI();
        g80_lpm_stats.sleeps++;
    } else {
        g80_lpm_stats.aborts++;
    }
    SN_PMU->CTRL = 0;
    /* 6. clock back to IHRC 48 MHz (stock @0x662A): flash wait states first */
    SN_FLASH->LPCTRL = G80_FLASH_LP(4u);
    SN_FLASH->LPCTRL = G80_FLASH_LP(5u);
    SN_SYS0->ANBCTRL = 1;
    g80_lpm_wait(&SN_SYS0->CSST, 1u, 1u);
    SN_SYS0->CLKCFG = 0;
    g80_lpm_wait(&SN_SYS0->CLKCFG, G80_SYSCLKST_MASK, 0u);
    SN_SYS0->AHBCP &= ~7u;
    /* 7. watchdog first, then SysTick */
    g80_wdt_resume();
    SysTick->CTRL = st_ctrl;
    if (st_pend) {
        SCB->ICSR = SCB_ICSR_PENDSTSET_Msk;
    }
    /* 8. wake sources off and cleared, the other interrupts back */
    SN_GPIO3->IE = 0;
    SN_GPIO3->IC = 0xFFFF;
    NVIC_DisableIRQ(P3_IRQn);
    NVIC_ClearPendingIRQ(P3_IRQn);
    SN_GPIO0->IE &= ~G80_SLIDER_P0;
    SN_GPIO0->IC = G80_SLIDER_P0;
    NVIC_DisableIRQ(P0_IRQn);
    NVIC_ClearPendingIRQ(P0_IRQn);
    /* 9. stale UART input dropped before the UART interrupt is back; channel key steady for 3 s */
    g80_radio_on_wake();
    g80_ind_channel_show();
    NVIC->ISER[0] = iser;
    /* LED engine and key scan (the matrix keeps its pre-sleep state until the first new pass) */
    g80_engine_start();
    chSysUnlock();
}

static void g80_lpm_try(bool held) {
    if (held && !g80_lpm_cleared) {
        /* keys held for G80_LPM_HELD_MS: release them on the host first (empty report), they stay
         * masked until released (no new key event while they are down) */
        clear_keyboard();
        g80_lpm_cleared = true;
        return; /* the report goes out first; tried again next tick */
    }
    const uint8_t g = g80_lpm_guard(held);
    g80_lpm_stats.guard = g;
    if (g != G80_G_OK) {
        g80_lpm_stats.blocked++;
        if (g80_lpm_countdown == 0) {
            g80_lpm_countdown = G80_CD_RETRY;
        }
        return;
    }
    g80_lpm_sleep();
    g80_lpm_key_t = timer_read32();
    g80_lpm_reload();
}

void g80_lpm_task(void) {
    if (!g80_lpm_ready) {
        g80_lpm_ready    = true;
        SN_SYS0->LVDCTRL = G80_LVDCTRL_VALUE; /* once (stock @0x06C4) */
        g80_lpm_act      = last_matrix_activity_time();
    }
    /* any debounced matrix change (key events, and the keys the on-board editor swallows) */
    const uint32_t act = last_matrix_activity_time();
    if (act != g80_lpm_act) {
        g80_lpm_act     = act;
        g80_lpm_key_t   = timer_read32();
        g80_lpm_cleared = false;
        g80_lpm_reload();
    }
    const bool wl = g80_wl_active();
    if (wl != g80_lpm_was_wl) {
        g80_lpm_was_wl = wl;
        g80_lpm_reload();
    }
    if (!wl || timer_elapsed(g80_lpm_tick_t) < G80_LPM_TICK_MS) {
        return;
    }
    g80_lpm_tick_t = timer_read();
    if (g80_ucfg.deep_sleep == 0 || g80_radio_pairing()) {
        return;
    }
    if (g80_lpm_keys) {
        if (timer_elapsed32(g80_lpm_key_t) >= G80_LPM_HELD_MS) {
            g80_lpm_try(true);
        }
        return;
    }
    if (g80_lpm_countdown && --g80_lpm_countdown == 0) {
        g80_lpm_try(false);
    }
}

#endif /* G80_FEATURES */
