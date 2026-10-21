# Galaxy80 QMK (unofficial)

[![build](https://github.com/gitgiovik/galaxy80-qmk/actions/workflows/build.yml/badge.svg)](https://github.com/gitgiovik/galaxy80-qmk/actions/workflows/build.yml)
![license](https://img.shields.io/badge/license-GPL--2.0--or--later-blue)

Open firmware for the **Epomaker x Feker Galaxy80** keyboard, built on QMK (SonixQMK fork). Wired USB,
2.4G and Bluetooth, per-key RGB, VIA, a painted-colour editor, gaming functions and a battery gauge on the
side bar. Italian summary: [README.it.md](README.it.md).

> **Unofficial project.** It is not affiliated with, endorsed by or supported by Epomaker, Feker,
> Keychron or Sonix. Product names are used only to identify the hardware. Flashing replaces the
> stock firmware, you do it at your own risk, and the authors give no warranty (GPL-2.0-or-later,
> see [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md)). The stock firmware, updater, driver and manual are
> not part of this repository and are not distributed here.

**Read first:** [Is this my board?](#is-this-my-board) and [Risks](#risks). If your board does not match,
do not flash.

## Contents

[Features](#features) - [Is this my board?](#is-this-my-board) - [Risks](#risks) - [Enter the ISP bootloader](#enter-the-isp-bootloader) -
[Flash](#flash-with-sonixflasherc) - [First boot](#first-boot) - [VIA](#via) - [Keys](#key-reference) -
[Wireless and battery](#wireless-and-battery) - [Back to stock](#back-to-stock) - [Build](#build-from-source-and-run-the-emulator) -
[Troubleshooting](#troubleshooting) - [Credits](#credits-contributing-license)

## Features

Every feature is marked by how far it has been checked:

* **Hardware-tested**: ran on the maintainer's keyboard (one board, see "Is this my board?") in the last
  hardware-tested build before the 1.0.0 super stage.
* **Emulator-tested only**: added in the 1.0.0 super stage. It passes the emulator suite (a full-system
  model of the chip, no real keyboard) and a code review, but **nobody has run it on hardware yet**.
  Expect surprises, and report them. The checklist that decides when these lose the label is
  [docs/hardware-test-checklist.md](docs/hardware-test-checklist.md).

### Hardware-tested

| Area | What works |
|---|---|
| Wired USB | 6KRO/NKRO keyboard, media keys, Win/Mac switch (Mac swaps Alt and GUI like the stock firmware) |
| 2.4G and Bluetooth | 2.4G dongle and Bluetooth 1-3: pairing, reconnect, fast typing, media keys, slider OFF returns to USB |
| RGB | effects, brightness, speed, colour, per-key effect, 8-colour stock palette (the effect list was shortened in 1.0.0, so the effects are re-checked as H-2) |
| Side bar | 15 modes (follow keys, gradient, gradient flow, rainbow wave, colour cycle, breathing, 7 solid colours, status, off) and the status overlay (Caps, game mode, layer) |
| Per-key colours | on-board editor (Fn+K), the VIA key painter, persistent colours |
| Gaming | Snap Tap / SOCD (last input wins), WASD <-> arrows swap, game mode, Win lock, Alt+Tab / Alt+F4 blocking, debounce time and eager mode |
| Other Fn functions | Caps Word, lock screen, show desktop, key backlight off, factory reset |
| VIA | keymap editing (3 layers), lighting menu and the "Galaxy80" menu; settings, keymaps and colours persist |
| Recovery | ROM bootloader entry by holding P while plugging the cable in |

(Caps Word, debounce, Win/Mac and factory reset date from earlier hardware-tested stages and are
re-checked as regressions in the checklist.)

### Emulator-tested only (new in 1.0.0)

| Feature | Notes |
|---|---|
| **Deep sleep (EXPERIMENTAL)** | Wireless idle sleep ("Wireless Sleep After", factory default **Never**) and the VIA `Sleep Now` keycode. It follows the stock firmware's register sequence and has bounded waits, but it is the riskiest code in the project: a hang while on battery lasts until the battery is empty (see [Risks](#risks)). Leave it at Never until you have run the sleep tests of the checklist. `Sleep Now` sleeps even with the option at Never |
| Battery gauge | Fn+Space (held) shows the charge on the side bar (20 % per LED, growing animation) and on the number row (10 % per key) |
| Battery policies | low-battery warning style, brightness cap on battery (75 / 50 / 25 %), charge LED when wired, wired idle lights-off timeout (1 / 5 / 10 / 30 min). Critical battery blocks every flash write |
| VIA stability | no flash write inside the USB handler; keymap/macro writes are queued, settings are saved a few seconds after the last change, and every write waits until you are not typing ([docs/stability.md](docs/stability.md)) |
| Paint Apply / Discard | painted colours stay in RAM until you Apply (Fn+Enter in the editor, or the VIA button); Discard (Fn+Delete, or the VIA button) reloads the stored colours; a blinking side-bar LED marks unsaved paint |
| SEC-1 | the stock updater's "enter bootloader" command is honoured only while Fn is physically held ([SECURITY.md](SECURITY.md)) |
| Wireless NKRO | more than 6 simultaneous keys over 2.4G/Bluetooth (VIA option "Wireless NKRO", on by default), system control keys (sleep) over wireless |
| Snap Click modes | Last, First, Second, Neutral input, per pair (A/D, W/S, Left/Right, Up/Down) |
| OS keys | Mission Control, Launchpad, Siri, Task View, File Explorer, Snip, as VIA custom keycodes |
| Home-row mods | Chordal Hold and Flow Tap for `MT()`/`LT()` keys assigned from VIA ("Any"); inert unless you assign such keys |
| Layer Lock | Fn+F13 keeps the Fn layer on until Fn or F13 is pressed again. While locked, plain letter keys act as their Fn functions (toggle settings, switch channels), so typing text is not possible until it is unlocked. The hold actions (Backspace 3 s factory reset, Q/W/E/R 3 s pairing) and the bootloader key (RShift+Esc) need the real Fn key and do nothing while the layer is locked |
| Key override | Shift+Backspace sends Delete (VIA image) |
| QMK magic keycodes | `CL_*`, `AG_*`, `GU_*`, `NK_*`, `GE_*`, `BS_*`, `EC_*` and friends take effect at once and are saved with the settings, except `AG_*` (Alt/GUI swap): the Win/Mac switch sets that again at every boot and switch move; `OS_TOGG`/`OS_ON`/`OS_OFF` and `PDF(n)` are saved the same way; `EE_CLR` runs the factory reset on a single press, no hold (refused on a critical battery) |
| Mouse keys, Repeat Key | assignable from VIA ("Any"); no hardware test is on record for them |
| Brightness to off | Fn+Down steps 100 / 75 / 50 / 25 % / off, Fn+Up brings the lights back |
| Other recovery paths | Esc held while plugging in, Fn+RShift+Esc, the fault handlers that end in the bootloader and the watchdog that restarts a hung keyboard (re-checked by H-0 of the checklist) |
| PC off | LEDs go dark when the PC is switched off but USB power stays |

### Known limits

* Snap Tap pairs are the four fixed pairs; arbitrary key pairs are not supported.
* VIA only, no Vial. VIA works over the cable only (the radio link carries no VIA traffic).
* Not built, for lack of RAM: Key Lock, Dynamic Macros, Combos, Tap Dance, Leader Key, Autocorrect.
  VIA macros (about 184 bytes) work.
* No wake-up from a charger being plugged in, and bytes arriving from the radio module during deep
  sleep are lost (both as in the stock firmware).
* Which end of the side bar is "bottom" for the battery gauge has not been confirmed on hardware.
* The RGB effect list is shorter than the stock firmware's (flash space).
* Whether the host operating system shows a battery percentage for the Bluetooth keyboard is unknown.
* Anti-cheat: some games treat SOCD cleaning and Snap Tap as cheating. Snap Tap is a **manual** switch
  (Fn+S) and is Off after a factory reset; there is no automatic game detection. Switch it off per game.
* Every flash wipes your settings, keymap and colours (see [First boot](#first-boot)).

## Is this my board?

Only one board has been tested. Details and how to check: [docs/is-this-my-board.md](docs/is-this-my-board.md).

| Check | Supported |
|---|---|
| Keyboard | Epomaker x Feker **Galaxy80**, wired + 2.4G + Bluetooth, slider and Win/Mac switch |
| Stock USB ID (before flashing) | **05AC:024F** (05AC is the vendor ID registered to Apple, which the stock firmware reuses; some tools therefore list the keyboard as "Apple") |
| Chip | SN32F248BF (Cortex-M0, 64 KiB flash) |
| PCB | marking "galaxy80-RGB-HFD", date code 20231124 (you do not need to open the case; do not open it on our account) |
| Stock firmware | **v1.10 only**. No other version has been tested |

If anything differs (other USB ID, other firmware version, other PCB marking, a clone with the same
look): **do not flash**. Open a [hardware-revision issue](https://github.com/gitgiovik/galaxy80-qmk/issues/new?template=hardware-revision.yml) with
the details it asks for. After flashing this firmware the keyboard enumerates as **4758:0080**
("Galaxy80 QMK (unofficial)").

## Risks

* **Recovery exists but is not magic.** The chip's mask-ROM bootloader (ISP) cannot be overwritten by
  any firmware, and it is entered by holding a key while plugging the cable in. That works no matter
  what is in flash, **if** the board is really powered off first ([docs/recovery.md](docs/recovery.md)).
  You can always re-flash from there.
* **A flash wipes the user data** (settings, VIA keymap, colours). Save your VIA layout first if you
  want to keep it.
* **Deep sleep is experimental.** If it hangs while the keyboard runs on battery, the watchdog is stopped
  for the duration and the keyboard stays unusable until the battery is empty (or you manage a real power
  cycle). It is not a brick - the bootloader and the flash are untouched - but it is a day of nuisance.
  The option is Never by default; leave it that way until you have tested it.
* **Flash writes** happen only in the `via` image and only inside one 2 KiB band at the very end of
  the flash (0xF7C0 to 0xFFBF), never near the code, the boot flag or the code-protection options. The
  `default`, `debug` and `minimal` images never write flash at run time.
* **Hardware status**: the 1.0.0 additions have not been through a hardware test yet (see Features).
* **Anti-cheat**: see the SOCD note above.
* **Do not use the vendor updater** on this firmware ([docs/recovery.md](docs/recovery.md)).

## Enter the ISP bootloader

You need it to flash and to recover. Summary (full text with checks in [docs/recovery.md](docs/recovery.md)):

1. Slider on **OFF** (wired position). **Unplug the USB cable and wait at least 30 seconds.** With the
   slider ON the battery keeps the chip powered, so re-plugging is not a reset and the key is never read.
2. Hold **P** (or **Esc**) and plug the cable in. Keep holding about 2-3 seconds. All LEDs stay dark.
3. Check the USB ID: **0C45:7040** is the ROM bootloader. (4758:0080 = this firmware, 05AC:024F = stock
   firmware: you are not in the bootloader, repeat from step 1.)

* From a running image of this firmware: **Fn + Right Shift + Esc** (all three).
* While the **stock** firmware is installed, holding P alone may not be enough: hold the whole group of keys
  that share its matrix column (**F10, 0, P, ;, . and Right Alt**) while plugging in. Unplugging without
  keys returns to the stock firmware, so trying is harmless.
* A fault or a halt of this firmware ends in the bootloader by itself; LEDs go off first. A hang ends in a
  normal watchdog restart (into the bootloader only if Esc or P is held at that moment).

## Flash with SonixFlasherC

[SonixFlasherC](https://github.com/SonixQMK/SonixFlasherC) 3.0.0 is the tool the maintainer used. It is not
bundled here. Get the release image from the Releases page of this repository (`galaxy80-vX.Y.Z-via.bin`
for daily use; `-default.bin` is the same without VIA and with no flash writes; `-minimal.bin` is a safety image),
and check it against `SHA256SUMS.txt`. Details: [docs/install.md](docs/install.md).

1. Close every program that talks to the keyboard (vendor driver/updater, VIA, browsers on VIA).
2. Use a data cable straight into the PC (no hub), slider **OFF**, and enter the bootloader as above
   (`0C45:7040`).
3. Work on a **copy** of the image: the flasher edits the file it reads.
4. Read-only check first. It must report an SN32F240B-family chip with 64 KB; if it does not, stop:

       sonixflasher -v 0c45/7040 -i

5. Flash (nothing else; never use the `-o`, `-j`, `-k` or `-r` options and never "Set Code Option"):

       sonixflasher -v 0c45/7040 -f galaxy80-vX.Y.Z-via.bin

   The tool waits 5 s, writes, verifies and returns to user mode. Do not unplug while it writes. If it is
   interrupted, enter the bootloader again and repeat: that is safe.
6. The keyboard re-enumerates as **4758:0080**. If it stays at `0C45:7040`, run
   `sonixflasher -v 0c45/7040 -u` or unplug and plug in without holding any key.

On the maintainer's Windows 11 PC the flasher talked to the keyboard without installing any driver. If
yours cannot find the device, do not install Zadig/WinUSB on anything but the `0C45:7040` device, and
**never** on `05AC:024F` or `4758:0080` (the keyboard would stop typing until the driver is removed).
For Linux and macOS notes see [docs/install.md](docs/install.md).

## First boot

* The first boot after any flash starts from the **factory defaults**: default lighting, Snap Tap Off,
  sleep Never, the default keymap, default key colours. VIA's own EEPROM magic is reset too.
* Type on every key (a key tester helps) and check Fn+F10..F12 (mute, volume).
* Hold Fn+Esc+RShift once to see that the bootloader entry works, then replug without keys.
* Then try the VIA image's features one by one, wired first, wireless after.

## VIA

Details: [docs/via.md](docs/via.md).

1. Open [usevia.app](https://usevia.app) or the VIA desktop app in a Chromium-based browser (Chrome, Edge).
   Enable **Settings > Show Design tab**, then in **Design** load `via_json/galaxy80.json`
   (`galaxy80.json` in the release assets). Authorize the device 4758:0080.
2. **Configure** shows three layers (base, Fn, one free layer), the lighting menu (brightness, effect, speed,
   colour) and the **Galaxy80** menu:

   | Menu | Items |
   |---|---|
   | Gaming | Game Mode, Snap Tap, Snap Tap Mode (Off / Last / First / Second / Neutral), the four pairs, Win Lock, Block Alt Tab, Block Alt F4, Swap WASD and Arrows |
   | Keyboard | Debounce ms (1-10), Eager Debounce, Wireless NKRO, Firmware Version (read only) |
   | Power | Wireless Sleep After (**Never**, 1, 5, 30 min; experimental), Low Battery Warning, Max Brightness on Battery, Charge LED when Wired, Wired Idle Lights Off |
   | Side Bar | Bar Mode, Gradient Start/End, Status Indicators, Key Lights Off |
   | Key Colors | Paint Color, Paint Turns Keys Off, Keyboard Editor, Editor Brush, Reset Key Colors, **Apply Key Colors**, **Discard Changes**, Unsaved Key Colors (read only) |

3. **Painting.** Pick a Paint Color and click keys in VIA (or turn on the Keyboard Editor and press keys on the
   board); the first stroke switches to the Per-key effect and keeps the colours the keys were showing. Nothing is saved while you paint. Press **Apply Key
   Colors** (in the editor: **Fn+Enter**) to store the colours; **Discard Changes** (**Fn+Delete**) goes back to
   the stored ones. Side-bar LED 5 blinks magenta while there is unsaved paint. A power cut or replug with
   unsaved paint loses it - that is the design.
4. The custom keycodes (Safe Boot, Win Lock, Factory Reset, Sleep Now, OS keys, ...) are in VIA's tab for
   custom keycodes; any QMK keycode can be typed on the **Any** key.

## Key reference

Hold **Fn** (between Right Alt and Menu) and press:

| Fn + | Function | Fn + | Function |
|---|---|---|---|
| Esc (with Right Shift held) | ROM bootloader | F1..F12 | stock media keys (My Computer, Browser home, Calculator, Mail, Media select, Stop, Previous, Play, Next, Mute, Vol -, Vol +) |
| Left Win | Win lock (blocks both Win keys and Menu) | Backspace (hold 3 s) | factory reset (7 white flashes, then restart; not while Layer Lock is on) |
| Enter | next effect (Shift: previous) | \ | next palette colour |
| Up / Down | brightness up / down (100, 75, 50, 25 %, off) | Right / Left | effect speed up / down |
| T | key backlight off/on (indicators stay) | Right Shift | next side-bar mode |
| Ins | all LEDs off/on | N | NKRO on/off |
| L / D | lock screen / show desktop | G | WASD <-> arrow keys |
| Tab / Caps Lock | block Left Alt+Tab / Left Alt+F4 | S | Snap Tap on/off |
| K | per-key colour editor on/off | Pause | game mode on/off |
| C | Caps Word | Q / W / E / R | Bluetooth 1 / 2 / 3 / 2.4G (slider ON); hold 3 s to pair |
| F13 | Layer Lock: the Fn layer stays on until Fn or F13 is pressed again (the 3 s holds and RShift+Esc need the real Fn) | Space (hold) | battery gauge |

In the on-board editor every key press cycles that key through 16 presets (off, 14 hues, white); a held
Shift goes backwards; the 1-5 keys with Fn address the five side-bar LEDs. Full list including the confirmation
flashes, indicators and the Mac/Win behaviour: [docs/keys.md](docs/keys.md).

Indicators: bar LED 1 white = Caps Lock, orange = Caps Word; bar LED 2 red = game mode, cyan = Snap Tap;
bar LED 5 = active layer above 0 (red / green / blue) and magenta blink = unsaved paint; the Fn key is red
while charging, green for 60 s once full, and blinks red on a low battery.

## Wireless and battery

Details: [docs/wireless.md](docs/wireless.md), [docs/battery-sleep.md](docs/battery-sleep.md).

* Slider **ON** = wireless: the keyboard stops its USB interface (even with the cable in; the cable then only
  charges) and sends keys through the radio module. Slider **OFF** = wired.
* Fn+Q / W / E = Bluetooth 1 / 2 / 3, Fn+R = 2.4G (green key, Bluetooth blue). **Hold 3 s to pair.** The
  channel key blinks fast while pairing (20 s), slowly while the link is lost (10 s), and stays lit
  for 3 s once connected. After a failed pairing remove the old bond on the host first.
* **Fn+Space (hold)**: battery gauge. The five side-bar LEDs fill from LED 1 (20 % each, the last one shows the
  remainder), green above 50 %, yellow 20-50 %, red below 20 %. The number row mirrors it, 10 % per key.
  While charging the gauge breathes. If the module does not answer (possible while wired) the last known value is shown dimmed, and a blinking white LED 1 means the level is unknown.
* Low battery makes the Fn key blink red (continuously, or five times per 30 s - VIA option). On a **critical**
  battery report no flash write is started and a factory reset is refused until the battery recovers
  or a charger is connected.
* Wireless sleep: a VIA option (Never / 1 / 5 / 30 min), **experimental**, Never by default.

## Back to stock

**Unverified.** In principle the same route works in reverse: enter the bootloader (stock needs the whole
`F10, 0, P, ;, ., Right Alt` group; this firmware needs P or Esc), then flash a stock image you obtained
yourself from the vendor with the same flasher command. The flasher puts the chip in the unprotected
state; the vendor updater can restore the protected state afterwards, which is not required for recovery.
The vendor updater cannot put this firmware into the bootloader, so use the key procedure. The
leftover settings band at the end of flash is harmless to the stock firmware. This path will be marked
verified when checklist item H-R1 passes. This repository does not provide any stock files.

## Build from source and run the emulator

No hardware is needed. You need `bash`, `git`, Python 3.10+, the `qmk` CLI (1.2.0 in CI),
`arm-none-eabi-gcc` **10.3-2021.10** and, for the tests, `pip install -r tests/requirements.txt`.

    tools/build.sh                 # via default minimal debug -> out/*.bin, SHA256SUMS, BUILDINFO.txt
    tools/build.ps1                # Windows wrapper (needs a bash such as Git Bash or MSYS2)
    python tools/check_size.py out/emu
    tools/dev/g80_check.sh OUT_DIR # build all four images and run the whole emulator suite

The build fetches the pinned SonixQMK tree (commit `62973ee48a976c6f7cc6d4f7b198bd48a2fdfe60`). The code
must end below 0xF7C0 and the free RAM must stay at least 512 bytes; the link fails otherwise. See
[docs/building.md](docs/building.md) and [docs/testing.md](docs/testing.md).

## Troubleshooting

| Symptom | Try |
|---|---|
| `0C45:7040` does not appear | slider OFF, unplug, wait 30 s, hold **P** (stock: the whole column group) *before* plugging, hold 3 s, direct port, other cable |
| The flasher does not find the device | confirm `0C45:7040`; close other programs; do not install drivers on any other device |
| Keyboard dead after a Zadig install | wrong device chosen: Device Manager > uninstall the driver for the device and replug |
| VIA does not connect | Chromium-based browser only; close other VIA tabs; the cable must be in and the slider OFF (VIA does not run over the radio) |
| Not pairing | slider ON, Fn+Q/W/E/R, hold 3 s while the key blinks; remove the old pairing on the host |
| Does not wake from sleep | press any key; if nothing happens, switch the slider OFF then ON; if still dead, unplug and wait. Then set "Wireless Sleep After" to Never and report it |
| Colours lost after a replug | they were painted but not applied: use Apply Key Colors / Fn+Enter in the editor |
| Everything reset after a flash | expected, see First boot |
| Build says "size" or "free RAM" failed | your change made the image too big; `check_size.py` prints the numbers |

## Credits, contributing, license

* [QMK Firmware](https://qmk.fm) and the [SonixQMK](https://github.com/SonixQMK/qmk_firmware) community, which
  made the SN32F2xx platform.
* Nick Brassel (wear-leveling), Alex Ong and contributors (debounce), Keychron (Snap Click and OS key behaviour),
  see [NOTICE.md](NOTICE.md).
* Issues and pull requests are welcome; use the issue templates. Hardware reports from other
  revisions are especially useful. Never attach or link vendor firmware.
* License: GPL-2.0-or-later for the sources; released binaries are distributed under GPL-3.0
  (see [NOTICE.md](NOTICE.md)). Security reports: [SECURITY.md](SECURITY.md). Changes: [CHANGELOG.md](CHANGELOG.md).
