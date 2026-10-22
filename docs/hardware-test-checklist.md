# Hardware test checklist

The 1.0.0 additions have been tested in the emulator only. This list is how they move to "hardware-tested". Anyone with
a supported board can run it; please report results in an issue (pass or fail, image, connection mode).

Rules:

* Do the items **in order**. H-0 comes first and before anything else: it proves that you can always get back.
* Test on the cable first, wireless later, sleep last.
* Flash only from the ROM bootloader, with the slider OFF and the cable direct ([install.md](install.md)). Write down
  the image name and checksum you flashed.
* Never test the bootloader command of the vendor updater against this firmware, and never run the vendor updater at all.
* "Replug" means: slider OFF, unplug, wait 30 s, plug in with no key held.

## Recovery and boot

| Id | Do | Expected |
|---|---|---|
| H-0 | Flash the image. Replug holding **P**, then (separately) holding **Esc**, then run the firmware and press Fn+RShift+Esc. Check the USB ID each time, then replug without keys | `0C45:7040` each time, keyboard dark; replug without keys returns to 4758:0080. Then flash the image again |
| H-1 | Use the keyboard on the cable and on battery (slider ON), then replug | boots on both; wired typing works, VIA connects, settings persist across a replug |

## Stability under VIA (via image)

| Id | Do | Expected |
|---|---|---|
| H-V1 | For 10 minutes, with a key tester open: drag the brightness, speed and colour sliders continuously, paint 80+ keys, remap 30 keys, write a 150-character macro, all while typing | no LED darkening or freeze over 0.1 s, no lost or stuck key, VIA never disconnects |
| H-V2 | After H-V1, replug | all changes are present, except paint that was never applied |
| H-V3 | (optional) Force a clean-up of the settings band by remapping many keys one after another, typing while you do | no lost key; report anything odd |

## Paint

| Id | Do | Expected |
|---|---|---|
| H-P1 | Fn+K, paint 20 keys with the keyboard, press Fn+Enter, replug | 2 green flashes at Apply; colours kept after the replug |
| H-P2 | Paint, then Fn+Delete | 2 red flashes, colours revert at once |
| H-P3 | Paint in VIA, press *Apply Key Colors*; replug. Then paint again without Apply and replug | the first set is kept, the second is lost |
| H-P4 | Paint, leave the editor with Fn+K | side-bar LED 5 blinks magenta slowly until Apply or Discard |

## Regression of earlier features

| Id | Do | Expected |
|---|---|---|
| H-2 | Go through the editor, side-bar modes, game mode, Snap Tap (Fn+S), debounce setting, Caps Word, Mac/Win switch, factory reset (Fn+Backspace 3 s), and the three effects in VIA (Solid, Rainbow, Per-key) | all as in the key reference |
| H-2a | Wired, switch the PC off with USB power staying on (if you can) | the LEDs go dark and come back with the PC |

## New keys and options

| Id | Do | Expected |
|---|---|---|
| H-K3 | Assign `CL_TOGG` (Ctrl/Caps swap) on *Any*, press it, wait 5 s, replug | the swap works at once and is still active after the replug; no stutter of the LEDs when pressing |
| H-K5 | Snap Tap Mode Last, First, Second, Neutral, held A+D and W+S in a key tester | the documented resolution in each mode |
| H-K6 | Assign an `MT()` key (for example `MT(MOD_LCTL, KC_A)`), tap it and hold it | tap types the letter, a hold over 200 ms is the modifier |

## Wireless

| Id | Do | Expected |
|---|---|---|
| H-W1..4 | Pair 2.4G, Bluetooth 1, 2 and 3 (hold Fn+R/Q/W/E 3 s); replug and reconnect each | pairing works, the channel key blinks as documented, reconnect works after a replug |
| H-W5 | Hold 10 keys at once over Bluetooth and over 2.4G, in a key tester; then switch *Wireless NKRO* off | all 10 keys show; with the option off only 6 |
| H-W6 | Assign a Sleep (system control) key and press it over Bluetooth | the host sleeps. If not, report: the bit order is inferred |
| H-W7 | Disconnect the host while Caps Lock is on | the Caps LED clears; the channel key blinks as for a lost link |

## Battery

| Id | Do | Expected |
|---|---|---|
| H-B1 | Hold Fn+Space on battery; compare with the level shown by the OS or the vendor software | within one bar LED. Note which end of the bar fills first |
| H-B1w | The same with the slider OFF (cable) | gauge shows a value, or the white "unknown" blink; report which |
| H-B2 | Charge LED (Fn key) wired and wireless | red while charging, green when full; wired only if *Charge LED when Wired* is on |
| H-B3 | Set *Max Brightness on Battery* to 50 % | the LEDs dim on battery, return on the cable; the brightness setting itself is unchanged |
| H-L4 | Set *Low Battery Warning* each way (only when the battery is really low) | Fn blinks as chosen |
| H-L5 | On the cable: *Lights Off After* = 1 min, wait without typing, then type a few letters | the LEDs go dark after about a minute; the letters are typed and the LEDs come back with the first one |
| H-L6 | The same on battery (slider ON, Deep Sleep After = Never); then plug the charger in and unplug it while dark | dark after a minute, a key lights them; neither the plug-in nor the unplug lights them; Fn+Space still shows the charge state |
| H-L7 | Factory defaults (after the flash): leave the keyboard alone on the cable | the LEDs go dark after about 5 minutes |

## Sleep (EXPERIMENTAL; do these last, on battery, with a plan to recover)

Have the recovery steps from [recovery.md](recovery.md) at hand. If a test hangs, switch the slider OFF, unplug and wait 30 s. If the keyboard ever does not wake: slider to OFF and unplug the cable (that removes all power), then set *Deep Sleep After* back to Never from VIA over the cable.

| Id | Do | Expected |
|---|---|---|
| H-S1 | First deep-sleep test: *Lights Off After* = 1 min, *Deep Sleep After* = **5 min** (check key wake and slider wake separately, H-S2 and H-S3, before leaving it on), keyboard connected | after about a minute the LEDs go off (keys still type); about 5 minutes after the last key the keyboard deep-sleeps |
| H-S2 | Press a key | wakes; record whether the first key press reaches the host; no stuck key |
| H-S3 | Sleep, then slider to OFF | wakes into USB mode and types on the cable |
| H-S4 | Not connected to any host, *Deep Sleep After* = 5 min | sleeps about 15 s after the last key (stock timing while unlinked) |
| H-S5 | 20 sleep and wake cycles | all complete; no hang |
| H-S6 | Wake, check Bluetooth reconnect. H-S6b: the same with 2.4G, time the reconnect | reconnects; note the latency |
| H-S7 | Leave it asleep overnight, then press a key | wakes; the battery drained only a little |
| H-S8 | Option = Never | still awake after 35 min |

## Lighting

| Id | Do | Expected |
|---|---|---|
| H-L3 | Fn+Down to off and Fn+Up back | steps 100 / 75 / 50 / 25 % / off; Fn+Up from off turns the LEDs on |
| H-G1 | Each Snap Tap mode in a key tester | as documented ([keys.md](keys.md)) |

## Back to stock (optional)

| Id | Do | Expected |
|---|---|---|
| H-R1 | Enter the ISP and flash your own copy of the vendor's image with the same flasher; then try the vendor updater | the stock firmware runs. The README's "Back to stock" is marked verified when this passes |
