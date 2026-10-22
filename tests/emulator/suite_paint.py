# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Galaxy80 paint session suite (design 16, sec. 2.6 P0-9, invariant I-V3; T-PAINT-1..7) and the
VIA menu items of the super stage (T-VIA-ITEMS).

    python -u suite_paint.py <fw_dir> [out_dir]

VIA packets go the real way (USB OUT endpoint -> raw_hid_task -> raw_hid_receive), the host waits for
each answer (via_host.G80Via); keys come from the emulated matrix. No flash stall model (timing is
not what is tested here). "Save sequence" = flash ops less than 200 ms apart.
  T-PAINT-1  on-board editor strokes (Fn+K, keys, Fn+K): no flash op, table unsaved; Fn+Enter in the
             editor = Apply: one save sequence, stored table = RAM table, effect stored as Per-key,
             survives a reboot. Fn+Enter outside the editor still selects the next effect.
  T-PAINT-2  VIA strokes (painter id 1, brush id 17 in the editor): no flash op; VIA Apply (id 24):
             one save sequence; reboot keeps them.
  T-PAINT-3  bar gradient (ids 22 / 23): no flash op; VIA Discard (id 25): no flash op, table back.
  T-PAINT-4  Reset Key Colors (id 18): no flash op; Apply: one save sequence, stored = defaults.
  T-PAINT-5  mixed strokes (VIA + editor), then Fn+Delete in the editor = Discard: RAM table equals
             the stored table byte for byte, unsaved cleared, no flash op.
  T-PAINT-6  editor idle exit (120 s without an editor key): editor off, no flash op, still unsaved,
             bar LED 5 blinks magenta while unsaved and off after Apply; the VIA read-only item 34
             reports the unsaved state.
  T-PAINT-7  Apply requested while typing: the write starts only once no key is down and none moved
             for 250 ms (write gate), then one save sequence.
  T-VIA-ITEMS  new VIA items 26..32 read back what was set, persist across a reboot (eeconfig_user)
             and read the factory defaults on a fresh EEPROM (sleep never, NKRO on, snap off, low
             battery stock, no cap, charge LED wired on, wired idle off); id 33 = the firmware
             version listed in via_json/galaxy80.json.
"""
import os, sys, json, struct, gc
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *
from via_host import G80Via, parse_args, run_until, via_sync, boot_to_loop
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

FW, OUT, opt, OUT_TMP = parse_args(sys.argv, 'paint')
NM = os.path.join(FW, 'nm_via.txt')
HERE = os.path.dirname(os.path.abspath(__file__))
IDS = json.load(open(os.path.join(HERE, 'via_effect_ids.json')))
PK_MODE = IDS['G80_PER_KEY']
FN, K_, ENT, DEL, A_, W_, D_ = (5, 11), (3, 8), (3, 13), (2, 14), (3, 1), (2, 2), (3, 3)
TYPE = [(2, c) for c in range(3, 11)]
BAR5 = 92
results = []


def check(tid, name, ok, detail):
    results.append(dict(id=tid, name=name, result='PASS' if ok else 'FAIL', detail=detail))
    print('%s  %-12s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail), flush=True)


def mkm(path):
    gc.collect()
    m = G80Via(path, NM, stress=True)
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    return m


def rd(m, sym, n, off=0):
    return bytes(m.uc.mem_read(m.sym[sym] + off, n))


def table(m):
    return rd(m, 'g80_pk', 186)


def stored(m):
    buf = m.sym['__heap_base__']
    m.call('via_read_custom_config', buf, 1, 186)
    return m.ram_read(buf, 186)


def flags(m):
    return dict(edit=rd(m, 'g80_pk_editing', 1)[0], dirty=rd(m, 'g80_pk_dirty', 1)[0], apply=rd(m, 'g80_pk_apply_req', 1)[0])


def mode(m):
    return rd(m, 'rgb_matrix_config', 1)[0] >> 2


def seqs(log):
    out = []
    for x in log:
        if x[3] == 'boot':
            continue
        if not out or x[0] - out[-1][1] > 200:
            out.append([x[0], x[0], 0])
        out[-1][1] = x[0]
        out[-1][2] += 1
    return out


def ctxs(log):
    return sorted(set(x[3] for x in log))


def via(m, vid, *vals, save=True):
    pk = [[0x07, 0, vid] + list(vals)]
    if save:
        pk.append([0x09, 0])
    return via_sync(m, pk)[0]


def via_get(m, vid):
    a = via_sync(m, [[0x08, 0, vid]])[0]
    return a[0][3] if a else None


def tap(m, keys, dur=30, gap=30):
    t = m.t_ms()
    m.pressed |= set(keys)
    run_until(m, t + dur, step=1.0)
    m.pressed -= set(keys)
    run_until(m, t + dur + gap, step=1.0)


def fn_combo(m, key):
    t = m.t_ms()
    m.pressed.add(FN)
    run_until(m, t + 15, step=1.0)
    m.pressed.add(key)
    run_until(m, t + 45, step=1.0)
    m.pressed.discard(key)
    run_until(m, t + 60, step=1.0)
    m.pressed.discard(FN)
    run_until(m, t + 90, step=1.0)


def frame_rgb(m, led):
    f = rd(m, 'g80_frame', 21 * 17)
    r, c = (6, 12 + led - 88) if led >= 88 else divmod(led, 17)
    return f[r * 17 + c], f[(7 + r) * 17 + c], f[(14 + r) * 17 + c]


def save_img(m, name):
    p = os.path.join(OUT, name)
    open(p, 'wb').write(bytes(m.flash))
    return p


# ------------------------------------------------------------------ base image
m0 = mkm(os.path.join(FW, 'epomaker_galaxy80_via.bin'))
run_until(m0, 1500, step=50.0)
BASE = save_img(m0, 'base_formatted_via.bin')
m0 = None

# ================================================================== T-PAINT-1: editor strokes, Fn+Enter = Apply
m = mkm(BASE)
boot_to_loop(m, 300)
mode0 = mode(m)
st0 = stored(m)
f0 = len(m.flash_log)
fn_combo(m, K_)                                 # enter (snapshot of the running effect)
run_until(m, m.t_ms() + 100)
for k in [A_, W_, D_, (1, 5), A_, (3, 5), (2, 7), (4, 4)]:
    tap(m, [k])
fn_combo(m, K_)                                 # exit: no save
run_until(m, m.t_ms() + 5000, step=10.0)
before = m.flash_log[f0:]
fl1 = flags(m)
tab1 = table(m)
fn_combo(m, K_)                                 # enter again, Fn+Enter = Apply
mode_in = mode(m)
f1 = len(m.flash_log)
fn_combo(m, ENT)
mode_after_ent = mode(m)
run_until(m, m.t_ms() + 1500, step=10.0)
ap = m.flash_log[f1:]
fl2 = flags(m)
st1 = stored(m)
fn_combo(m, K_)                                 # leave the editor
run_until(m, m.t_ms() + 200)
p = save_img(m, 'paint1.bin')
# Fn+Enter outside the editor: next effect (RM_NEXT), unchanged behaviour
mo_a = mode(m)
fn_combo(m, ENT)
mo_b = mode(m)
m = None
m = mkm(p)
boot_to_loop(m, 300)
tab_rb, mode_rb = table(m), mode(m)
m = None
ok = (not before and fl1['dirty'] == 1 and fl1['edit'] == 0 and tab1 != st0 and len(seqs(ap)) == 1 and ctxs(ap) == ['main']
      and fl2['dirty'] == 0 and st1 == tab1 and mode_after_ent == mode_in == PK_MODE and tab_rb == tab1 and mode_rb == PK_MODE
      and mo_b != mo_a)
check('T-PAINT-1', 'on-board editor strokes + exit: no flash op, unsaved; Fn+Enter in the editor = Apply: one save sequence (main loop), '
      'stored table = RAM table, effect Per-key kept, reboot keeps both; outside the editor Fn+Enter still selects the next effect', ok,
      'flash ops before Apply %d, flags after exit %s, table changed %s; Apply: %d ops in %d sequence(s) ctx %s, flags %s, stored == RAM %s, '
      'effect in editor %d / after Fn+Enter %d (Per-key %d, boot effect %d); after reboot table == applied %s effect %d; Fn+Enter outside: '
      'effect %d -> %d' % (len(before), fl1, tab1 != st0, len(ap), len(seqs(ap)), ctxs(ap), fl2, st1 == tab1, mode_in, mode_after_ent,
                           PK_MODE, mode0, tab_rb == tab1, mode_rb, mo_a, mo_b))

# ================================================================== T-PAINT-2: VIA strokes, VIA Apply
m = mkm(BASE)
boot_to_loop(m, 300)
f0 = len(m.flash_log)
for i in range(20):
    via_sync(m, [[0x07, 0, 1, (i * 4) % 88, 2, (i * 13) & 255, 255, (i * 29) & 255, 200], [0x09, 0]])
via(m, 16, 1)                                   # editor on, brush recolours the last edited key
tap(m, [A_])
via(m, 17, 170, 255)
via(m, 16, 0)
run_until(m, m.t_ms() + 5000, step=10.0)
before = m.flash_log[f0:]
fl1 = flags(m)
tab1 = table(m)
f1 = len(m.flash_log)
via(m, 24, 1)                                   # Apply
run_until(m, m.t_ms() + 1500, step=10.0)
ap = m.flash_log[f1:]
fl2 = flags(m)
st1 = stored(m)
p = save_img(m, 'paint2.bin')
m = None
m = mkm(p)
boot_to_loop(m, 300)
tab_rb, mode_rb = table(m), mode(m)
m = None
ok = (not before and fl1['dirty'] == 1 and len(seqs(ap)) == 1 and 'rawhid' not in ctxs(ap) and fl2['dirty'] == 0 and st1 == tab1
      and tab_rb == tab1 and mode_rb == PK_MODE and tab1[2 * 52] == 170)
check('T-PAINT-2', 'VIA strokes (20 painter packets, editor brush): no flash op; VIA Apply (id 24): one save sequence outside raw_hid_receive; '
      'reboot keeps table and Per-key', ok,
      'flash ops before Apply %d, flags %s; Apply %d ops in %d sequence(s) ctx %s, flags %s, stored == RAM %s; after reboot table == applied '
      '%s effect %d, A hue %d' % (len(before), fl1, len(ap), len(seqs(ap)), ctxs(ap), fl2, st1 == tab1, tab_rb == tab1, mode_rb, tab1[2 * 52]))

# ================================================================== T-PAINT-3: gradient, VIA Discard
m = mkm(BASE)
boot_to_loop(m, 300)
st0 = stored(m)
f0 = len(m.flash_log)
for i in range(10):
    via(m, 22, (i * 20) & 255, 255)
    via(m, 23, (i * 37) & 255, 200)
run_until(m, m.t_ms() + 4000, step=10.0)
grad = m.flash_log[f0:]
fl1 = flags(m)
tab1 = table(m)
f1 = len(m.flash_log)
via(m, 25, 1)                                   # Discard
run_until(m, m.t_ms() + 4000, step=10.0)
dis = m.flash_log[f1:]
fl2 = flags(m)
tab2 = table(m)
m = None
ok = not grad and fl1['dirty'] == 1 and tab1 != st0 and not dis and fl2['dirty'] == 0 and tab2 == st0
check('T-PAINT-3', 'bar gradient strokes (ids 22 / 23): no flash op; VIA Discard (id 25): no flash op, table back to the stored one', ok,
      'flash ops gradient %d discard %d, flags %s -> %s, table changed %s, restored %s' % (len(grad), len(dis), fl1, fl2, tab1 != st0, tab2 == st0))

# ================================================================== T-PAINT-4: Reset Key Colors, Apply
m = mkm(BASE)
boot_to_loop(m, 300)
via_sync(m, [[0x07, 0, 1, 0, 4, 85, 255, 85, 255, 85, 255, 85, 255], [0x09, 0]])
via(m, 24, 1)
run_until(m, m.t_ms() + 1500, step=10.0)
painted = stored(m)
f0 = len(m.flash_log)
via(m, 18, 1)                                   # Reset Key Colors
run_until(m, m.t_ms() + 4000, step=10.0)
rs = m.flash_log[f0:]
fl1 = flags(m)
defaults = table(m)
f1 = len(m.flash_log)
via(m, 24, 1)
run_until(m, m.t_ms() + 1500, step=10.0)
ap = m.flash_log[f1:]
st1 = stored(m)
m = None
ok = not rs and fl1['dirty'] == 1 and painted != defaults and len(seqs(ap)) == 1 and st1 == defaults
check('T-PAINT-4', 'Reset Key Colors (id 18): no flash op, unsaved; Apply: one save sequence, stored table = defaults', ok,
      'flash ops after reset %d, flags %s, Apply %d ops in %d sequence(s), stored == defaults %s' % (len(rs), fl1, len(ap), len(seqs(ap)), st1 == defaults))

# ================================================================== T-PAINT-5: mixed strokes, Fn+Delete = Discard
m = mkm(BASE)
boot_to_loop(m, 300)
st0 = stored(m)
f0 = len(m.flash_log)
via_sync(m, [[0x07, 0, 1, 10, 3, 1, 255, 2, 255, 3, 255], [0x09, 0]])
via(m, 22, 40, 255)
fn_combo(m, K_)
for k in [A_, W_, (1, 3)]:
    tab_k = tap(m, [k])
fl1 = flags(m)
tab1 = table(m)
fn_combo(m, DEL)                                # Discard in the editor
ed_after = flags(m)['edit']
run_until(m, m.t_ms() + 300)
tab2 = table(m)
fl2 = flags(m)
fn_combo(m, K_)
run_until(m, m.t_ms() + 4000, step=10.0)
ops = m.flash_log[f0:]
m = None
ok = tab1 != st0 and fl1['dirty'] == 1 and tab2 == st0 and fl2['dirty'] == 0 and ed_after == 1 and not ops
check('T-PAINT-5', 'mixed strokes (VIA painter, gradient, editor keys), Fn+Delete in the editor = Discard: table equals the stored one byte '
      'for byte, unsaved cleared, editor stays on, no flash op', ok,
      'changed before %s, flags %s -> %s, restored byte for byte %s (%d bytes differ), editing after Fn+Delete %d, flash ops %d' % (
          tab1 != st0, fl1, fl2, tab2 == st0, sum(1 for a, b in zip(tab2, st0) if a != b), ed_after, len(ops)))

# ================================================================== T-PAINT-6: idle exit, unsaved indicator
m = mkm(BASE)
boot_to_loop(m, 300)
f0 = len(m.flash_log)
fn_combo(m, K_)
for k in [A_, W_]:
    tap(m, [k])
ed_in = flags(m)['edit']
now = m.call('timer_read32')
m.uc.mem_write(m.sym['g80_pk_activity'], struct.pack('<I', (now - 120001) & 0xFFFFFFFF))   # last editor key 2 min ago
run_until(m, m.t_ms() + 100)
fl1 = flags(m)
samples = []
t = m.t_ms()
while m.t_ms() < t + 4200:
    run_until(m, m.t_ms() + 64)
    samples.append(frame_rgb(m, BAR5))
mag = sum(1 for x in samples if x == (0, 252, 252))
u34 = via_get(m, 34)
ops = m.flash_log[f0:]
via(m, 24, 1)
run_until(m, m.t_ms() + 1500, step=10.0)
s2 = []
t = m.t_ms()
while m.t_ms() < t + 2200:
    run_until(m, m.t_ms() + 64)
    s2.append(frame_rgb(m, BAR5))
mag2 = sum(1 for x in s2 if x == (0, 252, 252))
u34b = via_get(m, 34)
m = None
ok = (ed_in == 1 and fl1['edit'] == 0 and fl1['dirty'] == 1 and not ops and mag >= 10 and len(samples) - mag >= 10 and mag2 == 0
      and u34 == 1 and u34b == 0)
check('T-PAINT-6', 'editor idle exit (120 s without an editor key): editor off, no flash op, still unsaved; bar LED 5 blinks magenta while '
      'unsaved (and not after Apply); VIA item 34 reads 1 / 0', ok,
      'editing %d -> %d, flags %s, flash ops %d, bar LED 5 magenta in %d of %d samples (after Apply %d of %d), id 34 %s -> %s' % (
          ed_in, fl1['edit'], fl1, len(ops), mag, len(samples), mag2, len(s2), u34, u34b))

# ================================================================== T-PAINT-7: Apply waits for the write gate
m = mkm(BASE)
boot_to_loop(m, 300)
via_sync(m, [[0x07, 0, 1, 20, 2, 100, 255, 120, 255], [0x09, 0]])
run_until(m, m.t_ms() + 100)
t0 = m.t_ms()
f0 = len(m.flash_log)
evs = []
t = t0
k = 0
via_done = None
while t < t0 + 2000:
    rc = TYPE[k % len(TYPE)]
    run_until(m, t, step=1.0)
    m.pressed.add(rc)
    evs.append((m.t_ms(), rc, True))
    if via_done is None and m.t_ms() > t0 + 400:
        m.hid_send_lockstep([[0x07, 0, 24, 1], [0x09, 0]], m.t_ms())
        via_done = m.t_ms()
    run_until(m, t + 35, step=1.0)
    m.pressed.discard(rc)
    evs.append((m.t_ms(), rc, False))
    t += 60
    k += 1
t_last = evs[-1][0]
run_until(m, t_last + 1500, step=5.0)
ops = m.flash_log[f0:]
first = ops[0][0] if ops else None
pend_mid = None
fl = flags(m)
m = None
ok = ops and first >= t_last + 250 and len(seqs(ops)) == 1 and fl['dirty'] == 0 and fl['apply'] == 0
check('T-PAINT-7', 'Apply requested (VIA id 24) while typing a key every 60 ms: no flash op until no key is down and none moved for 250 ms, '
      'then one save sequence', bool(ok),
      'Apply sent at %.0f ms, last key release %.0f ms, first flash op %s ms (%s ms after it), %d ops in %d sequence(s), flags %s' % (
          via_done or -1, t_last, None if first is None else round(first), None if first is None else round(first - t_last), len(ops),
          len(seqs(ops)), fl))

# ================================================================== T-VIA-ITEMS
m = mkm(BASE)
boot_to_loop(m, 300)
items = {26: 'deep_sleep', 27: 'wl_nkro', 28: 'snap_mode', 29: 'low_batt', 30: 'batt_cap', 31: 'chg_wired', 32: 'led_off'}
defaults = {v: via_get(m, v) for v in items}
exp_def = {26: 0, 27: 1, 28: 0, 29: 0, 30: 0, 31: 1, 32: 0}
setv = {26: 2, 27: 0, 28: 3, 29: 1, 30: 2, 31: 0, 32: 4}
for v, x in setv.items():
    via(m, v, x)
back = {v: via_get(m, v) for v in items}
bad_in = {}
for v, x in ((26, 9), (28, 9), (32, 7)):         # out of range: code 0 (the default)
    via(m, v, x)
    bad_in[v] = via_get(m, v)
for v in (26, 28, 32):
    via(m, v, setv[v])
fwv = via_get(m, 33)
ucfg = struct.unpack('<I', rd(m, 'g80_ucfg', 4))[0]
run_until(m, m.t_ms() + 4000, step=10.0)
p = save_img(m, 'items.bin')
m = None
m = mkm(p)
boot_to_loop(m, 300)
after = {v: via_get(m, v) for v in items}
m = None
vj = os.environ.get('G80_KBD', os.path.join(HERE, '..', '..', 'keyboards', 'epomaker', 'galaxy80')) + '/via_json/galaxy80.json'
fw_opts = None
try:
    j = json.load(open(vj))

    def walk(n):
        global fw_opts
        if isinstance(n, dict):
            c = n.get('content')
            if isinstance(c, list) and len(c) == 3 and c[1] == 0 and c[2] == 33:
                fw_opts = n.get('options')
            for x in n.values():
                walk(x)
        elif isinstance(n, list):
            for x in n:
                walk(x)
    walk(j['menus'])
except Exception as e:
    fw_opts = 'json error %s' % e
ver_ok = isinstance(fw_opts, list) and len(fw_opts) == 1 and fw_opts[0][1] == fwv
ok = defaults == exp_def and back == setv and after == setv and bad_in == {26: 0, 28: 0, 32: 0} and ver_ok and ucfg == 0x527C   # g80_user_config_t layout
check('T-VIA-ITEMS', 'VIA items 26..32: factory defaults (deep sleep never, NKRO on, snap off, low battery stock, no cap, charge LED wired on, '
      'LEDs off after 5 min = code 0), read back what was set, out-of-range -> code 0, persist across a reboot (eeconfig_user, deep_sleep bits '
      '13..15, led_off bits 10..12); id 33 = the version in the json', ok,
      'defaults %s, set %s -> read %s, out of range %s, after reboot %s, eeconfig_user %#x, '
      'firmware version code %s, json option %s' % (defaults, setv, back, bad_in, after, ucfg, fwv, fw_opts))

json.dump(results, open(os.path.join(OUT, 'suite_paint_results.json'), 'w'), indent=1, default=str)
print('\nPAINT SUMMARY: %d PASS, %d FAIL' % (sum(r['result'] == 'PASS' for r in results), sum(r['result'] == 'FAIL' for r in results)))
if OUT_TMP:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
