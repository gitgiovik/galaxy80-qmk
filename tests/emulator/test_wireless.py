"""Stage 7b wireless mode (g80_radio.c) on the VIA image, with a simulated radio module.
usage: python test_wireless.py <fw_dir> [kind=via]
The UART model (g80emu.py, stage 7b) has no TX FIFO: a TH write over a full holding register is
recorded in uart_overwrites (the 2026-10-19 hardware bug). The simulated module ACKs every frame
it receives (61 0D 0A ~0.3 ms after the last byte) unless .ack is False, and can send its own frames.
W1  slider OFF at boot: A6 11 once ACKed, USB untouched, keys stay on USB (no A1 frame)
W2  slider ON at boot: wireless entered: USB stopped, A6 31 (BT1 default) sent
W3  key A in wireless: A6 00 (link not up) + A1 00 00 04 .. cs, then the release; after 5B 32 no A6 00
W4  Fn+R -> A6 30 and the channel saved in eeconfig_kb (bits 22..23 = 3) ~3 s later
W5  Fn+Q held 3.7 s -> A6 31, then A6 51 x3 0.5 s apart; nothing typed on the host
W6  Vol- (Fn+F11) -> A3 EA 00 cs, then A3 00 00 cs
W7  module silent: every keyboard frame tried 3 times, queued again once (3 more), then dropped; the
    next key still goes out
W10 PC asleep on the cable, slider ON for ~60 ms only (blip): USB stopped by the suspend loop, then
    restarted because the debounced slider never went ON (review 7b #1)
W11 Fn+Q held, slider OFF after 1 s: no A6 51 in USB mode (review 7b #2)
W8  slider back OFF: A6 11, USB restarted, keys on USB again
W9  PC asleep on the cable (USB suspended) and slider ON: the suspend loop ends, wireless entered
W*  whole run: no TX overwrite, every frame a valid stock frame, no fault / reset / ISP
"""
import gc
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *

FW = sys.argv[1]
KIND = sys.argv[2] if len(sys.argv) > 2 else 'via'
FN, A_, Q, R, F11 = (5, 11), (3, 1), (2, 1), (2, 4), (0, 11)
USB_STOP, USB_SUSPENDED = 1, 5
results = []


def check(tid, ok, detail):
    results.append(ok)
    print('%s  %-4s %s' % ('PASS' if ok else 'FAIL', tid, detail), flush=True)


class Module:
    """parses what the keyboard sends, ACKs it, can inject frames"""
    LEN = {0xA1: 10, 0xA3: 4, 0xA6: 3, 0xA8: 7, 0x61: 3}

    def __init__(self, m):
        self.m, self.pos, self.frames, self.ack, self.buf, self.last_t = m, 0, [], True, [], 0

    def poll(self):
        tx = self.m.uart_tx
        while self.pos < len(tx):
            t, b = tx[self.pos]
            self.pos += 1
            if self.buf and t - self.last_t > 1.0:
                self.frames.append((self.buf_t, tuple(self.buf), 'TRUNC'))
                self.buf = []
            if not self.buf:
                self.buf_t = t
            self.buf.append(b)
            self.last_t = t
            n = self.LEN.get(self.buf[0])
            if n is None:
                self.frames.append((t, tuple(self.buf), 'BAD'))
                self.buf = []
            elif len(self.buf) == n:
                f = tuple(self.buf)
                ok = (sum(f[:-1]) & 0xFF) == f[-1] if f[0] != 0x61 else f == (0x61, 0x0D, 0x0A)
                self.frames.append((self.buf_t, f, 'OK' if ok else 'CKSUM'))
                self.buf = []
                if self.ack and f[0] != 0x61:
                    self.send(t + 0.3, (0x61, 0x0D, 0x0A))

    def send(self, t, frame):
        self.m.uart_rx += [(t + 0.09 * i, b) for i, b in enumerate(frame)]


def mk(slider_on=False):
    gc.collect()  # unicorn segfaults in mem_map with many live engines
    m = G80(FW + '/epomaker_galaxy80_%s.bin' % KIND, FW + '/nm_%s.txt' % KIND, ext_pins={(0, 7): 1 if slider_on else 0})
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.schedule = []
    return m, Module(m)


def run(m, mod, t_end, step=1.0):
    t = m.t_ms()
    while t < t_end:
        t = min(t_end, t + step)
        m.run(t)
        mod.poll()


def usb_state(m):
    return m.uc.mem_read(m.sym['USBD1'], 1)[0]


def frames(mod, t0=0, t1=1e9, kind=None):
    return [f for t, f, st in mod.frames if t0 <= t < t1 and (kind is None or f[0] == kind)]


def ok_sys(m):
    return not m.faults and not m.resets and m.isp is None


def tap(m, t, keys, hold=60):
    for k in keys:
        m.schedule.append((t, t + hold, k))


def fn(m, t, key, hold=60):
    m.schedule += [(t, t + hold + 40, FN), (t + 10, t + 10 + hold, key)]


def cfg(m):
    return struct.unpack('<I', bytes(m.uc.mem_read(m.sym['g80_config'], 4)))[0]


all_bad, all_over = [], []

# W1
m = None  # free the previous unicorn engine first
m, mod = mk(False)
run(m, mod, 1500)
tap(m, 1600, [A_])
run(m, mod, 2000)
f = frames(mod)
check('W1', f[:1] == [(0xA6, 0x11, 0xB7)] and not frames(mod, kind=0xA1) and usb_state(m) != USB_STOP and ok_sys(m),
      'frames %s, USB state %d' % (f[:4], usb_state(m)))
all_bad += [x for x in mod.frames if x[2] != 'OK']; all_over += m.uart_overwrites

# W2..W8 on one run
m = None  # free the previous unicorn engine first
m, mod = mk(True)
run(m, mod, 1500)
f = frames(mod)
check('W2', usb_state(m) == USB_STOP and (0xA6, 0x31, 0xD7) in f and (0xA6, 0x11, 0xB7) not in f,
      'USB state %d, frames %s' % (usb_state(m), f[:4]))

tap(m, 1600, [A_])
run(m, mod, 1800)
f = frames(mod, 1600, 1800)
press = (0xA1, 0, 0, 0x04, 0, 0, 0, 0, 0, 0xA5)
rel = (0xA1, 0, 0, 0, 0, 0, 0, 0, 0, 0xA1)
ok3a = f.count(press) == 1 and f.count(rel) == 1 and (0xA6, 0x00, 0xA6) in f and f.index((0xA6, 0x00, 0xA6)) < f.index(press)
mod.send(1800, (0x5B, 0x32, 0x8D))     # connected
run(m, mod, 1850)
tap(m, 1900, [A_])
run(m, mod, 2100)
f2 = frames(mod, 1900, 2100)
check('W3', ok3a and f2.count(press) == 1 and f2.count(rel) == 1 and (0xA6, 0x00, 0xA6) not in f2,
      'link down: %s | after 5B 32: %s' % (f, f2))

fn(m, 2200, R)
run(m, mod, 6000)
f = frames(mod, 2200, 2500)
mode = (cfg(m) >> 22) & 3
check('W4', (0xA6, 0x30, 0xD6) in f and mode == 3 and not frames(mod, 2200, 2500, 0xA1) or (0xA6, 0x30, 0xD6) in f and mode == 3,
      'frames %s, eeconfig_kb wl_mode=%d (cfg %#x)' % (f, mode, cfg(m)))

m.schedule += [(6100, 9900, FN), (6110, 9800, Q)]
run(m, mod, 10200)
f = frames(mod, 6100, 10200)
t51 = [round(t - 6110) for t, fr, st in mod.frames if 6100 <= t < 10200 and fr == (0xA6, 0x51, 0xF7)]
keys = [fr for fr in f if fr[0] == 0xA1 and any(fr[3:9])]
check('W5', (0xA6, 0x31, 0xD7) in f and len(t51) == 3 and all(abs(a - b) < 80 for a, b in zip(t51, (3000, 3500, 4000))) and not keys,
      'A6 51 at +%s ms, typed keys %s' % (t51, keys))

fn(m, 10300, F11)
run(m, mod, 10600)
f = frames(mod, 10300, 10600, 0xA3)
check('W6', (0xA3, 0xEA, 0x00, 0x8D) in f and (0xA3, 0, 0, 0xA3) in f, 'consumer frames %s' % f)

mod.ack = False
n0 = len(mod.frames)
tap(m, 10700, [A_])
run(m, mod, 11000)
f = frames(mod, 10700, 11000, 0xA1)
mod.ack = True
tap(m, 11100, [A_])
run(m, mod, 11300)
f2 = frames(mod, 11100, 11300, 0xA1)
lost = m.uc.mem_read(m.sym['g80_radio_stats'] + 0, 1)  # (just to touch the symbol)
# review 7b: a keyboard report given up after 3 tries is queued again once (lost release = stuck key)
check('W7', f.count(press) == 6 and f.count(rel) == 6 and f2.count(press) == 1 and f2.count(rel) == 1,
      'silent module: press x%d release x%d; then ACKing: %s' % (f.count(press), f.count(rel), f2))

m.ext_pins[(0, 7)] = 0
run(m, mod, 12000)
f = frames(mod, 11300, 12000)
tap(m, 12100, [A_])
run(m, mod, 12400)
check('W8', (0xA6, 0x11, 0xB7) in f and usb_state(m) != USB_STOP and not frames(mod, 12100, 12400, 0xA1) and ok_sys(m),
      'frames after OFF %s, USB state %d, A1 after OFF %s, faults %s resets %s' % (f, usb_state(m), frames(mod, 12100, 12400, 0xA1), m.faults[:1], m.resets))
all_bad += [x for x in mod.frames if x[2] != 'OK']; all_over += m.uart_overwrites

# W9: USB suspended (PC asleep on the cable), then slider ON
m = None  # free the previous unicorn engine first
m, mod = mk(False)
run(m, mod, 1000)
usbd = m.sym['USBD1']
m.uc.mem_write(usbd, bytes([USB_SUSPENDED]))
run(m, mod, 1500)
m.ext_pins[(0, 7)] = 1
run(m, mod, 2500)
f = frames(mod, 1500, 2500)
check('W9', usb_state(m) == USB_STOP and (0xA6, 0x31, 0xD7) in f and ok_sys(m), 'USB state %d, frames %s' % (usb_state(m), f[:3]))
all_bad += [x for x in mod.frames if x[2] != 'OK']; all_over += m.uart_overwrites

# W10
m = None  # free the previous unicorn engine first
m, mod = mk(False)
run(m, mod, 1000)
m.uc.mem_write(m.sym['USBD1'], bytes([USB_SUSPENDED]))
run(m, mod, 1300)
m.ext_pins[(0, 7)] = 1
run(m, mod, 1360, 0.5)
st_mid = usb_state(m)
m.ext_pins[(0, 7)] = 0
run(m, mod, 2500)
check('W10', st_mid == USB_STOP and usb_state(m) not in (USB_STOP,) and not m.uc.mem_read(m.sym['g80_wl_on'], 1)[0] and ok_sys(m),
      'USB state during the blip %d, after %d, wl_on %d' % (st_mid, usb_state(m), m.uc.mem_read(m.sym['g80_wl_on'], 1)[0]))
all_bad += [x for x in mod.frames if x[2] != 'OK']; all_over += m.uart_overwrites

# W11
m = None  # free the previous unicorn engine first
m, mod = mk(True)
run(m, mod, 1500)
m.schedule += [(1600, 6000, FN), (1610, 5900, Q)]
run(m, mod, 2600)
m.ext_pins[(0, 7)] = 0
run(m, mod, 6500)
f = frames(mod, 1600, 6500)
check('W11', (0xA6, 0x51, 0xF7) not in f and (0xA6, 0x11, 0xB7) in f and ok_sys(m), 'frames %s' % f)
all_bad += [x for x in mod.frames if x[2] != 'OK']; all_over += m.uart_overwrites

check('W*', not all_bad and not all_over, 'bad/truncated frames %s, TX overwrites %s' % (all_bad[:3], all_over[:3]))
print('%d/%d PASS' % (sum(results), len(results)))
