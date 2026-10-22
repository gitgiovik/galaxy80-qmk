# Recovery and the ISP bootloader

The SN32F248BF has a bootloader in mask ROM ("ISP"). It cannot be erased or overwritten by any firmware,
and it is entered at power-on by pulling one pin low. On this keyboard that pin (BOOT, P2.2) is shared with a
matrix column, so holding a key on that column while the chip powers up enters the ISP. **This works
whatever is in flash, if the chip really loses power first.** The case never has to be opened.

The ISP shows up on USB as **0C45:7040**. It writes nothing by itself: it only answers a flasher program on
the PC.

## The one rule: a true power-off

With the slider ON (wireless) the battery keeps the chip powered, so plugging the cable in is not a
reset and the keys are never read. So:

1. Slider to **OFF** (wired position).
2. **Unplug the USB cable.** Wait at least **30 seconds**.
3. Hold the key (below), plug the cable in, keep holding 2-3 seconds.
4. Check the USB ID. `0C45:7040` means you are in the ISP; the keyboard is dark and does not type.

If the ID is 4758:0080 (this firmware) or 05AC:024F (stock), the chip did not see the key: repeat with a longer
wait, a direct USB port (no hub) and a data cable.

To leave the ISP without flashing: unplug, wait a few seconds, plug in with no key held.

## Ways in

| Situation | How |
|---|---|
| This firmware, not running or cold | hold **P** or **Esc** while plugging in (both are checked at every reset, before anything else starts) |
| This firmware, running | **Fn + Right Shift + Esc**. The Fn+Esc key alone does nothing; Right Shift must be held |
| This firmware, crashed | automatic: a fault or a halt ends in the ISP, LEDs off first. A hang ends in a normal watchdog restart instead (into the ISP only if Esc or P is held at that moment) |
| This firmware, VIA | assign *Safe Boot* (needs Right Shift held too) or QMK `QK_BOOT` to a key |
| Stock firmware installed | hold the whole group of keys on the BOOT column: **F10, 0, P, ;, . and Right Alt** together while plugging in. Holding only P was not enough on the stock firmware in the maintainer's test, and which of the six are needed was not isolated |

Notes:

* Esc is checked at every reset, so a key held down while the keyboard resets (for example by the watchdog)
  also lands in the ISP. If the keyboard keeps "dropping to the bootloader" check for a stuck Esc or P.
* The updater command of the vendor driver is not a way in for you: this firmware accepts it only while the
  Fn key is physically held (SECURITY.md). Do not run the vendor updater against this firmware.

## Flashing from the ISP

See [install.md](install.md). Any image can be flashed from here, including the `minimal` safety image and
the stock firmware.

## What to do when something goes wrong

| Symptom | Action |
|---|---|
| `0C45:7040` does not appear with the key held | unplug, wait 30 s, slider OFF, try again holding longer, or use Esc instead of P, direct port, other cable. Do not flash while not in the ISP |
| The flasher does not see the device | confirm `0C45:7040`; close other programs that use the keyboard; do not install drivers on any other device |
| `-i` shows a different chip family or size | stop, unplug, and open a hardware-revision issue |
| Write or verify error, or the cable was pulled during the write | enter the ISP again and flash again: it is safe, the ISP is untouched |
| After two failed attempts | flash the `minimal` image, which is the simplest firmware, then report |
| The ID stays `0C45:7040` after flashing | `sonixflasher -v 0c45/7040 -u`, or unplug and plug in with no key held. If it persists, the image may fault at start: re-flash another image |
| Keyboard "dead" after flashing | a fault leaves the keyboard in the ISP: look for `0C45:7040`; otherwise unplug, wait 30 s and enter the ISP with P |
| Repeated restarts every few seconds | the watchdog is catching a hang: enter the ISP, flash `minimal`, report with the image name |
| Driver installed on the wrong device | Device Manager > uninstall the device's driver (tick "delete driver software"), unplug and replug |
| Slider ON, replug has no effect | slider OFF, unplug, wait 30 s |
| Wireless deep sleep seems stuck | the chip may be asleep or hung on battery: slider OFF, unplug the cable, wait 30 s, then enter the ISP if needed. Then plug the cable in and set "Deep Sleep After" to Never in VIA (the stored option re-arms deep sleep in wireless after a reset) |

## Going back to stock

Unverified, see the README. The route is: enter the ISP (stock needs the whole key group, this firmware
needs P or Esc), then flash a stock image that you obtained from the vendor yourself, with the same flasher
command as in [install.md](install.md). The vendor's updater only works against the stock firmware,
not against this one. This repository does not contain or link any vendor image.

## What the firmware itself does for safety

* Boot-entry keys are read in the first instructions after reset, before the clocks, RAM and USB start; a
  wrong build can therefore not lock them out (the Esc/P positions are fixed in the code).
* A hardware watchdog (about 8 s, worst case about 1 s) restarts a hung main loop.
* Hard fault, unhandled interrupt and system halt jump to the ISP.
* The firmware never touches the chip's code-option / code-protection area, never writes the boot flag at the
  end of flash, and flash writes (via image only) are limited to the last 2 KiB band before it. A
  link-time check refuses images whose code reaches that band.
