# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""P2 features (design 16, sec. 2.3): Layer Lock, growing gauge, number-row battery mirror, key override.
usage: python suite_p2.py <fw_dir>      (VIA image, simulated module)

T-LLCK-1   Fn+F13 (QK_LLCK) locks the Fn layer: layer 1 stays on after Fn is released, 1 still types 1
           (transparent), F1 gives the Fn-layer media key; Fn pressed again unlocks (layer 0, nothing locked)
T-LLCK-2   Layer Lock does not arm the hold-to-confirm factory reset: with the Fn layer locked, Backspace (= G80_FRST
           there) held 3.5 s starts nothing (no white flashes, no reset, layer still locked); after the unlock,
           Fn+Backspace held 3.5 s does start it (control)
T-LLCK-3   Layer Lock does not arm the 3 s pairing either (wireless): locked, R held 4.5 s switches to 2.4G (A6 30)
           but sends no pairing request (A6 51); Fn+R held 4.5 s sends the three A6 51 (control)
T-LLCK-4   Layer Lock does not arm the bootloader key: locked, RShift (= G80_BAR there, also a Right Shift) + Esc
           (= G80_SBOOT there) and Ctrl+RShift+Esc give no reset and no ISP; after the unlock Fn+RShift+Esc enters
           the ROM ISP (control)
T-GAUGE-7  growing animation: after the Fn+Space press the bar fills LED by LED, one more every 50 ms
           (G80_GAUGE_STEP_MS, seen with up to ~30 ms of frame delay), never fewer LEDs than before, all 5 at
           100 % from about 200 ms on
T-GAUGE-8  number-row mirror while Fn+Space is held: keys 1..0 = 10 % each in the bar colour (100 %: all
           green; 37 %: 1..4 yellow, 5..0 dark; 0 %: all dark), growing with the bar (at most two keys per
           bar LED); after the release the effect colour is back on the number row
T-KO-1     key override Shift+Backspace = Delete: Delete without Shift in the report; Backspace alone and
           Shift+A unchanged
"""
import struct
from bat_common import *

F13, F1, ONE, A_, LSFT, BSPC = (0, 13), (0, 1), (1, 1), (3, 1), (4, 0), (1, 13)
ESC, RSFT, LCTL = (0, 0), (4, 13), (5, 0)
NUM = list(range(18, 28))                    # LEDs of the keys 1..0
BAR = [LED_BAR0 + i for i in range(5)]
HID_1, HID_A, HID_BSPC, HID_DEL = 0x1E, 0x04, 0x2A, 0x4C


def ram32(m, a):
    return struct.unpack('<I', bytes(m.uc.mem_read(a, 4)))[0]


def u8(m, sym):
    """layer_state_t is 8 bits here (QMK: at most 8 layers)"""
    return bytes(m.uc.mem_read(m.sym[sym], 1))[0]


def report(m):
    """(mods, set of HID codes) from the 6KRO and NKRO report structures"""
    keys, mods = set(), 0
    kp = m.sym.get('keyboard_report')
    if kp is not None:
        b = m.ram_read(ram32(m, kp), 8)
        mods |= b[0]
        keys |= {k for k in b[2:8] if k}
    np_ = m.sym.get('nkro_report')
    if np_ is not None:
        b = m.ram_read(ram32(m, np_), 32)
        mods |= b[1]
        for i, byte in enumerate(b[2:32]):
            for bit in range(8):
                if byte >> bit & 1:
                    keys.add(i * 8 + bit)
    return mods, keys


def consumer(m):
    return struct.unpack('<H', bytes(m.uc.mem_read(m.sym['last_consumer_usage'], 2)))[0]


def lit(c):
    return max(c) > 20


def green(c):
    """the 100 % gauge colour. Counting only it skips the frame-buffer glitches (a read between the effect
    pass and the indicator pass shows the blue effect) and the amber slider blink of bar LED 1"""
    return c[1] > 200 and c[0] < 20 and c[2] < 20


# ------------------------------------------------------------------ T-LLCK-1
m, mod = mk(False)
solid(m, 250, 170)
run(m, mod, 1500)
hold(m, 1600, 1700, FN)
hold(m, 1620, 1660, F13)                     # Fn+F13 = Layer Lock
run(m, mod, 1800)
ls1, lk1 = u8(m, 'layer_state'), u8(m, 'locked_layers')
hold(m, 1900, 1960, ONE)
run(m, mod, 1930)
r1 = report(m)
run(m, mod, 2000)
hold(m, 2100, 2160, F1)                      # Fn layer: KC_MYCM (AL Local Machine Browser, 0x194)
run(m, mod, 2130)
c1 = consumer(m)
run(m, mod, 2300)
hold(m, 2400, 2460, FN)                      # Fn again: unlock
run(m, mod, 2600)
ls2, lk2 = u8(m, 'layer_state'), u8(m, 'locked_layers')
hold(m, 2700, 2760, ONE)
run(m, mod, 2730)
r2 = report(m)
run(m, mod, 2900)
check('T-LLCK-1', ls1 == 2 and lk1 == 2 and HID_1 in r1[1] and c1 == 0x194 and ls2 == 0 and lk2 == 0 and HID_1 in r2[1] and ok_sys(m),
      'after Fn+F13 and Fn up: layer_state %#x locked %#x, 1 -> %s, F1 -> consumer %#x; Fn again: layer_state %#x locked %#x, '
      '1 -> %s' % (ls1, lk1, sorted(r1[1]), c1, ls2, lk2, sorted(r2[1])))

# ------------------------------------------------------------------ T-KO-1 (same engine, wired)
hold(m, 3000, 3200, LSFT)
hold(m, 3050, 3120, BSPC)
run(m, mod, 3090)
k1 = report(m)
run(m, mod, 3300)
hold(m, 3400, 3460, BSPC)
run(m, mod, 3430)
k2 = report(m)
run(m, mod, 3500)
hold(m, 3600, 3700, LSFT)
hold(m, 3630, 3670, A_)
run(m, mod, 3650)
k3 = report(m)
run(m, mod, 3800)
k4 = report(m)
check('T-KO-1', HID_DEL in k1[1] and HID_BSPC not in k1[1] and not k1[0] & 0x22 and k2[1] == {HID_BSPC} and not k2[0]
      and k3[1] == {HID_A} and k3[0] & 0x02 and not k4[1] and not k4[0] and ok_sys(m),
      'Shift+Backspace: mods %#x keys %s; Backspace: mods %#x keys %s; Shift+A: mods %#x keys %s; all up: %s' % (
          k1[0], sorted(k1[1]), k2[0], sorted(k2[1]), k3[0], sorted(k3[1]), k4))

# ------------------------------------------------------------------ T-LLCK-2 (factory reset refused while locked)
m = None
m, mod = mk(False)
run(m, mod, 1500)
hold(m, 1600, 1700, FN)
hold(m, 1620, 1660, F13)                     # Fn+F13 = Layer Lock
run(m, mod, 1800)
lk_a = u8(m, 'locked_layers')
hold(m, 2000, 5500, BSPC)                    # 3.5 s, a normal "delete a lot of text" hold
frst_a = 0
t = 1800
while t < 7500:                              # the reset would start at 5000 and reset ~1.6 s later
    t += 50
    run(m, mod, t)
    frst_a |= u8(m, 'g80_frst_running')
lk_b, res_a = u8(m, 'locked_layers'), list(m.resets)
hold(m, 7600, 7660, FN)                      # Fn again: unlock
run(m, mod, 7800)
lk_c = u8(m, 'locked_layers')
hold(m, 8000, 11500, FN)
hold(m, 8050, 11450, BSPC)                   # Fn+Backspace held: the factory reset (control)
frst_b = 0
t = 7800
while t < 11400:
    t += 50
    run(m, mod, t)
    frst_b |= u8(m, 'g80_frst_running')
check('T-LLCK-2', lk_a == 2 and frst_a == 0 and not res_a and lk_b == 2 and not m.faults and lk_c == 0 and frst_b == 1,
      'locked %#x, Backspace held 3.5 s: factory reset running %d, resets %s, still locked %#x; unlocked %#x, Fn+Backspace '
      'held: running %d' % (lk_a, frst_a, res_a, lk_b, lk_c, frst_b))
m = None

# ------------------------------------------------------------------ T-LLCK-3 (pairing refused while locked)
m, mod = mk(True)
run(m, mod, 1500)
hold(m, 1600, 1700, FN)
hold(m, 1620, 1660, F13)
run(m, mod, 1800)
lk_a = u8(m, 'locked_layers')
hold(m, 2000, 6500, R_)                      # 4.5 s: past the 3 s + 2 x 0.5 s of the pairing requests
run(m, mod, 7000)
sw = frames(mod, 2000, 7000, 0xA6, 0x30)
pr_a = frames(mod, 2000, 7000, 0xA6, 0x51)
hold(m, 7200, 7260, FN)                      # unlock
run(m, mod, 7400)
hold(m, 7500, 12100, FN)
hold(m, 7550, 12050, R_)                     # Fn+R held: pairing (control)
run(m, mod, 12500)
pr_b = frames(mod, 7500, 12500, 0xA6, 0x51)
check('T-LLCK-3', lk_a == 2 and sw and not pr_a and len(pr_b) == 3 and ok_sys(m),
      'locked %#x, R held 4.5 s: A6 30 x%d, A6 51 x%d; Fn+R held 4.5 s: A6 51 x%d' % (lk_a, len(sw), len(pr_a), len(pr_b)))
m = None

# ------------------------------------------------------------------ T-LLCK-4 (bootloader key refused while locked)
m, mod = mk(False)
run(m, mod, 1500)
hold(m, 1600, 1700, FN)
hold(m, 1620, 1660, F13)
run(m, mod, 1800)
lk_a = u8(m, 'locked_layers')
hold(m, 2000, 2200, RSFT)
hold(m, 2050, 2150, ESC)                     # RShift+Esc
hold(m, 2400, 2700, LCTL)
hold(m, 2420, 2680, RSFT)
hold(m, 2450, 2650, ESC)                     # Ctrl+RShift+Esc (Task Manager)
run(m, mod, 3500)
lk_b, res_a, isp_a = u8(m, 'locked_layers'), list(m.resets), m.isp
hold(m, 3600, 3660, FN)                      # unlock
run(m, mod, 3800)
lk_c = u8(m, 'locked_layers')
hold(m, 4000, 4300, FN)
hold(m, 4020, 4300, RSFT)
hold(m, 4050, 4300, ESC)                     # Fn+RShift+Esc (control)
run(m, mod, 4600)
check('T-LLCK-4', lk_a == 2 and lk_b == 2 and not res_a and isp_a is None and not m.faults and lk_c == 0 and m.isp is not None,
      'locked %#x, RShift+Esc and Ctrl+RShift+Esc: resets %s, ISP %s, still locked %#x; unlocked %#x, Fn+RShift+Esc: '
      'ISP %s' % (lk_a, res_a, isp_a, lk_b, lk_c, m.isp and m.isp.get('t_ms')))
m = None

# ------------------------------------------------------------------ T-GAUGE-7 / 8 (wireless, simulated module)
m = None
m, mod = mk(True)
solid(m, 250, 170)                           # blue effect: the number row is blue outside the gauge
run(m, mod, 1400)
poke(m, 'g80_config', struct.pack('<I', 14))  # bar mode "off"
run(m, mod, 1800)
mod.frame(0x5C, 100)
run(m, mod, 1900)
T0 = 2000
hold(m, T0, 3200, FN, SPACE)
samp = []
t = T0
while t <= T0 + 400:
    # two reads 1 ms apart, the larger count kept: a read inside the effect pass (under 0.5 ms, where the
    # indicator LEDs briefly show the effect colour) lands in at most one of them
    n = []
    for dt in (0, 1):
        run(m, mod, t + dt)
        n.append((sum(green(led(m, i)) for i in BAR), sum(green(led(m, i)) for i in NUM)))
    samp.append((t, max(x[0] for x in n), max(x[1] for x in n)))
    t += 4
first = {}
for t, nb, nn in samp:
    for k in range(1, nb + 1):
        first.setdefault(k, t)
# LED k first seen about 50 (k - 1) ms after the first one; the effect pass redraws the frame in chunks
# of 16 LEDs, so a change shows up with a delay of up to about 30 ms
steps = [first[k + 1] - first[k] for k in range(1, 5) if k in first and k + 1 in first]
late = [round(first[k] - first[1] - 50 * (k - 1)) for k in range(1, 6) if k in first]
# a read can land between two draws of the frame buffer: allow 3 % of the samples to go backwards
back = sum(1 for (_, a, _), (_, b, _) in zip(samp, samp[1:]) if b < a)
# number row: at most two keys per bar LED the animation has reached (it may run up to ~30 ms ahead of the
# bar, which is drawn in a later chunk of the frame)
over = sum(1 for t, nb, nn in samp if 1 in first and nn > 2 * min(5, int(t - first[1] + 30) // 50 + 1))
check('T-GAUGE-7', len(late) == 5 and all(-10 <= d <= 35 for d in late) and all(s >= 20 for s in steps) and first[5] - T0 <= 280
      and back <= 0.03 * len(samp) and samp[-1][1] == 5,
      'bar LEDs lit first at %s ms after the press (steps %s ms, offset from 50 ms per LED %s), %d backward reads of %d, '
      'at +400 ms %d LEDs' % ({k: round(v - T0) for k, v in first.items()}, steps, late, back, len(samp), samp[-1][1]))

g100 = [led(m, i) for i in NUM]
run(m, mod, T0 + 500)
mod.frame(0x5C, 37)
run(m, mod, T0 + 750)
g37 = [led(m, i) for i in NUM]
mod.frame(0x5C, 0)
run(m, mod, T0 + 1000)
g0 = [led(m, i) for i in NUM]
run(m, mod, 3500)
after = [led(m, i) for i in NUM]
ok100 = all(c[1] > 200 and c[0] < 20 and c[2] < 20 for c in g100)
ok37 = all(c[0] > 200 and 140 <= c[1] <= 190 and c[2] < 20 for c in g37[:4]) and not any(lit(c) for c in g37[4:])
ok0 = not any(lit(c) for c in g0)
okaft = all(c[2] > 200 and c[0] < 20 and c[1] < 20 for c in after)
check('T-GAUGE-8', ok100 and ok37 and ok0 and okaft and over == 0 and ok_sys(m),
      '100 %%: %s; 37 %%: %s; 0 %%: %s; released: %s; number keys ahead of the animation in %d samples' % (
          g100[:2] + g100[-1:], g37[3:5], g0[:1], after[:1], over))
m = None
done()
