# Stability: how settings are saved

Writing the chip's flash stalls the processor for a while (erasing and programming take milliseconds, a clean-up of
the whole settings band much longer), and the same processor scans the keys and drives the LEDs from an interrupt.
A flash write in the wrong moment can therefore freeze the LEDs or lose a keystroke. The firmware is organised so
that this does not happen, even when VIA sends many requests in a row. Everything in this page applies to the
`via` image; the other images never write flash. Status: emulator-tested only (the VIA stress suite);
checklist items H-V1 and H-V2 are the hardware check.

## Where the data lives

* The flash band **0xF7C0 to 0xFFBF** (2 KiB) holds a wear-leveling log with a 1 KiB logical EEPROM: the QMK/VIA
  settings, the VIA keymaps (3 layers) and macros, the Galaxy80 settings words and the per-key colour table.
* Nothing else is ever erased or programmed. The boot flag at 0xFFFC and the chip's option area are never touched, and the
  linker refuses an image whose code would reach the band.
* The backing store is the project's own driver (`g80_wear_leveling.c`): it stores values complemented, because the
  chip's flash reads as 0xFF when erased, and that way a fresh band is recognised as empty. The stock QMK backend
  erased and rewrote the whole band at every boot.
* The first byte of the custom block is a layout version. If a new firmware has another layout, VIA data is reset at the
  first boot instead of being misread.

## The save model in one paragraph

**Settings and lighting save automatically a few seconds after your last change. Key colours save only when you press
Apply. Keymap and macro writes from VIA are queued and written while you are not typing.**

## Settings (Fn toggles, VIA menu, lighting)

* A change only marks the settings "dirty" in RAM. Nothing is written in the key event or inside the USB handler.
* The write becomes due **3 s after the last change**, or **30 s after the first unsaved change** (a slider dragged for minutes is
  still saved), and never sooner than 3 s after the previous settings write. A value toggled back to what is stored is not
  written at all.
* A due write starts at the first moment the **write gate** is open.

## The write gate

A settings save, an Apply, and a VIA write that would clean up the log start only while:

* no key is down and no key event has happened for 250 ms (a key held without any change for 10 minutes stops
  counting, so an object resting on a key cannot block saving forever),
* nothing is queued or in flight on the radio link,
* the battery is not critical.

Otherwise the write waits and is retried. The write keeps feeding the watchdog.

## Per-key colours: Apply and Discard

* Painting (from the on-board editor, from VIA, the brush, the bar gradient, Reset Key Colors) changes **only RAM**
  and marks the colours **unsaved**.
* **Apply** (Fn+Enter inside the editor, or the VIA button) writes the table and the effect once, behind the gate.
  **Discard** (Fn+Delete inside the editor, or the VIA button) reloads the stored table, no flash write.
* Leaving the editor, also by its 2-minute idle timeout, does not save. Unsaved colours are shown by the slow magenta
  blink of side-bar LED 5.
* A replug, reset or power cut with unsaved colours loses them. This is by design.

## VIA keymap and macro writes

* VIA sends one request and waits for the answer. The firmware's USB handler does no flash work at all. Keymap
  and macro writes are copied and executed from the main loop in bounded steps; **the answer is sent when the write is
  done**, so VIA never shows something as saved before it is.
* A step that will fill the log, and so force a clean-up (erase of the band, 32 small sectors, and a rewrite of the
  1 KiB), waits for the write gate, so it does not run while you type.
* A request that arrives while a deferred write is still open (a host that does not wait for answers) is answered
  "unhandled" and ignored.
* The per-loop volume of VIA packets is bounded by the small receive queue (2 packets).

## Critical battery

When the radio module reports a critical battery, **no flash write starts**: not the settings, not Apply, not a VIA keymap write
that cleans up the log, and the factory reset is refused. Everything waits in RAM until the battery recovers or a charger
is connected. Deep sleep with a pending save is still allowed in that case (the save stays in RAM).

## Factory reset

Fn+Backspace held for 3 s (or `EE_CLR` from VIA) shows 7 white flashes, then invalidates the stored data and restarts. On the
next boot everything is re-created with the defaults. It is refused on a critical battery, also if the report arrives during
the flashes.

## Limits and power cuts

* A **power cut during a write** can lose the last change. A cut during the clean-up of the band (the rare case that
  the log is full) can lose the band's contents; the keyboard then starts with the defaults (VIA data and key colours
  are reset). Nothing outside the band can be damaged, and the bootloader always remains.
  The exact behaviour of the flash after a cut at the worst moment has not been measured on hardware.
* The longest stall of the key scan during a flash operation has not been measured on hardware, and nothing in
  this repository measures it (the emulator's flash timing is a model). The write gate keeps the operations away
  from typing; a key tapped shorter than a stall that begins exactly then could still be missed. Checklist
  items H-V1 and H-V2 look for the symptoms.
* Writes consume flash endurance (the chip is rated for a limited number of erase cycles per sector). The log
  approach and the coalescing keep the number of cleanups small; do not script thousands of writes.
* A key held down while a VIA write is requested delays that write; it does not fail.
