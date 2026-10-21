# Using VIA

The `via` image speaks the VIA protocol on its raw HID interface. VIA has no definition for this keyboard in
its public list, so you **sideload** the definition that comes with the firmware. VIA works over the cable only,
not over Bluetooth or 2.4G, and the slider must be OFF.

## Connect

1. Use a Chromium-based browser (Chrome, Edge). Open <https://usevia.app> (or the VIA desktop app).
2. Open *Settings*, switch on **Show Design tab**.
3. In the *Design* tab load `galaxy80.json` (release assets, or `keyboards/epomaker/galaxy80/via_json/galaxy80.json`
   in the source). The tab also lets you check the file.
4. Back in *Configure*, **Authorize** the device **4758:0080** ("Galaxy80 QMK (unofficial)").

If the keyboard is not offered, see Troubleshooting in the README. A flashed `default` image has no VIA.

## Keymap

Three layers: 0 base, 1 Fn (the Fn functions of [keys.md](keys.md)), 2 empty. The EEPROM is small, so there are
no more layers; about 184 bytes are left for VIA macros. Layers 0 and 1 are the ones to edit. Changing keys on layer 1
replaces the matching Fn function. The firmware's own keys are in the custom keycodes tab; any other QMK keycode
can be entered on *Any*.

Keymap and macro writes are queued and saved by the keyboard in small steps while you are not typing
([stability.md](stability.md)). VIA waits for the answer of each write, so it never shows a change as saved before it is
in flash; a large upload (for example a full macro) can take a moment.

## Lighting menu

Brightness, Effect, Speed and Colour of the main effect. The effect list: Off, Solid, Breathing, Color Cycle, Rainbow
Wave, Rainbow Rise, Reactive, Tap Off, Colorful, Flowing, Shuttle, Per-key. Speed and colour do not apply to Per-key
(and Off). The changes are applied at once and stored about 3 seconds after the last change.

## Galaxy80 menu

| Section | Item | Meaning |
|---|---|---|
| Gaming | Game Mode | Win lock + Alt+Tab/F4 block + Snap Tap |
| | Snap Tap | on/off (Fn+S) |
| | Snap Tap Mode | Off, Last Input, First Input, Second Input, Neutral |
| | Snap Tap on A and D / W and S / Left and Right / Up and Down | which pairs are cleaned |
| | Win Lock, Block Alt Tab, Block Alt F4 | as the Fn keys |
| | Swap WASD and Arrows | Fn+G |
| Keyboard | Debounce ms | 1-10, default 5 |
| | Eager Debounce | press accepted at once, release debounced |
| | Wireless NKRO | more than 6 keys over the radio (on by default) |
| | Firmware Version | read only |
| Power | Wireless Sleep After | Never (default), 1, 5, 30 minutes. **Experimental** ([battery-sleep.md](battery-sleep.md)) |
| | Low Battery Warning | Fn blinks continuously, or 5 times every 30 s |
| | Max Brightness on Battery | No limit, 75, 50, 25 percent (wireless on battery only) |
| | Charge LED when Wired | Fn key shows charging/full while on the cable |
| | Wired Idle Lights Off | Never, 1, 5, 10, 30 minutes without a key |
| Side Bar | Bar Mode | the 15 modes |
| | Gradient Start / End | colours of the Gradient and Gradient Flow modes |
| | Status Indicators | the status overlay on the bar |
| | Key Lights Off | key backlight off (Fn+T) |
| Key Colors | Paint Color | palette; clicking a key in the keymap view paints it with this colour |
| | Paint Turns Keys Off | the painter switches keys off instead of painting |
| | Keyboard Editor | the on-board editor (Fn+K) |
| | Editor Brush | a free colour used by the editor |
| | Reset Key Colors | back to the default table (unsaved until Apply) |
| | **Apply Key Colors** | store the painted colours |
| | **Discard Changes** | back to the stored colours |
| | Unsaved Key Colors | read only: Yes while there is unapplied paint |

## Painting keys, with Apply and Discard

1. Nothing to prepare: the first stroke (or entering the editor) copies the colours the running effect is showing into the
   table and switches to the **Per-key** effect, so the keys you do not touch keep their colour.
2. Pick a *Paint Color* and paint keys by clicking them in VIA; or turn *Keyboard Editor* on and use the keyboard
   itself ([keys.md](keys.md)).
3. While you paint nothing is written to flash; the change is only in RAM. Side-bar LED 5 blinks magenta when
   there is something unsaved and the editor is off.
4. **Apply Key Colors** (Fn+Enter in the editor) stores the table and the effect. It starts when you are not typing; 2
   green flashes confirm. **Discard Changes** (Fn+Delete in the editor) reloads the stored colours; 2 red flashes.
5. Replugging or a power cut with unsaved paint loses it. This is intended: you decide when the flash is written.

## Settings and what survives

Menu settings and the main effect are stored about 3 seconds after your last change. Keymap and macro changes
are stored as VIA sends them. A flash of new firmware wipes all of it. Save a layout file from VIA's *Save + Load*
first if you care.
