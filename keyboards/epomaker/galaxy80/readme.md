# Epomaker x Feker Galaxy80 (unofficial QMK port)

SONiX SN32F248BF (Cortex-M0, 64 KiB flash), 17x6 matrix, per-key RGB and a 5-LED side bar. Wired USB, Bluetooth 1-3
and 2.4G through the radio module on UART2.

* Keyboard maintainer: gitgiovik
* Hardware supported: Epomaker x Feker Galaxy80, PCB "galaxy80-RGB-HFD" 20231124, stock firmware v1.10
* USB ID: `4758:0080` (not the stock `05AC:024F`), product string "Galaxy80 QMK (unofficial)"
* Bootloader: SONiX ROM ISP (`0C45:7040`), entered by holding **P** or **Esc** while plugging in the cable, or with
  **Fn + Right Shift + Esc** from the running firmware. The ISP cannot be overwritten, so a bad flash is recoverable.

Unofficial, not affiliated with Epomaker, Feker, Keychron or Sonix. Flashing replaces the stock firmware: read
the repository README and `docs/recovery.md` first. This directory builds against SonixQMK
(`62973ee48a976c6f7cc6d4f7b198bd48a2fdfe60`); `tools/build.sh` in the repository root fetches that tree.

## Keymaps

| Keymap | Content |
|---|---|
| `via` | full firmware with VIA; settings, keymap and key colours persist in the flash band 0xF7C0..0xFFBF (helper sectors 0xF6C0..0xF7BF) |
| `default` | the same without VIA; settings in RAM only (no flash writes at run time) |
| `minimal` | safety image: plain USB keyboard, LEDs off, no RGB / radio code |
| `debug` | `default` plus the QMK console (raw matrix, LED engine, radio link counters) |

Make example (inside a SonixQMK tree with this directory in `keyboards/epomaker/galaxy80`):

    qmk compile -kb epomaker/galaxy80 -km via

The VIA definition to sideload is `via_json/galaxy80.json` (lighting menu and a "Galaxy80" menu).

## Source files

| File | Content |
|---|---|
| `galaxy80.c/.h` | board bring-up, early ISP key check, watchdog, fault handlers, USB host-lost logic, QMK hooks, SEC-1 |
| `g80_rgb.c/.h`, `matrix.c` | LED engine (CT16B1 ISR with the key scan inside), rgb_matrix driver, polled-scan fallback |
| `g80_settings.c/.h` | persistent settings, deferred save and write gate, Win/Mac switch, wireless slider, charger pins |
| `g80_fn.c/.h` | Fn actions and flashes, factory reset, RGB and magic keycodes |
| `g80_gaming.c/.h` | Snap Click / SOCD, WASD swap, game mode, Win lock, Alt+Tab / Alt+F4 blocking |
| `g80_perkey.c/.h` | per-key colour table, paint session (Apply / Discard), on-board editor |
| `g80_bar.c/.h`, `g80_indicators.c/.h` | side-bar modes and gauge, indicator pass |
| `g80_battery.c/.h` | battery events, brightness cap, lights-off timeout (wired and wireless) |
| `g80_lpm.c/.h` | wireless deep-sleep countdown, deep sleep (experimental), low-voltage reset |
| `g80_radio.c/.h` | radio module protocol and the wireless mode |
| `g80_via.c/.h` | VIA custom channel, key painter, deferred keymap writes |
| `g80_debounce.c`, `g80_wear_leveling.c` | runtime debounce, flash backing store for the VIA EEPROM |
| `g80_test_hooks.h`, `g80_layers.h`, `rgb_matrix_kb.inc` | symbols for the emulator tests, shared layers, the Per-key effect |

## Documentation

Everything else is in the repository: `README.md` (features and their test status, install, VIA, keys), and under `docs/`:
`install.md`, `recovery.md`, `is-this-my-board.md`, `keys.md`, `via.md`, `wireless.md`, `battery-sleep.md`,
`stability.md`, `building.md`, `testing.md`, `hardware-test-checklist.md` and `protocol/radio-uart.md`.

Deep sleep (the "Deep Sleep After" option, wireless only) is experimental and has passed the emulator tests only; the
default is Never. The lights-off timeout ("Lights Off After", wired and wireless) defaults to 5 minutes.
