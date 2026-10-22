# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Nested and torn power cuts in the wear-leveling consolidation (g80_wear_leveling.c).

    python -u suite_powercut_nested.py <fw_dir> [out_dir] [--ksamples=15] [--seeds=20] [--emu-seeds=1]
                                       [--only=2a,2b,2c,3a,3f]

  PC-2a  1a trigger (Apply of 88 keys): for sampled cut points k of the consolidation (plus both ends) the
         recovery boot is cut again at every 3rd of its flash ops (plus its first and last): after two
         clean boots the cache is the one the uncut recovery gives (= main + log at the cut, exact), the
         second clean boot does 0 flash ops.
  PC-2b  chains of 5 random nested cuts (each boot cut at a random flash op of its own, emulator power
         cut), then two clean boots: same cache, 0 ops in the second.
  PC-2c  state-log exhaustion: the recovery of a 16-sector consolidation (1f) cut again and again inside
         the program of its next state word (torn word, consumed): the documented erase-all fallback is
         reached only after >= 14 such cuts, and the data is still right when the fallback runs uncut.
  PC-3a/3f  torn-op model (g80emu.torn_op: a cut program writes a random subset of its 0-bits, a cut erase
         sets a random subset of bits to 1) at EVERY op of the 1a / 1f consolidation, --seeds seeds per op
         through the offline recovery model, --emu-seeds of them booted twice in the emulator (model ==
         firmware): never FULL_RESET or BAD (every byte = the consolidation-start cache or, for the tail of
         the write that filled the log, its old value).
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

FW, OUT, opt, OUT_TMP = parse_args(sys.argv, 'powercut_nested')
KS = int(opt.get('ksamples', 15))
SEEDS = int(opt.get('seeds', 20))
EMU_SEEDS = int(opt.get('emu-seeds', 1))
ONLY = set(opt['only'].split(',')) if 'only' in opt else None
results = []
t_start = time.time()


def want(n):
    return ONLY is None or n in ONLY


def check(tid, name, ok, detail, data=None):
    results.append(dict(id=tid, name=name, result='PASS' if ok else 'FAIL', detail=detail, data=data or {}))
    print('%s  %-8s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail), flush=True)


B = Bench(FW, OUT)
T1a = T1f = None
if want('2a') or want('2b') or want('3a'):
    T1a = B.trig_apply(88, 100, 1)
if want('2c') or want('3f'):
    T1f = B.trig_apply(88, 100, 6, extra=Bench.every_sector)


def boot_cut(flash, cut_at, torn_seed=None):
    """boot with an emulator power cut before flash op cut_at of this boot -> (flash after, ops, cut)"""
    gc.collect()
    m = mk(write_img(B.tmp, flash), B.nm)
    m.power_cut_at = cut_at
    if torn_seed is not None:
        m.power_cut_torn = random.Random(torn_seed)
    run_until(m, 6000, lambda mm: mm.power_cut or (mm.first_main_loop_t is not None and mm.t_ms() > mm.first_main_loop_t + 30), step=10.0)
    r = (bytes(m.flash), list(m.oprec), m.power_cut)
    m = None
    return r


# ------------------------------------------------------------------ PC-2a
if want('2a'):
    pre, ops = T1a
    i0, i1 = window(ops)
    ks = sorted(set([i0, i0 + 1, i1, i1 + 1] + [i0 + (i1 - i0) * j // (KS - 1) for j in range(KS)]))
    n_boots, bad, per_k = 0, [], {}
    for k in ks:
        f1 = apply_ops(pre, ops[:k])
        want_cache = model_boot(f1)['cache']
        r1, _ = B.boot_twice(f1)
        rec = r1['oprec']
        js = sorted(set([0, 1, len(rec) - 1] + list(range(0, len(rec), 3)))) if rec else []
        nb = 0
        for j in js:
            f2 = apply_ops(f1, rec[:j])
            a, b = B.boot_twice(f2)
            n_boots += 1
            nb += 1
            if a['cache'] != want_cache or b['cache'] != want_cache or b['ops'] or not a['ok'] or not b['ok'] or a['viol']:
                bad.append(dict(k=k, j=j, op=(rec[j][0], hex(rec[j][1])) if j < len(rec) else None, eq=(a['cache'] == want_cache, b['cache'] == want_cache),
                                ops=(a['ops'], b['ops']), ok=(a['ok'], b['ok']), regions=regions_diff(a['cache'], want_cache)))
        per_k[k] = (len(rec), nb)
    check('PC-2a', 'nested cuts: 1a consolidation cut at %d points, each recovery boot cut again at every 3rd op (+ ends): '
          'after 2 clean boots the cache = the uncut recovery cache (exact), 2nd clean boot 0 ops' % len(ks), not bad,
          '%d nested cut images booted twice; recovery ops per k %s; bad %d %s' % (n_boots, {k: v[0] for k, v in per_k.items()}, len(bad), bad[:5]),
          dict(per_k=per_k, bad=bad[:20]))

# ------------------------------------------------------------------ PC-2b
if want('2b'):
    pre, ops = T1a
    i0, i1 = window(ops)
    rnd = random.Random(2026)
    chains, bad = [], []
    for c in range(12):
        k = rnd.randrange(i0, i1 + 1)
        f = apply_ops(pre, ops[:k])
        want_cache = model_boot(f)['cache']
        cuts = []
        for depth in range(5):
            # number of flash ops this boot would do uncut, then a random cut inside them
            f_try, rec, _ = boot_cut(f, None)
            if not rec:
                cuts.append(None)
                break
            j = rnd.randrange(0, len(rec))
            f, _, cut = boot_cut(f, j)
            cuts.append((j, len(rec)))
        a, b = B.boot_twice(f)
        ok = a['cache'] == want_cache and b['cache'] == want_cache and b['ops'] == 0 and a['ok'] and b['ok']
        chains.append(dict(k=k, cuts=cuts, ok=ok))
        if not ok:
            bad.append(dict(k=k, cuts=cuts, ops=(a['ops'], b['ops']), regions=regions_diff(a['cache'], want_cache)))
    check('PC-2b', 'chains of 5 random nested cuts (12 chains from random points of the 1a consolidation): then 2 clean boots give '
          'the cache of the first cut image, 0 ops in the 2nd', not bad,
          'chains %s; bad %s' % ([(x['k'], x['cuts']) for x in chains[:6]], bad[:4]), dict(chains=chains))

# ------------------------------------------------------------------ PC-2c
if want('2c'):
    pre, ops = T1f
    i0, i1 = window(ops)
    # start: cut right after the 3rd COPY word (phase 1, 3 sectors done)
    st_progs = [i for i in range(i0, i1) if ops[i][0] == 'P' and ST0 <= ops[i][1] < WL0]
    f = apply_ops(pre, ops[:st_progs[3] + 1])
    want_cache = model_boot(f)['cache']
    torn_n, fallback_at, log = 0, None, []
    rs = random.Random(14)
    for it in range(40):
        _, rec, _ = boot_cut(f, None)
        wl_er = sum(1 for o in rec if o[0] == 'E' and WL0 <= o[1] < WL1)
        st_p = [j for j, o in enumerate(rec) if o[0] == 'P' and ST0 <= o[1] < WL0]
        if wl_er >= 32 and (not st_p or rec.index(next(o for o in rec if o[0] == 'E' and WL0 <= o[1] < WL1)) < (st_p[0] if st_p else 1 << 30)):
            fallback_at = torn_n
            log.append(('fallback', len(rec), wl_er))
            break
        if not st_p:
            log.append(('no state word', len(rec)))
            break
        j = st_p[0]
        # torn program of the next state word: keep cutting until the torn word is neither erased nor the intended value
        for s in range(50):
            seed = rs.randrange(1 << 30)
            f2, _, cut = boot_cut(f, j, torn_seed=seed)
            w = struct.unpack_from('<I', f2, rec[j][1])[0]
            if w not in (0xFFFFFFFF, rec[j][2][0]):
                break
        f = f2
        torn_n += 1
        log.append((j, hex(w)))
    a, b = B.boot_twice(f)
    ok = fallback_at is not None and fallback_at >= 14 and a['cache'] == want_cache and b['ops'] == 0 and a['ok']
    check('PC-2c', 'state-log exhaustion: the recovery of a 16-sector consolidation cut inside each of its next state-word programs '
          '(torn word kept): the erase-all fallback runs only after >= 14 consumed words, and uncut it gives the right data, '
          '2nd boot 0 ops', ok,
          'torn state words before the fallback %s; data after %s; 2nd boot ops %d; trace %s' % (
              fallback_at, 'ok' if a['cache'] == want_cache else 'WRONG %s' % regions_diff(a['cache'], want_cache), b['ops'], log[:20]))


# ------------------------------------------------------------------ PC-3
def torn_sweep(tid, name, T):
    pre, ops = T
    i0, i1 = window(ops)
    C = ops[i0][5]
    L = model_boot(apply_ops(pre, ops[:i0]))['cache']
    cnt, cnt_emu, bad, mism = Counter(), Counter(), [], []
    kinds = Counter()
    for k in range(i0, i1 + 1):
        o = ops[k]
        kinds['state' if ST0 <= o[1] < WL0 else 'scratch' if o[1] < ST0 else 'main' if o[1] < WL0 + 1024 else 'hash/log'] += 1
        base = apply_ops(pre, ops[:k])
        for s in range(SEEDS):
            f = apply_ops(base, [], torn=(o, random.Random(k * 1000 + s)))
            mod = model_boot(f)
            out, c = outcome(mod['cache'], C, L, B.D)
            cnt[out] += 1
            if out in ('FULL_RESET', 'BAD'):
                bad.append(dict(k=k, seed=s, op=(o[0], hex(o[1])), cls=c, path=mod['path'], regions=regions_diff(mod['cache'], C)))
            if s < EMU_SEEDS:
                a, b = B.boot_twice(f)
                out2, _ = outcome(a['cache'], C, L, B.D)
                cnt_emu[out2] += 1
                if a['cache'] != mod['cache'] or b['ops'] or b['cache'] != a['cache'] or not a['ok'] or a['viol']:
                    mism.append(dict(k=k, seed=s, op=(o[0], hex(o[1])), model=mod['path'], eq=a['cache'] == mod['cache'],
                                     ops=(a['ops'], b['ops']), ok=a['ok'], regions=regions_diff(a['cache'], mod['cache'])))
    check(tid, '%s: torn op at every op of the consolidation (%d ops: %s), %d seeds each (offline recovery model), %d booted twice '
          'in the emulator: never FULL_RESET / BAD, emulator = model, 2nd boot 0 ops' % (name, i1 - i0 + 1, dict(kinds), SEEDS, EMU_SEEDS),
          not bad and not mism,
          'model outcomes %s; emulator outcomes %s; bad %d %s; model/emulator mismatches %d %s' % (
              dict(cnt), dict(cnt_emu), len(bad), bad[:4], len(mism), mism[:4]), dict(model=dict(cnt), emu=dict(cnt_emu)))


if want('3a'):
    torn_sweep('PC-3a', '1a (Apply, 88 keys)', T1a)
if want('3f'):
    torn_sweep('PC-3f', '1f (all 16 sectors)', T1f)

json.dump(results, open(os.path.join(OUT, 'suite_powercut_nested_results.json'), 'w'), indent=1, default=str)
print('\nPOWERCUT-NESTED SUMMARY: %d PASS, %d FAIL, wall %.0f s' % (sum(r['result'] == 'PASS' for r in results),
                                                                  sum(r['result'] == 'FAIL' for r in results), time.time() - t_start), flush=True)
if OUT_TMP:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
