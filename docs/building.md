# Building from source

The repository contains only the keyboard (`keyboards/epomaker/galaxy80`), tools and tests. The QMK tree it is
built in is fetched at a pinned commit: **SonixQMK/qmk_firmware `62973ee48a976c6f7cc6d4f7b198bd48a2fdfe60`**
(with the ChibiOS, ChibiOS-Contrib, LUFA, printf and V-USB submodule commits listed in `tools/fetch_qmk.sh`). No
patch to the QMK tree is applied.

## Requirements

| Tool | Version |
|---|---|
| bash, git | Git Bash or MSYS2 on Windows |
| Python | 3.10 or newer |
| `qmk` CLI | 1.2.0 (what CI uses); `python -m pip install qmk==1.2.0` |
| `arm-none-eabi-gcc` | **10.3-2021.10** (GNU Arm Embedded). Other versions may build, but the size budget was set with this one; check with `arm-none-eabi-gcc --version` |

## Build

    tools/build.sh                 # all four keymaps
    tools/build.sh via default     # a subset
    tools/build.ps1 -Keymaps via   # Windows wrapper around tools/build.sh

What it does: `tools/fetch_qmk.sh` clones the pinned commit into `third_party/qmk_firmware` (git-ignored), verifies
the commits of the submodules and copies the keyboard directory in; then `qmk compile -kb epomaker/galaxy80 -km <keymap>`
for each keymap. Output in `out/` (override with `G80_OUT`):

    galaxy80_<keymap>.bin   firmware images
    SHA256SUMS              hashes of the .bin files
    BUILDINFO.txt           repository commit, QMK and submodule commits, compiler, build date
    emu/                    the .bin/.elf/.map and nm_*.txt files the emulator tests use
                            (the minimal image is called "stage1" there)

Other variables: `G80_QMK_DIR` (use another QMK checkout), `G80_JOBS` (make jobs), `G80_ENV_SCRIPT` (a shell script that
puts the toolchain on PATH), `G80_BUILD_DATE` (see below). On Windows, `G80_BASH` selects the bash for `build.ps1`.

## Keymaps

| Keymap | Content |
|---|---|
| `via` | full firmware with VIA and persistent settings in 0xF7C0..0xFFBF; Shift+Backspace = Delete |
| `default` | the same without VIA; settings in RAM only, no flash writes at run time |
| `minimal` | safety image: USB keyboard, LEDs off, no RGB or radio, no VIA |
| `debug` | `default` plus the QMK console on a HID interface (raw matrix, LED engine, radio counters); use `tools/g80_console.py` to read it and `tools/g80_matrix_report.py` to analyse a capture. Read-only tools |

## Size and RAM limits

* Code plus initialised data must end **below 0xF7C0**. The linker script `galaxy80_guard.ld` fails the build otherwise.
* Free RAM (the unused ChibiOS heap) must stay at **least 512 bytes**; the link also fails otherwise. The `via` image
  is close to both limits, so adding a feature usually means removing another.
* `python tools/check_size.py out/emu` checks all of it on the built images (also the 65536-byte image size and
  the boot flag 0xAAAA5555 at 0xFFFC) and prints the free bytes.
* `python tests/emulator/static_check.py out/emu via default stage1` checks the images for stray pointers into
  the chip's option area and that the settings band is blank.

## Reproducible builds

QMK stamps the build date into the VIA EEPROM magic. The build scripts pin the date (the commit date of HEAD, or
`G80_BUILD_DATE=YYYY-MM-DD-HH:MM:SS`) so the same sources give the same image; the date inside the firmware is a
fixed constant (`g80_version.h`). Changing that constant, or the EEPROM layout byte `G80_EE_LAYOUT`, makes a new
firmware reset the stored VIA data at the first boot. Releases are built by CI (`.github/workflows`) from a tag with
the same scripts; `BUILDINFO.txt` lists the exact inputs.

## Working on the code

* Keep the free-flash and heap numbers in mind; compare `check_size.py` before and after.
* Keyboard sources are meant to stay upstream-clean, so that they can be proposed to SonixQMK as a plain directory.
* Run the emulator suite ([testing.md](testing.md)) before sending a change. There is no substitute for the hardware
  checklist for anything that touches sleep, flash writes, the radio or the boot path.
* Nothing in this repository may flash a keyboard or talk to one automatically.
