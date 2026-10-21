// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Epomaker x Feker Galaxy80 (SONiX SN32F248BF): board bring-up, safety layers and QMK hooks.
// "minimal" keymap (RGB_MATRIX_ENABLE = no): LEDs forced off, polled scan, USB only. Full images:
// CT16B1 LED engine with the key scan in its ISR (g80_rgb.c), stock functions (g80_fn.c and
// friends), radio link (g80_radio.c). Every minimal-image safety layer below applies to both;
// the LED engine only adds "LEDs off first" (g80_led_shutdown) to the exit paths.
//
// Safety layers implemented here (recovery paths: docs/recovery.md):
//   1. early_hardware_init_pre(): before clocks, RAM init and USB, raw-register check of
//      Esc (c0/r0) and P (c10/r2, the hardware BOOT column). Pressed -> mask-ROM ISP.
//   2. bootmagic (Esc) and G80_SBOOT (Fn+RShift+Esc) -> bootloader_jump().
//   3. hardware watchdog (~8.2 s nominal, >= ~1 s worst case, reset mode) fed from the
//      main loop, the USB suspend loop, every key event and every VIA command.
//      The suspend loop feeds only while the bus is really idle (no SOF frames), so a
//      missed USB wake-up can no longer keep a dead keyboard alive forever.
//   4. HardFault / unhandled IRQ / chSysHalt -> magic + reset -> ROM ISP.
//      (NMI stays with ChibiOS: the ARMv6-M port uses it for context switching.)
//   5. no code-option/CS access, never writes the 0xFFFC flag.
//   6. VIA raw HID: the stock updater magic AA 42 89 5A FF 71 62 CC -> ISP, only while the Fn key
//      is physically held (G80_UPDATER_MAGIC, default on; 0 removes the handler).
//
// Pin facts:
//   columns (strobe, driven HIGH one at a time, others LOW): P2.12..P2.0, P2.15, P1.15, P1.13, P1.12
//   rows (sense, active high, external pull-downs):          P3.11..P3.6
//   P3.7 = external RESET pin when SYS0_EXRSTCTRL.RESETDIS=0 -> never written, never an output.
//   P3.6 = SWCLK while SYS0_SWDCTRL.SWDDIS=0 -> SWD disabled like stock (needed for row 5).
//   LED select lines (active high, stock "all off" = output LOW):
//       P0.8..P0.15, P1.0..P1.4, P1.6..P1.11, P1.14, P3.3

#include "galaxy80.h"
#include "g80_test_hooks.h"
#include <stddef.h>
#include <string.h>
#include <hal.h>
#ifdef RGB_MATRIX_ENABLE
#    include "g80_rgb.h"
#endif
#ifdef G80_FEATURES
#    include "g80_fn.h"
#    include "g80_radio.h"
#    include "g80_settings.h"
#    include "g80_via.h"
#    include "g80_lpm.h"
static void g80_host_task(void);
#endif

/* ------------------------------------------------------------------------- */
/* Pin masks                                                                  */
/* ------------------------------------------------------------------------- */
#define G80_COL_MASK_P2 0x9FFFu   /* P2.0-12, P2.15 */
#define G80_COL_MASK_P1 0xB000u   /* P1.12, P1.13, P1.15 */
#define G80_ROW_MASK_P3 0x0FC0u   /* P3.6-11 */
/* The LED select masks are plain numbers: fault_to_isp() pastes them into assembly. */
#define G80_LED_MASK_P0 0xFF00    /* P0.8-15 */
#define G80_LED_MASK_P1 0x4FDF    /* P1.0-4, P1.6-11, P1.14 */
#define G80_LED_MASK_P3 0x0008    /* P3.3 */

#define G80_ESC_COL_P2 (1u << 12) /* c0  = P2.12 */
#define G80_ESC_ROW_P3 (1u << 11) /* r0  = P3.11 */
#define G80_P_COL_P2 (1u << 2)    /* c10 = P2.2 (BOOT pin) */
#define G80_P_ROW_P3 (1u << 9)    /* r2  = P3.9 */

/* P3 rows P3.6-11: CFG field (2 bits per pin, 00 = pull-up, 10 = no pull) and its "no pull" value. */
#define G80_ROWS_CFG_MASK 0x00FFF000u
#define G80_ROWS_CFG_NOPULL 0x00AAA000u

/* GPIOn_CFG: 2 bits per pin. 00 = pull-up, 10 = no pull (Schmitt on, reset default). */
#define G80_CFG_P0 0xAAAA8000u    /* P0.0-6 pull-up (stock), P0.7 no pull, P0.8-15 outputs */
#define G80_CFG_P1 0xAAAAA2AAu    /* P1.5 pull-up (unused input, stock), rest outputs */
#define G80_CFG_P2 0x82AAAAAAu    /* P2.13, P2.14 pull-up (unused inputs, stock), rest outputs */
#define G80_CFG_P3 0x00AAA280u    /* P3.0-2 pull-up, P3.3 out, P3.4 no pull, P3.5 pull-up,
                                     P3.6-11 no pull (rows have external pull-downs) */

/* ------------------------------------------------------------------------- */
/* Watchdog (SN32F240B datasheet ch. 9: clock = ILRC 32 kHz, /128 fixed)      */
/* timeout = (TC+1) * 128 * 2^PRE / 32000 s                                  */
/* ------------------------------------------------------------------------- */
#ifndef G80_WDT_PRE
#    define G80_WDT_PRE 3 /* /8 -> 32 ms per count */
#endif
#ifndef G80_WDT_TC
#    define G80_WDT_TC 255 /* 256 counts -> ~8.2 s nominal (~1.0 s if PRE not applied) */
#endif
#define G80_WDKEY 0x5AFA0000u
#define G80_WDT_FEED_VALUE 0x55AAu
#define G80_WDT_AHBCLKEN (1u << 24)

static inline void g80_wdt_stop(void) {
    SN_SYS1->AHBCLKEN |= G80_WDT_AHBCLKEN; /* register block clock, needed for the write */
    (void)SN_SYS1->AHBCLKEN;
    SN_WDT->CFG = G80_WDKEY; /* WDTEN=0, WDTIE=0 (reset mode), WDTINT cleared */
}

static inline void g80_wdt_feed(void) {
    SN_WDT->FEED = G80_WDKEY | G80_WDT_FEED_VALUE;
}

static bool g80_wdt_running = false;

/* Feed point for long keyboard-level operations outside the main loop (flash writes in
 * g80_perkey.c / g80_settings.c). Same feed as the main loop; no-op before start. */
void g80_wdt_kick(void) {
    if (g80_wdt_running) {
        g80_wdt_feed();
    }
}

#ifdef G80_FEATURES
/* also used by g80_wdt_resume(): kept inline so the boot start stays in keyboard_post_init_kb */
#    define G80_WDT_START_ATTR inline __attribute__((always_inline))
#else
#    define G80_WDT_START_ATTR
#endif
static G80_WDT_START_ATTR void g80_wdt_start(void) {
    g80_wdt_stop();
    SN_SYS1->APBCP1 = (SN_SYS1->APBCP1 & ~(7u << 20)) | ((uint32_t)G80_WDT_PRE << 20);
    SN_WDT->TC = G80_WDKEY | (uint32_t)G80_WDT_TC;
    g80_wdt_feed();
    SN_WDT->CFG = G80_WDKEY | 1u; /* WDTEN=1, reset mode */
    g80_wdt_running = true;
}

#ifdef G80_FEATURES
/* Deep sleep (g80_lpm.c, interrupts masked): the watchdog runs on ILRC and would reset the sleeping
 * keyboard, so it is stopped right before the clock switch and fully re-initialised first on wake. */
void g80_wdt_suspend(void) {
    g80_wdt_stop();
    g80_wdt_running = false;
}

void g80_wdt_resume(void) {
    g80_wdt_start();
}
#endif

/* ------------------------------------------------------------------------- */
/* Bootloader entry                                                           */
/* ------------------------------------------------------------------------- */
/* Same magic location/value as platforms/chibios/bootloaders/sn32_dfu.c */
#define G80_SYMVAL(sym) (uint32_t)(((uint8_t *)&(sym)) - ((uint8_t *)0))
extern uint32_t __ram0_end__;
#define G80_BOOT_MAGIC 0xDEADBEEF /* plain number: pasted into assembly in galaxy80_fault_to_isp() */
#define G80_MAGIC_ADDR ((volatile uint32_t *)(G80_SYMVAL(__ram0_end__) - 4))

/* Overrides the weak SonixQMK bootloader_jump(): identical, plus watchdog stop. */
void bootloader_jump(void) {
#ifdef RGB_MATRIX_ENABLE
    g80_led_shutdown(); /* LEDs off in hardware and LED ISR stopped, before anything else */
#endif
    /* Release every key on the host first: otherwise keys held at the moment of the jump
     * stay "pressed" in Windows until they are pressed again (seen on 2026-10-17). */
    clear_keyboard();
    wait_ms(30);
    g80_wdt_stop();
    *G80_MAGIC_ADDR = G80_BOOT_MAGIC;
    __DSB();
    NVIC_SystemReset();
}

/* Overrides the weak sn32_dfu.c mcu_reset() (QK_REBOOT, soft reset): stop the watchdog
 * first so a software reset can never leave it running with a stale configuration. */
void mcu_reset(void) {
#ifdef RGB_MATRIX_ENABLE
    g80_led_shutdown();
#endif
    g80_wdt_stop();
    __DSB();
    NVIC_SystemReset();
    while (1) {
    }
}

/* Register addresses the fault path pastes into assembly (no C runtime there); the assertions tie
 * them to the device headers. */
#define G80_STR_(x) #x
#define G80_STR(x) G80_STR_(x)
#define G80_GPIO0_BCLR 0x40044028
#define G80_GPIO1_BCLR 0x40046028
#define G80_GPIO3_BCLR 0x4004A028
#define G80_SYS1_AHBCLKEN 0x4005E000
#define G80_CT16B1_PWMIOENB 0x400020A0
#define G80_SCB_AIRCR 0xE000ED0C
#define G80_SCB_AIRCR_RESET 0x05FA0004 /* VECTKEY | SYSRESETREQ */
_Static_assert(SN_GPIO0_BASE + offsetof(SN_GPIO0_Type, BCLR) == G80_GPIO0_BCLR, "GPIO0 BCLR address");
_Static_assert(SN_GPIO1_BASE + offsetof(SN_GPIO0_Type, BCLR) == G80_GPIO1_BCLR, "GPIO1 BCLR address");
_Static_assert(SN_GPIO3_BASE + offsetof(SN_GPIO3_Type, BCLR) == G80_GPIO3_BCLR, "GPIO3 BCLR address");
_Static_assert(SN_SYS1_BASE + offsetof(SN_SYS1_Type, AHBCLKEN) == G80_SYS1_AHBCLKEN, "SYS1 AHBCLKEN address");
_Static_assert(SN_CT16B1_BASE + offsetof(SN_CT16B1_Type, PWMIOENB) == G80_CT16B1_PWMIOENB, "CT16B1 PWMIOENB address");

/* Fault path: no stack use, no C runtime assumptions.
 * Writes the magic at __ram0_end__-4 and requests a system reset (AIRCR.SYSRESETREQ).
 * The watchdog is reset by the system reset and, defensively, stopped again in the
 * early hook before jumping to the ROM. */
__attribute__((naked, noreturn, used)) void galaxy80_fault_to_isp(void) {
    __asm__ volatile(
        "cpsid i                 \n"
#ifdef RGB_MATRIX_ENABLE
        /* LEDs off without stack: the 21 select lines LOW (BCLR), then the PWM detached from
         * the column pins (CT16B1_PWMIOENB = 0) only if the CT16B1 clock is enabled. */
        "ldr   r0, =" G80_STR(G80_GPIO0_BCLR) "\n"
        "ldr   r1, =" G80_STR(G80_LED_MASK_P0) "\n"
        "str   r1, [r0]          \n"
        "ldr   r0, =" G80_STR(G80_GPIO1_BCLR) "\n"
        "ldr   r1, =" G80_STR(G80_LED_MASK_P1) "\n"
        "str   r1, [r0]          \n"
        "ldr   r0, =" G80_STR(G80_GPIO3_BCLR) "\n"
        "movs  r1, #" G80_STR(G80_LED_MASK_P3) "\n"
        "str   r1, [r0]          \n"
        "ldr   r0, =" G80_STR(G80_SYS1_AHBCLKEN) "\n"
        "ldr   r1, [r0]          \n"
        "lsl   r1, r1, #24       \n" /* bit 7 (CT16B1CLKEN) -> N flag */
        "bpl   2f                \n"
        "ldr   r0, =" G80_STR(G80_CT16B1_PWMIOENB) "\n"
        "movs  r1, #0            \n"
        "str   r1, [r0]          \n"
        "2:                      \n"
#endif
        "ldr   r0, =__ram0_end__ - 4 \n"
        "ldr   r1, =" G80_STR(G80_BOOT_MAGIC) "\n"
        "str   r1, [r0]          \n"
        "dsb                     \n"
        "ldr   r0, =" G80_STR(G80_SCB_AIRCR) "\n"
        "ldr   r1, =" G80_STR(G80_SCB_AIRCR_RESET) "\n" /* VECTKEY | SYSRESETREQ */
        "str   r1, [r0]          \n"
        "dsb                     \n"
        "1: b 1b                 \n"
        ".ltorg                  \n");
}

/* Fault vectors (weak in ChibiOS vectors.S). Cortex-M0 has only NMI and HardFault.
 * NMI_Handler is NOT overridden: the ChibiOS ARMv6-M port (chcore.c) uses the NMI for
 * its context-switch return path. _unhandled_exception catches every exception/IRQ
 * vector that has no driver handler (spurious interrupts). */
__attribute__((naked, noreturn)) void HardFault_Handler(void) {
    /* long branch: with LTO the target can be out of reach of a 16-bit "b" (no stack used) */
    __asm__ volatile("ldr r0, =galaxy80_fault_to_isp \n"
                     "bx  r0                     \n");
}
__attribute__((naked, noreturn)) void _unhandled_exception(void) {
    /* long branch: with LTO the target can be out of reach of a 16-bit "b" (no stack used) */
    __asm__ volatile("ldr r0, =galaxy80_fault_to_isp \n"
                     "bx  r0                     \n");
}

/* ------------------------------------------------------------------------- */
/* Early key check (runs before sn32_clock_init, before .data/.bss init)      */
/* Only locals + peripheral registers. Must stay tiny and self-contained.     */
/* ------------------------------------------------------------------------- */
#define G80_EARLY_SETTLE 400u /* nop-loop iterations (~4-5 cycles each): runs at the post-reset
                               * clock (12 MHz IHRC) -> roughly 130-170 us per settle */
#define G80_EARLY_ROUNDS 8u

static inline __attribute__((always_inline)) void g80_spin(uint32_t n) {
    while (n--) {
        __asm__ volatile("nop");
    }
}

/* Differential check: row must be LOW while every column is driven LOW and HIGH while
 * only the tested column is driven HIGH, for G80_EARLY_ROUNDS consecutive rounds.
 * A stuck-high or floating row therefore never produces a false "pressed". */
static __attribute__((noinline)) bool g80_early_key(uint32_t col_bit_p2, uint32_t row_bit_p3) {
    for (uint32_t i = 0; i < G80_EARLY_ROUNDS; i++) {
        SN_GPIO2->BCLR = col_bit_p2;
        g80_spin(G80_EARLY_SETTLE);
        if (SN_GPIO3->DATA & row_bit_p3) {
            return false;
        }
        SN_GPIO2->BSET = col_bit_p2;
        g80_spin(G80_EARLY_SETTLE);
        if (!(SN_GPIO3->DATA & row_bit_p3)) {
            SN_GPIO2->BCLR = col_bit_p2;
            return false;
        }
    }
    SN_GPIO2->BCLR = col_bit_p2;
    return true;
}

static __attribute__((noreturn)) void g80_jump_to_rom_isp(void) {
    g80_wdt_stop();
    void (*rom_isp)(void) = (void (*)(void))SN32_BOOTLOADER_ADDRESS; /* 0x1FFF0301 */
    rom_isp();
    while (1) {
    }
}

void early_hardware_init_pre(void) {
    /* (a) SonixQMK software request (QK_BOOT, bootmagic, faults) */
    if (*G80_MAGIC_ADDR == G80_BOOT_MAGIC) {
        *G80_MAGIC_ADDR = 0;
        g80_jump_to_rom_isp();
    }

    /* (b) Esc or P held at power-on/reset.
     * Rows P3.6-11: input, no pull (their reset state; set explicitly). P3.7 stays an input.
     * Columns: data LOW first, then output -> all 17 columns driven low (stock scheme). */
    SN_GPIO3->MODE &= ~G80_ROW_MASK_P3;
    SN_GPIO3->CFG = (SN_GPIO3->CFG & ~G80_ROWS_CFG_MASK) | G80_ROWS_CFG_NOPULL;
    SN_GPIO2->BCLR = G80_COL_MASK_P2;
    SN_GPIO1->BCLR = G80_COL_MASK_P1;
    SN_GPIO2->MODE |= G80_COL_MASK_P2;
    SN_GPIO1->MODE |= G80_COL_MASK_P1;
    g80_spin(G80_EARLY_SETTLE);

    bool enter = g80_early_key(G80_ESC_COL_P2, G80_ESC_ROW_P3) || g80_early_key(G80_P_COL_P2, G80_P_ROW_P3);

    /* Back to the reset state (inputs) before the ROM or ChibiOS take over. */
    SN_GPIO2->BCLR = G80_COL_MASK_P2;
    SN_GPIO1->BCLR = G80_COL_MASK_P1;
    SN_GPIO2->MODE &= ~G80_COL_MASK_P2;
    SN_GPIO1->MODE &= ~G80_COL_MASK_P1;

    if (enter) {
        g80_jump_to_rom_isp();
    }
}

/* ------------------------------------------------------------------------- */
/* Board init: called from halInit() -> boardInit(), after palInit().         */
/* Puts every pin in a defined, stock-equivalent state with all LEDs off.     */
/* ------------------------------------------------------------------------- */
void board_init(void) {
    /* SWD off, like stock main (0x574): P3.6 (row 5) and P3.5 become GPIO. */
    SN_SYS0->SWDCTRL_b.SWDDIS = 1;

    /* Data set before switching to output: no glitch.
     * LED selects LOW (stock "all off"). Columns HIGH: this is the stock LED-off idle
     * (CT16B1 PWM mode 1 with MR=0 keeps every column output HIGH = LED column driver
     * off); matrix.c only pulls them into "16 LOW + 1 HIGH" for the few us of a read. */
    SN_GPIO0->BCLR = G80_LED_MASK_P0;
    SN_GPIO1->BCLR = G80_LED_MASK_P1;
    SN_GPIO1->BSET = G80_COL_MASK_P1;
    SN_GPIO2->BSET = G80_COL_MASK_P2;
    SN_GPIO3->BCLR = G80_LED_MASK_P3;

    SN_GPIO0->CFG = G80_CFG_P0;
    SN_GPIO1->CFG = G80_CFG_P1;
    SN_GPIO2->CFG = G80_CFG_P2;
    SN_GPIO3->CFG = G80_CFG_P3;

    SN_GPIO0->MODE = G80_LED_MASK_P0;                   /* 0xFF00 */
    SN_GPIO1->MODE = G80_LED_MASK_P1 | G80_COL_MASK_P1; /* 0xFFDF */
    SN_GPIO2->MODE = G80_COL_MASK_P2;                   /* 0x9FFF */
    SN_GPIO3->MODE = G80_LED_MASK_P3;                   /* 0x0008: rows P3.6-11 inputs */
}

/* ------------------------------------------------------------------------- */
/* QMK hooks                                                                  */
/* ------------------------------------------------------------------------- */
void keyboard_post_init_kb(void) {
#ifdef G80_FEATURES
    g80_features_init(); /* reads eeconfig_kb and the slow pins, no flash write */
#endif
    /* Started after EEPROM/VIA/bootmagic init so a first-boot EEPROM format can never
     * be interrupted by the watchdog. Init-phase hangs are covered by Esc/P at plug-in. */
    g80_wdt_start();
#ifdef G80_FEATURES
    g80_radio_init(); /* UART2 to the radio module */
#endif
    keyboard_post_init_user();
}

void housekeeping_task_kb(void) {
    if (g80_wdt_running) {
        g80_wdt_feed();
    }
#ifdef G80_FEATURES
    g80_features_task();
    g80_host_task();
#endif
#ifdef G80_FEATURES
    g80_radio_task();
    g80_lpm_task(); /* idle countdown and deep sleep, after the radio task (TX state current) */
#endif
    housekeeping_task_user();
}

/* Bootmagic (Esc held, only reached if the early check missed, e.g. a stuck row):
 * the default implementation calls eeconfig_disable(), a flash write in the VIA image,
 * right before jumping. Never write flash on this path. */
void bootmagic_reset_eeprom(void) {}

#ifdef G80_FEATURES
/* No watchdog feed may keep an endless loop alive. QMK's suspend loop
 * (protocol_pre_task: while (USBD1.state == USB_SUSPENDED) suspend_power_down()) has no exit of
 * its own: if the SN32 driver ever missed the bus wake-up, the keyboard would sit in it for good,
 * dead but fed. A host that really suspended the port sends no SOF, so the 11-bit frame number
 * stands still; if it keeps moving for G80_SUSPEND_LIVE_CALLS consecutive calls (~17 ms each)
 * the "suspended" state is stale: stop feeding and let the watchdog reset the keyboard (normal
 * reboot + re-enumeration). Bounded: the counter only grows while frames move. */
#    ifndef G80_SUSPEND_LIVE_CALLS
#        define G80_SUSPEND_LIVE_CALLS 30 /* ~0.5 s of SOF traffic while "suspended" */
#    endif
static uint16_t g80_susp_frame;
static uint8_t  g80_susp_live;

/* Host-lost detection: many PCs keep the USB 5 V on when
 * switched off, so the keyboard stays powered, but the host is gone without the bus being seen as
 * suspended (port reset / left unconfigured, or a reboot with nobody to enumerate it), and QMK's
 * RGB sleep only follows USB suspend. The main loop now wants a live host for the LEDs: SOF frames
 * moving (a running host sends one per ms) or, until frames were ever seen moving, USB_ACTIVE (so a
 * frame register that never moves can not darken a live PC). Host lost for G80_HOST_LOST_MS ->
 * LEDs off through the same path as USB suspend; back on when the host is there again.
 * Wired only: wireless must skip this while the radio is the transport. */
#    ifndef G80_HOST_LOST_MS
#        define G80_HOST_LOST_MS 2000
#    endif
#    define G80_HOST_FRAME_MS 50 /* no frame change for this long = no SOF */
static uint16_t g80_host_frame;
static uint32_t g80_host_frame_t;  /* last frame number change */
static uint32_t g80_host_t;        /* last time the host was seen alive */
static bool     g80_frames_seen;
static bool     g80_host_dark;     /* LEDs switched off here (not by a USB suspend) */

static void g80_host_seen(void) {
    g80_host_t = g80_host_frame_t = timer_read32();
}

static void g80_host_task(void) {
    if (g80_wl_active()) { /* no USB host in wireless: the LEDs follow the radio */
        g80_host_t = timer_read32();
        if (g80_host_dark) {
            g80_host_dark = false;
            rgb_matrix_set_suspend_state(false);
        }
        return;
    }
    if (USBD1.state == USB_STOP) {
        return; /* USB clock off (switching to wireless): no frame register to read */
    }
    const uint16_t f = (uint16_t)usbGetFrameNumberX(&USBD1);
    if (f != g80_host_frame) {
        g80_host_frame   = f;
        g80_host_frame_t = timer_read32();
        g80_frames_seen  = true;
    }
    const bool alive = g80_frames_seen ? timer_elapsed32(g80_host_frame_t) < G80_HOST_FRAME_MS : USBD1.state == USB_ACTIVE;
    if (alive) {
        g80_host_t = timer_read32();
        if (g80_host_dark) {
            g80_host_dark = false;
            rgb_matrix_set_suspend_state(false);
        }
    } else if (timer_elapsed32(g80_host_t) >= G80_HOST_LOST_MS && !rgb_matrix_get_suspend_state()) {
        /* also after a USB suspend/wake-up cycle lit them again while the host is still gone */
        g80_host_dark = true;
        rgb_matrix_set_suspend_state(true);
    }
}

void suspend_power_down_kb(void) {
    g80_wl_suspend_check(); /* slider ON while the PC sleeps: leave the loop for wireless */
    if (USBD1.state == USB_STOP) {
        if (g80_wdt_running) {
            g80_wdt_feed();
        }
        return; /* just stopped: the loop ends, no frame register to read with the USB clock off */
    }
    const uint16_t f = (uint16_t)usbGetFrameNumberX(&USBD1);
    if (f != g80_susp_frame) {
        g80_susp_frame = f;
        if (g80_susp_live < 255) {
            g80_susp_live++;
        }
    } else {
        g80_susp_live = 0;
    }
    if (g80_wdt_running && g80_susp_live < G80_SUSPEND_LIVE_CALLS) {
        g80_wdt_feed();
    }
    suspend_power_down_user();
}

void suspend_wakeup_init_kb(void) {
    g80_susp_live = 0;
    g80_host_seen();
    suspend_wakeup_init_user();
}
#else
void suspend_power_down_kb(void) {
    /* USB suspend loop (platforms/chibios/suspend.c) does not run housekeeping. */
    if (g80_wdt_running) {
        g80_wdt_feed();
    }
    suspend_power_down_user();
}
#endif

void post_process_record_kb(uint16_t keycode, keyrecord_t *record) {
    /* Long actions (macros, send_string, VIA dynamic-keymap reset) run inside one key
     * event: feed once more when it finishes. */
    if (g80_wdt_running) {
        g80_wdt_feed();
    }
    post_process_record_user(keycode, record);
}

#ifdef G80_FEATURES
/* Debounced matrix after every scan: the idle countdown needs to know whether a key is down. */
void matrix_scan_kb(void) {
    g80_lpm_matrix_scan();
    matrix_scan_user();
}
#endif

bool process_record_kb(uint16_t keycode, keyrecord_t *record) {
    if (g80_wdt_running) {
        g80_wdt_feed();
    }
#ifdef G80_FEATURES
    /* every key event reloads the idle countdown; G80_SLEEP is handled there */
    if (!g80_lpm_process_record(keycode, record)) {
        return false;
    }
#endif
    if (!process_record_user(keycode, record)) {
        return false;
    }
#ifdef G80_FEATURES
    if (!g80_features_process_record(keycode, record)) {
        return false;
    }
#endif
    switch (keycode) {
        case G80_SBOOT:
            /* Fn layer locked: Esc is G80_SBOOT and Right Shift is G80_BAR (also a Right Shift), so a
             * plain RShift+Esc (Ctrl+Shift+Esc) would enter the ISP: needs the real Fn (it unlocks) */
            if (record->event.pressed && !G80_LAYER_LOCKED() && (get_mods() & MOD_BIT(KC_RSFT))) {
                reset_keyboard(); /* -> bootloader_jump() */
            }
            return false;
        default:
            return true;
    }
}

#ifdef VIA_ENABLE
#    ifndef G80_UPDATER_MAGIC
#        define G80_UPDATER_MAGIC 1
#    endif
#    if G80_UPDATER_MAGIC
/* Stock HFD updater magic (per stock updater v1.10). Accepted only as the first
 * 8 bytes of a VIA raw HID packet; no stock command id starts with 0xAA.
 * SEC-1: any host program can send it, and the ROM ISP then accepts any firmware, so it is honoured
 * only while the Fn key is physically held (debounced matrix, Fn position of the PCB, not the
 * keymap); otherwise the packet goes on to VIA (answered id_unhandled). The physical ISP paths
 * (Esc / P at plug-in, Fn+RShift+Esc, bootmagic) are not affected. */
static const uint8_t g80_isp_magic[8] = {0xAA, 0x42, 0x89, 0x5A, 0xFF, 0x71, 0x62, 0xCC};
#        define G80_FN_ROW 5
#        define G80_FN_COL 11
#    endif

bool via_command_kb(uint8_t *data, uint8_t length) {
    if (g80_wdt_running) {
        g80_wdt_feed(); /* one feed per VIA command */
    }
#    if G80_UPDATER_MAGIC
    if (length >= sizeof(g80_isp_magic) && memcmp(data, g80_isp_magic, sizeof(g80_isp_magic)) == 0 && (matrix_get_row(G80_FN_ROW) & ((matrix_row_t)1 << G80_FN_COL))) {
        reset_keyboard();
        return true;
    }
#    endif
#    ifdef G80_FEATURES
    return g80_via_command(data, length); /* no flash write in the RAW HID handler (g80_via.c) */
#    else
    return false;
#    endif
}
#endif
