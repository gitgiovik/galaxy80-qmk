# Battery, charging and sleep

The radio module measures the battery and reports it to the keyboard. Unless stated otherwise this page is about the
keyboard running on its battery (slider ON). Everything on it is **emulator-tested only**.

## Fn+Space: battery gauge

Hold **Fn + Space**. The side bar becomes the gauge and the number row mirrors it:

| Level | Bar |
|---|---|
| 1-100 % | n = ceil(level / 20) LEDs lit, starting at bar LED 1; the last lit LED is dimmed to show the remainder; **red** below 20 %, **yellow** 20-49 %, **green** from 50 % |
| 0 % | LED 1 blinks red |
| unknown | LED 1 blinks white, slowly |

* Number row keys 1 to 0 are 10 % each, in the same colour.
* The gauge grows from LED 1, one more LED about every 50 ms after the press.
* While charging the lit LEDs breathe. If the module has not answered, the last known value is shown dimmed.
* The firmware asks the module for the level when you press, again every 2 s while you hold, every 60 s while linked,
  and after a wake-up.
* Nothing is typed. It also works while Layer Lock keeps the Fn layer on.
* Which end of the bar is "bottom" is an assumption not yet checked on hardware (checklist H-B1).

## Charging and low battery

* The **Fn key** shows red while charging, green for 60 s once the battery is full, and blinks red on a low battery. On
  the cable the charge colours show only if the VIA option *Charge LED when Wired* is on (default on).
* **Low battery** (module event): the Fn key blinks red every 0.4 s continuously (stock behaviour), or, as a VIA option,
  five blinks per 30 s started by a key press. *Power > Low Battery Warning*.
* **Critical battery** (module event): no flash write is started (settings, Apply, VIA keymap writes, factory reset). A
  factory reset is refused with red flashes. Changes stay in RAM and are saved when the module reports a normal level
  or a charger is connected. A cable that supplies power never inhibits saving.
* **Max Brightness on Battery** (75 / 50 / 25 %): while on battery (wireless, no charger) the LED driver scales every
  colour. Your brightness setting is neither changed nor stored.
* **Wired Idle Lights Off** (1 / 5 / 10 / 30 min): on the cable the LEDs go dark after that long without a key and come back
  with the next one. Default Never.
* A low-voltage detector resets the chip when the supply falls to about 2.4 V (the stock value). It only resets and never
  enters the bootloader.

## Wireless sleep (EXPERIMENTAL)

> Deep sleep is the least tested code in the project. It follows the stock firmware's register sequence, uses bounded
> waits and restarts the watchdog first on wake, and it passes the emulator, but it has **not run on hardware**. A hang
> while on battery can leave the keyboard dead until the battery is empty. The factory setting is **Never**.

**Wireless Sleep After** (VIA, Power): Never (default), 1 min, 5 min, 30 min.

How it behaves (stock timing):

* A countdown runs only in wireless mode with the option not Never. Every key event restarts it. While no host is linked
  it is 15 s; once linked it is the chosen time (the module's "linked" message first sets 5 min until the next key); a
  critical-battery or lost-link message cuts it to 15 s. It does not run while a key is down or while pairing.
* A key held for 10 minutes with no other key event stops blocking sleep: the host gets "all keys up" and the key is
  ignored until it is released.
* At zero the keyboard sleeps only if every guard holds: slider ON, USB stopped, radio idle, no key down, no settings save
  or Apply pending (unless saving is inhibited on a critical battery), no pairing, no factory reset running. Otherwise it
  tries again a second later. It is never half entered.
* A pairing window (20 s) or reconnect indication (10 s) that ends without a link starts a short countdown (3 s and 0.5 s),
  so a failed pairing does not keep the keyboard awake.
* **Sleep Now** (VIA custom keycode, on no key by default) sleeps once all keys are released, also with the option Never.
  Wired it does nothing.
* **Waking**: any key (the key is then sent), or the slider to OFF (USB mode). The charger does not wake it, and radio
  bytes that arrive while it sleeps are lost, as on the stock firmware. After the wake the link re-establishes and a fresh
  battery level is requested. The stock firmware sends one extra message after a 2.4G wake that this firmware omits;
  whether that matters is unconfirmed (checklist H-S6b measures the reconnect delay).

What the code does for safety:

* Register steps in the stock order and with the stock values; every wait for a clock is bounded and a timeout resets the
  chip.
* The watchdog is stopped just before the clock switch and restarted as the first step after the wake.
* No flash is touched while asleep, and a pending save blocks sleep.
* The bootloader entry still works after a wake (emulator-tested).

If a keyboard seems stuck asleep: slider OFF, unplug the cable, wait 30 s ([recovery.md](recovery.md)).
