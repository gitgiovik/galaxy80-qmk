# Testing

There are two layers of checking: the **emulator suite**, which anyone can run without hardware, and the
**hardware checklist** ([hardware-test-checklist.md](hardware-test-checklist.md)), which only a person with the
keyboard can do. A feature counts as "hardware-tested" in the README only after the second.

## Emulator suite

`tests/emulator/g80emu.py` is a full-system model of the SN32F248B built on the Unicorn CPU emulator. It boots a built image
from the reset vector through ChibiOS and QMK, models USB, the key matrix, the LED engine, flash and EEPROM, the watchdog, the
power-management registers and the radio UART, and the tests drive keys, VIA traffic and simulated radio frames. It never
talks to a real keyboard, and the model itself is not a proof that the silicon behaves the same (that is what the
hardware checklist is for).

Everything, including the four builds, in parallel:

    tools/dev/g80_check.sh OUT_DIR

`OUT_DIR/SUMMARY.txt` lists the image sizes, the heap, PASS/FAIL per file and every FAIL line. `-t` only builds, `-T` only
runs the tests on images already in `OUT_DIR/fw`. Toolchain locations come from environment variables or the
git-ignored `tools/dev/local.env`.

**Expected result:** every test passes. A unicorn crash (exit status 139)
is harness noise and is rerun automatically; a FAIL line is never rerun.

Memory: every emulator instance commits about 1 GiB. Run at most two to four processes at once, and do not run Python with `-I`.

Full description of the files, how to run one suite, the test ids and the conventions for new tests: [tests/README.md](../tests/README.md).

## What the suites cover

| Area | Examples |
|---|---|
| Boot and safety | boot entry keys, faults to the bootloader, watchdog, no access to the option area, flash writes only inside the band |
| Matrix, LEDs, USB | scan, debounce, the LED engine, USB enumeration and suspend |
| Features | Fn layer, Snap Tap and game mode, effects, factory reset |
| VIA and storage | the definition, EEPROM layout, the deferred save and its write gate, paint Apply/Discard, a VIA stress test |
| Wireless | radio frames, ACK and retry, channels, hybrid NKRO, system control |
| Battery and sleep | events, gauge, indicators, critical-battery inhibit, lights-off timeout, deep sleep, countdown, low-voltage reset |
| Security | the Fn-gated bootloader command (SEC-1) |
| Magic keycodes, key override | the magic-keycode suite, suite_gaming.py (T-KO-1) |

## Static checks

`python tools/check_size.py out/emu` and `python tests/emulator/static_check.py out/emu via default stage1` run in CI after the
build (see [building.md](building.md)).

## CI

`.github/workflows/build.yml` installs the pinned toolchain, builds the images, runs the size and static checks and the
emulator suite, and uploads the images as workflow artifacts. A tag `v*` runs `release.yml`, which attaches the images,
the VIA definition and the checksums to a GitHub release.

The emulator step in CI only reports: unicorn often crashes on the hosted runners. Run the full suite
locally (`tools/dev/g80_check.sh`) before tagging a release.

## Writing a test

Copy the structure of a small suite (for example `suite_magic.py`): build a machine from the image, drive it, print `PASS`
or `FAIL` lines, free the machine (`gc.collect()`) before creating the next one. New tests have to fail on the code they
guard, so check that a test really fails against the old behaviour.
