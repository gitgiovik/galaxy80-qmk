# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""WP-C3 Fn+Space battery gauge on the side bar (g80_battery.c, g80_bar.c g80_bar_gauge).
usage: python suite_battery_gauge.py <fw_dir>      (VIA image, simulated module)
T-BATT-5   gauge for p = 1 19 20 21 49 50 79 81 100 (LED count = ceil(p/20), colour red < 20 / yellow < 50 / green,
           top LED dimmed to r/20 with r = p - 20 (n-1), unlit LEDs dark), p = 0 (LED 1 red blink), p = 0xFF (LED 1
           slow white blink), charging (the lit LEDs breathe)
T-BATT-6   A6 53 queued on press and every 2 s while held, none after release, also with the slider OFF; wired:
           cached value shown at once, dimmed to 25 % when the module does not answer within 300 ms, full again on
           the answer; wired with no value ever received: white blink (stale-dimmed)
Bar orientation: LED 1 = rgb_matrix LED 88 (first of the five, keyboard.json x 198 -> 224), see g80_bar.h.
"""
import struct
from bat_common import *

BAR = [LED_BAR0 + i for i in range(5)]


def bar_once(m):
    return [led(m, i) for i in BAR]


def bar(m, mod=None):
    """Steady colours: per LED the most frequent of 5 reads 1 ms apart (a read can land between two draws of
    the single frame buffer: a glitch)."""
    if mod is None:
        return bar_once(m)
    reads = []
    for _ in range(5):
        reads.append(bar_once(m))
        run(m, mod, m.t_ms() + 1.0)
    return [max(set(r[i] for r in reads), key=lambda c: ([r[i] for r in reads].count(c), sum(c))) for i in range(5)]


def lit(c):
    return max(c) > 20


def others_dark(s):
    """LEDs 2..5 dark in at least 95 % of the samples (the rest are frame-buffer glitches: the effect colour of the
    bar between the effect pass and the indicator pass)"""
    return sum(1 for _, c in s if any(lit(c[i]) for i in range(1, 5))) <= 0.05 * len(s)


def whitish(c):
    """equal channels and visible (a read between two draws of the frame buffer shows the effect colour)"""
    return max(c) > 20 and abs(c[0] - c[1]) <= 2 and abs(c[1] - c[2]) <= 2


def expect_gauge(p):
    n = (p + 19) // 20
    col = (DUTY, 0, 0) if p < 20 else (DUTY, 170, 0) if p < 50 else (0, DUTY, 0)
    r = p - 20 * (n - 1)
    out = []
    for i in range(5):
        if i >= n:
            out.append((0, 0, 0))
        else:
            f = r / 20.0 if i == n - 1 else 1.0
            out.append(tuple(round(c * f) for c in col))
    return out


def close(a, b, tol=4):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ------------------------------------------------------------------ wireless
m, mod = mk(True)
run(m, mod, 1400)
poke(m, 'g80_config', struct.pack('<I', 14))        # bar mode "off": unlit bar LEDs are black
run(m, mod, 1900)
T0 = 2000
hold(m, T0, 7900, FN, SPACE)
cases = [1, 19, 20, 21, 49, 50, 79, 81, 100]
bad = []
for k, p in enumerate(cases):
    t = T0 + 100 + 330 * k
    run(m, mod, t)
    mod.frame(0x5C, p)
    run(m, mod, t + 250)
    got, exp = bar(m, mod), expect_gauge(p)
    if not all(close(g, e) for g, e in zip(got, exp)):
        bad.append((p, got, exp))
check('T-BATT-5a', not bad, 'gauge levels %s: %s' % (cases, 'all as specified (n LEDs, colour, dimmed top LED, rest dark)' if not bad else bad[:2]))

t = T0 + 100 + 330 * len(cases)
run(m, mod, t)
mod.frame(0x5C, 0)
s = sample(m, mod, t + 100, t + 700, 10, lambda: bar(m))
check('T-BATT-5b', any(c[0][0] > 200 and c[0][1] < 20 and c[0][2] < 20 for _, c in s) and any(not lit(c[0]) for _, c in s)
      and others_dark(s),
      'p=0: LED 1 blinks red (%d of %d samples lit), LEDs 2..5 dark' % (sum(lit(c[0]) for _, c in s), len(s)))

t += 800
run(m, mod, t)
mod.frame(0x5C, 0xFF)
s = sample(m, mod, t + 100, t + 1800, 10, lambda: bar(m))
wh = [c[0] for _, c in s if whitish(c[0])]
ed = edges([(tt, whitish(c[0])) for tt, c in s], 60)
check('T-BATT-5c', wh and wh[0][0] > 200 and len(ed) >= 2 and all(400 <= b - a <= 650 for a, b in zip(ed, ed[1:]))
      and others_dark(s),
      'p=0xFF: LED 1 white (%s), slow blink (phases %s ms), LEDs 2..5 dark' % (wh[:1], [round(b - a) for a, b in zip(ed, ed[1:])][:2]))

# T-BATT-6: requests while held (hold ends at 7900)
run(m, mod, 10300)
req = [round(t) for t, f in frames(mod, 1800, 10400, 0xA6, 0x53)]
rel = [t for t in req if t > 8050]
exp_t = [T0, T0 + 2000, T0 + 4000]
check('T-BATT-6a', len(req) == 3 and all(abs(a - b) < 150 for a, b in zip(req, exp_t)) and not rel,
      'A6 53 at %s ms (press at ~%d, then every 2 s while held 2.0-7.9 s), after release: %s' % (req, T0, rel))

# charging: the lit LEDs breathe
mod.frame(0x5C, 60)
m.ext_pins[CHG] = 0
hold(m, 10600, 13300, FN, SPACE)
run(m, mod, 10700)
s = sample(m, mod, 10800, 13200, 25, lambda: bar(m))
g1 = [c[0][1] for _, c in s]
g4 = [c[3][1] for _, c in s]
ok_b = (min(g1) <= 90 and max(g1) >= 230 and sum(1 for v in g4 if v > 5) <= 0.05 * len(g4)
        and sum(1 for _, c in s if c[2][1] == 0) <= 0.05 * len(s))
check('T-BATT-5d', ok_b, 'charging, p=60 (3 LEDs): LED 1 green breathes min %d max %d over 2.4 s, LED 4 stays dark (%d of %d samples lit)' % (min(g1), max(g1), sum(1 for v in g4 if v > 5), len(g4)))
m.ext_pins[CHG] = 1
check('T-BATT-5x', ok_sys(m), 'no fault / reset / ISP, TX overwrites %d' % len(m.uart_overwrites))

# ------------------------------------------------------------------ wired (slider OFF)
m = None
m, mod = mk(False)
run(m, mod, 1400)
poke(m, 'g80_config', struct.pack('<I', 14))
hold(m, 1600, 3500, FN, SPACE)
s = sample(m, mod, 1700, 3450, 10, lambda: bar(m))
ons = [c[0] for _, c in s if whitish(c[0])]
ed = edges([(tt, whitish(c[0])) for tt, c in s], 60)
check('T-BATT-6b', ons and len(ed) >= 2 and sum(1 for c in ons if 55 <= c[0] <= 70) >= 20 and others_dark(s)
      and frames(mod, 1500, 1800, 0xA6, 0x53),
      'wired, no value ever received: LED 1 white blink at 25 %% (%s, edges %s), A6 53 sent with the slider OFF: %s' % (
          ons[:1], [round(x) for x in ed[:3]], bool(frames(mod, 1500, 1800, 0xA6, 0x53))))

run(m, mod, 3700)
mod.frame(0x5C, 50)                                  # cached value 50 %
hold(m, 3900, 5300, FN, SPACE)
run(m, mod, 4050)
full = bar(m, mod)
run(m, mod, 4500)
dim = bar(m, mod)
mod.frame(0x5C, 50)                                  # the module answers
run(m, mod, 4700)
back = bar(m, mod)
exp = expect_gauge(50)
check('T-BATT-6c', close(full[0], exp[0]) and close(full[2], exp[2], 6) and 56 <= dim[0][1] <= 70 and lit(dim[2]) and not lit(dim[3])
      and close(back[0], exp[0]) and ok_sys(m),
      'wired: cached 50 %% at once %s, no answer after 300 ms -> 25 %% %s, answered -> %s' % (full[:3], dim[:3], back[:3]))
done()
