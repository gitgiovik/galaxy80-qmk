# Key reference

Layer 0 is the standard layout of the Galaxy80 (the same HID table as the stock firmware, including the
three extra ISO/JIS positions the stock firmware maps). The **Fn** key (between Right Alt and Menu) turns
layer 1 on while held. Layer 2 of the VIA image is empty (transparent) and free to use.

## The Fn layer

| Fn + | Function | Notes |
|---|---|---|
| Esc | ROM bootloader | only while Right Shift is held (Fn + RShift + Esc); needs the real Fn, ignored while Layer Lock is on |
| F1 .. F12 | My Computer, Browser home, Calculator, Mail, Media select, Stop, Previous, Play/Pause, Next, Mute, Volume -, Volume + | stock media keys |
| F13 | Layer Lock | keeps the Fn layer on until Fn or F13 is pressed again. While locked the whole Fn layer is active, so Space shows the battery gauge, Enter changes the effect, and so on. Plain letter keys act as their Fn functions too (toggle settings, switch channels), so typing text is not possible until the lock is released. The two hold actions and the bootloader key are the exception: Backspace (factory reset) and Q/W/E/R (pairing) held 3 s do nothing while the layer is locked, so a long Backspace to delete text never wipes the keyboard, and RShift+Esc (for example Ctrl+Shift+Esc) does not enter the bootloader. They need the real Fn key (pressing Fn also releases the lock); Q/W/E/R still switch the channel on a single press |
| Pause | game mode on/off | Win lock + Alt+Tab block + Alt+F4 block + Snap Tap, all at once |
| Backspace (hold 3 s) | factory reset | 7 white flashes, then the keyboard restarts with the defaults. Refused (3 red flashes) when the battery is critical; ignored while Layer Lock is on (hold the real Fn) |
| Ins | all LEDs off/on | |
| Tab | block Left Alt+Tab on/off | key flashes green (on) or red (off) 3 times |
| Caps Lock | block Left Alt+F4 on/off | same flashes |
| Q, W, E, R | Bluetooth 1, 2, 3, 2.4G | only with the slider ON; hold 3 s to pair (with the real Fn, not under Layer Lock) |
| T | key backlight off/on | indicators (Caps, Win lock, bar) stay lit |
| \ | next palette colour | 8 stock colours: red, orange, yellow, green, cyan, blue, pink, white |
| S | Snap Tap on/off | flashes the S key |
| D | show desktop | Win+D, on Mac F11 |
| G | swap WASD with the arrow keys | flashes the G key |
| K | per-key colour editor on/off | see below |
| L | lock screen | Win+L, on Mac Ctrl+Cmd+Q |
| C | Caps Word | orange bar LED 1 while on |
| N | NKRO on/off | stored with the settings (VIA image) |
| Enter | next effect | with Shift: previous. Inside the editor: **Apply** |
| Delete | (inside the editor) **Discard** | outside the editor it types Delete as usual |
| Left Win | Win lock | blocks both Win keys and Menu |
| Right Shift | next side-bar mode | |
| Up / Down | brightness up / down | levels 100, 75, 50, 25 %, off. At the ends the four arrow keys flash white. Up from off lights the LEDs again |
| Right / Left | effect speed up / down | white arrow flashes at the ends |
| Space (hold) | battery gauge | see [battery-sleep.md](battery-sleep.md); nothing is sent to the host |

Fn+Q/W/E/R and Fn+Space send nothing to the host (they are the wireless and gauge keys). Every other key of the Fn layer not listed
is transparent and types its base key, including Fn+PrtSc (a keymap slot kept free for later).

The Win/Mac switch is read at run time. "Mac" swaps the Alt and GUI keys like the stock firmware does. The swap is
never saved, and the switch gives 3 blue (Mac) or 3 green (Win) flashes on all keys when you move it.

## Other defaults

* **Shift + Backspace = Delete** (VIA image). The Shift key is not sent with it.
* With the Win lock or game mode on, the Windows-key shortcuts of the OS keys below are blocked too.

## The per-key colour editor (Fn+K)

Press Fn+K: the editor copies the colours the running effect is showing into the table and switches to the *Per-key* effect.
While it is on, keys are not typed; the side bar alternates between its own colours and magenta.

* Press a key to give it the next of 16 presets (off, 14 hues, white). A held Shift goes backwards.
  Esc is an ordinary editable key.
* Fn + 1..5 edits the five side-bar LEDs. The VIA *Editor Brush* colour, when set, replaces the preset cycle.
* **Fn+Enter** applies (one flash write in the VIA image, 2 green flashes), **Fn+Delete** discards (2 red flashes, back to the
  stored colours), **Fn+K** leaves the editor without saving (the changes stay in RAM, marked by the magenta
  blink of side-bar LED 5). The editor also leaves by itself after 2 minutes without a key, likewise without
  saving.
* Fn+RShift+Esc, Fn+Backspace (factory reset) and the side-bar key keep working inside the editor.

## Side bar

Modes (Fn + Right Shift steps through them, or pick one in VIA): Follow Keys (default), Gradient, Gradient
Flow, Rainbow Wave, Color Cycle, Breathing, Red, Orange, Yellow, Green, Cyan, Blue, Pink, Status, Off.

*Status indicators* (VIA option, on by default) draw on top of any mode:

| Bar LED | Meaning |
|---|---|
| 1 | white = Caps Lock, orange = Caps Word; slow amber blink = slider is ON while the keyboard runs from USB |
| 2 | red = game mode, cyan = Snap Tap on |
| 5 | layer above 0: red = layer 1 (Fn), green = layer 2, blue = layer 3; slow magenta blink = unsaved key colours |

Other LEDs: the Caps Lock key is white while Caps Lock is on, the Left Win key is white while the Win lock is
on, the Fn key is red while charging, green for 60 s once the battery is full, and blinks red on a low battery.
The channel key (Q/W/E/R) shows the wireless link (see [wireless.md](wireless.md)).

## Custom keycodes for VIA

These are available in VIA's custom keycodes tab, in this order (never reordered, only appended):

Safe Boot (bootloader, with Right Shift held), Win Lock, Factory Reset, Next Color, Keys Off, Bar Mode, Lock
Screen, Show Desktop, WASD Swap, Alt Tab Block, Alt F4 Block, Snap Tap, Key Colors (editor), Game Mode,
**Sleep Now** (experimental), Mission Control, Launchpad, Siri, Task View, File Explorer, Snip.

| OS key | Sends |
|---|---|
| Mission Control, Launchpad | the macOS Mission Control and Launchpad keys |
| Siri | Cmd+Space held for 0.5 s |
| Task View | Win+Tab |
| File Explorer | Win+E |
| Snip | Win+Shift+S |

QMK keycodes can be typed on VIA's *Any* key. These are handled specially:

* `RM_*` and `UG_*` (RGB): applied at once, saved with the settings after a few seconds, never inside the key event.
* QMK *magic* keycodes (`CL_*`, `AG_*`, `GU_*`, `GE_*`, `BS_*`, `NK_*`, `EC_*`, `CG_*`...): the change is made at once and
  saved with the settings. Exception: the Win/Mac switch owns the Alt/GUI swap, so an `AG_*` change lasts only until
  the next replug or switch move. `EH_LEFT`/`EH_RGHT` do nothing (split keyboards only).
* `PDF(n)` (persistent default layer): the default layer changes on the release and is saved with the settings. A layer
  the keymap does not have (`PDF(3)` and up in the `via` image) is ignored.
* `OS_TOGG`, `OS_ON`, `OS_OFF` (one-shot keys on/off): the change is made at once and saved with the settings.
* `EE_CLR` is the factory reset **on a single press**, with no hold and no confirmation: a key with `EE_CLR` wipes the
  keymap, macros, per-key colours and settings the moment it is pressed (refused only on a critical battery).
* Caps Word (`CW_TOGG`), Repeat Key, Layer Lock (`QK_LLCK`), Mouse Keys and `MT()`/`LT()` tap-hold keys work. For the
  tap-hold keys, Chordal Hold (a hold counts only together with a key of the other hand) and Flow Tap
  (a tap-hold key typed within 150 ms of the previous key is a tap) are on, tapping term 200 ms.
* Not built: Key Lock, Dynamic Macros, Combos, Tap Dance, Leader Key, Autocorrect (RAM).

## Gaming functions

* **Snap Tap / SOCD.** Pairs: A/D, W/S, Left/Right, Up/Down. When both keys of a pair are held, the mode decides: *Last
  Input* (the newest wins, the other returns when the newest is released), *First* or *Second* Input (always the
  first / second key of the pair, A, W, Left, Up / D, S, Right, Down), or *Neutral* (neither). With the mode Off
  (the factory default) the Fn+S switch and game mode behave as "last input". Any other mode turns the cleaning
  on by itself. Each pair can be switched off in VIA. Off after a factory reset. Some games and
  tournaments forbid it: you decide per game.
* **Swap WASD and arrows** (Fn+G) works together with Snap Tap.
* **Game mode** (Fn+Pause): Win lock, Alt+Tab and Alt+F4 blocking (left Alt only) and Snap Tap.
* Debounce 1-10 ms (default 5 ms) and an eager mode (press at once, release deferred) in VIA.
