# Installing the firmware

Read [is-this-my-board.md](is-this-my-board.md) and [recovery.md](recovery.md) first. Everything here assumes
a supported board. You flash at your own risk.

## 1. Pick an image

Each release has these files (names with the release tag, for example `galaxy80-v1.0.0-via.bin`):

| File | What it is |
|---|---|
| `galaxy80-<tag>-via.bin` | the full firmware with VIA. Settings, keymap and key colours persist in the last 2 KiB of flash. **Use this one** |
| `galaxy80-<tag>-default.bin` | the same functions without VIA. Settings live in RAM only and are lost at power-off; the firmware never writes flash |
| `galaxy80-<tag>-minimal.bin` | safety image: a plain USB keyboard, LEDs off, no RGB or radio. For recovery and for ruling out a fault |
| `galaxy80.json` | the VIA definition to sideload (see [via.md](via.md)) |
| `SHA256SUMS.txt`, `BUILDINFO.txt` | checksums, and the commits and compiler the release was built from |

The debug image (QMK console) is not released; build it yourself if a maintainer asks for a log.

Check the download: `sha256sum -c SHA256SUMS.txt` (Linux, macOS, Git Bash) or `Get-FileHash <file>` in PowerShell
and compare with `SHA256SUMS.txt`.

Recommended first flash: `via`. If the very first boot is not right, flash `minimal` and report.

## 2. Get the flasher

[SonixFlasherC](https://github.com/SonixQMK/SonixFlasherC) 3.0.0 command-line tool (`sonixflasher`). It is
GPL-3.0 software of its own, not part of this project. Download it from its project page and keep it out of this
repository.

## 3. Prepare

1. Close the vendor software, VIA, and any browser tab that uses the keyboard.
2. Slider **OFF**. Data cable straight into the PC.
3. Make a working **copy** of the image (the flasher modifies the file it is given).
4. If you want to keep your VIA layout: in VIA, *Save + Load* and save the layout file before flashing. The
   flash wipes the keymap, settings and colours.

## 4. Enter the ISP bootloader

Unplug, wait 30 s, hold **P** (this firmware) or the whole key group (stock firmware, see
[recovery.md](recovery.md)), plug in, hold 2-3 s. Check for USB ID **0C45:7040**.

## 5. Check, then flash

List and read-only info (change nothing):

    sonixflasher -c
    sonixflasher -v 0c45/7040 -i

The info must show an SN32F240B-family chip with 64 KB flash. Otherwise stop.

Flash:

    sonixflasher -v 0c45/7040 -f galaxy80-<tag>-via.bin

* The tool waits 5 s before writing (Ctrl+C there does nothing harmful), writes in 64-byte blocks, verifies
  and returns to user mode.
* If the chip was in a protected state, the tool performs a mass erase and unprotects it first. That is
  normal after the stock firmware.
* Do not pull the cable while it writes. If that happens anyway, enter the ISP again and repeat.
* **Do not use any other option.** In particular `-o`, `-j`, `-k`, `-r` and "Set Code Option" change chip
  settings the firmware does not expect. Allowed: `-c`, `-l`, `-i`, `-u` (return to user mode), `-v`, `-f`, `-d`.

## 6. Check

* The tool ends without a verify or checksum error and the keyboard re-enumerates as **4758:0080**. If it stays at
  `0C45:7040`: `sonixflasher -v 0c45/7040 -u`, or unplug and plug in with no key held.
* Do the first-boot steps of the README: type every key, check Fn+F10..F12, try the bootloader entry once.
* Then continue with [via.md](via.md).

## Operating system notes

* **Windows 11** (the maintainer's setup): the flasher worked without installing any driver. If yours cannot
  open the device, a WinUSB driver (Zadig) on the `0C45:7040` device might help; this is an untested guess, because
  the maintainer never needed it. Pick the device by its USB ID and make sure it says `0C45 7040`. Never replace the driver of `05AC:024F` or
  `4758:0080`. To undo a wrong choice: Device Manager > the device > Uninstall device, tick "delete the driver
  software", unplug and replug.
* **Linux and macOS**: untested by the maintainer. SonixFlasherC can be built with a hidapi backend
  (`make BACKEND=hidapi`, see its documentation). On Linux you will probably need a udev rule so that your user may open the
  `0c45:7040` device. You will have to find out the details yourself; please report what worked so
  that this page can say so.

## Updating

Flash the new release the same way. Settings and the keymap are wiped each time. A release only changes the
VIA definition when the notes say so; reload `galaxy80.json` in VIA when it does.
