# Notice

This project is an **unofficial** firmware port. It is not affiliated with, endorsed by or supported by
Epomaker, Feker, Keychron, Sonix, Apple or the QMK and VIA projects. Product and company names, and the USB
vendor ID 05AC of the stock firmware, are used only to identify the hardware and are the property of their owners.
The project gives no warranty (see the GPL).

## License

The sources in this repository are licensed under the GNU General Public License, version 2 or (at your
option) any later version (`GPL-2.0-or-later`); the full GPL-2.0 text is in `LICENSE`. This includes the
VIA definition (`keyboards/epomaker/galaxy80/via_json/galaxy80.json`).

**Released firmware binaries are distributed under GPL-3.0.** They link the ChibiOS RT kernel (GPL-3.0)
together with GPL-2.0-or-later (QMK) and Apache-2.0 (ChibiOS HAL, ChibiOS-Contrib) code; all of these can
be combined into a GPL-3.0 work. The corresponding source for a release is the tagged commit of this
repository plus the pinned upstream revisions listed in the release's `BUILDINFO.txt`.

## Upstream components

Built against, not copied into this repository (fetched by `tools/fetch_qmk.sh` at a pinned commit):

| Component | Source | License |
|---|---|---|
| QMK Firmware | https://github.com/qmk/qmk_firmware | GPL-2.0-or-later |
| SonixQMK fork (SN32 platform support) | https://github.com/SonixQMK/qmk_firmware @ `62973ee48a976c6f7cc6d4f7b198bd48a2fdfe60` | GPL-2.0-or-later |
| ChibiOS (RT kernel) | https://github.com/ChibiOS/ChibiOS | GPL-3.0 |
| ChibiOS HAL | same | Apache-2.0 |
| ChibiOS-Contrib (SN32 HAL, SonixQMK branch) | https://github.com/SonixQMK/ChibiOS-Contrib | Apache-2.0 |
| LUFA, printf, V-USB | pinned submodules of the QMK tree | see their own license files |

Thanks to the QMK and SonixQMK communities for the platform this port stands on.

## Third-party code in this repository

- Wear-leveling driver: derived from QMK's wear-leveling code by Nick Brassel (GPL-2.0-or-later).
- Debounce (`keyboards/epomaker/galaxy80/g80_debounce.c`): derived from QMK's `sym_defer_pk` and
  `asym_eager_defer_pk` debounce algorithms, Copyright 2017 Alex Ong, 2020 Andrei Purdea, 2021 Simon Arlott
  (GPL-2.0-or-later); the original copyright lines are kept in the file header.
- Keychron-derived code (Keychron `keychron_qmk`, commit `666862cb`,
  `Copyright 2024 @ Keychron (https://www.keychron.com)`, header GPL-2.0-or-later, verified):
  - `keyboards/epomaker/galaxy80/g80_gaming.c` / `g80_gaming.h`: the Snap Click modes (Last, First,
    Second, Neutral) follow the state machine of `keyboards/keychron/common/snap_click/snap_click.c`
    (both keys of a pair held, `last_single_key`, the other key returns on release). Re-expressed on
    per-key hold counters and the `add_key()`/`del_key()` report; the Keychron raw-HID protocol and
    EEPROM block of that file are not used. The original copyright line is kept in the file header.
  - `keyboards/epomaker/galaxy80/g80_fn.c`: the OS keycodes (Mission Control, Launchpad, Siri =
    Cmd+Space held 500 ms, Task View, File Explorer, Snip) follow the behaviour of
    `keyboards/keychron/common/keychron_common.c` (`process_record_keychron_common`,
    `keychron_common_task`). The Windows Snip chord is Win+Shift+S (Keychron's `KC_SNAP` is the Mac
    screenshot chord). The original copyright line is kept in the file header.
  - The brightness-to-off idea (`RGB_MATRIX_BRIGHTNESS_TURN_OFF_VAL` in
    `keyboards/keychron/common/rgb/keychron_rgb.c`) is reimplemented, no code copied.
- VIA: the keyboard definition follows the format of the VIA project (https://www.caniusevia.com);
  VIA is a trademark of its authors, no VIA code is bundled.

## Tools not bundled

[SonixFlasherC](https://github.com/SonixQMK/SonixFlasherC) (GPL-3.0) is only linked from the documentation as
a flashing tool; it is not part of this repository or of the releases. The emulator tests use the
[Unicorn](https://www.unicorn-engine.org/) CPU emulator (GPL-2.0), installed from PyPI and not bundled, and
optionally the VIA definition reader from npm (`@the-via/reader`), also not bundled. The vendor firmware, updater, driver and manual are not distributed
and no hash of them is published as a substitute.
