#!/usr/bin/env python3
"""Galaxy80 QMK debug log analyser (offline, touches no device).

Input: a log written by g80_console.py (lines "<host timestamp>\\t<firmware line>") or a
raw console capture (firmware lines only). Firmware line formats (keymaps/debug/keymap.c):

    M <ms> <r0> .. <r5>           raw matrix changed (5-hex-digit column bitmaps, bit n = col n)
    D <ms> <n>                    n M lines suppressed by the firmware rate limit
    N <ms> <scans> [r,c:n ...]    2 s noise window: toggles while the debounced matrix is empty
    K <r> <c> <1|0>               debounced key event
    B ...                         boot register dump / electrical baseline

The log is split into sessions (one per keyboard boot: firmware ms going backwards, or a
"# connected" line from the reader). For each session the report gives:
  1. debounced press list, in order, with the key label of the (r,c) position;
  2. raw-only positions (seen in M lines but never debounced) and multi-key raw frames;
  3. noise statistics (per position totals, windows hit, scan rate, dropped M lines);
  4. the boot register dump decoded and checked against the values board_init() sets,
     plus the column/row electrical baseline.
Finally the sessions are compared (does the set of noisy / dead positions change per boot?).

Usage:  python g80_matrix_report.py LOGFILE [--out REPORT.txt]
"""
import argparse
import re
import sys
from collections import Counter, OrderedDict, defaultdict

ROWS, COLS = 6, 17

LABELS = {(0, 0): 'Esc', (0, 1): 'F1', (0, 2): 'F2', (0, 3): 'F3', (0, 4): 'F4', (0, 5): 'F5', (0, 6): 'F6', (0, 7): 'F7', (0, 8): 'F8', (0, 9): 'F9', (0, 10): 'F10', (0, 11): 'F11', (0, 12): 'F12', (0, 13): 'F13', (0, 14): 'PrtSc', (0, 15): 'ScrLk', (0, 16): 'Pause',
          (1, 0): '`', (1, 1): '1', (1, 2): '2', (1, 3): '3', (1, 4): '4', (1, 5): '5', (1, 6): '6', (1, 7): '7', (1, 8): '8', (1, 9): '9', (1, 10): '0', (1, 11): '-', (1, 12): '=', (1, 13): 'Backspace', (1, 14): 'Ins', (1, 15): 'Home', (1, 16): 'PgUp',
          (2, 0): 'Tab', (2, 1): 'Q', (2, 2): 'W', (2, 3): 'E', (2, 4): 'R', (2, 5): 'T', (2, 6): 'Y', (2, 7): 'U', (2, 8): 'I', (2, 9): 'O', (2, 10): 'P', (2, 11): '[', (2, 12): ']', (2, 13): '\\', (2, 14): 'Del', (2, 15): 'End', (2, 16): 'PgDn',
          (3, 0): 'Caps', (3, 1): 'A', (3, 2): 'S', (3, 3): 'D', (3, 4): 'F', (3, 5): 'G', (3, 6): 'H', (3, 7): 'J', (3, 8): 'K', (3, 9): 'L', (3, 10): ';', (3, 11): "'", (3, 12): 'ISO #~', (3, 13): 'Enter',
          (4, 0): 'LShift', (4, 1): 'ISO \\|', (4, 2): 'Z', (4, 3): 'X', (4, 4): 'C', (4, 5): 'V', (4, 6): 'B', (4, 7): 'N', (4, 8): 'M', (4, 9): ',', (4, 10): '.', (4, 11): '/', (4, 12): 'JIS Ro', (4, 13): 'RShift', (4, 15): 'Up',
          (5, 0): 'LCtrl', (5, 1): 'LGui', (5, 2): 'LAlt', (5, 6): 'Space', (5, 10): 'RAlt', (5, 11): 'Fn', (5, 12): 'App', (5, 13): 'RCtrl', (5, 14): 'Left', (5, 15): 'Down', (5, 16): 'Right'}

# Hardware map (galaxy80.c / keyboard.json)
COL_PINS = ['P2.12', 'P2.11', 'P2.10', 'P2.9', 'P2.8', 'P2.7', 'P2.6', 'P2.5', 'P2.4', 'P2.3', 'P2.2', 'P2.1', 'P2.0', 'P2.15', 'P1.15', 'P1.13', 'P1.12']
ROW_PINS = ['P3.11', 'P3.10', 'P3.9', 'P3.8', 'P3.7', 'P3.6']
COL_PORT_BIT = [(2, 12), (2, 11), (2, 10), (2, 9), (2, 8), (2, 7), (2, 6), (2, 5), (2, 4), (2, 3), (2, 2), (2, 1), (2, 0), (2, 15), (1, 15), (1, 13), (1, 12)]
COL_MASK = {2: 0x9FFF, 1: 0xB000}
EXPECT_MODE = {0: 0xFF00, 1: 0xFFDF, 2: 0x9FFF, 3: 0x0008}
EXPECT_CFG = {0: 0xAAAA8000, 1: 0xAAAAA2AA, 2: 0x82AAAAAA, 3: 0x00AAA280}
CFG_NAMES = {0: 'pull-up', 1: 'pull-down', 2: 'no-pull', 3: 'repeater'}
AHB_BITS = {0: 'P0', 1: 'P1', 2: 'P2', 3: 'P3', 4: 'USB', 6: 'CT16B0', 7: 'CT16B1', 11: 'ADC', 12: 'SPI0', 16: 'UART0', 17: 'UART1', 18: 'UART2', 21: 'I2C0', 24: 'WDT'}
RSTST_BITS = {0: 'SW', 1: 'WDT', 2: 'LVD', 3: 'EXT', 4: 'POR'}

TS_RE = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\t(.*)$')


def label(r, c):
    return LABELS.get((r, c), '(no key)')


def pos(r, c):
    return 'r%d,c%-2d %-10s' % (r, c, label(r, c))


def bits(v, n):
    return [i for i in range(n) if v >> i & 1]


class Session:
    def __init__(self, idx, first_ts):
        self.idx = idx
        self.first_ts = first_ts
        self.k_events = []          # (host_ts, ms_of_last_M, r, c, state)
        self.m_frames = []          # (ms, [rows])
        self.noise_total = Counter()
        self.noise_windows = Counter()
        self.n_reports = []         # (ms, scans)
        self.dropped = 0
        self.dumps = OrderedDict()  # dump# -> list of B lines (split tokens)
        self._cur_dump = None
        self.last_ms = None
        self.unparsed = 0


def parse(path):
    sessions = []
    cur = None

    def new(ts):
        nonlocal cur
        cur = Session(len(sessions) + 1, ts)
        sessions.append(cur)

    with open(path, encoding='utf-8', errors='replace') as f:
        for raw in f:
            raw = raw.rstrip('\r\n')
            m = TS_RE.match(raw)
            ts, line = (m.group(1), m.group(2)) if m else ('', raw)
            line = line.strip()
            if not line:
                continue
            if line.startswith('#'):
                if 'connected:' in line and 'disconnected' not in line:
                    new(ts)
                continue
            if cur is None:
                new(ts)
            t = line.split()
            kind = t[0]
            try:
                if kind == 'M' and len(t) == 2 + ROWS:
                    ms = int(t[1])
                    if cur.last_ms is not None and ms + 1000 < cur.last_ms:
                        new(ts)
                    cur.last_ms = ms
                    cur.m_frames.append((ms, [int(x, 16) for x in t[2:]]))
                elif kind == 'N' and len(t) >= 3:
                    ms = int(t[1])
                    if cur.last_ms is not None and ms + 1000 < cur.last_ms:
                        new(ts)
                    cur.last_ms = ms
                    cur.n_reports.append((ms, int(t[2])))
                    for e in t[3:]:
                        rc, n = e.split(':')
                        r, c = (int(x) for x in rc.split(','))
                        cur.noise_total[(r, c)] += int(n)
                        cur.noise_windows[(r, c)] += 1
                elif kind == 'D' and len(t) == 3:
                    cur.dropped += int(t[2])
                elif kind == 'K' and len(t) == 4:
                    lm = cur.m_frames[-1][0] if cur.m_frames else None
                    cur.k_events.append((ts, lm, int(t[1]), int(t[2]), int(t[3])))
                elif kind == 'B' and len(t) >= 2:
                    if t[1] == 'H':
                        ms = int(t[2])
                        if cur.last_ms is not None and ms + 1000 < cur.last_ms:
                            new(ts)
                        cur.last_ms = ms
                        cur._cur_dump = int(t[3])
                        cur.dumps[cur._cur_dump] = [t]
                    elif cur._cur_dump is not None:
                        cur.dumps[cur._cur_dump].append(t)
                        if t[1] == 'END':
                            cur._cur_dump = None
                else:
                    cur.unparsed += 1
            except (ValueError, IndexError):
                cur.unparsed += 1
    return sessions


def kv(tokens):
    out = {}
    for x in tokens:
        if '=' in x:
            k, v = x.split('=', 1)
            out[k] = int(v, 16)
    return out


def decode_dump(lines, out):
    problems = []
    gpio = {}
    for t in lines:
        if t[1] == 'H':
            d = kv(t[4:])
            rst = d.get('RSTST', 0)
            out.append('  boot ms=%s  RSTST=%02X [%s]  SWDCTRL=%X (SWD %s)  EXRSTCTRL=%X (RESET pin P3.7 %s)' % (
                t[2], rst, ','.join(n for b, n in RSTST_BITS.items() if rst >> b & 1) or '-',
                d.get('SWDCTRL', 0), 'disabled' if d.get('SWDCTRL', 0) & 1 else 'ENABLED (P3.6=SWCLK, row 5 lost)',
                d.get('EXRSTCTRL', 0), 'is GPIO' if d.get('EXRSTCTRL', 0) & 1 else 'is RESET function (row 4 = P3.7 may misread)'))
            if not d.get('SWDCTRL', 0) & 1:
                problems.append('SWD still enabled: P3.6 (row 5) is SWCLK')
            if not d.get('EXRSTCTRL', 0) & 1:
                problems.append('EXRSTCTRL.RESETDIS=0: P3.7 (row 4) is the reset pin')
        elif t[1] == 'GPIO':
            n = int(t[2])
            d = kv(t[3:])
            gpio[n] = d
            mode, cfg, data = d['MODE'], d['CFG'], d['DATA']
            out.append('  P%d MODE=%04X%s CFG=%08X%s DATA=%04X' % (
                n, mode, '' if mode == EXPECT_MODE[n] else ' (expected %04X!)' % EXPECT_MODE[n],
                cfg, '' if cfg == EXPECT_CFG[n] else ' (expected %08X!)' % EXPECT_CFG[n], data))
            if mode != EXPECT_MODE[n]:
                diff = mode ^ EXPECT_MODE[n]
                for b in bits(diff, 16):
                    problems.append('P%d.%d is %s, expected %s' % (n, b, 'output' if mode >> b & 1 else 'input', 'output' if EXPECT_MODE[n] >> b & 1 else 'input'))
            if cfg != EXPECT_CFG[n]:
                for b in range(16):
                    a, e = cfg >> (2 * b) & 3, EXPECT_CFG[n] >> (2 * b) & 3
                    if a != e:
                        problems.append('P%d.%d CFG %s, expected %s' % (n, b, CFG_NAMES[a], CFG_NAMES[e]))
            outs_high = data & mode
            if outs_high:
                problems.append('P%d outputs driven HIGH at dump time: %s' % (n, ', '.join('P%d.%d' % (n, b) for b in bits(outs_high, 16))))
            if n == 3:
                rows = [ROW_PINS[r] for r in range(ROWS) if data >> (11 - r) & 1]
                out.append('     rows P3.6-11 (no key pressed expected 0): %s' % (', '.join(rows) if rows else 'all low'))
                for r in range(ROWS):
                    b = 11 - r
                    a = cfg >> (2 * b) & 3
                    out.append('     row %d %s: %s, %s' % (r, ROW_PINS[r], 'output' if mode >> b & 1 else 'input', CFG_NAMES[a]))
        elif t[1].startswith('AHBCLKEN='):
            v = int(t[1].split('=')[1], 16)
            out.append('  AHBCLKEN=%08X [%s]' % (v, ','.join(nm for b, nm in AHB_BITS.items() if v >> b & 1)))
        elif t[1].startswith('PFPA='):
            v = int(t[1].split('=')[1], 16)
            out.append('  PFPA(CT16B1)=%08X%s' % (v, '' if v == 0 else '  alternate PWM pin assignment bits: %s' % bits(v, 32)))
        elif t[1] == 'CT16B1':
            if len(t) > 2 and t[2] == 'off':
                out.append('  CT16B1: clock gated (not running, PWM cannot own any pin)')
            else:
                d = kv(t[2:])
                out.append('  CT16B1 TMRCTRL=%08X (CEN=%d) PWMENB=%08X PWMIOENB=%08X' % (d.get('TMRCTRL', 0), d.get('TMRCTRL', 0) & 1, d.get('PWMENB', 0), d.get('PWMIOENB', 0)))
                if d.get('PWMIOENB', 0):
                    problems.append('CT16B1 PWMIOENB=%08X: PWM owns pins %s (overrides GPIO!)' % (d['PWMIOENB'], bits(d['PWMIOENB'], 32)))
    # baseline
    out.append('  baseline (rows read; no key should be held during the dump):')
    rowhigh = defaultdict(list)
    for t in lines:
        if t[1] == 'COL':
            c, tag = int(t[2]), t[3]
            vals = [int(x, 16) for x in t[4:8]]
            d = kv(t[8:])
            port, bit = COL_PORT_BIT[c]
            rb = d.get('P%d' % port, 0)
            col_ok = bool(rb >> bit & 1)
            others = (d.get('P2', 0) & COL_MASK[2]) | ((d.get('P1', 0) & COL_MASK[1]) << 16)
            own = (1 << bit) << (16 if port == 1 else 0)
            other_high = others & ~own
            note = []
            if not col_ok:
                note.append('COLUMN DID NOT READ BACK HIGH')
                problems.append('col %d (%s) does not read back high (settle %s)' % (c, COL_PINS[c], tag))
            if other_high:
                note.append('other columns high')
                problems.append('col %d test: other column lines read high (settle %s)' % (c, tag))
            if any(vals):
                rr = sorted(set(r for v in vals for r in bits(v, ROWS)))
                note.append('rows %s' % rr)
                rowhigh[(tag, tuple(rr))].append(c)
            if note or tag == 'S' and c == 0:
                out.append('    COL %2d %-5s %s %s  %s' % (c, COL_PINS[c], tag, ' '.join('%02X' % v for v in vals), '; '.join(note) or 'ok'))
        elif t[1] == 'LOW':
            vals = [int(x, 16) for x in t[3:7]]
            d = kv(t[7:])
            ok = not any(vals)
            out.append('    ALL COLUMNS LOW %s: %s P3=%04X  %s' % (t[2], ' '.join('%02X' % v for v in vals), d.get('P3', 0), 'ok' if ok else 'ROWS HIGH WITH NOTHING DRIVEN'))
            if not ok:
                rr = sorted(set(r for v in vals for r in bits(v, ROWS)))
                problems.append('all columns low (settle %s): rows %s still high -> stuck/floating row' % (t[2], rr))
    for (tag, rr), cols in sorted(rowhigh.items()):
        problems.append('settle %s: rows %s read high with no key while driving column(s) %s' % (tag, list(rr), ','.join(map(str, cols)) if len(cols) < COLS else 'ALL'))
    out.append('    (columns not listed read 00 00 00 00 with correct readback)')
    return problems


def report(sessions):
    out = []
    all_noise = []
    for s in sessions:
        out.append('=' * 78)
        out.append('SESSION %d  (first host timestamp %s)' % (s.idx, s.first_ts or '-'))
        out.append('=' * 78)
        # 1. presses
        out.append('[1] debounced presses in order (K ... 1):')
        presses = [(ts, lm, r, c) for ts, lm, r, c, st in s.k_events if st == 1]
        if not presses:
            out.append('    none')
        for i, (ts, lm, r, c) in enumerate(presses, 1):
            out.append('    %3d  %s %s%s' % (i, ts[11:] if ts else '', pos(r, c), '' if lm is None else '  (fw ms~%d)' % lm))
        kd = Counter((r, c) for _, _, r, c in presses)
        if kd:
            out.append('    distinct positions: %d   seq: %s' % (len(kd), ' '.join(label(r, c) for _, _, r, c in presses)))
        unreleased = defaultdict(int)
        for _, _, r, c, st in s.k_events:
            unreleased[(r, c)] += 1 if st else -1
        stuck = [k for k, v in unreleased.items() if v > 0]
        if stuck:
            out.append('    still down at end of log: %s' % ', '.join(pos(*k).strip() for k in sorted(stuck)))
        # 2. raw
        raw_pos = Counter()
        multi = 0
        prev = [0] * ROWS
        for ms, rows in s.m_frames:
            on = [(r, c) for r in range(ROWS) for c in bits(rows[r], COLS)]
            if len(on) > 1:
                multi += 1
            for r in range(ROWS):
                for c in bits(rows[r] & ~prev[r], COLS):
                    raw_pos[(r, c)] += 1
            prev = rows
        raw_only = sorted(set(raw_pos) - set(kd))
        out.append('[2] raw matrix: %d M frames, %d with >1 position closed, %d M lines dropped by fw rate limit' % (len(s.m_frames), multi, s.dropped))
        if raw_only:
            out.append('    raw-only positions (closed in raw scan, never debounced):')
            for k in raw_only:
                out.append('      %s raw rising edges=%d' % (pos(*k), raw_pos[k]))
        # 3. noise
        out.append('[3] noise (toggles while debounced matrix empty), %d windows of 2 s:' % len(s.n_reports))
        if s.n_reports:
            sc = [n for _, n in s.n_reports[1:]] or [s.n_reports[0][1]]
            out.append('    scans/window min %d max %d -> ~%.0f scans/s' % (min(sc), max(sc), sum(sc) / len(sc) / 2.0))
        if s.noise_total:
            for k, n in s.noise_total.most_common(30):
                out.append('    %s toggles=%-6d windows=%d/%d   row pin %s col pin %s' % (pos(*k), n, s.noise_windows[k], len(s.n_reports), ROW_PINS[k[0]], COL_PINS[k[1]]))
            byrow = Counter()
            bycol = Counter()
            for (r, c), n in s.noise_total.items():
                byrow[r] += n
                bycol[c] += n
            out.append('    by row: %s' % ', '.join('r%d(%s)=%d' % (r, ROW_PINS[r], n) for r, n in sorted(byrow.items())))
            out.append('    by col: %s' % ', '.join('c%d(%s)=%d' % (c, COL_PINS[c], n) for c, n in sorted(bycol.items())))
        else:
            out.append('    no noise')
        all_noise.append(set(s.noise_total))
        # 4. dumps
        out.append('[4] boot register dump(s): %d' % len(s.dumps))
        for n, lines in s.dumps.items():
            out.append('  --- dump %d ---' % n)
            probs = decode_dump(lines, out)
            if probs:
                out.append('  PROBLEMS:')
                for p in probs:
                    out.append('    ! ' + p)
            else:
                out.append('  all registers as board_init() sets them, baseline clean')
        if s.unparsed:
            out.append('  (%d unparsed lines)' % s.unparsed)
        out.append('')
    if len(sessions) > 1:
        out.append('=' * 78)
        out.append('CROSS-SESSION COMPARISON (noisy positions per boot)')
        for s, ns in zip(sessions, all_noise):
            out.append('  session %d: %s' % (s.idx, ' '.join('r%dc%d' % k for k in sorted(ns)) or '-'))
        common = set.intersection(*all_noise) if all_noise else set()
        out.append('  in every session: %s' % (' '.join('r%dc%d' % k for k in sorted(common)) or '-'))
        out.append('  -> %s' % ('noise set CHANGES between boots' if len(set(map(frozenset, all_noise))) > 1 else 'noise set identical across boots'))
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('log')
    ap.add_argument('--out')
    a = ap.parse_args()
    text = report(parse(a.log))
    sys.stdout.write(text)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(text)


if __name__ == '__main__':
    main()
