# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""WP-C3 battery events (g80_battery.c): low-battery Fn blink, critical battery write inhibit, charge edge.
usage: python suite_battery.py <fw_dir>      (VIA image, wireless mode, simulated module)
T-BATT-1  5B 21: the Fn key alternates red / dark every 400 ms (+-10 %), continuously (stock cadence)
T-BATT-2  5B 23 stops the blink
T-BATT-3  the charge edge stops it (charging = solid red; after unplugging the key stays dark)
T-BATT-3b a 5B 21 that arrives while charging does not blink (charging red has priority) and blinks after unplugging
T-BATT-4  5B 22: g80_save_inhibit(true) once, the Fn blink continues; a second 5B 22 adds no call; 5B 23 -> inhibit(false)
T-BATT-7  I-F5 release paths: critical + charge edge -> inhibit(false); 5B 23 without critical -> no call; low / normal
          events alone never inhibit; the battery code performs no flash operation at all; no fault / reset
T-BATT-8  P1-7 option "five blinks": nothing without a key, one burst of 5 blinks on a key, no second burst within
          30 s, a new burst after 30 s (timer jump)
T-BATT-7b I-F5 with real pending work: a dirty setting (Fn+S) and an editor Apply request (Fn+K, paint, Fn+Enter) made
          just before 5B 22, then while critical the RGB keys (Fn+Down brightness, Fn+Enter = RM_NEXT outside the
          editor), NK_TOGG (Fn+N) and Fn+Backspace held 3.3 s: 0 flash ops while critical, RAM changed, the factory
          reset refused (red flash, no reset); after 5B 23 exactly one save sequence behind the write gate (no key
          down, >= 250 ms after the last key), nothing pending after it, and the EEPROM copies of rgb_matrix_config,
          eeconfig_kb and keymap_config equal RAM
T-BATT-9  5B 22 while a charging cable is connected: no inhibit, a setting is saved; unplugging while still critical
          inhibits again; 5B 23 releases
(T-BATT-5/6 gauge: suite_battery_gauge.py; indicator priority, P1-5/10/11, unsaved, idle: suite_battery_ind.py.
The 5B 22 -> 15 s sleep countdown belongs to the sleep package.)
"""
from bat_common import *

m, mod = mk(True)
solid(m, 250, 170)                      # solid blue: red channel is 0 unless an indicator draws red
inh = recorder(m, 'g80_save_inhibit')
run(m, mod, 1500)
mod.frame(0x5B, 0x32)                   # link connected
run(m, mod, 2000)
flash0 = len(m.flash_ops)
fn_red = lambda: led(m, LED_FN)[0] > 100
base = led(m, LED_FN)

# ---- T-BATT-1
mod.frame(0x5B, 0x21)
s = sample(m, mod, 2000, 4600, 10, fn_red)
e = edges(s, 60)
half = [b - a for a, b in zip(e[1:], e[2:])]       # drop the first, partial phase
mean = sum(half) / len(half) if half else 0
check('T-BATT-1', len(e) >= 6 and 360 <= mean <= 440 and base[2] > 200,
      'baseline %s, %d edges, mean half period %.0f ms (400 +-10%%), samples red=%d/%d' % (base, len(e), mean, sum(v for _, v in s), len(s)))

# ---- T-BATT-2
run(m, mod, 4700)
mod.frame(0x5B, 0x23)
s = sample(m, mod, 4900, 5600, 10, fn_red)
check('T-BATT-2', not any(v for _, v in s), '5B 23: Fn red samples %d/%d' % (sum(v for _, v in s), len(s)))

# ---- T-BATT-3: charge edge
mod.frame(0x5B, 0x21)
run(m, mod, 5900)
m.ext_pins[CHG] = 0
s1 = sample(m, mod, 6200, 6900, 10, fn_red)
m.ext_pins[CHG] = 1
s2 = sample(m, mod, 7200, 8200, 10, fn_red)
check('T-BATT-3', frac(s1) >= 0.9 and not any(v for _, v in s2),
      'charging: red %d/%d (solid); after unplugging: red %d/%d (stays dark)' % (sum(v for _, v in s1), len(s1), sum(v for _, v in s2), len(s2)))

# ---- T-BATT-3b: low arrives while charging
m.ext_pins[CHG] = 0
run(m, mod, 8500)
mod.frame(0x5B, 0x21)
s1 = sample(m, mod, 8700, 9300, 10, fn_red)
m.ext_pins[CHG] = 1
s2 = sample(m, mod, 9600, 10800, 10, fn_red)
check('T-BATT-3b', frac(s1) >= 0.9 and len(edges(s2, 60)) >= 2,
      'low while charging: red %d/%d (solid, no blink); after unplugging: %d edges in 1.2 s' % (sum(v for _, v in s1), len(s1), len(edges(s2, 60))))
n_before = len(inh)

# ---- T-BATT-4: critical
run(m, mod, 10900)
mod.frame(0x5B, 0x22)
s = sample(m, mod, 11000, 12000, 10, fn_red)
c1 = list(inh)
mod.frame(0x5B, 0x22)
run(m, mod, 12200)
c2 = list(inh)
mod.frame(0x5B, 0x23)
s2 = sample(m, mod, 12400, 12800, 10, fn_red)
c3 = list(inh)
check('T-BATT-4', n_before == 0 and [v for _, v in c1] == [1] and len(edges(s, 60)) >= 2 and [v for _, v in c2] == [1]
      and [v for _, v in c3] == [1, 0] and not any(v for _, v in s2),
      'inhibit calls before=%d, after 5B 22: %s, blink edges %d, after a 2nd 5B 22: %s, after 5B 23: %s, dark %d/%d' % (
          n_before, c1, len(edges(s, 60)), c2, c3, len(s2) - sum(v for _, v in s2), len(s2)))

# ---- T-BATT-7: release paths, no flash
mod.frame(0x5B, 0x22)
run(m, mod, 13000)
m.ext_pins[CHG] = 0                       # charger plugged in while critical
run(m, mod, 13300)
c4 = list(inh)
m.ext_pins[CHG] = 1
run(m, mod, 13400)
mod.frame(0x5B, 0x23)                     # normal without critical
run(m, mod, 13500)
c5 = list(inh)
check('T-BATT-7', [v for _, v in c4] == [1, 0, 1, 0] and c5 == c4 and len(m.flash_ops) == flash0 and ok_sys(m),
      'inhibit sequence %s (critical, normal, critical, charge edge), unchanged by a lone 5B 23: %s, flash ops since t=2 s: %d, faults %s resets %s' % (
          [v for _, v in c4], c5 == c4, len(m.flash_ops) - flash0, m.faults[:1], m.resets))

# ---- T-BATT-8: option "five blinks"
patch_getter(m, 'g80_opt_low_batt_mode', 1)
mod.frame(0x5B, 0x21)
s0 = sample(m, mod, 13700, 14700, 10, fn_red)


def bursts(t0, t1):
    hold(m, t0, t0 + 50, Z_)
    s = sample(m, mod, t0, t1, 5, fn_red)
    return s, (len(edges(s, 60)) + 1) // 2      # the series starts dark: every second edge is a rising one


s1, n1 = bursts(14800, 17500)
hold(m, 17600, 17650, Z_)
s2 = sample(m, mod, 17600, 19000, 10, fn_red)
tjump(m, 31000)
s3, n3 = bursts(19100, 21800)
check('T-BATT-8', not any(v for _, v in s0) and n1 == 5 and not any(v for _, v in s2) and n3 == 5,
      'no key: red %d/%d; first key: %d blinks; second key 2.8 s later: red %d/%d; key 30+ s after the burst: %d blinks' % (
          sum(v for _, v in s0), len(s0), n1, sum(v for _, v in s2), len(s2), n3))

# ---- T-BATT-7b / T-BATT-9: fresh engine (no getter patch)
m = None
m, mod = mk(True)
solid(m, 250, 170)
inh = recorder(m, 'g80_save_inhibit')
run(m, mod, 1500)
mod.frame(0x5B, 0x32)
run(m, mod, 2000)
S_, ENT_, DOWN_, N_, BKSP_, A_ = (3, 2), (3, 13), (5, 15), (4, 7), (1, 13), (3, 1)


def fn_key(t, key, dur=50):
    hold(m, t, t + dur + 60, FN)
    hold(m, t + 20, t + 20 + dur, key)


def u8(sym, n=1, off=0):
    return bytes(m.uc.mem_read(m.sym[sym] + off, n))


def ee_read(func, n):
    """EEPROM copy (wear-leveling cache) through the firmware's own reader, into free RAM"""
    out = {}
    buf = m.sym['__heap_base__']

    def f(mm):
        if func == 'eeconfig_read_kb':
            out['v'] = struct.pack('<I', mm.call(func) & 0xFFFFFFFF)
        else:
            mm.call(func, buf)
            out['v'] = mm.ram_read(buf, n)
    m.inject.append((m.t_ms() + 0.5, f))
    run(m, mod, m.t_ms() + 2)
    return out.get('v')


flash1 = len(m.flash_ops)
rgb0, km0 = u8('rgb_matrix_config', 6), u8('keymap_config', 2)
fn_key(2100, S_)                          # SOCD toggle: settings dirty (saved >= 3 s later)
fn_key(2300, K_)                          # editor on
hold(m, 2500, 2550, A_)                   # paint A
hold(m, 2700, 2950, FN)
hold(m, 2720, 2770, ENT_)                 # Fn+Enter in the editor = Apply request
run(m, mod, 2750)
mod.frame(0x5B, 0x22)                     # critical while Fn is still down (write gate closed)
fn_key(3100, K_)                          # editor off
run(m, mod, 3400)
req = (u8('g80_save_dirty')[0], u8('g80_pk_apply_req')[0])
mode_ed = u8('rgb_matrix_config', 1)[0] >> 2      # effect after the editor (Per-key)
fn_key(3500, DOWN_)                       # brightness down (RM_VALD)
fn_key(3800, ENT_)                        # RM_NEXT outside the editor
fn_key(4100, N_)                          # NK_TOGG
hold(m, 4400, 7800, FN)
hold(m, 4420, 7750, BKSP_)                # factory reset key held 3.3 s
fl = []
run(m, mod, 7500)
fl.append(u8('g80_fl', 5))                # (target, led, r, g, b) shortly after the 3 s mark
run(m, mod, 12000)
ops_crit = m.flash_ops[flash1:]
rgb1, km1 = u8('rgb_matrix_config', 6), u8('keymap_config', 2)
pend = (u8('g80_save_dirty')[0], u8('g80_pk_apply_req')[0], u8('g80_save_rgb')[0], u8('g80_save_keymap')[0])
frst = u8('g80_frst_running')[0]
t23 = m.t_ms()
mod.frame(0x5B, 0x23)
run(m, mod, 16000)
ops_after = m.flash_ops[flash1 + len(ops_crit):]
groups = []
for op in ops_after:
    if not groups or op[-1] - groups[-1][1] > 200:
        groups.append([op[-1], op[-1], 0])
    groups[-1][1] = op[-1]
    groups[-1][2] += 1
pend2 = (u8('g80_save_dirty')[0], u8('g80_pk_apply_req')[0], u8('g80_save_rgb')[0], u8('g80_save_keymap')[0])
ee_rgb = ee_read('eeconfig_read_rgb_matrix', 6)
ee_kb = ee_read('eeconfig_read_kb', 4)
ee_km = ee_read('eeconfig_read_keymap', 2)
ram_kb = u8('g80_config', 4)
changed = rgb1[3] == rgb0[3] - 64 and (rgb1[0] >> 2) != mode_ed and ((km1[0] ^ km0[0]) & 0x80) != 0   # nkro = bit 7
check('T-BATT-7b', req == (1, 1) and not ops_crit and pend[:2] == (1, 1) and pend[2:] == (1, 1) and changed and frst == 0
      and fl[0][0] != 0 and fl[0][2:] == bytes([255, 0, 0]) and len(groups) == 1 and groups[0][0] >= t23 and groups[0][0] - 7800 >= 250
      and pend2 == (0, 0, 0, 0) and ee_rgb == rgb1 and ee_kb == ram_kb and ee_km == km1 and ok_sys(m),
      'pending at 5B 22 (dirty, apply)=%s; while critical: %d flash ops, pending %s, val %d->%d, mode after the editor %d -> after RM_NEXT %d, keymap_config %s->%s, '
      'factory reset running=%d, flash %s; after 5B 23: %d ops in %d sequence(s) %s, pending %s, EEPROM == RAM rgb %s kb %s keymap %s, '
      'faults %s resets %s' % (
          req, len(ops_crit), pend, rgb0[3], rgb1[3], mode_ed, rgb1[0] >> 2, km0.hex(), km1.hex(), frst, fl[0].hex(),
          len(ops_after), len(groups), [(round(a), round(b), n) for a, b, n in groups], pend2, ee_rgb == rgb1, ee_kb == ram_kb,
          ee_km == km1, m.faults[:1], m.resets))

# ---- T-BATT-9: critical while a cable is connected
n_inh = len(inh)
m.ext_pins[CHG] = 0
run(m, mod, 16500)
mod.frame(0x5B, 0x22)
run(m, mod, 16700)
i1 = [v for _, v in inh[n_inh:]]
n_ops = len(m.flash_ops)
fn_key(16800, S_)
run(m, mod, 21000)
saved_ext = len(m.flash_ops) - n_ops
m.ext_pins[CHG] = 1
run(m, mod, 21500)
i2 = [v for _, v in inh[n_inh:]]
mod.frame(0x5B, 0x23)
run(m, mod, 21700)
i3 = [v for _, v in inh[n_inh:]]
check('T-BATT-9', i1 == [] and saved_ext > 0 and i2 == [1] and i3 == [1, 0] and ok_sys(m),
      '5B 22 with the cable in: inhibit calls %s, Fn+S saved with %d flash ops; after unplugging: %s; after 5B 23: %s' % (
          i1, saved_ext, i2, i3))
m = None
done()
