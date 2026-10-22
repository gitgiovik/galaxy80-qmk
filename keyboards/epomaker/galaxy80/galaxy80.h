// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include "quantum.h"

/* Keyboard-level custom keycodes. The order is the VIA customKeycodes order
 * (via_json/galaxy80.json): never reorder, only append.
 * G80_SBOOT: enters the ROM ISP bootloader ONLY while Right Shift is held.
 * Default keymap puts it on Fn+Esc, so the full combo is Fn + RShift + Esc. */
enum galaxy80_keycodes {
    G80_SBOOT = QK_KB_0, /* Fn+Esc (+RShift): ROM ISP */
    G80_WLCK,            /* Fn+LWin: Win-lock (GUI and App blocked), stock D1 */
    G80_FRST,            /* Fn+Backspace held 3 s: factory reset, stock D2 */
    G80_COLOR,           /* Fn+\ : next stock palette colour, stock DA */
    G80_BLTG,            /* Fn+T : key backlight off/on, indicators stay, stock 9C */
    G80_BAR,             /* Fn+RShift: next side-bar mode, stock EE (also registers RShift) */
    G80_LOCK,            /* Fn+L : lock screen (Win+L / Mac Ctrl+Cmd+Q), stock 90 */
    G80_DESK,            /* Fn+D : show desktop (Win+D / Mac F11), stock 91 */
    G80_WASD,            /* Fn+G : WASD <-> arrows swap, stock F8-FF */
    G80_ATAB,            /* Fn+Tab : block LAlt+Tab on/off, stock settings byte 2 */
    G80_AF4,             /* Fn+Caps: block LAlt+F4 on/off, stock settings byte 3 */
    G80_SOCD,            /* Fn+S : SOCD / snap tap on/off (last input wins on A/D, W/S, arrows) */
    G80_EDIT,            /* Fn+K : on-board per-key colour editor enter/exit (exit saves) */
    G80_GAME,            /* Fn+Pause: game mode (Win-lock + Alt+Tab/F4 block + SOCD) */
    G80_MCTRL,           /* Mac: Mission Control */
    G80_LNPAD,           /* Mac: Launchpad */
    G80_SIRI,            /* Mac: Siri = Cmd+Space held 500 ms */
    G80_TASK,            /* Win: Task View = Win+Tab */
    G80_FILE,            /* Win: File Explorer = Win+E */
    G80_SNIP,            /* Win: Snip & Sketch = Win+Shift+S */
    G80_KB_SAFE_RANGE,
};

/* Watchdog feed for long operations outside the main loop (flash writes); no-op before start. */
void g80_wdt_kick(void);
/* Deep sleep (g80_lpm.c): watchdog stopped right before, restarted (full init) first on wake. */
void g80_wdt_suspend(void);
void g80_wdt_resume(void);

/* Stops the hardware watchdog, sets the SonixQMK bootloader magic and resets.
 * The early boot hook then jumps to the mask-ROM ISP (0x1FFF0301). */
void galaxy80_fault_to_isp(void) __attribute__((noreturn));

/* G80_FEATURES: the full firmware (RGB engine, Fn functions, side bar, radio, VIA menu). The
 * "minimal" keymap (G80_MINIMAL, RGB_MATRIX_ENABLE = no) is the safety image without any of it. */
#if defined(RGB_MATRIX_ENABLE) && !defined(G80_MINIMAL)
#    define G80_FEATURES 1
#    include "g80_rgb.h"

/* Side-bar modes: "follow the keys" is 0 = the default, then the gradient (the 5 bar entries of
 * the per-key table), the stock modes and the status bar. Order = VIA dropdown. */
enum g80_bar_mode {
    G80_BAR_FOLLOW = 0,   /* the bar shows the main effect */
    G80_BAR_GRADIENT,     /* the bar entries of g80_pk (VIA gradient start / end, painter) */
    G80_BAR_GRAD_FLOW,    /* the same colours, flowing along the bar */
    G80_BAR_WAVE,         /* stock 0: rainbow wave across the 5 LEDs */
    G80_BAR_SPECTRUM,     /* stock 1: whole bar cycles the hue wheel */
    G80_BAR_BREATH,       /* stock 2: breathing, next palette colour every cycle */
    G80_BAR_RED,          /* stock 3..9: fixed palette colours */
    G80_BAR_ORANGE,
    G80_BAR_YELLOW,
    G80_BAR_GREEN,
    G80_BAR_CYAN,
    G80_BAR_BLUE,
    G80_BAR_PINK,
    G80_BAR_STATUS,       /* Caps, Win-lock, NKRO, Mac/Win, layer */
    G80_BAR_OFF,          /* stock 10 */
    G80_BAR_MODES
};

/* Persistent keyboard settings (eeconfig_kb, 32 bits; all zero = defaults). Fields are only ever
 * appended; the layout is part of the EEPROM format. */
typedef union {
    uint32_t raw;
    struct {
        uint8_t  bar_mode : 4;
        bool     win_lock : 1;
        bool     block_alt_tab : 1;
        bool     block_alt_f4 : 1;
        bool     wasd_swap : 1;
        bool     keys_off : 1;      /* G80_BLTG */
        uint32_t socd_on : 1;       /* bit 9: SOCD master switch (G80_SOCD) */
        uint32_t socd_pair_off : 4; /* bits 10..13: 1 = pair excluded. bit0 W/S, bit1 A/D, bit2 Up/Down, bit3 Left/Right */
        uint32_t debounce : 4;      /* bits 14..17: debounce ms 1..10, 0 = default (G80_DEBOUNCE_DEFAULT) */
        uint32_t game_mode : 1;     /* bit 18 */
        uint32_t bar_ind_off : 1;   /* bit 19: 1 = no side-bar status overlay */
        uint32_t reserved20 : 1;    /* bit 20: unused, keeps the positions of the fields below */
        uint32_t deb_eager : 1;     /* bit 21: debounce "eager press" (press immediate, release deferred) */
        uint32_t wl_mode : 2;       /* bits 22..23: wireless channel 0..2 = BT1..3, 3 = 2.4G */
        uint32_t reserved : 8;
    };
} g80_config_t;
_Static_assert(sizeof(g80_config_t) == 4, "g80_config_t must stay 32 bits (eeconfig_kb)");

extern g80_config_t g80_config;

/* Per-key colour table (effect "Per-key", on-board editor, VIA key painter):
 * 2 bytes per LED, 93 LEDs incl. the side bar = 186 bytes (EEPROM: VIA custom-config block).
 *   byte 2n   = hue (QMK 0..255)
 *   byte 2n+1 = saturation (high nibble, 0..15 -> 0..255) | value (low nibble, 0 = off,
 *               1..15 = fraction of the global brightness, 15 = full)
 * Code 0..15 of the on-board editor = 16 presets (0 off, 1..14 hues, 15 white). */
#    define G80_PK_BYTES (G80_LED_COUNT * 2)
#    define G80_PK_PRESETS 16
#    define G80_PK_OFF 0
#    define G80_PK_WHITE 15
#    define G80_PK_VMAX 15 /* highest 4-bit saturation / value nibble */
extern uint8_t g80_pk[G80_PK_BYTES];
extern bool    g80_pk_editing;
rgb_t          g80_pk_rgb(uint8_t led, uint8_t val);

/* Slow input pins (Mac/Win switch, slider, charger) */
typedef struct {
    bool    stable;
    bool    last;
    uint8_t count;
} g80_deb_t; /* stock-style debounced input: `stable` changes after G80_SLOW_DEBOUNCE equal samples */
extern g80_deb_t g80_in_mac, g80_in_slider, g80_in_chg, g80_in_full;
extern bool      g80_full_window; /* end-of-charge green window is running */
static inline bool g80_is_mac(void) {
    return g80_in_mac.stable; /* P0.2 debounced */
}
static inline bool g80_slider_wireless(void) {
    return g80_in_slider.stable; /* P0.7 debounced (true = ON / wireless position) */
}
static inline uint8_t g80_charge_state(void) { /* 0 none, 1 charging, 2 full (green window) */
    if (g80_in_full.stable) {
        return g80_full_window ? 2 : 0;
    }
    return g80_in_chg.stable ? 1 : 0;
}
#endif

/* LED indices (rgb_matrix) used by the indicators */
#define G80_LED_TAB 34
#define G80_LED_CAPS 51
#define G80_LED_G 56
#define G80_LED_LGUI 78
#define G80_LED_FN 82
#define G80_LED_UP 76
#define G80_LED_LEFT 85
#define G80_LED_DOWN 86
#define G80_LED_RIGHT 87
#define G80_LED_BAR0 88
#define G80_LED_BAR_COUNT 5
#define G80_LED_KEYS 88
#define G80_LED_PAUSE 16
#define G80_LED_S 53
