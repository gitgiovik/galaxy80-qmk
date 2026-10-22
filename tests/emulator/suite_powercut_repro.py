# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unplug-anytime reproduction of the 2026-10-21 report ("sometimes everything resets when I unplug"):
7 realistic sessions on the VIA image, a power cut at EVERY flash op of each, classified offline with the
Python model of the wear-leveling read path INCLUDING the boot recovery of g80_wear_leveling.c, and a
sample of the cut images booted in the emulator (model == firmware).

    python -u suite_powercut_repro.py <fw_dir> [out_dir] [--sessions=7] [--verify=20] [--prog-us=2000] [--erase-us=20000]

Each session is a fresh power-on from the flash the previous one left: VIA lighting (set + save), Fn keys,
Galaxy80 menu items, VIA remaps, per-key paint + Apply (10 / 30 / 88 keys), in two orders (paint last,
lighting last). Flash stall model on.
  PC-8   every cut point k gives ALL_KEPT (= the session end), SESSION_LOST (= the session start) or
         PARTIAL (every byte = a value the cache really had during the session: changes not yet saved) or
         TORN_ENTRY (cut between the two 4 B lines of one QMK log entry: its last bytes read 0, a QMK log
         limit independent of the consolidation); FULL_RESET (defaults / eeconfig or VIA magic or layout
         byte lost) = 0 and BAD = 0; >= 136 sampled boots agree with the model.
  PC-9   unplug time sweep 0..8 s after the last action, 25 ms steps: PARTIAL for at most 1.3 s after
         the last action (settings saved G80_SAVE_DELAY_MS = 1 s after the last change + write gate),
         ALL_KEPT after; a consolidation running at that time is reported.
  PC-10  wear per consolidation: erases per sector (main <= 1, hash + log 1, scratch <= 8 per scratch
         sector on average, state log 1 per state sector).
"""
import os, sys, json, time, random, bisect, gc
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pc_common import *
from via_host import parse_args
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

FW, OUT, opt, OUT_TMP = parse_args(sys.argv, 'powercut_repro')
NSESS = int(opt.get('sessions', 7))
NVERIFY = int(opt.get('verify', 20))
PROG_US = float(opt.get('prog-us', 2000))
ERASE_US = float(opt.get('erase-us', 20000))
SEED = int(opt.get('seed', 7))
FN, ENT, S_ = (5, 11), (3, 13), (3, 2)
results = []
t_start = time.time()


def check(tid, name, ok, detail, data=None):
    results.append(dict(id=tid, name=name, result='PASS' if ok else 'FAIL', detail=detail, data=data or {}))
    print('%s  %-6s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail), flush=True)


B = Bench(FW, OUT)
NM = B.nm


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


def session(m, idx, rnd, order):
    marks = []

    def mark(lbl):
        marks.append((round(m.t_ms(), 1), lbl))

    def v(pkts):
        via_sync(m, pkts)

    def pause(ms):
        run_until(m, m.t_ms() + ms, step=2.0)

    def lighting():
        mark('lighting')
        for k in range(5):                                  # brightness slider drag
            v([[0x07, 3, 1, (40 + 37 * k + 11 * idx) & 0xFF], [0x09, 3]])
            pause(60)
        v([[0x07, 3, 2, 1 + (idx % 6)], [0x09, 3]])          # effect
        pause(300)
        for k in range(3):                                  # colour picker
            v([[0x07, 3, 4, (idx * 50 + k * 20) & 0xFF, 255 - 40 * k], [0x09, 3]])
            pause(60)
        pause(300)
        mark('fn_keys')
        fn_combo(m, ENT)                                    # RM_NEXT
        pause(250)
        fn_combo(m, S_)                                     # SOCD toggle
        pause(300)

    def menu():
        mark('menu')
        v([[0x07, 0, 14, idx & 1], [0x09, 0]])               # game mode
        pause(300)
        v([[0x07, 0, 15, (idx >> 1) & 1], [0x09, 0]])        # status indicators
        pause(300)
        v([[0x07, 0, 19, idx % 3], [0x09, 0]])               # bar mode
        pause(300)
        v([[0x07, 0, 26, idx % 4], [0x09, 0]])               # sleep option
        pause(300)

    def remaps():
        mark('remaps')
        for k in range(3):
            v([[0x05, 2, 1 + k, 3 + ((idx + k) % 10), 0, 0x04 + ((idx * 3 + k) % 26)]])
            pause(250)

    def paint():
        n = [10, 30, 88][idx % 3]
        mark('paint%d' % n)
        for led in sorted(rnd.sample(range(88), n)):
            v([[0x07, 0, 1, led, 1, rnd.randrange(256), rnd.choice([255, 170, 85])], [0x09, 0]])
            pause(15)
        pause(300)
        mark('apply')
        v([[0x07, 0, 24, 1], [0x09, 0]])
        pause(50)

    seq = {'paint_last': [lighting, menu, remaps, paint], 'light_last': [paint, remaps, menu, lighting]}[order]
    for f in seq:
        f()
    t_last = m.t_ms()
    mark('last_action')
    return t_last, marks


def consolidation_windows(ops):
    out, i = [], 0
    while i < len(ops):
        w0, w1 = window(ops[i:])
        if w0 is None:
            break
        out.append((i + w0, i + w1))
        i = i + w1 + 1
    return out


cur = B.f_def
P_first = B.D
sessions, total_cls, ver_all, wear = [], Counter(), [], []
rnd = random.Random(SEED)
for si in range(NSESS):
    order = 'paint_last' if si % 2 == 0 else 'light_last'
    gc.collect()
    m = mk(write_img(B.tmp, cur), NM, stress=True, snap_cache=True, prog_us=PROG_US, erase_us=ERASE_US)
    boot_to_loop(m, 300)
    base = bytes(m.flash)
    P = m.wl_cache()
    boot_ops = len(m.oprec)
    n0 = len(m.oprec)
    t_last, marks = session(m, si + 1, rnd, order)
    run_until(m, t_last + 9000)
    F = m.wl_cache()
    ops = m.oprec[n0:]
    cur = bytes(m.flash)
    m = None
    seen = [set([P[b]]) for b in range(1024)]
    # cache history: the cache at each op (values the session really had)
    hist = [o[5] for o in ops] + [F]
    for c in hist:
        for b in range(1024):
            seen[b].add(c[b])
    def klass(R):
        if R == F:
            return 'ALL_KEPT'
        if R == P:
            return 'SESSION_LOST'
        if R == B.D or R[0:2] != P[0:2] or R[37:42] != P[37:42]:
            return 'FULL_RESET'
        if all(R[b] in seen[b] for b in range(1024)):
            return 'PARTIAL'
        return 'BAD'

    def log_line(o):
        return o[0] == 'P' and WL0 + LOG_START <= o[1] < WL1

    cls, paths = [], Counter()
    f = bytearray(base)
    for k in range(len(ops) + 1):
        if k:
            f = apply_ops(f, [ops[k - 1]])
        mod = model_boot(f)
        R = mod['cache']
        paths[mod['path']] += 1
        c = klass(R)
        if c == 'BAD' and 0 < k < len(ops) and log_line(ops[k - 1]) and log_line(ops[k]) and ops[k][1] == ops[k - 1][1] + 4                 and klass(model_boot(apply_ops(f, [ops[k]]))['cache']) != 'BAD':
            # QMK log limit (not the consolidation): the cut fell between the two 4 B lines of one log entry;
            # the playback applies the header line with an erased (= 0) second line
            c = 'TORN_ENTRY'
        cls.append(c)
    total_cls.update(cls)
    # unplug-time sweep after the last action
    times = [o[3] for o in ops]
    sweep = []
    t = t_last
    while t <= t_last + 8000:
        k = bisect.bisect_left(times, t)
        sweep.append((round(t - t_last), k, cls[k]))
        t += 25.0
    partial_end = max([s[0] for s in sweep if s[2] != 'ALL_KEPT'], default=-25) + 25
    wins = consolidation_windows(ops)
    cons = [(round(ops[a][3] - t_last), round(ops[b][3] - t_last)) for a, b in wins]
    for a, b in wins:
        per = Counter(o[1] & ~63 for o in ops[a:b + 1] if o[0] == 'E')
        wear.append(dict(main=sum(v for s, v in per.items() if WL0 <= s < WL0 + 1024),
                         main_max=max([v for s, v in per.items() if WL0 <= s < WL0 + 1024], default=0),
                         hashlog_max=max([v for s, v in per.items() if s >= WL0 + 1024], default=0),
                         scr0=per.get(AUX0, 0), scr1=per.get(AUX0 + 64, 0), st0=per.get(ST0, 0), st1=per.get(ST0 + 64, 0)))
    # verification boots: first / middle / last k of each class, consolidation edges, evenly spread
    by = defaultdict(list)
    for k, c in enumerate(cls):
        by[c].append(k)
    cand = set()
    for c, ks in by.items():
        cand |= {ks[0], ks[len(ks) // 2], ks[-1]}
    for a, b in wins:
        cand |= {a, a + 1, (a + b) // 2, b, b + 1}
    cand |= set(range(0, len(ops) + 1, max(1, len(ops) // NVERIFY)))
    cand = sorted(x for x in cand if 0 <= x <= len(ops))
    if len(cand) > NVERIFY + 10:
        keep = set(cand[::max(1, len(cand) // NVERIFY)]) | {by[c][0] for c in by}
        for a, b in wins:
            keep |= {a + 1, (a + b) // 2}
        cand = sorted(keep)
    ver = []
    for k in cand:
        fk = apply_ops(base, ops[:k])
        a, b2 = B.boot_twice(fk)
        mod = model_boot(fk)
        ver.append(dict(k=k, offline=cls[k], match=a['cache'] == mod['cache'], ok=a['ok'], ops2=b2['ops']))
    ver_all += ver
    res = dict(session=si + 1, order=order, t_last=round(t_last), n_ops=len(ops), boot_ops=boot_ops, classes=dict(Counter(cls)),
               partial_end_ms=partial_end, consolidations_rel_ms=cons, paths=dict(paths), verify=len(ver),
               verify_bad=[x for x in ver if not x['match'] or not x['ok'] or x['ops2']],
               marks=[(round(t - t_last), l) for t, l in marks])
    sessions.append(res)
    print('[session %d %s] ops %d, classes %s, PARTIAL until +%d ms after the last action, consolidations (ms rel. last action) %s, '
          'verify %d boots, bad %d' % (si + 1, order, len(ops), dict(Counter(cls)), partial_end, cons, len(ver), len(res['verify_bad'])), flush=True)

vbad = [x for x in ver_all if not x['match'] or not x['ok'] or x['ops2']]
check('PC-8', '%d sessions, power cut at every flash op (%d cuts), classified with the recovery model: only ALL_KEPT / SESSION_LOST / '
      'PARTIAL (values the session really had), FULL_RESET = 0; %d sampled boots: firmware = model, 2nd boot 0 ops' % (
          NSESS, sum(total_cls.values()), len(ver_all)),
      total_cls.get('FULL_RESET', 0) == 0 and total_cls.get('BAD', 0) == 0 and len(ver_all) >= 136 and not vbad,
      'classes %s (TORN_ENTRY = a cut between the 2 lines of one QMK log entry: the last bytes of that entry read 0; a QMK log '
      'limit, not the consolidation, also on the old firmware); verify mismatches %d %s' % (dict(total_cls), len(vbad), vbad[:4]),
      dict(sessions=sessions))
pe = [s['partial_end_ms'] for s in sessions]
cons_late = [(s['session'], c) for s in sessions for c in s['consolidations_rel_ms'] if c[1] >= 0]
check('PC-9', 'unplug 0..8 s after the last action (25 ms steps): unsaved changes lost for at most 1.3 s after the last action, '
      'ALL_KEPT after', max(pe) <= 1300,
      'PARTIAL until (ms after the last action) per session %s; consolidations still running after the last action %s' % (pe, cons_late),
      dict(partial_end=pe))
if wear:
    n = len(wear)
    avg = {k: round(sum(w[k] for w in wear) / n, 2) for k in ('main', 'scr0', 'scr1', 'st0', 'st1')}
    ok10 = (all(w['main_max'] <= 1 and w['hashlog_max'] <= 1 and w['st0'] <= 1 and w['st1'] <= 1 for w in wear)
            and avg['scr0'] <= 8 and avg['scr1'] <= 8)
    check('PC-10', 'wear per consolidation over the %d-session workload: main and hash/log sectors <= 1 erase each, scratch <= 8 '
          'per scratch sector on average, state log 1 per state sector' % NSESS, ok10,
          '%d consolidations; average erases per consolidation %s (main = sum over the changed sectors); per consolidation %s' % (
              n, avg, wear), dict(wear=wear))
else:
    check('PC-10', 'wear per consolidation', False, 'no consolidation in the workload')

json.dump(results, open(os.path.join(OUT, 'suite_powercut_repro_results.json'), 'w'), indent=1, default=str)
print('\nPOWERCUT-REPRO SUMMARY: %d PASS, %d FAIL, wall %.0f s' % (sum(r['result'] == 'PASS' for r in results),
                                                                 sum(r['result'] == 'FAIL' for r in results), time.time() - t_start), flush=True)
if OUT_TMP:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
