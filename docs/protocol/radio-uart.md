# Radio module serial protocol

Facts about the serial link between the main chip and the radio module (Bluetooth and the 2.4G dongle link), as
implemented in `keyboards/epomaker/galaxy80/g80_radio.c`. They were learned by observing the link on the real keyboard
and by testing; no vendor code or data is reproduced here. "[I]" marks things inferred and not confirmed.

## Physical layer

* UART2 of the SN32F248BF: **P0.0 = RX, P0.1 = TX**, 115200 baud (48 MHz / 16 / 26 = 115385), 8 data bits, no parity, 1 stop bit,
  FIFO enabled with a 1-byte receive trigger.
* **TX one byte per THRE.** The transmit holding register must be filled one byte at a time, each time the "holding register
  empty" flag is set (first byte from the main loop, the others from the interrupt). Writing several bytes back to back
  into the "FIFO" reached the module as garbage and was never answered.
* The P0.1 output latch is set to 1 when the port is initialised (the module line must idle high; whether this is
  needed by itself is unproven, it is harmless).
* A gap of about 2 ms after the transmitter is empty is kept between frames.

## Frame format

Every frame sent or received is a few bytes with a **checksum = sum of all previous bytes of the frame, modulo 256**
(the last byte). There is no length byte; the first byte decides the length.

### Keyboard to module

| First byte | Frame | Length | Content |
|---|---|---|---|
| `A1` | keyboard report | 10 | `A1 mods 00 k1 k2 k3 k4 k5 k6 sum`: modifier bits and six key usages |
| `A2` | extra NKRO bitmap | 16 | `A2 b0..b13 sum`: bitmap of the key usages 0x00..0x6F (bit n of byte n/8) |
| `A3` | consumer key | 4 | `A3 usage_lo usage_hi sum` |
| `A4` | system control | 3 | `A4 bits sum`: bit 0 Power, bit 1 Sleep, bit 2 Wake, 0 = released [I: bit order] |
| `A6` | command | 3 | `A6 code sum` |
| `A8` | mouse | 7 | `A8 buttons x y wheel pan sum` [I: field order] |

Hybrid NKRO: the A1 frame carries the modifiers and the first five pressed keys in press order (the sixth slot is
zero); further keys with a usage below 0x70 go into the A2 bitmap, which is sent only when it changes (and once more,
all zero, after the last extra key is released). With the option off, A1 carries six keys and A2 is never sent.
Newer reports of the same kind replace a queued one.

Commands (`A6 code`):

| Code | Meaning |
|---|---|
| `00` | wake the module / reconnect (never retried; sent before a data frame while the link is not connected) |
| `11` | USB mode (sent when the slider goes OFF and 0.5 s after boot with the slider OFF) |
| `30` | select 2.4G |
| `31` `32` `33` | select Bluetooth 1, 2, 3 |
| `51` | start pairing (sent three times, 0.5 s apart, after the channel key is held 3 s) |
| `53` | request the battery level |

Only these codes are ever sent: a whitelist in the driver refuses every other code. The firmware
also never sends frames of type `A9` (name) or `AB` (events), and does not answer the 2.4G configuration tunnel.

### Module to keyboard

| First byte | Frame | Length | Content |
|---|---|---|---|
| `5A` | host LED state | 3 | `5A leds sum`: Caps/Num/Scroll bits as the host sets them; also means "connected" |
| `5B` | status | 3 | `5B code sum` |
| `5C` | battery level | 3 | `5C percent sum` |
| `5D`, `60` | (reserved start bytes, accepted as 3-byte frames, not used) | 3 | |
| `61` | acknowledge | 3 | `61 0D 0A` |
| `81` | 2.4G configuration tunnel | 34 | ignored: not served, not acknowledged |

`5B` codes:

| Code | Meaning |
|---|---|
| `31` | pairing |
| `32` | connected |
| `33` | link lost |
| `21` | battery low |
| `22` | battery critical |
| `23` | battery normal |
| `42`, `43` | module ready / UART closing: no state, no acknowledgement |

## Acknowledgement and retry

* Every valid 3-byte frame from the module (except `5B 42/43`) is acknowledged by the keyboard with `61 0D 0A`.
* The keyboard's own data frames are acknowledged by the module with `61 0D 0A`. A frame without acknowledgement is resent
  after 5 ms, at most 3 times in all, then dropped (a keyboard or NKRO report that is dropped is queued once more
  so the last key state arrives).
* Outgoing frames wait in a queue of 6; a newer report of the same kind replaces a queued one.

## Receiving

* A frame must arrive within about 3 ms; a partial frame older than that is discarded. A byte that is not a known start byte
  is discarded and counted as a framing error.
* Frames with a wrong checksum are counted and ignored.

## Timing and sequences

* Boot: 0.5 s after start, with the slider OFF, `A6 11`.
* Slider ON: `A6 <channel>`; slider OFF: `A6 11`.
* While linked in wireless mode the battery is requested (`A6 53`) every 60 s, after a wake-up, and while the Fn+Space gauge is held
  (every 2 s).
* The module's `5B 31 / 32 / 33` drive the channel key indicator; `5B 21 / 22 / 23` drive the battery handling.
