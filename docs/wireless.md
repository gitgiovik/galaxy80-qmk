# Wireless: Bluetooth and 2.4G

The Galaxy80 has a separate radio module (Bluetooth 1-3 and the 2.4G dongle link). The main chip talks to it over
a serial line ([protocol/radio-uart.md](protocol/radio-uart.md)). Wireless follows the stock firmware's behaviour. The
basic modes were hardware-tested in the 2.4G/Bluetooth stages; the items marked below are emulator-tested only.

## Modes

* **Slider OFF**: wired. USB is the transport. The radio module is told "USB mode".
* **Slider ON**: wireless. The firmware **stops its USB interface** (even with the cable in; the cable then only
  charges the battery, as with the stock firmware) and sends keys, media keys and mouse-key reports through the
  module. Moving the slider back restarts USB (a pause of about 50 ms).
* The VIA interface is part of USB, so **VIA does not work wirelessly**.

## Channels and pairing

| Fn + | Channel | Indicator colour |
|---|---|---|
| Q | Bluetooth 1 | blue |
| W | Bluetooth 2 | blue |
| E | Bluetooth 3 | blue |
| R | 2.4G | green |

These work only with the slider ON (with the slider OFF they send nothing). The chosen channel is stored with the
settings (VIA image) and used at the next start.

* **Pair**: hold the channel key (Fn+Q/W/E/R) for **3 seconds**. The firmware then sends the pair command three
  times, 0.5 s apart. Put the host in Bluetooth discovery (for 2.4G: dongle plugged in). Remove an old bond for
  the keyboard on the host first.
* The channel key's LED shows the link: **fast blink** while pairing (up to 20 s), **slow blink** while the link is
  lost or just switched (10 s), **steady for 3 s** once connected, after a channel switch and after a wake-up. If the
  pairing or reconnect window ends without a link, the keyboard goes to sleep shortly after (3 s and 0.5 s) when
  the "Deep Sleep After" option is on.
* A key pressed while the link is down first asks the module to reconnect, then the report is sent.

## What is sent

* The keyboard report: modifiers and the first 5 keys in press order, plus an extra bitmap for further keys, so that
  with **Wireless NKRO** on (VIA, default) you can hold more than 6 keys at once. Off: the plain 6-key report
  (switching NKRO off with Fn+N also gives 6 keys).
* Consumer (media) keys, system control (Power / Sleep / Wake) keys and mouse-key reports.
* Caps Lock and the other host LED state come back from the module; Caps Lock shows on the Caps key and bar LED 1.

Status: the 6-key keyboard report, consumer keys, channel switching, pairing and the indicators are
hardware-tested. The extra NKRO bitmap, the system-control report and the mouse report are emulator-tested only (checklist H-W5,
H-W6). The bit order of the system-control report is inferred, not confirmed.

## Limits

* No 2.4G configuration tunnel (the vendor software cannot configure the dongle through this firmware).
* No wake-up from the charger and no VIA over the radio.
* Reports are queued; a report that is not acknowledged is retried three times, 5 ms apart, and then given up (the
  last key state is sent once more so that no key sticks).
* What the host operating system shows as the battery level of the Bluetooth keyboard is unknown.
