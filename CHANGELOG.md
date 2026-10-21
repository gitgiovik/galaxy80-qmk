# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow semantic versioning.

"Hardware-tested" below means: run on a real keyboard by the maintainer. Everything marked *emulator only* passes the
emulator suite but has not been tried on hardware yet (see `docs/hardware-test-checklist.md`).

## [1.0.0] - not yet released

First public version: the "super stage" on top of the hardware-tested wired, 2.4G, Bluetooth, RGB, editor, side-bar,
gaming and VIA firmware (hardware-tested until the stage before it).

### Added (emulator only)

* **Deep sleep (experimental).** Wireless idle sleep with the VIA option "Wireless Sleep After" (Never by default, 1 / 5 / 30 min),
  the `Sleep Now` VIA keycode, wake by any key or by the slider, low-voltage reset at about 2.4 V. Register sequence from the stock
  firmware, bounded waits, watchdog restarted first on wake.
* **Battery gauge**: Fn+Space on the side bar (20 % per LED, growing animation) and the number row (10 % per key).
* Power options in VIA: low-battery warning style, brightness cap on battery, charge LED when wired, wired idle lights-off timeout.
  Critical battery inhibits every flash write and refuses the factory reset.
* **VIA stability**: no flash work in the USB handler, queued keymap and macro writes answered when done, a write gate (no
  writes while typing, while the radio is busy or on a critical battery), a coalesced settings save (3 s after the last change,
  30 s maximum latency, 3 s minimum interval), watchdog feeding during flash cleanups.
* **Paint Apply / Discard**: painted key colours stay in RAM until Apply (Fn+Enter in the editor or the VIA button); Discard
  (Fn+Delete or the VIA button); an unsaved-paint indicator on side-bar LED 5. Leaving the editor no longer saves.
* **SEC-1**: the stock updater's bootloader command is honoured only while Fn is physically held.
* **Wireless**: hybrid NKRO over the radio (VIA option, default on) and system-control keys.
* **Snap Click** modes (Last, First, Second, Neutral) per pair, set in VIA.
* **OS keys**: Mission Control, Launchpad, Siri, Task View, File Explorer, Snip (VIA custom keycodes).
* Chordal Hold and Flow Tap for `MT()`/`LT()` keys assigned in VIA.
* **Layer Lock** on Fn+F13 (the 3 s hold actions, factory reset and pairing, and the bootloader key RShift+Esc need the
  real Fn key and are ignored while locked), and **Shift+Backspace = Delete** (key override, `via` image).
* QMK **magic keycodes** (`CL_*`, `AG_*`, `GU_*`, `NK_*`, `EC_*`...) are applied in RAM at once and saved with the settings
  instead of writing flash inside the key event, and so are `PDF(n)` (persistent default layer; a layer the keymap does not have
  is ignored) and the one-shot switches `OS_TOGG`/`OS_ON`/`OS_OFF`; `EE_CLR` runs the factory reset and is refused on a critical battery.
* Brightness steps down to off and Fn+Up brings the lights back.
* Documentation (`docs/`), build scripts (`tools/build.sh`, `tools/build.ps1`, `tools/fetch_qmk.sh`), CI workflows, issue templates, and the
  emulator test suite with its tools.

### Build notes

* Flash was reclaimed (about 9 KB) by cutting the RGB effect list to 11 effects and restructuring the sources; the `via`
  image keeps about 1.5 KB of flash and about 550 bytes of RAM in reserve.
* The VIA EEPROM layout is version 0xA7 (3 layers, 2 bytes per LED). A new layout or a flash wipes the stored VIA data.
* Builds are reproducible: the build date used by VIA's EEPROM magic is pinned.

### Known limits

See the README. In particular: deep sleep and everything above are emulator only, the back-to-stock route is unverified, and
only one hardware revision (stock USB ID 05AC:024F, stock firmware v1.10) has been tested.
