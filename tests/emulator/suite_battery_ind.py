# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""WP-C3 indicators: priorities, charge LED when wired (P1-5), channel indicator (P1-10), brightness cap on battery
(P1-11), paint "unsaved" blink, LED-off timeout (wired and wireless).
usage: python suite_battery_ind.py <fw_dir>      (VIA image, simulated module)
The option getters (g80_opt_*, g80_battery.c, noinline) read g80_ucfg; the suite patches their bodies in the emulated flash.
T-IND-1  P1-5   charging, slider OFF: Fn key solid red (option default on); option off: not shown; back on
T-IND-2  Fn key priority: charging red > full green > low-battery blink (a low state latched under "full" blinks after)
T-IND-3  bar LED 5: "unsaved" magenta slow blink < layer colour < Fn+Space gauge; bar LED 1: Caps white < gauge
T-IND-4  unsaved cleared -> LED 5 dark
T-IND-5  LED-off timeout, wired: factory option = 5 min (61 s idle: still lit); 1 min without activity (timer jump) -> all LEDs
         dark, the next key lights them; dark again, a VIA setting change lights them; option never: lit after 61 s idle
T-IND-6  P1-11  brightness cap on battery 50 % / 25 %: colours scaled, not while charging, user brightness untouched
T-IND-7  P1-10  channel key: dark after the link window, steady 3 s after g80_ind_channel_show() (wake), steady after a switch
T-IND-8  LED-off timeout, wireless: 1 min idle -> dark (no deep sleep, option never), a key lights them; a charger edge (plug in,
         unplug) does not light them (the Fn key still shows the charge state when lit); slider to wired (mode change) lights them
"""
import struct
from bat_common import *

LED_KEY = 45


def via_set(m, vid, val):
    """VIA id_custom_set_value on channel 0 through raw_hid_receive"""
    buf = m.sym['__heap_base__']
    m.uc.mem_write(buf, (bytes([0x07, 0, vid, val]) + b'\x00' * 32)[:32])
    m.call('raw_hid_receive', buf, 32, max_insn=60000000)


def best(m, mod, i, n=5):
    """most frequent of n reads 1 ms apart (see bat_common.edges: single frame buffer glitches)"""
    out = []
    for _ in range(n):
        out.append(led(m, i))
        run(m, mod, m.t_ms() + 1.0)
    return max(set(out), key=lambda c: (out.count(c), sum(c)))


def is_red(c):
    return c[0] > 200 and c[1] < 30 and c[2] < 30


def is_green(c):
    return c[1] > 200 and c[0] < 30 and c[2] < 30


def is_mag(c):
    return c[0] > 200 and c[2] > 200 and c[1] < 30


def is_white(c):
    return min(c) > 200


def fn_frac(m, mod, t0, t1, pred, i=LED_FN, step=10):
    return frac(sample(m, mod, t0, t1, step, lambda: pred(led(m, i))))


# ================================================================== wired
m, mod = mk(False)
solid(m, 250, 170)
run(m, mod, 1400)
poke(m, 'g80_config', struct.pack('<I', 14))     # bar mode off
run(m, mod, 1900)

# ---- T-IND-1
m.ext_pins[CHG] = 0
run(m, mod, 2000)
a = fn_frac(m, mod, 2300, 2900, is_red)
patch_getter(m, 'g80_opt_charge_wired', 0)
b = fn_frac(m, mod, 3300, 3800, is_red)
patch_getter(m, 'g80_opt_charge_wired', 1)
c = fn_frac(m, mod, 4200, 4700, is_red)
check('T-IND-1', a >= 0.85 and b <= 0.1 and c >= 0.85,
      'wired, charging: Fn red %.0f%% (option on) -> %.0f%% (option off) -> %.0f%% (on again)' % (100 * a, 100 * b, 100 * c))
m.ext_pins[CHG] = 1

# ---- T-IND-2
run(m, mod, 4900)
m.ext_pins[FULL] = 1
run(m, mod, 5200)
mod.frame(0x5B, 0x21)
g = fn_frac(m, mod, 5400, 6200, is_green)
m.ext_pins[FULL] = 0
run(m, mod, 6500)
s = sample(m, mod, 6500, 7700, 10, lambda: is_red(led(m, LED_FN)))
check('T-IND-2', g >= 0.85 and len(edges(s, 60)) >= 2,
      'full (green window) with a low-battery state: Fn green %.0f%%; after the charger is gone the latched low state blinks (%d edges)' % (100 * g, len(edges(s, 60))))
mod.frame(0x5B, 0x23)


# ---- T-IND-3 / 4
def caps_on(mm):
    mm.call('usb_device_state_set_leds', 2)


m.inject.append((7850, caps_on))
poke(m, 'g80_pk_dirty', b'\x01')
run(m, mod, 7950)
s = sample(m, mod, 7950, 9000, 10, lambda: is_mag(led(m, LED_BAR0 + 4)))
w = fn_frac(m, mod, 9000, 9150, is_white, LED_BAR0)
ed = edges(s, 60)
blink_ok = len(ed) >= 2 and 0.3 <= frac(s) <= 0.7
hold(m, 9300, 10250, FN)
lay = fn_frac(m, mod, 9500, 10200, is_red, LED_BAR0 + 4)
mg = fn_frac(m, mod, 9500, 10200, is_mag, LED_BAR0 + 4)
hold(m, 10400, 11600, FN, SPACE)
run(m, mod, 10550)
mod.frame(0x5C, 30)                               # the module answers the request sent at the press
run(m, mod, 10900)
b0, b1, b4 = best(m, mod, LED_BAR0), best(m, mod, LED_BAR0 + 1), best(m, mod, LED_BAR0 + 4)
gauge_ok = b0[0] > 200 and 140 <= b0[1] <= 200 and b0[2] < 30 and 0 < b1[0] < 200 and max(b4) < 20
check('T-IND-3', blink_ok and w >= 0.85 and lay >= 0.85 and mg <= 0.1 and gauge_ok,
      'LED 5: unsaved magenta blink (%d edges, %.0f%% lit), Caps white on LED 1 (%.0f%%); Fn held: layer red %.0f%% / magenta %.0f%%; '
      'Fn+Space p=30: LED 1 %s (yellow over Caps white), LED 2 %s (dimmed), LED 5 %s (dark, over layer red)' % (len(ed), 100 * frac(s), 100 * w, 100 * lay, 100 * mg, b0, b1, b4))
run(m, mod, 11700)
poke(m, 'g80_pk_dirty', b'\x00')
f5 = fn_frac(m, mod, 12000, 12600, lambda c: max(c) > 20, LED_BAR0 + 4)
check('T-IND-4', f5 <= 0.05, 'unsaved cleared: LED 5 lit %.0f%% of the samples' % (100 * f5))

# ---- T-IND-5
def_min = m.call('g80_opt_led_off_min') & 0xFF    # factory option (code 0)
tjump(m, 61000)                                   # 61 s idle < 5 min: nothing may happen
run(m, mod, 12900)
before = best(m, mod, LED_KEY)
patch_getter(m, 'g80_opt_led_off_min', 1)
run(m, mod, 13300)
dark = sum(best(m, mod, LED_KEY))
hold(m, 13500, 13540, Z_)
run(m, mod, 13900)
after = best(m, mod, LED_KEY)
tjump(m, 61000)
run(m, mod, 14300)
dark2 = sum(best(m, mod, LED_KEY))
via_set(m, 29, 0)                                 # a VIA change (Low Battery Warning, same value)
run(m, mod, 14700)
after_via = best(m, mod, LED_KEY)
patch_getter(m, 'g80_opt_led_off_min', 0)         # never
tjump(m, 61000)
run(m, mod, 15100)
never = best(m, mod, LED_KEY)
check('T-IND-5', def_min == 5 and before[2] > 200 and dark < 5 and after[2] > 200 and dark2 < 5 and after_via[2] > 200 and never[2] > 200
      and ok_sys(m),
      'factory option %d min, idle 61 s -> key LED %s; option 1 min -> dark (%d); next key -> %s; dark again (%d); VIA set -> %s; '
      'option never, idle 61 s -> %s' % (def_min, before, dark, after, dark2, after_via, never))
wired_ok = ok_sys(m)

# ================================================================== wireless
m = None
m, mod = mk(True)
solid(m, 250, 0)                                  # solid red
run(m, mod, 1500)
mod.frame(0x5B, 0x32)
run(m, mod, 2000)
v0 = best(m, mod, LED_KEY)
patch_getter(m, 'g80_opt_batt_cap', 2)
run(m, mod, 2400)
v50 = best(m, mod, LED_KEY)
m.ext_pins[CHG] = 0
run(m, mod, 2900)
vchg = best(m, mod, LED_KEY)
m.ext_pins[CHG] = 1
run(m, mod, 3300)
vb = best(m, mod, LED_KEY)
patch_getter(m, 'g80_opt_batt_cap', 3)
run(m, mod, 3600)
v25 = best(m, mod, LED_KEY)
patch_getter(m, 'g80_opt_batt_cap', 0)
run(m, mod, 3900)
voff = best(m, mod, LED_KEY)
user_v = m.call('rgb_matrix_get_val') & 0xFF
check('T-IND-6', v0[0] >= 250 and 118 <= v50[0] <= 134 and vchg[0] >= 250 and 118 <= vb[0] <= 134 and 58 <= v25[0] <= 68 and voff[0] >= 250 and user_v == 255,
      'red key: %d (no cap) -> %d (50%%) -> %d (charging) -> %d (unplugged) -> %d (25%%) -> %d (off); rgb_matrix_get_val %d unchanged' % (
          v0[0], v50[0], vchg[0], vb[0], v25[0], voff[0], user_v))

# ---- T-IND-7: channel key (Q = BT1 is LED 35), green effect so that blue means "indicator"
solid(m, 4000, 85)
run(m, mod, 5300)
exp_ = fn_frac(m, mod, 5300, 5600, lambda c: c[2] > 200, LED_Q)
m.inject.append((5700, lambda mm: mm.call('g80_ind_channel_show')))
w1 = fn_frac(m, mod, 5900, 6100, lambda c: c[2] > 200, LED_Q)
w2 = fn_frac(m, mod, 7500, 7700, lambda c: c[2] > 200, LED_Q)
w3 = fn_frac(m, mod, 9000, 9200, lambda c: c[2] > 200, LED_Q)
hold(m, 9400, 9600, FN)
hold(m, 9410, 9480, E_)                           # Fn+E: BT3
run(m, mod, 9900)
mod.frame(0x5B, 0x32)
w4 = fn_frac(m, mod, 10300, 10600, lambda c: c[2] > 200, LED_Q + 2)
check('T-IND-7', exp_ <= 0.1 and w1 >= 0.85 and w2 >= 0.85 and w3 <= 0.1 and w4 >= 0.85 and ok_sys(m) and wired_ok,
      'Q key blue: expired window %.0f%%; after g80_ind_channel_show: +0.2 s %.0f%%, +1.9 s %.0f%%, +3.4 s %.0f%% (3 s window); after Fn+E and link up, E key %.0f%%' % (
          100 * exp_, 100 * w1, 100 * w2, 100 * w3, 100 * w4))

# ---- T-IND-8: LED-off timeout in wireless (green effect)
def lit(m, mod):
    return best(m, mod, LED_KEY)[1] > 200


def is_dark(m, mod):
    return sum(best(m, mod, LED_KEY)) < 5


patch_getter(m, 'g80_opt_led_off_min', 1)
run(m, mod, 10900)
l0 = lit(m, mod)
tjump(m, 61000)
run(m, mod, 11300)
d1 = is_dark(m, mod)
hold(m, 11500, 11540, Z_)
run(m, mod, 11900)
l1 = lit(m, mod)
tjump(m, 61000)
run(m, mod, 12300)
d2 = is_dark(m, mod)
m.ext_pins[CHG] = 0                               # charger plugged in: an edge
run(m, mod, 12900)
l2 = is_dark(m, mod)                              # a plug-in does not relight
hold(m, 13000, 13040, Z_)                         # a key lights them; the Fn key shows the charge state
run(m, mod, 13200)
fn_chg = fn_frac(m, mod, 13200, 13400, is_red)
tjump(m, 61000)
run(m, mod, 13600)
d3 = is_dark(m, mod)
m.ext_pins[CHG] = 1                               # unplugged: a falling edge, the LEDs stay dark
run(m, mod, 14200)
l3 = is_dark(m, mod)
tjump(m, 61000)
run(m, mod, 14600)
d4 = is_dark(m, mod)
slept = bytes(m.uc.mem_read(m.sym['g80_lpm_stats'], 2))   # g80_lpm_stats.sleeps
m.ext_pins[SLIDER] = 0                            # slider to wired: a mode change
run(m, mod, 15600)
l4 = lit(m, mod)
check('T-IND-8', l0 and d1 and l1 and d2 and l2 and fn_chg >= 0.85 and d3 and l3 and d4 and slept == b'\x00\x00' and l4 and ok_sys(m),
      'wireless, 1 min: lit %s, idle 61 s dark %s, key -> lit %s; dark %s, charger in -> still dark %s, key -> Fn red %.0f%%, dark %s, unplugged -> still dark %s; '
      'dark %s (deep sleeps %d), slider to wired -> lit %s' % (l0, d1, l1, d2, l2, 100 * fn_chg, d3, l3, d4, slept[0], l4))
done()
