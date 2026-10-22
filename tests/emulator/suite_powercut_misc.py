# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Format path, regressions and timing of the power-fail-safe wear-leveling backend (g80_wear_leveling.c).

    python -u suite_powercut_misc.py <fw_dir> [out_dir] [--step=3] [--only=4a,4b,6,7]

  PC-4a  factory reset (eeconfig_disable(), what EE_CLR / Fn+Backspace run) and the reboot that writes the
         defaults: a cut at every op of the format and at every --step-th op of the reboot (+ ends), two
         boots each: the data is the old data or the defaults; the 2nd boot does 0 ops; the format erases
         the state log first (no stale scratch can be restored after a format: every cut inside the format
         boots on the plain path).
  PC-4b  the boot from a blank area (first boot after flashing over an erased EEPROM): a cut at every
         --step-th op (+ ends), two boots each: defaults, 2nd boot 0 ops.
  PC-6   a healthy boot does 0 flash ops, also in the 3 s after it; a normal session (remaps, lighting,
         Apply with room in the log) writes only log lines (no erase, nothing in the aux sectors).
  PC-7   timing and watchdog with the flash stall model (program 2 ms, erase 20 ms): the worst-case
         consolidation (all 16 sectors, Apply of 88 keys) takes <= 2.2 s, no watchdog reset, the WDT
         feed gap stays < 500 ms, the main loop runs again afterwards, the green Apply flash starts after
         the consolidation.
"""
import os, sys, json, time, random, gc
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pc_common import *
from via_host import parse_args
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

FW, OUT, opt, OUT_TMP = parse_args(sys.argv, 'powercut_misc')
STEP = int(opt.get('step', 3))
ONLY = set(opt['only'].split(',')) if 'only' in opt else None
results = []
t_start = time.time()


def want(n):
    return ONLY is None or n in ONLY


def check(tid, name, ok, detail, data=None):
    results.append(dict(id=tid, name=name, result='PASS' if ok else 'FAIL', detail=detail, data=data or {}))
    print('%s  %-6s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail), flush=True)


B = Bench(FW, OUT)


def cut_sweep(pre, ops, ks, old, D):
    cnt, bad, paths = Counter(), [], Counter()
    for k in ks:
        f = apply_ops(pre, ops[:k])
        mod = model_boot(f)
        a, b = B.boot_twice(f)
        R = a['cache']
        c = 'OLD' if R == old else 'DEFAULTS' if R == D else 'MIX'
        if c == 'MIX' and R[0:2] == D[0:2] and all(2 <= i < 37 for i in range(1024) if R[i] != D[i]):
            # QMK core order, not the backend: eeconfig_init_quantum() writes the eeconfig magic before the
            # fields, so a cut in between leaves some eeconfig fields 0 under a valid magic (VIA data = defaults)
            c = 'QMK-INIT-ORDER'
        cnt[c] += 1
        paths[mod['path']] += 1
        # (the model covers wear_leveling_init only; these boots also run eeconfig_init: no model comparison)
        if c == 'MIX' or b['ops'] or b['cache'] != R or not a['ok'] or not b['ok'] or a['viol']:
            bad.append(dict(k=k, op=(ops[k][0], hex(ops[k][1]), ops[k][4]) if k < len(ops) else None, cls=c,
                            ops=(a['ops'], b['ops']), ok=(a['ok'], b['ok']), model=mod['path'],
                            regions_vs_D=regions_diff(R, D), regions_vs_old=regions_diff(R, old)))
    return cnt, bad, paths


# ------------------------------------------------------------------ PC-4a factory reset
if want('4a'):
    m = B.booted(B.P0, 50)
    old = m.wl_cache()
    pre = bytes(m.flash)
    n0 = len(m.oprec)
    m.call('eeconfig_disable', max_insn=80000000)
    ops_f = m.oprec[n0:]
    mid = bytes(m.flash)
    m = None
    m = mk(write_img(B.tmp, mid), B.nm)
    run_until(m, 6000, lambda mm: mm.first_main_loop_t is not None and mm.t_ms() > mm.first_main_loop_t + 300, step=10.0)
    ops_r = list(m.oprec)
    m = None
    ops = ops_f + ops_r
    nf = len(ops_f)
    ks = sorted(set(list(range(0, nf + 1)) + list(range(nf, len(ops) + 1, STEP)) + [len(ops) - 1, len(ops)]))
    cnt, bad, paths = cut_sweep(pre, ops, ks, old, B.D)
    first = ops_f[0] if ops_f else None
    st_first = first is not None and first[0] == 'E' and first[1] == ST0 and ops_f[1][0] == 'E' and ops_f[1][1] == ST0 + 64
    fmt_paths = Counter(model_boot(apply_ops(pre, ops[:k]))['path'] for k in range(0, nf + 1))
    check('PC-4a', 'factory reset (eeconfig_disable) + reboot: cut at every op of the format (%d) and every %d. op of the reboot (%d ops): '
          'old data or defaults, never a mix (except QMK-INIT-ORDER: eeconfig_init writes its magic before the fields, reported); 2nd '
          'boot 0 ops; the format erases the state log first, every cut inside it boots on the plain path (no scratch restore)' % (
              nf, STEP, len(ops_r)),
          not bad and st_first and set(fmt_paths) <= {'plain'},
          'outcomes %s; recovery paths %s (format part: %s); format starts with %s; bad %d %s' % (
              dict(cnt), dict(paths), dict(fmt_paths), [(o[0], hex(o[1])) for o in ops_f[:3]], len(bad), bad[:4]),
          dict(outcomes=dict(cnt), bad=bad[:20]))

# ------------------------------------------------------------------ PC-4b boot from a blank area
if want('4b'):
    m = mk(write_img(B.tmp, B.blank), B.nm)
    run_until(m, 6000, lambda mm: mm.first_main_loop_t is not None and mm.t_ms() > mm.first_main_loop_t + 300, step=10.0)
    ops = list(m.oprec)
    m = None
    ks = sorted(set(list(range(0, len(ops) + 1, STEP)) + [0, 1, len(ops) - 1, len(ops)]))
    cnt, bad, paths = cut_sweep(B.blank, ops, ks, B.D, B.D)
    check('PC-4b', 'boot from a blank area (%d flash ops: format + defaults + consolidations): cut at every %d. op, 2 boots each: the '
          'defaults (except QMK-INIT-ORDER, reported), 2nd boot 0 ops' % (len(ops), STEP), not bad,
          'outcomes %s; recovery paths %s; bad %d %s' % (dict(cnt), dict(paths), len(bad), bad[:4]), dict(outcomes=dict(cnt), bad=bad[:20]))

# ------------------------------------------------------------------ PC-6 regressions
if want('6'):
    m = B.booted(B.P0, 3000)
    healthy = (len(m.oprec), boot_ok(m))
    m = None
    m = B.via_m(B.P0)
    n0 = len(m.oprec)
    free0 = log_free(m)
    via_sync(m, [[0x05, 1, 2, 7, 0, 0x2C]])
    run_until(m, m.t_ms() + 400)
    via_sync(m, [[0x07, 3, 1, 120], [0x09, 3]])
    run_until(m, m.t_ms() + 1600)
    Bench.paint(m, 4, 9)
    via_sync(m, [[0x07, 0, 24, 1], [0x09, 0]])
    run_until(m, m.t_ms() + 1500)
    ops = m.oprec[n0:]
    free1 = log_free(m)
    m = None
    aux = [o for o in ops if o[1] < WL0]
    er = [o for o in ops if o[0] == 'E']
    ok = healthy == (0, True) and ops and not aux and not er and free1 < free0
    check('PC-6', 'a healthy boot: 0 flash ops in the boot and the 3 s after it; a normal session (remap, lighting save, Apply of 4 keys, '
          'room in the log): log lines only, no erase, the aux sectors untouched', ok,
          'healthy boot ops %d (ok %s); session ops %d (log %d -> %d B free), erases %d, aux ops %d' % (
              healthy[0], healthy[1], len(ops), free0, free1, len(er), len(aux)))

# ------------------------------------------------------------------ PC-7 timing / watchdog
if want('7'):
    flashes = []

    def hook_flash(m):
        a = m.sym['g80_flash'] & ~1
        m.uc.hook_add(UC_HOOK_CODE, lambda uc, addr, size, user: flashes.append((round(m.t_ms(), 1), uc.reg_read(UC_ARM_REG_R2) & 0xFF, uc.reg_read(UC_ARM_REG_R3) & 0xFF)),
                      begin=a, end=a)

    m = B.via_m(B.P0, prog_us=2000, erase_us=20000)
    hook_flash(m)
    Bench.every_sector(m)
    prefill(m, 100)
    run_until(m, m.t_ms() + 20)
    m.pending_stall = 0
    Bench.paint(m, 88, 6)
    m.max_feed_gap = 0.0
    n0 = len(m.oprec)
    hits0 = m.main_loop_hits
    t_ap = m.t_ms()
    ntx = len(m.hid_tx)
    m.hid_send_lockstep([[0x07, 0, 24, 1], [0x09, 0]], m.t_ms())
    run_until(m, m.t_ms() + 6000)
    ops = m.oprec[n0:]
    i0, i1 = window(ops)
    t0c, t1c = (ops[i0][3], ops[i1][3] + 20.0) if i0 is not None else (None, None)
    replies = [round(t - t_ap, 1) for t, _ in m.hid_tx[ntx:]]
    green = [t for t, r, g in flashes if t >= t_ap and r == 0 and g == 255]
    loop_after = m.main_loop_hits - hits0
    resets = list(m.resets)
    gap = m.max_feed_gap
    me = sum(o[0] == 'E' and WL0 <= o[1] < WL0 + 1024 for o in ops[i0:i1 + 1]) if i0 is not None else 0
    m = None
    dur = (t1c - t0c) if t0c is not None else None
    ok = (dur is not None and dur <= 2200 and me == 16 and not resets and gap < 0.5 and loop_after > 100 and green and green[0] >= t1c)
    check('PC-7', 'worst-case consolidation (16 sectors, Apply of 88 keys) with the stall model (program 2 ms, erase 20 ms): <= 2.2 s, '
          'no watchdog reset, WDT feed gap < 500 ms, the main loop runs again, the green flash starts after the consolidation', ok,
          'consolidation %s ms (%s .. %s ms, %d main erases, %d ops), resets %s, WDT feed gap max %.1f ms, main-loop passes after %d, '
          'VIA replies at Apply+%s ms, green flash at Apply+%s ms' % (
              None if dur is None else round(dur), t0c, t1c, me, (i1 - i0 + 1) if i0 is not None else 0, resets, gap * 1e3, loop_after,
              replies, [round(t - t_ap, 1) for t in green[:2]]))

json.dump(results, open(os.path.join(OUT, 'suite_powercut_misc_results.json'), 'w'), indent=1, default=str)
print('\nPOWERCUT-MISC SUMMARY: %d PASS, %d FAIL, wall %.0f s' % (sum(r['result'] == 'PASS' for r in results),
                                                                sum(r['result'] == 'FAIL' for r in results), time.time() - t_start), flush=True)
if OUT_TMP:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
