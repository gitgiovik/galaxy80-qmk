# Security policy

## Reporting a vulnerability

Please report security problems **privately**: on the repository page open *Security* and *Report a vulnerability*
(GitHub private vulnerability reporting). If that button is not available, open a normal issue that says only that
you have a security report and ask for a private way to send it; do not put details in a public issue.

Give the firmware release and image (`via`, `default`, `minimal`), what an attacker needs (physical access, a program on the
host, a nearby radio), and how to reproduce it. This is a hobby project; expect a reply within a few days to a few
weeks, not a service level. Please allow time for a fix before publishing.

Supported: the latest release.

## Threat model in short

* The keyboard runs one firmware with no signature check; anybody with physical access can flash it through the ROM
  bootloader. That is by design (it is also the recovery path) and not a vulnerability.
* A program on the PC can talk to the VIA interface. It can change keymaps, macros and settings (like VIA itself),
  but the firmware does no flash work inside the USB handler, bounds what it writes and never touches the chip's code-option
  area or the boot flag.
* The radio module is trusted for key transport only; the firmware does not serve the module's 2.4G configuration
  tunnel and sends only a fixed list of commands.

## SEC-1: the vendor updater's bootloader command

The stock vendor updater enters the chip's ROM bootloader by sending a special packet (an 8-byte magic sequence) to the
keyboard's HID interface. Once the ROM bootloader runs it accepts **any** firmware. If the firmware honoured that packet
from any program, any program on the host could replace the firmware.

**This firmware honours the command only while the Fn key is physically held** (checked in the debounced key matrix at the
physical Fn position, not in the keymap). Without Fn held, the packet is treated as an ordinary unknown VIA request and
answered "unhandled". The physical routes into the bootloader (Esc or P held while plugging in, Fn+Right Shift+Esc, the
fault paths) are not affected. Status: emulator-tested only (the SEC-1 suite).

A consequence: the vendor updater cannot put this firmware into the bootloader on its own, and it should not be used on it.

## Other safety properties

* Flash writes (`via` image only) are limited to the last 2 KiB before the boot flag; a link-time check rejects an
  image whose code would reach that band. The `default`, `debug` and `minimal` images never write flash at run time.
* A hardware watchdog restarts a hung keyboard, and faults end in the bootloader.
* The repository never contains vendor firmware, updaters or drivers.
