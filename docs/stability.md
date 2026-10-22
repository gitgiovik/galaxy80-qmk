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
* The four sectors just below it, **0xF6C0 to 0xF7BF** (256 B), belong to the same driver: two copy buffers and a small
  state log that make the clean-up of the band safe against power cuts (see [Power cuts](#power-cuts-and-unplugging)).
* Nothing else is ever erased or programmed. The boot flag at 0xFFFC and the chip's option area are never touched, and the
  linker refuses an image whose code would reach 0xF6C0.
* The backing store is the project's own driver (`g80_wear_leveling.c`): it stores values complemented, because the
  chip's flash reads as 0xFF when erased, and that way a fresh band is recognised as empty. The stock QMK backend
  erased and rewrote the whole band at every boot.
* The first byte of the custom block is a layout version. If a new firmware has another layout, VIA data is reset at the
  first boot instead of being misread.

## The save model in one paragraph

**Settings and lighting save automatically about one second after your last change. Key colours save only when you
press Apply. Keymap and macro writes from VIA are queued and written while you are not typing. Unplug only after the
magenta blink of side-bar LED 5 has stopped and, after an Apply, after the green flash.**

## Settings (Fn toggles, VIA menu, lighting)

* A change only marks the settings "dirty" in RAM. Nothing is written in the key event or inside the USB handler.
* The write becomes due **1 s after the last change** (`G80_SAVE_DELAY_MS`), or **30 s after the first unsaved change** (a slider
  dragged for minutes is still saved), and never sooner than 1 s after the previous settings write (`G80_SAVE_MIN_INTERVAL_MS`;
  a VIA slider drag costs at most one small write per second). A value toggled back to what is stored is not written at all.
  Until 2026-10-21 both were 3 s: an unplug up to ~3.25 s after a change lost it.
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
* A step that will fill the log, and so force a clean-up (the consolidation: the changed 64-byte sectors of the 1 KiB
  are rewritten one by one, then the log is erased), waits for the write gate, so it does not run while you type.
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

## Power cuts and unplugging

When the log is full, the driver consolidates: the current 1 KiB is written back as the new base copy and the log is
erased. QMK's own procedure erases the whole band first and rewrites it afterwards, so an unplug in that window (more
than a second, and it ran right after an Apply or a settings save that filled the log, which is when people unplug)
left no valid copy: the next boot reset **everything** to the defaults, VIA keymaps and macros included. That was the
"sometimes everything is reset when I unplug" bug of 2026-10-21 (firmware s8 and earlier, also s7b).

Since then `g80_wear_leveling.c` consolidates **sector by sector**. QMK core, the band and the EEPROM layout are
unchanged, so the firmware itself forces no reformat; but flashing still wipes the stored data, because the `via` .bin is
a full 64 KiB image padded with 0x00 over 0xF6C0..0xFFBF and the flasher writes all of it (the first boot then recreates
the defaults, test PC-5-flash). The first flash of this firmware therefore does not keep the current settings, keymap or
colours either: save the VIA layout before flashing ([install.md](install.md)). The procedure:

1. A state log (32 words in the sectors at 0xF740 and 0xF780) gets a BEGIN word.
2. For each 64-byte sector of the 1 KiB that changed: its new content goes to a copy buffer (0xF6C0 or 0xF700), a COPY
   word with the sector number and a check value is written, then the sector itself is erased and rewritten. The log
   stays untouched all the time, so base copy + log always give the data back.
3. A PHASE2 word, then the hash and the log are erased and the new hash is written; the state log is erased.

At boot, before anything is read, the driver looks at the state log: if a consolidation was cut it finishes the sector
that was being rewritten from its copy buffer, takes the base copy as valid, lets QMK replay the log on top of it and
continue the consolidation; a cut after PHASE2 just finishes phase 2. A cut at any moment therefore gives back the data
as it was when the consolidation started. The only exception is the write that filled the log: what of it no longer fit
in the log (at most 5 bytes of an Apply, at most one settings word) can come back old. A cut during the factory reset or
the first boot on an erased band gives the old data or the defaults.

Tested in the emulator only (power cut before every flash operation, cuts during the recovery itself, chains of cuts,
cells half-programmed or half-erased by the cut): `suite_powercut*.py`, [tests/README.md](../tests/README.md). The behaviour
of the real flash cells when the power fails during one operation has not been measured on hardware.

* **When to unplug.** Settings, Fn toggles and lighting are on flash about 1.25 s after the last change: the magenta blink
  of side-bar LED 5 stops when nothing is pending. An Apply is written at once and ends with the green flash; if it has to
  consolidate, the keyboard is busy for up to about 2 s first. Unplugging earlier loses only the changes not yet saved.
* The state log has room for 14 interruptions inside one consolidation that leave a half-written state word; after that
  the driver falls back to the old erase-and-rewrite (not safe against a cut). Normal cuts do not use that room.
* A cut inside a single 4-byte log write (a few tens of microseconds) can still damage that entry; this is far rarer than
  the old window and is not handled beyond what QMK's log check does.
* Two QMK limits that the consolidation does not change (the old firmware has them too): a cut between the two 4-byte
  lines of one log entry makes the last bytes of that entry read 0 (a settings word or up to 4 colour bytes); and QMK's
  `eeconfig_init()` writes its valid marker before the fields, so a cut during the first boot after a factory reset can
  leave a few QMK settings (for example the NKRO / swap bits) at 0 instead of their defaults. Neither touches the VIA
  keymaps, and both need the cut inside a window of milliseconds.

## Limits

* Nothing outside 0xF6C0..0xFFBF can be damaged by a power cut, and the bootloader always remains.
* The longest stall of the key scan during a flash operation has not been measured on hardware, and nothing in
  this repository measures it (the emulator's flash timing is a model). The write gate keeps the operations away
  from typing; a key tapped shorter than a stall that begins exactly then could still be missed. Checklist
  items H-V1 and H-V2 look for the symptoms.
* Writes consume flash endurance (the chip is rated for a limited number of erase cycles per sector). The log
  approach and the coalescing keep the number of cleanups small; do not script thousands of writes.
* A key held down while a VIA write is requested delays that write; it does not fail.
