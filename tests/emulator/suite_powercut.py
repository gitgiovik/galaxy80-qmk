# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Power-cut sweep of the wear-leveling consolidation (g80_wear_leveling.c, power-fail-safe since
2026-10-21) and the flash map of the auxiliary sectors.

    python -u suite_powercut.py <fw_dir> [out_dir] [--step=N] [--only=1a,1f,5]

Every trigger is run once with every flash op recorded (data + the wear-leveling cache at that op). Then
for EVERY op k of the consolidation window (from its first op to the state-log clear after the hash, both
ends included; --step=N samples) the flash image "power cut before op k" is rebuilt offline, booted, and
booted a second time from what the first boot left. Checked per cut:
  - the board boots (no fault, no reset, no ISP), no flash op outside 0xF6C0..0xFFBF;
  - the cache after the boot is the cache at the consolidation start (C) byte for byte, except the
    bytes of the write that filled the log which did not fit in it any more (L = C without that tail,
    what a cut just before the consolidation gives); those come back old or new: 'TAIL-MIX(n)';
    never anything else ('BAD'), never the defaults (FULL_RESET);
  - the second boot does 0 flash ops and reads the same cache (steady state after one boot);
  - the offline model of the boot recovery (pc_common.model_boot) gives the same cache.
Triggers (VIA image, user data differing from the defaults):
  1a  Apply after painting 88 keys (log pre-filled so that Apply consolidates)
  1b  lighting change (deferred save) then Apply of 30 painted keys
  1c  deferred settings save that fills the log
  1d  boot-time consolidation, log full (image = 1c cut just before its consolidation)
  1e  boot-time consolidate_force from an invalid log entry (B4 image)
  1f  worst case: every logical sector changed (16), the Apply of 88 keys consolidates
Flash map (PC-5): the new image booted over a user data area with the aux sectors 0xFF, 0x00, random
and (only if G80_S8_BIN names an older .bin) that image's bytes 0xF6C0..0xF7BF: data kept,
state treated as empty, 0 boot ops; code ends <= 0xF6C0 (galaxy80_guard.ld), heap >= 512 B. That models
replacing the code only; the documented flash writes the whole .bin, 0x00 over 0xF6C0..0xFFBF (PC-5-flash):
the stored data are wiped and the first boot recreates the defaults; PC-5-flash-cut sweeps the first
consolidation on such a freshly flashed band.
"""
import os, sys, json, time, random, struct, gc
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pc_common import *
from via_host import parse_args
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

FW, OUT, opt, OUT_TMP = parse_args(sys.argv, 'powercut')
NM = os.path.join(FW, 'nm_via.txt')
BIN = os.path.join(FW, 'epomaker_galaxy80_via.bin')
STEP = int(opt.get('step', 1))
ONLY = set(opt['only'].split(',')) if 'only' in opt else None
TMP = os.path.join(OUT, 'pc_tmp.bin')
results = []
t_start = time.time()


def want(n):
    return ONLY is None or n in ONLY


def check(tid, name, ok, detail, data=None):
    results.append(dict(id=tid, name=name, result='PASS' if ok else 'FAIL', detail=detail, data=data or {}))
    print('%s  %-8s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail), flush=True)


B = Bench(FW, OUT)
D, P0, TMP = B.D, B.P0, B.tmp
booted = B.booted
def sweep(tid, desc, pre, ops):
    i0, i1 = window(ops)
    if i0 is None:
        check(tid, desc, False, 'no consolidation in the trigger (%d ops)' % len(ops))
        return None
    C = ops[i0][5]
    L = model_boot(apply_ops(pre, ops[:i0]))['cache']
    U = sum(a != b for a, b in zip(C, L))
    ks = sorted(set(list(range(i0, i1 + 2, STEP)) + [i0, i0 + 1, i1, i1 + 1]))
    cnt, bad, cls_k, paths = Counter(), [], {}, Counter()
    full = 0
    rec_ops = []
    t0 = time.time()
    for k in ks:
        f = apply_ops(pre, ops[:k])
        mod = model_boot(f)
        r1, r2 = boot_twice(TMP, NM, f)
        out, c = outcome(r1['cache'], C, L, D)
        full += out == 'FULL_RESET'
        cnt[out] += 1
        paths[mod['path']] += 1
        cls_k[k] = c
        rec_ops.append(r1['ops'])
        if (out in ('BAD', 'FULL_RESET') or not r1['ok'] or not r2['ok'] or r2['ops'] or r2['cache'] != r1['cache']
                or r1['viol'] or r2['viol'] or mod['cache'] != r1['cache']):
            bad.append(dict(k=k, op=(ops[k][0], hex(ops[k][1])) if k < len(ops) else None, cls=c, ok=(r1['ok'], r2['ok']),
                            ops=(r1['ops'], r2['ops']), model=mod['path'], model_eq=mod['cache'] == r1['cache'],
                            viol=(r1['viol'] + r2['viol'])[:2], regions=regions_diff(r1['cache'], C)))
    w = ops[i0:i1 + 1]
    st = dict(window=(i0, i1), ops=len(w), erases=sum(o[0] == 'E' for o in w), aux_erases=sum(o[0] == 'E' and o[1] < WL0 for o in w),
              programs=sum(o[0] == 'P' for o in w), state_words=sum(o[0] == 'P' and ST0 <= o[1] < WL0 for o in w),
              main_erases=sum(o[0] == 'E' and WL0 <= o[1] < WL0 + 1024 for o in w), tail_bytes=U,
              cuts=len(ks), outcome=dict(cnt), classes=dict(Counter(cls_k.values())), recovery_paths=dict(paths),
              max_recovery_ops=max(rec_ops), dur_ms=round(w[-1][3] - w[0][3], 1), wall_s=round(time.time() - t0))
    ok = not bad and full == 0
    check(tid, '%s: cut at every op of the consolidation (%d cuts), 2 boots each: cache = the consolidation-start cache '
          '(except the not-logged tail of the write that filled the log: %d B), never the defaults; 2nd boot 0 ops; '
          'offline recovery model = firmware' % (desc, len(ks), U), ok,
          'table %s; classes %s; window ops %d (%d erases: %d aux, %d main; %d programs, %d state words); recovery paths %s; '
          'max recovery-boot ops %d; bad %d %s' % (dict(cnt), st['classes'], st['ops'], st['erases'], st['aux_erases'],
                                                 st['main_erases'], st['programs'], st['state_words'], dict(paths),
                                                 st['max_recovery_ops'], len(bad), bad[:4]), dict(st, bad=bad[:20]))
    return dict(pre=pre, ops=ops, C=C, L=L, i0=i0, i1=i1, stats=st)


SW = {}
if want('1a'):
    SW['1a'] = sweep('PC-1a', 'Apply after painting 88 keys', *B.trig_apply(88, 100, 1))
if want('1b'):
    SW['1b'] = sweep('PC-1b', 'lighting change, then Apply of 30 keys', *B.trig_apply(30, 110, 2, lighting=True))
if want('1c') or want('1d'):
    pre, ops = B.trig_save_fill()
    if want('1c'):
        SW['1c'] = sweep('PC-1c', 'deferred settings save that fills the log', pre, ops)
    if want('1d'):
        i0, _ = window(ops)
        SW['1d'] = sweep('PC-1d', 'boot-time consolidation with a full log', *B.trig_boot(apply_ops(pre, ops[:i0])))
if want('1e'):
    SW['1e'] = sweep('PC-1e', 'boot-time consolidate_force from an invalid log entry (B4 image)', *B.trig_boot(B.b4_image()))
if want('1f'):
    SW['1f'] = sweep('PC-1f', 'worst case, all 16 logical sectors changed (Apply of 88 keys)', *B.trig_apply(88, 100, 6, extra=Bench.every_sector))
    if SW['1f']:
        st = SW['1f']['stats']
        check('PC-1f-ws', 'worst case uses every sector: 16 main sector erases, 16 COPY + BEGIN + PHASE2 = 18 state words '
              '(of 32), 16 scratch erases (+2 state erases)', st['main_erases'] == 16 and st['state_words'] == 18,
              'main erases %d, state words %d, aux erases %d' % (st['main_erases'], st['state_words'], st['aux_erases']))
json.dump({k: v['stats'] for k, v in SW.items() if v}, open(os.path.join(OUT, 'powercut_sweep_table.json'), 'w'), indent=1, default=str)
if SW:
    print('\nSWEEP TABLE (cuts per outcome: KEPT = cache at the consolidation start, LAST_LOST = without the not-logged '
          'tail of the write that filled the log, PARTIAL = part of that tail old, FULL_RESET = defaults)', flush=True)
    for k, v in SW.items():
        if v:
            st = v['stats']
            print('  %-3s cuts %4d  KEPT %4d  LAST_LOST %4d  PARTIAL %4d  FULL_RESET %d  BAD %d   (tail %d B, window %d ops, %.0f ms)' % (
                k, st['cuts'], st['outcome'].get('KEPT', 0), st['outcome'].get('LAST_LOST', 0), st['outcome'].get('PARTIAL', 0),
                st['outcome'].get('FULL_RESET', 0), st['outcome'].get('BAD', 0), st['tail_bytes'], st['ops'], st['dur_ms']), flush=True)

# ------------------------------------------------------------------ PC-5 flash map / upgrade from s8
if want('5'):
    # optional: G80_S8_BIN = an older .bin whose bytes 0xF6C0..0xF7BF are used as one more aux variant
    s8_bin = os.environ.get('G80_S8_BIN')
    res = {}
    m = booted(P0, 30)
    want_cache = m.wl_cache()
    m = None
    variants = {'aux 0xFF': None, 'aux 0x00 (bin padding)': b'\x00' * 256,
                'aux random bytes': bytes(random.Random(8).getrandbits(8) for _ in range(256))}
    if s8_bin and os.path.exists(s8_bin):
        variants['aux = s8 image bytes'] = open(s8_bin, 'rb').read()[AUX0:WL0]
    for name, aux in variants.items():
        f = bytearray(P0)
        f[AUX0:WL0] = b'\xff' * 256 if aux is None else aux
        m = booted(bytes(f), 3000)
        res[name] = dict(ops=len(m.oprec), kept=m.wl_cache() == want_cache, ok=boot_ok(m), aux_same=bytes(m.flash[AUX0:WL0]) == bytes(f[AUX0:WL0]))
        m = None
    sy = Syms(NM)
    end = None
    try:
        import subprocess
        out = subprocess.run([os.environ.get('G80_OBJDUMP', 'arm-none-eabi-objdump').replace('objdump', 'size'),
                              os.path.join(FW, 'epomaker_galaxy80_via.elf')], capture_output=True, text=True, timeout=60).stdout
        t_, d_ = out.splitlines()[-1].split()[:2]
        end = int(t_) + int(d_)                     # = g80_check.sh "flash"
    except Exception:
        end = None
    img = open(BIN, 'rb').read()
    last_code = max(i for i in range(0xFFC0) if img[i]) + 1      # the .bin pads with 0x00
    heap = sy['__heap_end__'] - sy['__heap_base__']
    free = (0xF6C0 - end) if end else None
    ok = (all(v['kept'] and v['ops'] == 0 and v['ok'] and v['aux_same'] for v in res.values()) and last_code <= 0xF6C0
          and not any(img[0xF6C0:0xFFC0]) and heap >= 512)
    check('PC-5', 'upgrade over the s8 user image: data kept, aux sectors (blank or holding s8 code bytes) taken as an empty state, '
          '0 flash ops in the boot + 3 s, aux untouched; image: code ends <= 0xF6C0, 0xF6C0..0xFFBF blank, heap >= 512 B', ok,
          'variants %s; image code end %#x, flash used %s B, free below 0xF6C0 %s B (target >= 400: %s), heap %d B' % (
              res, last_code, end, free, 'met' if free is not None and free >= 400 else 'NOT MET', heap),
          dict(variants=res, free=free, heap=heap))
    check('PC-5-size', 'free flash below 0xF6C0 >= 1024 B', free is not None and free >= 1024,
          'free %s B, heap %d B' % (free, heap))

    # PC-5-flash: what the documented flash (sonixflasher -f <via .bin>) really does. The .bin is a full
    # 64 KiB image padded with 0x00 over 0xF6C0..0xFFBF, so the flash overwrites the aux sectors AND the
    # wear-leveling band with zeros: the stored user data are lost (docs/install.md, CHANGELOG). Checked: the
    # flashed image boots to the defaults (= first boot on a blank band), the zero state log is not taken as
    # an interrupted consolidation, the second boot does 0 ops, and a VIA change made after it survives a
    # reboot and a power cut during the first consolidation of the new band (state log starts from zeros).
    m = booted(P0, 30)
    user_cache = m.wl_cache()
    m = None
    gc.collect()
    f = bytearray(P0)
    f[:len(img)] = img                                # the flasher writes the whole file
    r1, r2 = boot_twice(TMP, NM, bytes(f), 300)
    st_after = r1['flash'][ST0:WL0]
    st_active = struct.unpack_from('<I', st_after, 0)[0] == ST_BEGIN
    m = booted(r1['flash'], 50)
    m.call('dynamic_keymap_set_keycode', 0, 2, 3, 0x0004, max_insn=50000000)
    run_until(m, m.t_ms() + 20)
    kc_cache = m.wl_cache()
    after_edit = bytes(m.flash)
    m = None
    gc.collect()
    r3, _ = boot_twice(TMP, NM, after_edit, 30)
    # first consolidation on the freshly flashed band (aux still holds the bin's zeros where untouched)
    sw = sweep('PC-5-flash-cut', 'Apply of 88 keys on a band freshly written by the flasher',
               *B.trig_apply(88, 100, 3, base=after_edit))
    okf = (user_cache != B.D and r1['ok'] and r2['ok'] and r1['cache'] == B.D and r2['ops'] == 0 and r2['cache'] == B.D
           and not st_active and not r1['viol'] and r3['ok'] and r3['cache'] == kc_cache and kc_cache != B.D)
    check('PC-5-flash', 'flashing the documented .bin (0x00 over 0xF6C0..0xFFBF) wipes the stored data: boot = defaults '
          '(as on a blank band), zero state log not taken as a cut consolidation, 2nd boot 0 ops, a VIA change made '
          'afterwards survives a reboot', okf,
          'user data differ from defaults %s; boot1 ok %s ops %d defaults %s; boot2 ops %d defaults %s; state log active '
          '%s; violations %s; edit kept after reboot %s' % (user_cache != B.D, r1['ok'], r1['ops'], r1['cache'] == B.D,
                                                            r2['ops'], r2['cache'] == B.D, st_active, r1['viol'][:2],
                                                            r3['ok'] and r3['cache'] == kc_cache))

json.dump([{k: v for k, v in r.items()} for r in results], open(os.path.join(OUT, 'suite_powercut_results.json'), 'w'), indent=1, default=str)
print('\nPOWERCUT SUMMARY: %d PASS, %d FAIL, wall %.0f s' % (sum(r['result'] == 'PASS' for r in results),
                                                           sum(r['result'] == 'FAIL' for r in results), time.time() - t_start), flush=True)
if OUT_TMP:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
