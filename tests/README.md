# Emulator test suite

The firmware is tested without hardware: `tests/emulator/g80emu.py` is a full-system model of the
SN32F248B (Cortex-M0, built on [Unicorn](https://www.unicorn-engine.org/)). It runs a built image from
the reset vector through ChibiOS and QMK init, models the USB device, matrix, LED drivers, flash/EEPROM,
watchdog and the radio UART, and the tests drive keys, VIA/HID traffic and simulated radio frames
against it. Nothing here talks to a real keyboard.

## Requirements

- Python 3.10+ and `pip install -r tests/requirements.txt` (unicorn 2.1.4, pinned).
- The built images (`tools/build.sh`, needs `arm-none-eabi-gcc` 10.3 and the `qmk` CLI) and
  `arm-none-eabi-objdump` for a few tests.
- Optional: Node.js plus `npm install` in `tests/emulator/via_reader/` for the check that VIA's own
  definition reader (`@the-via/reader`) accepts `via_json/galaxy80.json` (part of the `S4-JSON` checks).
- RAM: every emulator instance commits about 1 GiB, so run at most 2-4 processes in parallel.

## Running

Everything (build the four images, then run all test processes in parallel):

    tools/dev/g80_check.sh OUT_DIR              # -t build only, -T tests only (images already in OUT_DIR/fw)

The toolchain locations come from `G80_QMK`, `G80_GCC_BIN`, `G80_MSYS_BASH`, `G80_QMK_ENV` (see the header
of the script); put them in the git-ignored `tools/dev/local.env`. `G80_JOBS` sets the parallelism.
`OUT_DIR/SUMMARY.txt` has sizes, heap, PASS/FAIL per file and every FAIL line.

Single files, after `tools/build.sh` (images in `out/emu`):

    export G80_KBD=$PWD/keyboards/epomaker/galaxy80      # default: derived from the repo layout
    export G80_OBJDUMP=arm-none-eabi-objdump
    cd tests/emulator
    python -u run_tests.py out/emu out/t --block=A       # blocks A B C D E F E-L1 .. E-L16 S3 S4
    python -u test_features.py out/emu out/t             # = run_tests.py --block=S3
    python -u suite_magic.py out/emu

Every test prints `PASS ...` / `FAIL ...` lines and exits 0 when it ran to the end. Do not run Python with
`-I` (unicorn is often installed in the user site-packages). Call `gc.collect()` between emulator
instances in new tests: unicorn can segfault in `mem_map` otherwise; `g80_check.sh` reruns a process
that exits with 139, a FAIL line is never rerun.

Expected baseline: every line PASS. `ref/` holds older reference builds (via_s3) used by `test_via_eeprom.py`
as a historical image to upgrade from; they are not built from the current source.

## What each file covers

| File | Covers |
| --- | --- |
| `emulator/g80emu.py` | the emulator (tests extend it by subclassing). 2026-10-21: flash writes allowed in 0xF6C0..0xFFBF (aux sectors), flash read-back coherence (a write made inside a hook lands before the firmware reads it back), torn power cut (`power_cut_torn`, `torn_op`) |
| `emulator/emu_lpm.py` | PMU / clock / GPIO-IRQ extension for the deep-sleep tests |
| `emulator/via_host.py` | USB host and VIA request models for the VIA suites |
| `emulator/bat_common.py` | simulated radio module, LED readout and patch helpers for the battery suites |
| `emulator/static_check.py` | static image checks (no stray pointers into the option/ISP area, reset vector, blank EEPROM area) |
| `emulator/run_tests.py` | blocks A-F (boot, matrix, LED, USB, faults, watchdog, regressions), E-L1..E-L16 (stage 2 behaviour), S3, S4 |
| `emulator/test_features.py` | block S3: SOCD, game mode, Fn layer shortcuts, effect keys |
| `emulator/test_via_eeprom.py` | block S4: VIA definition/JSON, EEPROM layout and persistence, paint-map storage |
| `emulator/test_wireless.py` | wireless mode with a simulated module: frames, ACK/retry, channel keys |
| `emulator/test_host_off.py` | PC off with USB 5 V present: LEDs off, wake, suspend |
| `emulator/suite_battery*.py` | battery events, Fn+Space gauge, indicator priorities, lights-off timeout (wired, wireless, VIA, charger and slider edges) |
| `emulator/suite_gaming.py` | Snap Click, OS keys, brightness to off, Chordal Hold, Flow Tap, MT() hold from VIA, key override |
| `emulator/suite_magic.py` | QMK magic keycodes and EE_CLR from VIA |
| `emulator/suite_paint.py` | paint session (Apply/Discard) and VIA menu items |
| `emulator/suite_radio.py` | radio protocol, NKRO, A4 |
| `emulator/suite_sec.py` | VIA security (SEC-1) |
| `emulator/suite_sleep.py` | deep sleep, its countdown (also from keys the editor takes), lights off before the deep sleep, LVD |
| `emulator/suite_via_stress.py` | VIA request fuzz / stress (`--quick`, `--full`, `--seed=N`) |
| `emulator/pc_common.py` | power-cut helpers: flash-op recording, offline cut images (plain and torn), Python model of the wear-leveling read path with the boot recovery of `g80_wear_leveling.c`, the trigger bench |
| `emulator/suite_powercut.py` | PC-1: cut before EVERY flash op of a consolidation (Apply 88 / 30 keys, settings save, boot-time full log, invalid log entry, all 16 sectors), two boots each, sweep table KEPT / LAST_LOST / PARTIAL / FULL_RESET; PC-5: upgrade over s8 data with only the code replaced, flash map, free flash; PC-5-flash: flashing the whole .bin (0x00 over the band) boots to the defaults, zero state log ignored, a later edit survives a reboot; PC-5-flash-cut: cut sweep of the first consolidation on that freshly flashed band (`--step=N` samples) |
| `emulator/suite_powercut_nested.py` | PC-2: cuts inside the recovery boot, chains of 5 cuts, state-log exhaustion fallback; PC-3: torn ops (half-programmed / half-erased cells) at every op |
| `emulator/suite_powercut_misc.py` | PC-4: factory reset and blank-band boot cut sweeps; PC-6: healthy boot 0 ops, aux sectors untouched outside a consolidation; PC-7: worst-case timing and watchdog with the stall model |
| `emulator/suite_powercut_repro.py` | PC-8..10: the 7-session unplug workload of the 2026-10-21 report, every cut classified with the recovery model, >= 136 boots verified; unplug-time sweep; wear per consolidation |
| `emulator/via_effect_ids.json` | RGB effect ids shared by the tests |
| `emulator/via_reader/` | Node check of the VIA definition (optional) |
| `emulator/ref/` | an older via build (s3) and its symbols, the historical image `test_via_eeprom.py` upgrades from |

The image named `stage1` in the test code is the `minimal` keymap (the safety image).

## Renamed files

| Old name | New name |
| --- | --- |
| `s3_tests.py` | `test_features.py` |
| `s4_tests.py` | `test_via_eeprom.py` |
| `s7b_wireless_test.py` | `test_wireless.py` |
| `s7_host_off_test.py` | `test_host_off.py` |

`s2fix_tests.py`, `test_debug.py` and `diag_inject.py` were development leftovers and are not part of the
public set.
