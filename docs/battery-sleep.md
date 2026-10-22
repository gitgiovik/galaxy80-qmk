# Battery, charging and sleep

The radio module measures the battery and reports it to the keyboard. Unless stated otherwise this page is about the
keyboard running on its battery (slider ON). Everything on it is **emulator-tested only**.

## Fn+Space: battery gauge

Hold **Fn + Space**. The side bar becomes the gauge:

| Level | Bar |
|---|---|
| 1-100 % | n = ceil(level / 20) LEDs lit, starting at bar LED 1; the last lit LED is dimmed to show the remainder; **red** below 20 %, **yellow** 20-49 %, **green** from 50 % |
| 0 % | LED 1 blinks red |
| unknown | LED 1 blinks white, slowly |

* While charging the lit LEDs breathe. If the module has not answered, the last known value is shown dimmed.
* The firmware asks the module for the level when you press, again every 2 s while you hold, every 60 s while linked,
  and after a wake-up.
* Nothing is typed.
* Which end of the bar is "bottom" is an assumption not yet checked on hardware (checklist H-B1).

## Charging and low battery

* The **Fn key** shows red while charging, green for 60 s once the battery is full, and blinks red on a low battery. On
  the cable the charge colours show only if the VIA option *Charge LED when Wired* is on (default on).
* **Low battery** (module event): the Fn key blinks red every 0.4 s continuously (stock behaviour), or, as a VIA option,
  five blinks per 30 s started by a key press. *Power > Low Battery Warning*.
* **Critical battery** (module event): no flash write is started (settings, Apply, VIA keymap writes, factory reset). A
  factory reset is refused with red flashes. Changes stay in RAM and are saved when the module reports a normal level
  or a charger is connected. A cable that supplies power never inhibits saving. With Deep Sleep After = Never (the
  default) the keyboard stays awake on a critical battery until the low-voltage reset: a critical battery does not
  deep-sleep it, the radio module / LVD cut power, and nothing is written to flash meanwhile.
* **Max Brightness on Battery** (75 / 50 / 25 %): while on battery (wireless, no charger) the LED driver scales every
  colour. Your brightness setting is neither changed nor stored.
* A low-voltage detector resets the chip when the supply falls to about 2.4 V (the stock value). It only resets and never
  enters the bootloader.

## Lights off

**Lights Off After** (VIA, Power): Never, 1 min, **5 min** (default), 10 min, 30 min. Wired and wireless.

* After that long without activity the LEDs go dark: effect, side bar and indicators (charging, low battery, critical
  battery, link-lost and pairing blinks, unsaved paint): a warning that comes while dark shows at the next key. Only the lights: the keyboard keeps scanning and typing, the radio link stays up.
* Activity is any key (also Fn+Space, so the gauge shows at once), any VIA command (not the key tester), and moving the
  slider (wired <-> wireless). Plugging or unplugging the charger does not light them. The next one lights everything again; the key is typed as usual.
* Never while the on-board colour editor is open (it closes itself after 2 min without a key).
* The `default` image has no VIA menu: its LEDs go off after 5 min, and it never deep-sleeps.

## Deep sleep (wireless, EXPERIMENTAL)

> Deep sleep is the least tested code in the project. It follows the stock firmware's register sequence, uses bounded
> waits and restarts the watchdog first on wake, and it passes the emulator, but it has **not run on hardware**. A hang
> while on battery can leave the keyboard dead until the battery is empty. The factory setting is **Never**.

**Deep Sleep After** (VIA, Power): Never (default), 5 min, 10 min, 30 min, 60 min, counted from the last key. Set it
longer than Lights Off After: the lights go off first and the keyboard sleeps later. A shorter value also works (the
deep sleep switches the LEDs off as well).

How it behaves (stock timing):

* A countdown runs only in wireless mode with the option not Never. Every key (also the keys the colour editor takes),
  a mode change and the module's "linked" message restart it with the chosen time; while no host is linked it is 15 s,
  and a critical-battery or lost-link message cuts it to 15 s (stock). It does not run while a key is down or while
  pairing.
* A key held for 10 minutes with no other key event stops blocking sleep: the host gets "all keys up" and the key is
  ignored until it is released.
* At zero the keyboard sleeps only if every guard holds: slider ON, USB stopped, radio idle, no key down, no settings save
  or Apply pending (unless saving is inhibited on a critical battery), no pairing, no factory reset running. Otherwise it
  tries again a second later. It is never half entered.
* A pairing window (20 s) or reconnect indication (10 s) that ends without a link starts a short countdown (3 s and 0.5 s),
  so a failed pairing does not keep the keyboard awake.
* **Waking**: any key (the key is then sent, and the lights come back), or the slider to OFF (USB mode). The charger does not wake it, and radio
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
