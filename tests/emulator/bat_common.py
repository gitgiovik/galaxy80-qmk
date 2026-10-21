# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared helpers of the battery / indicator suites (suite_battery*.py): a simulated radio module, LED frame
readout, timer jump, patching of the weak option getters and a call recorder. Test code only; the emulator
(g80emu.py) is not modified.

The suites run on the VIA image (fw_dir/epomaker_galaxy80_via.bin, nm_via.txt)."""
import gc
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *  # noqa: E402,F401,F403

FW = sys.argv[1]
KIND = 'via'
HERE = os.path.dirname(os.path.abspath(__file__))
EFFECT_IDS = json.load(open(os.path.join(HERE, 'via_effect_ids.json')))

FN, SPACE, Z_, K_ = (5, 11), (5, 6), (4, 2), (3, 8)
Q_, E_, R_ = (2, 1), (2, 3), (2, 4)
NO_LED = 0xFF
LED_FN, LED_CAPS, LED_BAR0, LED_Q = 82, 51, 88, 35
LED_MAP = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16],
           [17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33],
           [34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50],
           [51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, NO_LED, 63, NO_LED, NO_LED, NO_LED],
           [64, NO_LED, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, NO_LED, 75, NO_LED, 76, NO_LED],
           [77, 78, 79, NO_LED, NO_LED, NO_LED, 80, NO_LED, NO_LED, NO_LED, 81, 82, 83, 84, 85, 86, 87],
           [NO_LED] * 12 + [88, 89, 90, 91, 92]]
POS = {v: (r, c) for r, row in enumerate(LED_MAP) for c, v in enumerate(row) if v != NO_LED}
DUTY = 252  # driver cap (G80_DUTY_MAX)

CHG = (3, 5)    # P3.5 low = charging
FULL = (3, 4)   # P3.4 high = charge complete
SLIDER = (0, 7)

results = []


def check(tid, ok, detail):
    ok = bool(ok)
    results.append(ok)
    print('%s  %-12s %s' % ('PASS' if ok else 'FAIL', tid, detail), flush=True)


def done():
    print('%d/%d PASS' % (sum(results), len(results)), flush=True)


class Module:
    """Parses what the keyboard sends, ACKs it, can inject frames (only ever at about the current time:
    uart_rx is a time-ordered queue)."""
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

    def frame(self, kind, val):
        """module frame 5A / 5B / 5C with the stock checksum, sent now"""
        self.send(self.m.t_ms() + 1.0, (kind, val, (kind + val) & 0xFF))


def mk(slider_on=False, **kw):
    gc.collect()  # unicorn segfaults in mem_map with many live engines
    pins = {SLIDER: 1 if slider_on else 0}
    pins.update(kw.pop('pins', {}))
    m = G80(FW + '/epomaker_galaxy80_%s.bin' % KIND, FW + '/nm_%s.txt' % KIND, ext_pins=pins, **kw)
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.schedule = []
    return m, Module(m)


def run(m, mod, t_end, step=1.0):
    t = m.t_ms()
    while t < t_end:
        t = min(t_end, t + step)
        m.run(t)
        mod.poll()


def hold(m, t0, t1, *keys):
    for k in keys:
        m.schedule.append((t0, t1, k))


def frames(mod, t0=0, t1=1e9, kind=None, second=None):
    return [(t, f) for t, f, st in mod.frames
            if t0 <= t < t1 and (kind is None or f[0] == kind) and (second is None or f[1] == second)]


def led(m, i):
    """logical (r, g, b) of an LED as the firmware drew it (frame values, driver cap 252; the bar's R and G
    are swapped on the PCB and swapped back here)"""
    f = bytes(m.uc.mem_read(m.sym['g80_frame'], 21 * 17))
    r, c = POS[i]
    R, G, B = f[r * 17 + c], f[(7 + r) * 17 + c], f[(14 + r) * 17 + c]
    return (G, R, B) if i >= 88 else (R, G, B)


def sample(m, mod, t0, t1, step, fn):
    out = []
    t = t0
    while t <= t1:
        run(m, mod, t)
        out.append((t, fn()))
        t += step
    return out


def edges(samples, minrun=0):
    """times at which a boolean sample series changes value. The LED engine has one frame buffer that the
    ISR shows as soon as it is written: for a few ms of every rgb_matrix pass an indicator colour is
    replaced by the effect colour and drawn again, and a sample that lands there is a glitch. Runs shorter
    than minrun ms are dropped (taken as part of the previous run)."""
    runs = []
    for t, v in samples:
        if runs and runs[-1][1] == v:
            continue
        runs.append([t, v])
    if minrun:
        out = []
        for i, (t, v) in enumerate(runs):
            nxt = runs[i + 1][0] if i + 1 < len(runs) else samples[-1][0]
            if out and (nxt - t) < minrun:
                continue
            if out and out[-1][1] == v:
                continue
            out.append([t, v])
        runs = out
    return [r[0] for r in runs[1:]]


def frac(samples):
    return sum(1 for _, v in samples if v) / max(1, len(samples))


def solid(m, t, h, s=255, v=255):
    """solid colour effect, no EEPROM write, injected at time t"""
    def f(mm):
        mm.call('rgb_matrix_enable_noeeprom')
        mm.call('rgb_matrix_mode_eeprom_helper', EFFECT_IDS['SOLID_COLOR'], 0)
        mm.call('rgb_matrix_sethsv_eeprom_helper', h, s, v, 0)
    m.inject.append((t, f))


def tjump(m, ms):
    """timer_read32() jumps forward by ms (QMK's chibios timer keeps ms_offset), to test minutes-long timers
    without emulating them; keep below 65 s so that 16-bit timers cannot wrap"""
    a = m.sym['ms_offset']
    v = struct.unpack('<I', bytes(m.uc.mem_read(a, 4)))[0]
    m.uc.mem_write(a, struct.pack('<I', v + ms))


def patch_getter(m, name, value):
    """Replaces a weak option getter (4 bytes: movs r0,#0 / bx lr) by one returning value."""
    a = m.sym[name] & ~1
    m.uc.mem_write(a, bytes([value & 0xFF, 0x20, 0x70, 0x47]))
    m.uc.ctl_flush_tb()  # the getter is already translated: drop the cached blocks


def poke(m, sym, data):
    m.uc.mem_write(m.sym[sym], data)


def recorder(m, name):
    """records (time_ms, r0 & 0xFF) at every entry of the function"""
    calls = []
    a = m.sym[name] & ~1

    def hk(uc, address, size, user):
        calls.append((m.t_ms(), uc.reg_read(UC_ARM_REG_R0) & 0xFF))
    m.uc.hook_add(UC_HOOK_CODE, hk, None, a, a + 1)
    return calls


def ok_sys(m):
    return not m.faults and not m.resets and m.isp is None
