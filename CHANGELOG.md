# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow semantic versioning.

"Hardware-tested" below means: run on a real keyboard by the maintainer. Everything marked *emulator only* passes the
emulator suite but has not been tried on hardware yet (see `docs/hardware-test-checklist.md`).

## [1.0.0] - not yet released

First public version: the "super stage" on top of the hardware-tested wired, 2.4G, Bluetooth, RGB, editor, side-bar,
gaming and VIA firmware (hardware-tested until the stage before it).

### Fixed (emulator only)

* **Settings sometimes all reset after unplugging** (report of 2026-10-21, `via` image s8 and earlier). When the
  wear-leveling log was full, QMK's consolidation erased the whole settings band before rewriting it; an unplug in that
  window (about 1.2 s, typically right after an Apply or a settings save) left no valid copy and the next boot reset
  everything, VIA keymaps and macros included. `g80_wear_leveling.c` now consolidates sector by sector with two copy
  buffers and a state log in four new sectors at 0xF6C0..0xF7BF and finishes an interrupted consolidation at boot: a cut
  at any point keeps the data (at most the not-yet-logged 5 bytes of the write that filled the log come back old). QMK
  core, the band and the EEPROM layout are unchanged, so the firmware itself forces no reformat. Flashing still wipes the
  stored data: the `via` .bin is a full 64 KiB image padded with 0x00 over 0xF6C0..0xFFBF, so the documented
  `sonixflasher -f` overwrites the band, and the first boot recreates the defaults. **The first flash of this image will
  not keep your current settings, keymap or colours**: save the VIA layout (*Save + Load*) before flashing and load it
  again afterwards (see `docs/install.md`). Code must now end below 0xF6C0.
* Settings save **1 s** after the last change (was 3 s) and at most once per second: an early unplug loses less. The
  per-key Apply writes runs of at most 5 bytes (same log use).

### Added (emulator only)

* **Lights off** after inactivity, wired and wireless: VIA "Lights Off After" (Never / 1 / 5 / 10 / 30 min, **5 min** by
  default). Only the LEDs go dark; any key, any VIA command (not the key tester) or the wired/wireless
  slider lights them again; plugging or unplugging the charger does not.
* **Deep sleep (experimental)**, wireless only: VIA "Deep Sleep After" (Never by default, 5 / 10 / 30 / 60 min from the last
  key, also the keys the colour editor takes), wake by any key or by the slider, low-voltage reset at about 2.4 V. Register
  sequence from the stock firmware, bounded waits, watchdog restarted first on wake.
* **Battery gauge**: Fn+Space on the side bar (20 % per LED).
* Power options in VIA: low-battery warning style, brightness cap on battery, charge LED when wired.
  Critical battery inhibits every flash write and refuses the factory reset.
* **VIA stability**: no flash work in the USB handler, queued keymap and macro writes answered when done, a write gate (no
  writes while typing, while the radio is busy or on a critical battery), a coalesced settings save (1 s after the last change,
  30 s maximum latency, 1 s minimum interval), watchdog feeding during flash cleanups.
* **Paint Apply / Discard**: painted key colours stay in RAM until Apply (Fn+Enter in the editor or the VIA button); Discard
  (Fn+Delete or the VIA button); an unsaved-paint indicator on side-bar LED 5. Leaving the editor no longer saves.
* **SEC-1**: the stock updater's bootloader command is honoured only while Fn is physically held.
* **Wireless**: hybrid NKRO over the radio (VIA option, default on) and system-control keys.
* **Snap Click** modes (Last, First, Second, Neutral) per pair, set in VIA.
* QMK **magic keycodes** (`CL_*`, `AG_*`, `GU_*`, `NK_*`, `EC_*`...) are applied in RAM at once and saved with the settings
  instead of writing flash inside the key event, and so are `PDF(n)` (persistent default layer; a layer the keymap does not have
  is ignored) and the one-shot switches `OS_TOGG`/`OS_ON`/`OS_OFF`; `EE_CLR` runs the factory reset and is refused on a critical battery.
* Brightness steps down to off and Fn+Up brings the lights back.
* Documentation (`docs/`), build scripts (`tools/build.sh`, `tools/build.ps1`, `tools/fetch_qmk.sh`), CI workflows, issue templates, and the
  emulator test suite with its tools.

### Removed

* RGB effects other than **Solid**, **Rainbow** (QMK's rainbow rise, the factory default) and **Per-key**: Breathing, Color
  Cycle, Rainbow Wave, Reactive and the stock replicas Tap Off, Colorful, Flowing and Shuttle. The side-bar modes are unchanged.
* Pre-release features that are not kept: Layer Lock (Fn+F13), Chordal Hold and Flow Tap, the OS keycodes (Mission Control,
  Launchpad, Siri, Task View, File Explorer, Snip), the `Sleep Now` keycode, the growing gauge animation and the number-row
  battery mirror. The VIA custom keycodes are now 14 (Safe Boot .. Game Mode).

### Build notes

* Flash was reclaimed by cutting the RGB effect list to three effects, removing the features above and restructuring the
  sources. The `via` image ends about 5.5 KB below 0xF6C0 (the power-fail-safe consolidation sectors) and has about 680
  bytes of free RAM.
* The VIA EEPROM layout is version 0xA8 (3 layers, 2 bytes per LED, the effect ids and the power options of this version).
  A new layout or a flash wipes the stored VIA data.
* Builds are reproducible: the build date used by VIA's EEPROM magic is pinned.

### Known limits

See the README. In particular: deep sleep and everything above are emulator only, the back-to-stock route is unverified, and
only one hardware revision (stock USB ID 05AC:024F, stock firmware v1.10) has been tested.
