# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Galaxy80 VIA stress suite (design 16, sec. 2.5 P0-8 and 3.6 T-VIA-STRESS-1..7).

    python -u suite_via_stress.py <fw_dir> [out_dir] [--quick | --full] [--only=1,3,5] [--seed=N]
                                  [--prog-us=2000] [--erase-us=20000]

Default --quick (routine size, run by tools/dev/g80_check.sh); --full = the design size.
Every VIA packet goes the real way: queued in the emulated USB OUT endpoint, taken by raw_hid_task()
in the QMK main loop, with the LED/scan ISR, SysTick and the watchdog running (G80 stress=True). The
host is the VIA app (via_host.G80Via): one command in flight, the next one 1 ms after the ANSWER.
Key presses come from a typing script on the emulated matrix; the host key reports are traced and
compared with the script.

Flash timing model: one FMC program START (one 4 B line) stalls the CPU --prog-us, one 64 B sector
erase --erase-us. Defaults 2000 / 20000 us are conservative placeholders [?] (no SN32F248B figure is
known; to be measured on hardware, H-V3). During a stall the CPU and every ISR stop, the timers and
the watchdog keep counting.

Tests (quick / full size):
  1  T-VIA-STRESS-1  N mixed VIA commands (500 / 5000) in lock-step bursts while a typing script runs
                     (200 / 2000 key events, 10-80 ms apart): no lost / stuck key, no reset, every
                     packet answered, read-backs match, WDT feed gap < 500 ms, every consolidation
                     started inside a write-gate window (no key down, none in the 250 ms before).
  2  T-VIA-STRESS-2  from run 1: no flash op inside raw_hid_receive (I-V2).
  3  T-VIA-STRESS-3  bursts of 64 packets from a host that does not wait for answers, with the real
                     endpoint capacity (2 buffers, 1 packet/ms refill): at most 4 packets per
                     raw_hid_task call (V-7), main loop never stalled > 50 ms, every packet answered
                     (a write sent before the previous one was answered gets id_unhandled = busy and is
                     not applied); the same 64 writes lock-step: all applied. INFO line: g80emu's raw
                     model (all 64 handed to one call at once, not possible on the hardware).
  4  T-VIA-STRESS-4  ISR gap per flash op type (line program, sector erase, full consolidation) with
                     the stall model. V-9: the scan cannot see keys during an erase stall, so the
                     acceptance is: line-program gaps <= the scan bound (1 ms x 5 ms debounce), every
                     erase inside a write-gate window (also when typing goes on while the write
                     waits), WDT feed gap < 500 ms. The erase gap itself is reported (FLAG).
  5  T-VIA-STRESS-5  after run 1 + idle: reboot from the emulated flash; logical EEPROM equals the
                     pre-reboot image, keymap / macros equal the host-side model, no re-format at boot.
  6  T-VIA-STRESS-6  slider drag (350 steps / 35 s, 600 / 60 s), set + save each step: first settings
                     write <= 30 s after the first change, writes >= 3 s apart, WDT feed gap < 500 ms.
  7  T-VIA-STRESS-7  power cut at a random flash op of the trigger write (10 / 100 trials): the board
                     always boots; a cut inside the consolidation leaves the old, the new or the default
                     keymap. Cuts in the log appends around it are reported as a KNOWN LIMIT: QMK
                     writes VIA data byte by byte (one log entry per byte), so an interrupted write
                     can be partly applied, down to one keycode with one old and one new byte.
"""
import os, sys, json, time, random, struct, tempfile, gc
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *
from via_host import G80Via, parse_args, run_until, via_sync, boot_to_loop
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROWS_N, COLS_N, LAYERS = 6, 17, 3
KM_BYTES = ROWS_N * COLS_N * 2 * LAYERS        # 612
MACRO_BYTES = 184
# plain letter keys away from the SOCD/WASD keys: E R T Y U I O P (row 2), F G H J K L (row 3)
TYPE_KEYS = [(2, c) for c in range(3, 11)] + [(3, c) for c in range(4, 10)]
FN = (5, 11)
WDT_WORST_S = 1.0      # galaxy80.c: watchdog ~8.2 s nominal, >= ~1 s worst case (ILRC tolerance)

FW, OUT, opt, OUT_TMP = parse_args(sys.argv, 'via_stress')
FULL = bool(opt.get('full'))
FEED_GAP_MAX_S = 0.5   # WP-C5 gate: WDT feed gap < 500 ms in every scenario
SEED = int(opt.get('seed', 2026))
PROG_US = float(opt.get('prog-us', 2000))
ERASE_US = float(opt.get('erase-us', 20000))
ONLY = set(opt['only'].split(',')) if 'only' in opt else None
NM = os.path.join(FW, 'nm_via.txt')
results = []


def want(n):
    return ONLY is None or str(n) in ONLY


def check(tid, name, ok, detail, data=None):
    results.append(dict(id=tid, name=name, result='PASS' if ok else 'FAIL', detail=detail, data=data or {}))
    print('%s  %-15s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail), flush=True)


def mkm(path, stall=True, **kw):
    gc.collect()
    m = G80Via(path, NM, stress=True, flash_prog_us=PROG_US if stall else None,
               flash_erase_us=ERASE_US if stall else None, **kw)
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    return m


def save_flash(m, name):
    p = os.path.join(OUT, name)
    open(p, 'wb').write(bytes(m.flash))
    return p


def km_read_call(m):
    """keymap + macro buffers through the firmware getters (thread context, used after boot)"""
    buf = m.sym['__heap_base__']
    km = b''
    for off in range(0, KM_BYTES, 64):
        n = min(64, KM_BYTES - off)
        m.call('dynamic_keymap_get_buffer', off, n, buf)
        km += m.ram_read(buf, n)
    mc = b''
    for off in range(0, MACRO_BYTES, 64):
        n = min(64, MACRO_BYTES - off)
        m.call('dynamic_keymap_macro_get_buffer', off, n, buf)
        mc += m.ram_read(buf, n)
    return km, mc


# ------------------------------------------------------------------ key report trace
def report_keys(rep):
    """(t, kind, bytes) -> set of HID usages (+ modifiers as 0xE0..0xE7)"""
    t, kind, b = rep
    keys = set()
    if kind == 'kb':
        mods = b[0]
        keys |= {k for k in b[2:8] if k}
    else:
        mods = b[1]
        for i, byte in enumerate(b[2:32]):
            for bit in range(8):
                if byte >> bit & 1:
                    keys.add(i * 8 + bit)
    keys |= {0xE0 + i for i in range(8) if mods >> i & 1}
    return keys


def key_timeline(m):
    """[(t, set)] host-visible key state after every report (6KRO and NKRO combined)"""
    st = {'kb': set(), 'nkro': set()}
    out = []
    for rep in m.reports:
        st[rep[1]] = report_keys(rep)
        out.append((rep[0], st['kb'] | st['nkro']))
    return out


def analyse_taps(m, taps, usage, t_end):
    """taps = [(rc, t_press, t_release)]: lost (never seen while it could be), press/release latency"""
    tl = key_timeline(m)
    times = [t for t, _ in tl]
    import bisect
    lost, lat_p, lat_r, late_rel = [], [], [], []
    by_key = {}
    for i, (rc, tp, tr) in enumerate(taps):
        by_key.setdefault(rc, []).append(i)
    for rc, idx in by_key.items():
        u = usage[rc]
        for j, i in enumerate(idx):
            _, tp, tr = taps[i]
            t_next = taps[idx[j + 1]][1] if j + 1 < len(idx) else t_end
            k = bisect.bisect_left(times, tp)
            seen = None
            while k < len(tl) and tl[k][0] < t_next:
                if u in tl[k][1]:
                    seen = tl[k][0]
                    break
                k += 1
            if seen is None:
                lost.append((rc, round(tp, 1), round(tr - tp, 1)))
                continue
            lat_p.append(seen - tp)
            rel = None
            while k < len(tl):
                if u not in tl[k][1]:
                    rel = tl[k][0]
                    break
                k += 1
            if rel is None or rel > t_next:
                late_rel.append((rc, round(tp, 1)))
            else:
                lat_r.append(rel - tr)
    stuck = sorted(tl[-1][1]) if tl else []
    return dict(lost=lost, stuck_end=stuck, press_lat_max=max(lat_p) if lat_p else None,
                press_lat_avg=sum(lat_p) / len(lat_p) if lat_p else None, rel_lat_max=max(lat_r) if lat_r else None,
                release_missing=late_rel)


def calibrate(m, t0):
    """tap every typing key once (no VIA traffic): rc -> HID usage seen by the host"""
    usage = {}
    t = t0
    for rc in TYPE_KEYS:
        n0 = len(m.reports)
        run_until(m, t)
        m.pressed = {rc}
        run_until(m, t + 30)
        m.pressed = set()
        run_until(m, t + 60)
        seen = set()
        for rep in m.reports[n0:]:
            seen |= report_keys(rep)
        usage[rc] = min(seen) if len(seen) == 1 else (sorted(seen)[0] if seen else None)
        t += 60
    return usage, t


def typing_script(rnd, n_events, t0):
    """n_events press/release events, 10-80 ms apart, at most 2 keys down (rollover)"""
    ev, held, t = [], [], t0
    taps = []
    down_t = {}
    while len(ev) < n_events:
        t += rnd.uniform(10, 80)
        if held and (len(held) == 2 or rnd.random() < 0.55):
            rc = held.pop(0)
            ev.append((t, rc, False))
            taps.append((rc, down_t.pop(rc), t))
        else:
            rc = rnd.choice([k for k in TYPE_KEYS if k not in held])
            held.append(rc)
            down_t[rc] = t
            ev.append((t, rc, True))
    for rc in held:
        t += 20
        ev.append((t, rc, False))
        taps.append((rc, down_t.pop(rc), t))
    return ev, taps, t


# ------------------------------------------------------------------ VIA command generator
SAFE_CUSTOM = [2, 3, 4, 12, 13, 15, 17, 18, 19, 20, 21, 22, 23]   # not 5-11/14 (SOCD, WASD, keys off, game) nor 16 (editor)


class ViaModel:
    def __init__(self, km, mc):
        self.km = bytearray(km)
        self.mc = bytearray(mc)

    def gen(self, rnd):
        """one VIA 'user action' -> list of packets (+ expected read-back checks)"""
        k = rnd.random()
        if k < 0.22:
            vid = rnd.choice(SAFE_CUSTOM)
            vals = [rnd.randrange(1, 11) if vid == 12 else rnd.randrange(256), rnd.randrange(256)]
            return [[0x07, 0, vid] + vals, [0x09, 0]], 'custom_set'
        if k < 0.32:
            return [[0x08, 0, rnd.randrange(1, 24), rnd.randrange(93), 1]], 'custom_get'
        if k < 0.42:
            n = rnd.randrange(1, 13)
            hs = []
            for _ in range(n):
                hs += [rnd.randrange(256), rnd.randrange(256)]
            return [[0x07, 0, 1, rnd.randrange(93), n] + hs, [0x09, 0]], 'paint'
        if k < 0.50:
            vid = rnd.choice([1, 2, 3, 4])
            vals = [rnd.randrange(256), rnd.randrange(256)] if vid == 4 else [rnd.randrange(30) if vid == 2 else rnd.randrange(256)]
            return [[0x07, 3, vid] + vals, [0x09, 3]], 'light_set'
        if k < 0.62:
            layer, row, col = rnd.randrange(1, 3), rnd.randrange(ROWS_N), rnd.randrange(COLS_N)
            kc = rnd.randrange(0x04, 0xE8)
            off = ((layer * ROWS_N + row) * COLS_N + col) * 2
            self.km[off:off + 2] = struct.pack('>H', kc)
            return [[0x05, layer, row, col, kc >> 8, kc & 0xFF]], 'km_set'
        if k < 0.70:
            size = rnd.randrange(2, 29) & ~1
            off = rnd.randrange(204, KM_BYTES - size + 1) & ~1
            data = []
            for _ in range(size // 2):
                kc = rnd.randrange(0x04, 0xE8)
                data += [kc >> 8, kc & 0xFF]
            self.km[off:off + size] = bytes(data)
            return [[0x13, off >> 8, off & 0xFF, size] + data], 'km_buf_set'
        if k < 0.76:
            size = rnd.randrange(1, 29)
            off = rnd.randrange(0, MACRO_BYTES - size + 1)
            data = [rnd.randrange(0x20, 0x7F) for _ in range(size)]
            if off + size == MACRO_BYTES:
                data[-1] = 0
            self.mc[off:off + size] = bytes(data)
            return [[0x0F, off >> 8, off & 0xFF, size] + data], 'macro_set'
        if k < 0.88:
            size = 28
            off = rnd.randrange(0, KM_BYTES - size + 1) & ~1
            return [[0x12, off >> 8, off & 0xFF, size]], 'km_get'
        if k < 0.94:
            size = 28
            off = rnd.randrange(0, MACRO_BYTES - size + 1)
            return [[0x0E, off >> 8, off & 0xFF, size]], 'macro_get'
        if k < 0.97:
            layer, row, col = rnd.randrange(3), rnd.randrange(ROWS_N), rnd.randrange(COLS_N)
            return [[0x04, layer, row, col]], 'km_getkc'
        return [[0x02, rnd.choice([1, 3])]], 'kb_value'

    def verify(self, pkt, ans):
        """read-back answers vs the model: None = not a read, True/False otherwise"""
        c = pkt[0]
        if ans is None:
            return False
        if c == 0x12:
            off, size = (pkt[1] << 8) | pkt[2], pkt[3]
            return ans[4:4 + size] == bytes(self.km[off:off + size])
        if c == 0x0E:
            off, size = (pkt[1] << 8) | pkt[2], pkt[3]
            return ans[4:4 + size] == bytes(self.mc[off:off + size])
        if c == 0x04:
            off = ((pkt[1] * ROWS_N + pkt[2]) * COLS_N + pkt[3]) * 2
            return ans[4:6] == bytes(self.km[off:off + 2])
        return None


def read_model_via(m):
    km = b''
    for off in range(0, KM_BYTES, 28):
        n = min(28, KM_BYTES - off)
        a, _ = via_sync(m, [[0x12, off >> 8, off & 0xFF, n]])
        km += a[0][4:4 + n]
    mc = b''
    for off in range(0, MACRO_BYTES, 28):
        n = min(28, MACRO_BYTES - off)
        a, _ = via_sync(m, [[0x0E, off >> 8, off & 0xFF, n]])
        mc += a[0][4:4 + n]
    return km, mc


def flash_summary(log, t0=-1, t1=1e18):
    from collections import Counter
    c = Counter()
    for t, kind, addr, ctx, us in log:
        if t0 <= t <= t1:
            c[(kind, ctx)] += 1
    return {'%s/%s' % k: v for k, v in sorted(c.items())}


def consolidations(log, t0=-1):
    """runs of >= 8 erases (+ the programs right after): [(t_start, t_end_incl_stall, n_erase, n_prog)]"""
    out = []
    i = 0
    L = [x for x in log if x[0] >= t0]
    while i < len(L):
        if L[i][1] == 'ERASE':
            j = i
            while j < len(L) and L[j][1] == 'ERASE':
                j += 1
            ne = j - i
            k = j
            while k < len(L) and L[k][1] == 'PROGRAM' and L[k][0] - L[k - 1][0] < 50:
                k += 1
            if ne >= 8:
                last = L[k - 1]
                out.append((L[i][0], last[0] + (last[4] or 0) / 1e3, ne, k - j))
            i = k
        else:
            i += 1
    return out


def gaps_in(m, t0, t1):
    return [g for g in m.isr_gaps_big if t0 - 1 <= g[0] <= t1]


def outside_gate(t, ev, gate_ms=250.0):
    """ev = [(t, rc, down)]: True if at time t a key was down or a key event happened < gate_ms before"""
    held = set()
    last = -1e18
    for te, rc, down in ev:
        if te > t:
            break
        last = te
        if down:
            held.add(rc)
        else:
            held.discard(rc)
    return bool(held) or t - last < gate_ms


# ================================================================== base image
t_start = time.time()
base = os.path.join(OUT, 'base_formatted_via.bin')
print('flash model: program %.0f us/line, erase %.0f us/sector [?: placeholders]; mode %s, seed %d' % (
    PROG_US, ERASE_US, 'FULL' if FULL else 'QUICK', SEED), flush=True)
m0 = mkm(os.path.join(FW, 'epomaker_galaxy80_via.bin'), stall=False)
run_until(m0, 4000, step=50.0)
fmt_ops = len(m0.flash_ops)
save_flash(m0, 'base_formatted_via.bin')
KM_DEF, MC_DEF = km_read_call(m0)
print('base: first boot formatted the EEPROM with %d flash ops, main loop at %s ms; saved %s' % (fmt_ops, m0.first_main_loop_t, base), flush=True)
m0 = None

# ================================================================== 1 / 2 / 5
if want(1) or want(2) or want(5):
    rnd = random.Random(SEED)
    n_cmd = 5000 if FULL else 500
    n_ev = 2000 if FULL else 200
    m = mkm(base)
    boot_to_loop(m, 200)
    boot_ops = len(m.flash_log)
    usage, t = calibrate(m, m.t_ms() + 20)
    km0, mc0 = read_model_via(m)
    model = ViaModel(km0, mc0)
    model_ok0 = (km0 == KM_DEF and mc0 == MC_DEF)
    t0 = m.t_ms() + 50
    ev, taps, t_end_typing = typing_script(rnd, n_ev, t0)
    # the action list fixes how many actions there are; the packets themselves are generated again
    # (same seed, same order) by vm while sending, so every read-back sees the model of its moment
    actions = []
    while sum(len(a[0]) for a in actions) < n_cmd:
        actions.append(model.gen(rnd))
    # bursts: 5..60 actions lock-step, pauses spread so the VIA traffic covers the typing window
    span = max(1000.0, t_end_typing - t0)
    bursts, i = [], 0
    while i < len(actions):
        n = rnd.randrange(5, 61)
        bursts.append(list(range(i, min(i + n, len(actions)))))
        i += n
    est = sum(len(actions[j][0]) for j in range(len(actions))) * 1.3
    pause = max(0.0, (span - est) / max(1, len(bursts)))
    m.stress_mark()
    f0 = len(m.flash_log)
    w0 = len(m.wdt_feeds)
    m.max_feed_gap = 0.0
    rx0, tx0 = len(m.hid_rx), len(m.hid_tx)
    readback = dict(ok=0, bad=0, bad_list=[])
    # replay: verify read-backs against a model advanced in the same order as the packets
    vm = ViaModel(km0, mc0)
    rnd3 = random.Random(SEED)
    typing_script(rnd3, n_ev, t0)
    sent = []          # (pkt, kind) in send order
    next_burst = t0 + rnd.uniform(0, pause)
    ei, bi = 0, 0
    t_wall = time.time()
    while ei < len(ev) or bi < len(bursts) or not m.hid_idle():
        nxt = ev[ei][0] if ei < len(ev) else 1e18
        if bi < len(bursts) and m.hid_idle() and m.t_ms() >= next_burst:
            pk = []
            for j in bursts[bi]:
                pkts, kind = vm.gen(rnd3)
                for p in pkts:
                    pk.append(p)
                    sent.append((p, kind, (bytes(vm.km), bytes(vm.mc)) if p[0] in (0x12, 0x0E, 0x04) else None))
            m.hid_send_lockstep(pk, m.t_ms())
            bi += 1
            next_burst = m.t_ms() + est / max(1, len(bursts)) + rnd.uniform(0.3, 1.7) * pause
        target = min(nxt, m.t_ms() + 5.0)
        r = m.run(target)
        if r not in ('TIME',):
            print('  run stopped: %s at %.1f ms' % (r, m.t_ms()))
            break
        while ei < len(ev) and ev[ei][0] <= m.t_ms():
            _, rc, down = ev[ei]
            if down:
                m.pressed.add(rc)
            else:
                m.pressed.discard(rc)
            ei += 1
        if m.t_ms() > t_end_typing + 600000:
            break
    t_stress_end = m.t_ms()
    model_final = (bytes(vm.km), bytes(vm.mc))
    run_until(m, m.t_ms() + 500)
    keys = analyse_taps(m, taps, usage, m.t_ms())
    # read-back verification: answers in order
    answers = [b for _, b in m.hid_tx[tx0:]]
    n_ans = len(answers)
    for (p, kind, snap), a in zip(sent, answers):
        if snap is None:
            continue
        mm_ = ViaModel(*snap)
        v = mm_.verify(p, a)
        if v:
            readback['ok'] += 1
        else:
            readback['bad'] += 1
            if len(readback['bad_list']) < 5:
                readback['bad_list'].append((kind, bytes(p[:4]).hex(), a[:8].hex() if a else None))
    fl = m.flash_log[f0:]
    cons = consolidations(fl)
    in_rawhid = [x for x in fl if x[3] == 'rawhid']
    wdt_resets = [x for x in m.resets if x[1] == 'WDT']
    big_isr = sorted(m.isr_gaps_big, key=lambda g: -g[1])[:5]
    big_loop = sorted(m.loop_gaps_big, key=lambda g: -g[1])[:5]
    durs = sorted(m.rawhid_durs, key=lambda d: -d[1])[:5]
    d1 = dict(commands=len(sent), answers=n_ans, actions=len(actions), bursts=len(bursts), typing_events=len(ev), taps=len(taps),
              emu_ms=round(t_stress_end - t0, 1), wall_s=round(time.time() - t_wall, 1),
              lost=len(keys['lost']), lost_list=keys['lost'][:10], stuck_end=keys['stuck_end'],
              release_missing=len(keys['release_missing']),
              press_lat_max_ms=keys['press_lat_max'], press_lat_avg_ms=keys['press_lat_avg'], rel_lat_max_ms=keys['rel_lat_max'],
              resets=m.resets, wdt_resets=len(wdt_resets), max_feed_gap_ms=round(m.max_feed_gap * 1e3, 1),
              loop_gap_max_ms=round(m.loop_gap_max, 2), loop_gaps_over_5ms=len(m.loop_gaps_big), loop_top=big_loop,
              isr_gap_max_ms=round(m.isr_gap_max, 3), isr_gaps_over_0_5ms=len(m.isr_gaps_big), isr_top=big_isr,
              isr_normal_ms=m.isr_gap_hist.most_common(1)[0][0] if m.isr_gap_hist else None,
              rawhid_handler_max=durs, flash=flash_summary(fl), flash_ops=len(fl), consolidations=len(cons),
              consolidation_list=cons[:5], readback=readback, faults=m.faults[:3], violations=m.violations[:3],
              boot_flash_ops=boot_ops, calib=sorted((str(k), v) for k, v in usage.items()), model_matches_defaults=model_ok0)
    cons_bad = [c for c in cons if outside_gate(c[0], ev)]
    erase_bad = [x for x in fl if x[1] == 'ERASE' and outside_gate(x[0], ev)]
    busy = sum(1 for a in answers if a and a[0] == 0xFF and True)
    d1.update(cons_outside_gate=len(cons_bad), erase_outside_gate=len(erase_bad), busy_answers=busy)
    ok1 = (not keys['lost'] and not keys['stuck_end'] and not keys['release_missing'] and not m.resets and not m.faults
           and readback['bad'] == 0 and n_ans == len(sent) and m.max_feed_gap < FEED_GAP_MAX_S and not erase_bad)
    if want(1):
        check('T-VIA-STRESS-1', '%d VIA commands (%d actions, %d lock-step bursts) + %d key events: no lost/stuck key, no reset, every packet '
              'answered, read-backs match, WDT feed gap < 500 ms, every erase inside a write-gate window' % (len(sent), len(actions), len(bursts), len(ev)), ok1,
              'lost %d/%d taps %s, stuck at end %s, release missing %d, press latency max %.1f ms avg %.1f ms, release latency max %s ms; '
              'resets %s; max WDT feed gap %.1f ms; main-loop gap max %.1f ms (%d > 5 ms); ISR gap max %.2f ms (normal %s ms, %d > 0.5 ms); '
              'answers %d/%d, read-backs ok %d bad %d; flash ops %d %s, consolidations %d (outside the gate %d, erases outside %d); '
              'emulated %.1f s (wall %.0f s)' % (
                  len(keys['lost']), len(taps), keys['lost'][:3], keys['stuck_end'], len(keys['release_missing']),
                  keys['press_lat_max'] or -1, keys['press_lat_avg'] or -1, None if keys['rel_lat_max'] is None else round(keys['rel_lat_max'], 1),
                  m.resets, m.max_feed_gap * 1e3, m.loop_gap_max, len(m.loop_gaps_big), m.isr_gap_max, d1['isr_normal_ms'],
                  len(m.isr_gaps_big), n_ans, len(sent), readback['ok'], readback['bad'], len(fl), d1['flash'], len(cons),
                  len(cons_bad), len(erase_bad), (t_stress_end - t0) / 1e3, d1['wall_s']), d1)
    if want(2):
        ctxs = {}
        for x in in_rawhid:
            ctxs[x[1]] = ctxs.get(x[1], 0) + 1
        check('T-VIA-STRESS-2', 'I-V2: no flash op inside raw_hid_receive (all flash work in housekeeping)', not in_rawhid,
              'flash ops inside raw_hid_receive %d of %d (%s); in main/housekeeping %d; longest raw_hid_receive calls (start ms, ms, cmd) %s' % (
                  len(in_rawhid), len(fl), ctxs, sum(1 for x in fl if x[3] == 'main'),
                  [(a, round(b, 1), '%#04x' % c) for a, b, c in durs[:3]]),
              dict(in_rawhid=len(in_rawhid), by_kind=ctxs, total=len(fl)))
    if want(5):
        # idle until every deferred save is done, then cut power and boot again from the flash
        run_until(m, m.t_ms() + 4500)
        pre = m.wl_cache()
        km_pre, mc_pre = km_read_call(m)
        p = save_flash(m, 'after_stress_via.bin')
        m = None
        m2 = mkm(p)
        boot_to_loop(m2, 300)
        post = m2.wl_cache()
        km_post, mc_post = km_read_call(m2)
        boot_fmt = [x for x in m2.flash_log if x[3] == 'boot']
        diff = [i for i in range(1024) if pre[i] != post[i]]
        ok5 = (pre == post and km_post == model_final[0] and mc_post == model_final[1] and not boot_fmt and not m2.faults
               and m2.first_main_loop_t is not None)
        check('T-VIA-STRESS-5', 'after the stress + idle: reboot from the emulated flash gives the same logical EEPROM, keymap/macros equal the '
              'host model, no re-format at boot (hash valid)', ok5,
              'logical image equal %s (%d bytes differ, first %s); keymap = model %s (pre-reboot %s), macros = model %s (pre-reboot %s); '
              'boot flash ops %d; main loop at %s ms; faults %s' % (
                  pre == post, len(diff), diff[:8], km_post == model_final[0], km_pre == model_final[0], mc_post == model_final[1],
                  mc_pre == model_final[1], len(boot_fmt), m2.first_main_loop_t, m2.faults[:2]),
              dict(diff=len(diff), boot_ops=len(boot_fmt)))
        m2 = None
    m = None

# ================================================================== 3: burst
if want(3):
    out3 = {}

    def kc_pkt(i):
        return [0x05, 2, i // 17 % 6, i % 17, 0, 0x04 + (i % 100)]

    for label, mkpk, ep in (('raw-model get x64', lambda i: [0x08, 0, 12], False),
                            ('get x64', lambda i: [0x08, 0, 12], True),
                            ('set_keycode x64', kc_pkt, True)):
        m = mkm(base, ep_model=ep)
        boot_to_loop(m, 200)
        m.stress_mark()
        m.max_feed_gap = 0.0
        t_b = m.t_ms() + 5
        for i in range(64):
            m.hid_send(mkpk(i), t_b)
        run_until(m, t_b + 4000, lambda mm: mm.hid_idle() and len(mm.hid_rx) >= 64 and mm.t_ms() > t_b + 50)
        run_until(m, m.t_ms() + 50)
        per = [n for _, n in m.hid_per_task]
        ans = [b for _, b in m.hid_tx]
        applied = None
        if label.startswith('set'):
            km, _ = km_read_call(m)
            # answers come back in completion order (busy ones at once, applied ones from housekeeping):
            # match them to the packets by the echoed bytes (64 different keys, each written once)
            by_key = {}
            for a in ans:
                by_key[(a[1], a[2], a[3])] = a[0]
            consistent = len(by_key) == 64
            for i in range(64):
                p = kc_pkt(i)
                off = ((2 * ROWS_N + p[2]) * COLS_N + p[3]) * 2
                st = by_key.get((p[1], p[2], p[3]))
                if st == 0x05 and km[off:off + 2] != bytes([0, p[5]]):
                    consistent = False      # answered OK but not in the keymap
                if st == 0xFF and km[off:off + 2] == bytes([0, p[5]]):
                    consistent = False      # answered busy but applied anyway
            applied = dict(ok=sum(1 for v in by_key.values() if v == 0x05), busy=sum(1 for v in by_key.values() if v == 0xFF),
                           consistent=consistent)
        out3[label] = dict(max_per_call=max(per) if per else 0, calls=len(per), loop_gap_max=round(m.loop_gap_max, 2),
                           isr_gap_max=round(m.isr_gap_max, 3), flash=flash_summary(m.flash_log), resets=m.resets,
                           answered=len(m.hid_tx), applied=applied, feed_gap_ms=round(m.max_feed_gap * 1e3, 1),
                           in_rawhid=sum(1 for x in m.flash_log if x[3] == 'rawhid'))
        m = None
    # lock-step (VIA app): the same 64 writes, each sent after the previous answer: all applied
    m = mkm(base, ep_model=True)
    boot_to_loop(m, 200)
    m.stress_mark()
    ans, _ = via_sync(m, [kc_pkt(i) for i in range(64)], timeout_ms=20000)
    km, _ = km_read_call(m)
    last = {}
    for i in range(64):
        p = kc_pkt(i)
        last[(p[2], p[3])] = p
    ls_ok = len(ans) == 64 and all(a[0] == 0x05 for a in ans) and all(
        km[((2 * ROWS_N + r) * COLS_N + c) * 2:((2 * ROWS_N + r) * COLS_N + c) * 2 + 2] == bytes([0, p[5]]) for (r, c), p in last.items())
    out3['lockstep set_keycode x64'] = dict(answered=len(ans), all_ok=ls_ok, loop_gap_max=round(m.loop_gap_max, 2),
                                           in_rawhid=sum(1 for x in m.flash_log if x[3] == 'rawhid'))
    m = None
    raw = out3.pop('raw-model get x64')
    print('INFO  T-VIA-STRESS-3  g80emu raw endpoint model (all 64 queued packets handed to one raw_hid_task call, not possible on '
          'the hardware: RAW_OUT_CAPACITY 2): max %d packets per call, main-loop gap max %.1f ms' % (raw['max_per_call'], raw['loop_gap_max']), flush=True)
    sk = out3['set_keycode x64']
    okc = (all(v['max_per_call'] <= 4 and v['loop_gap_max'] < 50 and v['answered'] >= 64 and not v['resets'] and v['feed_gap_ms'] < 500
               for k, v in out3.items() if 'max_per_call' in v)
           and sk['applied']['consistent'] and sk['in_rawhid'] == 0 and out3['lockstep set_keycode x64']['all_ok']
           and out3['lockstep set_keycode x64']['in_rawhid'] == 0)
    check('T-VIA-STRESS-3', '64-packet bursts from a host that does not wait (endpoint: 2 buffers, 1 packet/ms): <= 4 packets per '
          'raw_hid_task call (V-7), main loop never stalled >= 50 ms, every packet answered, a write sent before the previous answer '
          'gets id_unhandled (busy) and is not applied, no flash op in raw_hid_receive; the same 64 writes lock-step: all applied', okc,
          '; '.join('%s: %s' % (k, v) for k, v in out3.items()), out3)

# ================================================================== 4: ISR gap per flash op type
def prefill_log(m, left_max=12):
    wa = m.sym['wear_leveling'] + 1024
    n = 0
    while n < 3000:
        left = 2048 - struct.unpack('<I', m.uc.mem_read(wa, 4))[0]
        if left <= left_max:
            break
        m.call('eeconfig_update_kb', (0x55000000 | n) if left > 40 else (n & 1), max_insn=50000000)
        n += 1
    m.pending_stall = 0
    return n


if want(4):
    bound = 1.0 * 5   # V-9: G80_SCAN_INTERVAL_US (1000 us, matrix.c) x default debounce (5 ms) = 5 ms
    m = mkm(base)
    boot_to_loop(m, 200)
    # (a) single line programs: 20 single keymap writes 60 ms apart
    m.stress_mark()
    m.max_feed_gap = 0.0
    ta = m.t_ms() + 5
    for i in range(20):
        m.hid_send([0x05, 2, 1, i % 17, 0, 0x10 + i], ta + 60 * i)
    run_until(m, ta + 60 * 20 + 100)
    la = [x for x in m.flash_log if x[0] >= ta]
    g_prog = m.isr_gap_max
    progs_per_write = len([x for x in la if x[1] == 'PROGRAM']) / 20.0
    # (b) consolidation: log pre-filled to <= 12 B before the end, then keymap writes 100 ms apart
    n = prefill_log(m)
    m.stress_mark()
    m.max_feed_gap = 0.0
    nb0 = len(m.flash_log)
    for i in range(40):
        m.hid_send([0x05, 2, 2, 3, 0, 0x2C + (i & 1)], m.t_ms() + 1)
        run_until(m, m.t_ms() + 8000, lambda mm: mm.hid_idle() and mm.t_ms() > mm.flash_log[-1][0] + 100)
        if any(x[1] == 'ERASE' for x in m.flash_log[nb0:]):
            break
    run_until(m, m.t_ms() + 300)
    lb = m.flash_log[nb0:]
    cons = consolidations(lb)
    er = [x for x in lb if x[1] == 'ERASE']
    pr = [x for x in lb if x[1] == 'PROGRAM']
    cls = {'erase': 0.0, 'program-only': 0.0}
    for g0, gl in m.isr_gaps_big:
        ops = [x for x in lb if g0 <= x[0] <= g0 + gl]
        if not ops:
            continue
        k = 'erase' if any(x[1] == 'ERASE' for x in ops) else 'program-only'
        cls[k] = max(cls[k], gl)
    c = cons[0] if cons else None
    feed_b = m.max_feed_gap
    loop_b = m.loop_gap_max
    m = None
    # (c) the write that will consolidate arrives while typing: it waits for the write gate
    m = mkm(base)
    boot_to_loop(m, 200)
    prefill_log(m)
    m.stress_mark()
    m.max_feed_gap = 0.0
    t0c = m.t_ms() + 5
    rnd4 = random.Random(SEED + 4)
    evc, tapc, t_endc = typing_script(rnd4, 80, t0c)
    nc0 = len(m.flash_log)
    sent_c = None
    ei = 0
    while ei < len(evc) or not m.hid_idle() or m.t_ms() < t_endc + 2000:
        if sent_c is None and m.t_ms() >= t0c + 300:
            # single keymap writes; the first one already needs more log than is left (consolidation)
            m.hid_send_lockstep([[0x05, 2, 2, 3 + (i % 5), 0, 0x30 + i] for i in range(6)], m.t_ms())
            sent_c = m.t_ms()
        nxt = evc[ei][0] if ei < len(evc) else m.t_ms() + 5.0
        r = m.run(min(nxt, m.t_ms() + 5.0))
        if r != 'TIME':
            break
        while ei < len(evc) and evc[ei][0] <= m.t_ms():
            _, rc, down = evc[ei]
            (m.pressed.add if down else m.pressed.discard)(rc)
            ei += 1
        if m.t_ms() > t_endc + 20000:
            break
    lc = m.flash_log[nc0:]
    er_c = [x for x in lc if x[1] == 'ERASE']
    er_c_bad = [x for x in er_c if outside_gate(x[0], evc)]
    first_er_c = er_c[0][0] if er_c else None
    feed_c = m.max_feed_gap
    m = None
    d4 = dict(prog_us=PROG_US, erase_us=ERASE_US, single_write_programs=progs_per_write, single_write_isr_gap_max=round(g_prog, 3),
              prefill_calls=n, consolidation=c, erases=len(er), programs=len(pr), ctx=flash_summary(lb),
              isr_gap_max_erase=round(cls['erase'], 3), isr_gap_max_program=round(cls['program-only'], 3),
              loop_gap_max=round(loop_b, 2), max_feed_gap_ms=round(feed_b * 1e3, 1),
              typing_case=dict(sent=sent_c, typing_end=t_endc, first_erase=first_er_c, erases=len(er_c), outside_gate=len(er_c_bad),
                               feed_gap_ms=round(feed_c * 1e3, 1)))
    ok4 = (g_prog <= bound and cls['program-only'] <= bound and c is not None and len(er) == 32 and feed_b < FEED_GAP_MAX_S
           and feed_c < FEED_GAP_MAX_S and er_c and not er_c_bad and first_er_c >= t_endc)
    check('T-VIA-STRESS-4', 'ISR gap per flash op type with the stall model (program %.0f us, erase %.0f us [?]): line programs within the '
          'V-9 scan bound %.0f ms; erases only inside write-gate windows, also when the consolidating write arrives while typing '
          '(it waits until typing stops); WDT feed gap < 500 ms' % (PROG_US, ERASE_US, bound), ok4,
          'single keymap write: %.1f line programs, ISR gap max %.2f ms; consolidation (log pre-filled with %d writes): %d erases + %d '
          'programs from %s ms to %s ms (%.0f ms), ISR gap max program-only %.2f ms, main-loop gap max %.1f ms, WDT feed gap max %.1f ms; '
          'FLAG: ISR gap with an erase %.1f ms > %.0f ms (the scan is blind during an erase stall: keys are protected by the write '
          'gate, not by the scan); typing case: write sent at %.0f ms, typing until %.0f ms, first erase at %s ms, erases outside the '
          'gate %d, WDT feed gap max %.1f ms; ctx %s' % (
              progs_per_write, g_prog, n, len(er), len(pr), c[0] if c else None, round(c[1], 1) if c else None, (c[1] - c[0]) if c else -1,
              cls['program-only'], loop_b, feed_b * 1e3, cls['erase'], bound, sent_c or -1, t_endc,
              None if first_er_c is None else round(first_er_c), len(er_c_bad), feed_c * 1e3, d4['ctx']), d4)

# ================================================================== 6: slider drag save timing
if want(6):
    steps, dur = (600, 60000.0) if FULL else (350, 35000.0)
    m = mkm(base)
    boot_to_loop(m, 200)
    m.max_feed_gap = 0.0
    f0 = len(m.flash_log)
    t6 = m.t_ms() + 10
    dt = dur / steps
    k = 0
    t_first = None
    while k < steps:
        tk = t6 + k * dt
        run_until(m, tk)
        if m.hid_idle():
            m.hid_send_lockstep([[0x07, 3, 1, (k * 7) % 256], [0x09, 3]], m.t_ms())
            if t_first is None:
                t_first = m.t_ms()
        k += 1
    t_last = m.t_ms()
    run_until(m, t_last + 5000)
    fl = [x for x in m.flash_log[f0:] if x[3] in ('main', 'rawhid')]
    saves = []
    for x in fl:
        if not saves or x[0] - saves[-1][1] > 200:
            saves.append([x[0], x[0], 0])
        saves[-1][1] = x[0]
        saves[-1][2] += 1
    first_lat = (saves[0][0] - t_first) if saves else None
    gaps = [b[0] - a[0] for a, b in zip(saves, saves[1:])]
    # the cap counts from the first change in the firmware (taken by raw_hid_task ~1 ms after the host
    # send used as reference here) plus one main-loop pass
    ok6 = saves and first_lat <= 30000 + 50 and all(g >= 3000 for g in gaps) and m.max_feed_gap < FEED_GAP_MAX_S
    check('T-VIA-STRESS-6', 'V-1: slider drag of %d steps over %.0f s (set + save each step): first settings write <= 30 s after the first change, '
          'writes >= 3 s apart, WDT feed gap < 500 ms' % (steps, dur / 1e3), bool(ok6),
          'first change at %.0f ms, drag end %.0f ms; save sequences %d at %s ms (ops %s); first write %s ms after the first change; '
          'min gap between writes %s ms; WDT feed gap max %.1f ms' % (t_first, t_last, len(saves), [round(s[0]) for s in saves[:6]],
                                                                      [s[2] for s in saves[:6]], None if first_lat is None else round(first_lat),
                                                                      round(min(gaps)) if gaps else None, m.max_feed_gap * 1e3),
          dict(saves=saves, first_lat=first_lat))
    m = None

# ================================================================== 7: power cut during consolidation
if want(7):
    trials = 100 if FULL else 10
    rnd = random.Random(SEED + 7)
    # pre-filled image: log <= 12 B from the end, one known layer-2 key changed (old != defaults)
    m = mkm(base, stall=False)
    boot_to_loop(m, 200)
    wa = m.sym['wear_leveling'] + 1024
    n = 0
    while n < 4000:
        left = 2048 - struct.unpack('<I', m.uc.mem_read(wa, 4))[0]
        if left <= 12:
            break
        m.call('dynamic_keymap_set_keycode', 2, 0, n % 17, 0x04 + (n % 50), max_insn=50000000)
        n += 1
    m.pending_stall = 0
    run_until(m, m.t_ms() + 50)
    pre_img = save_flash(m, 'prefilled_via.bin')
    km_old, mc_old = km_read_call(m)
    m = None
    new_chunk = [0, 0x1E, 0, 0x1F, 0, 0x20, 0, 0x21, 0, 0x22, 0, 0x23, 0, 0x24, 0, 0x25, 0, 0x26, 0, 0x27, 0, 0x2C, 0, 0x2D, 0, 0x2E, 0, 0x2F]
    OFF = 204 + 34 * 2      # layer 1, row 2
    trig = [0x13, OFF >> 8, OFF & 0xFF, len(new_chunk)] + new_chunk

    def trigger_run(cut_at=None):
        m = mkm(pre_img, stall=False)
        boot_to_loop(m, 100)
        n0 = len(m.flash_ops)
        if cut_at is not None:
            m.power_cut_at = n0 + cut_at
        m.hid_send(trig, m.t_ms() + 2)
        r = run_until(m, m.t_ms() + 600, lambda mm: mm.power_cut)
        return m, n0, r

    m, n0, _ = trigger_run()
    ops = m.flash_ops[n0:]
    km_new, mc_new = km_read_call(m)
    first_er = next((i for i, o in enumerate(ops) if o[0] == 'ERASE'), None)
    n_ops = len(ops)
    m = None
    # fixed points: first / mid / last erase, first programs, the last consolidated line and the 2 hash
    # lines (32 erases + 256 + 2 programs), the first log append after it, the last op; then random
    # points inside the consolidation
    fe = first_er or 0
    pts = [fe, fe + 1, fe + 16, fe + 31, fe + 32, fe + 33, fe + 288, fe + 289, fe + 290, n_ops - 1] if first_er is not None else []
    while len(pts) < trials:
        pts.append(rnd.randrange(fe, min(n_ops, fe + 291)))
    pts = pts[:trials]
    res = []
    for k in pts:
        m, n0b, r = trigger_run(k)
        cut = m.power_cut
        p = save_flash(m, 'cut_tmp.bin')
        m = None
        m = None
        m2 = mkm(p, stall=False)
        rr = boot_to_loop(m2, 300)
        ok_boot = m2.first_main_loop_t is not None and not m2.faults and m2.isp is None and not m2.lockup
        km, mc = km_read_call(m2) if ok_boot else (None, None)
        if km is None:
            which = 'NO-BOOT'
        elif km == km_old:
            which = 'old'
        elif km == km_new:
            which = 'new'
        elif km == KM_DEF:
            which = 'default'
        elif all(km[i] in (km_old[i], km_new[i]) for i in range(KM_BYTES)):
            # the trigger is one 28 B set_buffer = 14 log entries; a cut between them leaves a prefix of it
            which = 'MIX-old/new'
        else:
            which = 'CORRUPT'
        if which == 'MIX-old/new':
            ch = [i for i in range(OFF, OFF + len(new_chunk)) if km[i] == km_new[i] != km_old[i]]
            which += '(%d B new)' % len(ch)
        # inside = at least one erase done and the hash (op fe + 289) not yet written
        in_cons = first_er is not None and fe < k < fe + 290
        if which.startswith('MIX'):
            # keycode-wise: every 2-byte keycode is the old or the new one (never half of each)
            torn = [i for i in range(0, KM_BYTES, 2) if km[i:i + 2] not in (km_old[i:i + 2], km_new[i:i + 2])]
            if torn:
                which = 'TORN'
        res.append(dict(cut=k, in_consolidation=in_cons, applied=cut, boot=ok_boot, keymap=which, macros_ok=mc in (mc_old, MC_DEF),
                        boot_ops=sum(1 for x in m2.flash_log if x[3] == 'boot'), resets=m2.resets, faults=m2.faults[:1]))
        m2 = None
    # design 3.6: inside the consolidation only old / new / default; in the log appends around it QMK's
    # byte-by-byte VIA writes can be cut between two bytes (KNOWN LIMIT, reported, not a failure:
    # TORN = a keycode with one byte old and one new)
    bad = [x for x in res if not x['boot'] or not x['macros_ok'] or x['keymap'] in ('NO-BOOT', 'CORRUPT')
           or (x['in_consolidation'] and x['keymap'] not in ('old', 'new', 'default'))]
    limit = [(x['cut'], x['keymap']) for x in res if not x['in_consolidation'] and x['keymap'] not in ('old', 'new', 'default')]
    from collections import Counter
    cnt = Counter(x['keymap'] for x in res)
    check('T-VIA-STRESS-7', '%d power cuts during the trigger write (consolidation from op %d, %d ops): the board always boots; a cut '
          'inside the consolidation leaves the old, new or default keymap, never a crash' % (
              len(res), first_er if first_er is not None else -1, n_ops), not bad,
          'outcomes %s (inside the consolidation: %s); bad %d %s; KNOWN LIMIT (log appends outside the consolidation, QMK writes VIA '
          'data byte by byte: MIX = partly applied, TORN = one keycode half old / half new): %s; re-format at boot (hash invalid -> '
          'defaults) in %d trials; prefill %d writes; cut points %s' % (
              dict(cnt), dict(Counter(x['keymap'] for x in res if x['in_consolidation'])), len(bad), 
              [(x['cut'], x['keymap'], x['boot']) for x in bad[:8]], limit, sum(1 for x in res if x['boot_ops']), n,
              sorted(x['cut'] for x in res)),
          dict(trials=res, first_erase=first_er, n_ops=n_ops))
    try:
        os.unlink(os.path.join(OUT, 'cut_tmp.bin'))
    except OSError:
        pass

json.dump(results, open(os.path.join(OUT, 'suite_via_stress_results.json'), 'w'), indent=1, default=str)
print('\nVIA-STRESS SUMMARY (%s, prog %.0f us, erase %.0f us): %d PASS, %d FAIL, wall %.0f s' % (
    'full' if FULL else 'quick', PROG_US, ERASE_US, sum(r['result'] == 'PASS' for r in results),
    sum(r['result'] == 'FAIL' for r in results), time.time() - t_start), flush=True)
if OUT_TMP:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
