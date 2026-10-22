"""Galaxy80 QMK stage 2: full-system emulation test suite (no hardware).
usage: python run_tests.py <fw_dir> [out_dir] [--only=PREFIX,PREFIX]
fw_dir must contain epomaker_galaxy80_<k>.bin + nm_<k>.txt for k in default, via, stage1
(and optionally probe/ and full/ sub-directories with the debug variants).

Part 1: the stage-1 suite (A1..F1), adapted where stage 2 legitimately differs:
        A2 now allows LED select lines HIGH only from the CT16B1 ISR (never during a row read),
        C1/C3 classify reads by state instead of by code address (the reads now happen in the ISR).
Part 2: E-L1..E-L17, the stage-2 system tests.
Part 3: stage-3 features (test_features.py). Part 4: stage-4 hardware-report fixes (test_via_eeprom.py).
"""
import sys, json, struct, time, os, gc
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *

args = [a for a in sys.argv[1:] if not a.startswith('--')]
FW = args[0] if args else '../s2-impl/b2'
OUT = args[1] if len(args) > 1 else '.'
ONLY = None
BLOCKS = None  # --block=A,E-L1: exact block names (a prefix in --only=E-L1 also matches E-L10..E-L16)
for a in sys.argv[1:]:
    if a.startswith('--only='):
        ONLY = a[7:].split(',')
    if a.startswith('--block='):
        BLOCKS = a[8:].split(',')
IMG = {k: ('%s/epomaker_galaxy80_%s.bin' % (FW, k), '%s/nm_%s.txt' % (FW, k)) for k in ('default', 'via', 'stage1')}
results = []
trace = []
KBD = (os.environ.get('G80_KBD') or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'keyboards', 'epomaker', 'galaxy80')))
EFFECT_IDS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'via_effect_ids.json')))
FX_SOLID, FX_UPDOWN = EFFECT_IDS['SOLID_COLOR'], EFFECT_IDS['CYCLE_UP_DOWN']


def want(prefix):
    if BLOCKS is not None:
        return prefix in BLOCKS
    return ONLY is None or any(prefix.startswith(o) for o in ONLY)


def T(*a):
    s = ' '.join(str(x) for x in a)
    trace.append(s)
    print(s, flush=True)


def check(tid, name, ok, detail):
    results.append(dict(id=tid, name=name, result='PASS' if ok else 'FAIL', detail=detail))
    T('%s  %-9s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail))


def mk(kind, **kw):
    gc.collect()
    return G80(*IMG[kind], **kw)


def ram32(m, a):
    return struct.unpack('<I', m.uc.mem_read(a, 4))[0]


def ram8(m, sym, off=0):
    return m.uc.mem_read(m.sym[sym] + off, 1)[0]


def dump_log(m, title, n=40):
    T('--- trace: %s ---' % title)
    for t, b, s in m.log[:n]:
        T('   [%9.3f ms boot#%d] %s' % (t, b, s))


def report(m):
    """(mods, set of HID codes) from both the 6KRO and the NKRO report structures"""
    keys = set()
    mods = 0
    kp = m.sym.get('keyboard_report')
    if kp is not None:
        p = ram32(m, kp)
        b = m.ram_read(p, 8)
        mods |= b[0]
        keys |= {k for k in b[2:8] if k}
    np_ = m.sym.get('nkro_report')
    if np_ is not None:
        p = ram32(m, np_)
        b = m.ram_read(p, 32)
        mods |= b[1]
        for i, byte in enumerate(b[2:32]):
            for bit in range(8):
                if byte >> bit & 1:
                    keys.add(i * 8 + bit)
    return mods, keys


def set_white(mm):
    mm.call('rgb_matrix_enable_noeeprom')
    mm.call('rgb_matrix_mode_eeprom_helper', FX_SOLID, 0)
    mm.call('rgb_matrix_sethsv_eeprom_helper', 0, 0, 255, 0)


def set_off(mm):
    mm.call('rgb_matrix_disable_noeeprom')


def set_anim(mm):
    mm.call('rgb_matrix_enable_noeeprom')
    mm.call('rgb_matrix_mode_eeprom_helper', FX_UPDOWN, 0)
    mm.call('rgb_matrix_sethsv_eeprom_helper', 0, 255, 255, 0)


def recent_select_rise(m, t_from):
    return any(ev[0] == 'CEN' and ev[1] >= t_from and ev[3][1] for ev in m.ct1_events)


# =====================================================================
# Part 1 - stage-1 suite on the stage-2 images (E-L17)
# =====================================================================
POLL_EXPLAIN = {
    (SYS0 + 0x08): 'SYS0_CSST.IHRCRDY (wait IHRC ready). Model: = ANBCTRL.IHRCEN. HW: IHRC is already on and ready at reset -> cannot hang.',
    (SYS0 + 0x0C): 'SYS0_CLKCFG.SYSCLKST (wait switch to IHRC). Model: mirrors SYSCLKSEL. HW: IHRC is already the reset clock -> loop exits at first read.',
    (CT16B0 + 0x00): 'CT16B0_TMRCTRL.CRST (wait counter reset done). Model: self-clears.',
    (CT16B1 + 0x00): 'CT16B1_TMRCTRL.CRST (LED engine, wait counter reset): bounded to 32 iterations by design -> cannot hang.',
    0x4004A000: 'GPIO3_DATA (rows): row-release waits, bounded to 20 iterations by design -> cannot hang.',
    (FMC + 0x04): 'FLASH_STATUS.BUSY (VIA only, efl driver). Model: never busy.',
}

boot = {}
if want('A'):
    for kind in ('default', 'via', 'stage1'):
        t0 = time.time()
        m = None
        m = mk(kind)
        r = m.run(400)
        ml_t = m.first_main_loop_t
        ok = (r == 'TIME' and ml_t is not None and not m.faults and not m.lockup and not m.resets
              and m.isp is None and m.main_loop_hits > 100)
        check('A1-' + kind, 'reset vector -> QMK main loop, no fault/hang (%s)' % kind, ok,
              'result=%s, main loop first reached at %.1f ms, %d main-loop iterations in 400 ms, faults=%s, resets=%s, '
              'exceptions taken=%s, WFI idle=%d, wall %.1fs' % (r, ml_t or -1, m.main_loop_hits, m.faults, m.resets,
                                                              dict(m.exc_count), m.wfi_count, time.time() - t0))
        if kind == 'stage1':
            sel_ever = [v for v in m.violations if 'select' in v] + (['%d select rises' % m.sel_edges['rise']] if m.sel_edges['rise'] else [])
            check('A2-stage1', 'stage1 image: no hardware-rule violation and LED select lines never HIGH (stage-1 rule)',
                  not m.violations and not m.sel_edges['rise'] and not m.ct1_clk(),
                  'violations=%s, select rises=%d, CT16B1 clock=%s' % (m.violations[:5], m.sel_edges['rise'], m.ct1_clk()))
        else:
            check('A2-' + kind, 'no hardware-rule violations during boot+400 ms (%s): selects HIGH only from the LED ISR, '
                  'never two at once, PWM only on column pins, CT16B1 never touched with its clock gated' % kind,
                  not m.violations and m.read_checks['bad'] == 0,
                  'violations=%s, ISR row reads=%d (bad %d), select rises=%d' % (
                      m.violations[:5], m.read_checks['reads'], m.read_checks['bad'], m.sel_edges['rise']))
        boot[kind] = m
        T('--- polled registers (%s) ---' % kind)
        loops = {}
        for (pc, a), n in m.reads_pc.items():
            if n >= 3:
                loops.setdefault(a, []).append((n, m.sym.at(pc)))
        for a in sorted(loops):
            lst = sorted(loops[a], reverse=True)[:4]
            tot = sum(n for n, _ in loops[a])
            T('   %#010x  total=%-8d %s' % (a, tot, ', '.join('%s x%d' % (p, n) for n, p in lst)))
            if POLL_EXPLAIN.get(a):
                T('              -> ' + POLL_EXPLAIN[a])
        if kind == 'via':
            rng = [op[1] for op in m.flash_ops]
            okr = all(0xF6C0 <= a < 0xFFC0 for a in rng)
            check('A3-via', 'VIA first boot: every flash erase/program inside 0xF6C0..0xFFBF (wear-leveling aux sectors + EEPROM), flag page untouched',
                  okr and m.flash[0xFFFC:0x10000] == b'\x55\x55\xaa\xaa' and m.flash[:0xF6C0] == m.img[:0xF6C0],
                  '%d ops, range %s..%s, flag=%s, code area unchanged=%s' % (
                      len(rng), hex(min(rng)) if rng else '-', hex(max(rng)) if rng else '-',
                      m.flash[0xFFFC:0x10000].hex(), m.flash[:0xF6C0] == m.img[:0xF6C0]))

    m = None
    m = mk('via', page_erase=False)
    r = m.run(400)
    check('A4-via', 'VIA first boot if FLASH page-erase does NOT exist in silicon: still reaches main loop, no flash write outside EEPROM area',
          r == 'TIME' and m.first_main_loop_t is not None and not m.faults and not m.violations,
          'result=%s main loop at %s ms, flash ops=%d, faults=%s, violations=%s' % (
              r, m.first_main_loop_t, len(m.flash_ops), m.faults[:2], m.violations[:2]))

if want('B'):
    ISP_CASES = [
        ('B1', 'Esc (r0,c0) held at power-on', {(0, 0)}, 0, True),
        ('B2', 'P (r2,c10 = BOOT pin P2.2) held at power-on', {(2, 10)}, 0, True),
        ('B3', 'Esc + P held', {(0, 0), (2, 10)}, 0, True),
        ('B4', 'no key', set(), 0, False),
        ('B5', 'F1 (r0,c1) held', {(0, 1)}, 0, False),
        ('B6', 'other keys of Esc column held (r1..r5,c0)', {(1, 0), (2, 0), (3, 0), (4, 0), (5, 0)}, 0, False),
        ('B7', 'other keys of P column held (r0,r1,r3,r4,r5 c10)', {(0, 10), (1, 10), (3, 10), (4, 10), (5, 10)}, 0, False),
        ('B8', 'row P3.11 stuck high (no key)', set(), 1 << 11, 'early-no'),
        ('B9', 'all rows stuck high', set(), ROW_MASK, 'early-no'),
        ('B10', 'Esc held + 20 other keys (ghost-free matrix)', {(0, 0)} | {(r, c) for r in range(1, 6) for c in range(1, 5)}, 0, True),
    ]
    for kind in ('default', 'via', 'stage1'):
        for tid, name, keys, stuck, wnt in ISP_CASES:
            if kind != 'default' and tid not in ('B1', 'B2', 'B4', 'B8'):
                continue
            m = None
            m = mk(kind, pressed=keys, stuck_rows=stuck)
            r = m.run(50 if wnt is True else 250)
            if wnt == 'early-no':
                i = m.isp or {}
                early_jump = i.get('boot') == 0 and m.sym.func(i.get('lr', 0)) == 'early_hardware_init_pre'
                snaps = [s for s in m.exit_snaps if s[0].startswith('SYSRESETREQ')]
                det = ('early hook did NOT jump; QMK then booted and %s' % (
                    'BOOTMAGIC saw Esc (row 0 high) -> bootloader_jump -> reset -> ROM ISP at %.1f ms; exit snapshot %s' % (i['t_ms'], snaps[:1])
                    if i and not early_jump and m.resets else 'reached main loop at %s ms (ISP=%s)' % (m.first_main_loop_t, bool(i))))
                check('%s-%s' % (tid, kind), name + ' -> early hook must not jump', not early_jump, det)
                continue
            if wnt:
                i = m.isp or {}
                ok = (r == 'ISP' and i.get('addr') == 0x1FFF0300 and i.get('usb_touched') is None and i.get('boot') == 0
                      and not i.get('wdt_en') and i.get('col_mode') == (0, 0) and m.first_main_loop_t is None
                      and i.get('hclk') == 12e6 and not (i.get('ahbclken', 0) >> 7) & 1)
                det = ('jump to %#x at t=%.4f ms, caller %s, USB touched before=%s, HCLK=%.0f MHz, WDT running=%s, '
                       'column MODE P2/P1 at jump=%s, CT16B1 clock=%d' % (
                           i.get('addr', 0), i.get('t_ms', -1), m.sym.at(i.get('lr', 0)), i.get('usb_touched'),
                           i.get('hclk', 0) / 1e6, i.get('wdt_en'), tuple(hex(x) for x in i.get('col_mode', ())),
                           (i.get('ahbclken', 0) >> 7) & 1))
            else:
                ok = r == 'TIME' and m.isp is None and m.first_main_loop_t is not None and not m.faults
                det = 'result=%s, ISP=%s, main loop reached at %s ms' % (r, m.isp is not None, m.first_main_loop_t)
            check('%s-%s' % (tid, kind), name + ' -> ' + ('ROM ISP' if wnt else 'normal boot'), ok, det)

    m = None
    m = mk('default')
    m.uc.mem_write(0x20001FFC, struct.pack('<I', 0xDEADBEEF))
    r = m.run(50)
    check('B11-default', 'RAM magic 0xDEADBEEF at __ram0_end__-4 -> ROM ISP, magic cleared', r == 'ISP' and ram32(m, 0x20001FFC) == 0
          and m.isp['usb_touched'] is None, 'result=%s magic after=%#x' % (r, ram32(m, 0x20001FFC)))
    m = None
    m = mk('default')
    m.schedule = [(150, 400, (0, 0)), (150, 400, (2, 10))]
    r = m.run(400)
    check('B12-default', 'Esc+P pressed only after boot (t=150 ms) -> no ISP, normal operation', r == 'TIME' and m.isp is None and not m.resets,
          'result=%s resets=%s' % (r, m.resets))


def classify_reads(m, t_from):
    reads = [x for x in m.col_reads if x[0] > t_from]
    sample_pcs = {pc for pc, n in m.scan_pcs_one.items() if n > 10}
    samples = [x for x in reads if x[3] in sample_pcs]
    others = [x for x in reads if x[3] not in sample_pcs]
    return samples, others, sample_pcs


def matrix_sweep(kind, settle_instr=None, positions=None, setup=None, t0=200.0, **kw):
    m = None
    m = mk(kind, settle_instr=settle_instr, **kw)
    m.keep_reg_writes = False
    m.keep_col_reads = False
    m.keep_ct1_events = False
    if setup:
        m.inject = [(150, setup)]
    m.run(t0 - 10)
    msym = m.sym['matrix']
    positions = positions or [(r, c) for r in range(6) for c in range(17)]
    t = t0
    bad = []
    for (r, c) in positions:
        m.schedule = [(t, t + 15, (r, c))]
        m.run(t + 12)
        got = struct.unpack('<6I', m.uc.mem_read(msym, 24))
        wnt = [0] * 6
        wnt[r] = 1 << c
        m.run(t + 30)
        got2 = struct.unpack('<6I', m.uc.mem_read(msym, 24))
        if list(got) != wnt or any(got2):
            bad.append(((r, c), [hex(x) for x in got], [hex(x) for x in got2]))
        t += 30
    return m, bad


if want('C'):
    for kind in ('default', 'stage1'):
        m = None
        m = mk(kind)
        m.run(400)
        t_from = m.first_main_loop_t
        samples, others, spcs = classify_reads(m, t_from)
        T('--- %s: P3.DATA read sites: %s' % (kind, ', '.join('%s x%d' % (m.sym.at(p), n) for p, n in sorted(m.scan_pcs.items()))))
        one_high = all(len(x[1]) == 1 for x in samples)
        seq = [next(iter(x[1])) for x in samples if len(x[1]) == 1]
        k0 = seq.index(0) if 0 in seq else 0
        s2 = seq[k0:]
        order_ok = bool(s2) and all(s2[i] == i % 17 for i in range(len(s2) - len(s2) % 17))
        sfx = '' if kind == 'default' else '-stage1'
        check('C1' + sfx, 'each row sample taken with exactly one column HIGH, all others LOW (%s)' % kind, one_high and len(samples) > 1000,
              '%d row samples in %.0f ms after the main loop started, all with exactly one column high=%s, sample sites=%s' % (
                  len(samples), 400 - t_from, one_high, [m.sym.at(p) for p in sorted(spcs)]))
        check('C2' + sfx, 'column strobe order c0..c16 = P2.12..P2.0,P2.15,P1.15,P1.13,P1.12 (stock order) (%s)' % kind, order_ok,
              'first 20 strobes: %s' % s2[:20])
        rel_ok = all(len(x[1]) == 0 for x in others)
        check('C3' + sfx, 'row-release / discharge reads happen with all columns LOW (%s)' % kind, rel_ok,
              '%d release reads, bad=%d' % (len(others), sum(1 for x in others if x[1])))
        p3_out = [v for (t, b, pc, a, v) in m.reg_writes if a == 0x4004A004 and v & ROW_MASK]
        bi_i = max(i for i, (t, b, pc, a, v) in enumerate(m.reg_writes) if m.sym.func(pc) == 'board_init')
        col_in = [(hex(a), hex(v), m.sym.at(pc)) for (t, b, pc, a, v) in m.reg_writes[bi_i + 1:] if ((a == 0x40048004 and (v & 0x9FFF) != 0x9FFF) or (a == 0x40046004 and (v & 0xB000) != 0xB000))]
        cfg3 = m.reg[0x4004A008]
        check('C4' + sfx, 'rows P3.6-11 always inputs, no internal pull (CFG=10b); columns outputs after board_init (%s)' % kind,
              not p3_out and not col_in and ((cfg3 >> 12) & 0xFFF) == 0xAAA,
              'P3 MODE writes with row bits=%d, column MODE drops after init=%s, P3.CFG=%#x' % (len(p3_out), col_in[:3], cfg3))
        d = min(m.read_deltas) if m.read_deltas else 0
        check('C5' + sfx, 'settle time column HIGH -> row sample >= 12 cycles (QMK default 0.25 us); build uses 96 (%s)' % kind,
              d >= 12, 'instruction deltas BSET -> sample: %s' % dict(sorted(m.read_deltas.items())[:6]))

    m, bad = matrix_sweep('default')
    check('C6', 'full-system: each of 102 matrix positions pressed for 15 ms appears in debounced matrix[row] bit col, and clears on release (ideal pins, LED ISR scan, default effect running)',
          not bad, 'failures=%d %s; resets=%s faults=%s' % (len(bad), bad[:3], m.resets, m.faults))
    sub = [(0, 0), (2, 10), (5, 16), (3, 13), (4, 13), (4, 1), (3, 12), (4, 12)]
    m, bad = matrix_sweep('default', settle_instr=12, positions=sub)
    check('C7', 'same with a minimal realistic pin model (row needs >=12 cycles = 0.25 us after column HIGH)',
          not bad, 'failures=%d of %d: %s' % (len(bad), len(sub), [b[0] for b in bad]))
    m, bad = matrix_sweep('default', settle_instr=48, positions=sub)
    check('C7b', 'same with a slower pin model (row needs 48 cycles = 1 us after column HIGH)',
          not bad, 'failures=%d of %d: %s' % (len(bad), len(sub), [b[0] for b in bad]))
    m, bad = matrix_sweep('via', settle_instr=12, positions=sub)
    check('C7-via', 'VIA image with the 0.25 us pin model', not bad, 'failures=%d of %d: %s' % (len(bad), len(sub), [b[0] for b in bad]))
    m, bad = matrix_sweep('stage1', positions=sub)
    check('C8-stage1', 'stage1 image: key positions decode in matrix[] (ideal pins)', not bad, 'failures=%s' % bad[:3])


def hang(mm):
    mm.uc.mem_write(0x20001F00, b'\xfe\xe7')
    mm.uc.reg_write(UC_ARM_REG_PC, 0x20001F00)
    mm.note('INJECT: main thread stuck in endless loop')


if want('D'):
    m = None
    m = mk('default')
    m.keep_reg_writes = False
    m.keep_col_reads = False
    r = m.run(3000)
    cfg = m.wdt_cfg_writes
    en = [w for w in cfg if w[2] & 1]
    feeds_ml = [f for f in m.wdt_feeds if f[0] * 1e3 > (m.first_main_loop_t or 0)]
    feeders = sorted(set(f[1] for f in m.wdt_feeds))
    check('D1', 'WDT enabled in reset mode after init (TC=255, PRE=3 -> 8.19 s nominal)',
          bool(en) and en[-1][2] == 0x5AFA0001 and m.wdt['tc'] == 255 and ((m.reg[SYS1 + 8] >> 20) & 7) == 3 and m.wdt['en'],
          'CFG writes=%s, TC=%d, APBCP1=%#x' % (cfg, m.wdt['tc'], m.reg[SYS1 + 8]))
    check('D2', 'WDT fed from main loop only (never from the LED ISR); no WDT reset in 3 s of operation',
          r == 'TIME' and not m.resets and len(feeds_ml) > 1000 and 'Vector80' not in feeders,
          '%d feeds after main loop, max gap %.3f ms, feeders=%s, resets=%s' % (len(feeds_ml), m.max_feed_gap * 1e3, feeders, m.resets))
    first_en = en[0][0] if en else None
    check('D3', 'WDT not running during the init phase, started in keyboard_post_init_kb',
          first_en is not None and en[0][3].startswith('keyboard_post_init_kb') and first_en < m.first_main_loop_t,
          'first enable at %.3f ms from %s; main loop at %.3f ms' % (first_en or -1, en[0][3] if en else '-', m.first_main_loop_t))

    class G80NoPre(G80):
        def wdt_period(self):
            return 128 / 32000.0

    m = G80NoPre(*IMG['default'])
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.inject = [(500, hang)]
    r = m.run(2000)
    wr = [x for x in m.resets if x[1] == 'WDT']
    check('D7', 'prescaler NOT applied + hang at 500 ms -> WDT reset at ~1.52 s -> normal reboot',
          bool(wr) and 1450 < wr[0][0] < 1600 and m.isp is None, 'WDT reset at %s ms, ISP=%s' % (wr[0][0] if wr else None, m.isp is not None))

    for kind in ('default', 'via'):
        m = None
        m = mk(kind)
        def inj_reboot(mm):
            mm.uc.reg_write(UC_ARM_REG_PC, mm.sym['mcu_reset']); mm.note('INJECT: mcu_reset()')
        m.inject = [(250, inj_reboot)]
        r = m.run(500)
        dis = [s for _, _, s in m.log]
        stop_i = next((i for i, s in enumerate(dis) if 'WDT disabled' in s), None)
        snap = [s for s in m.exit_snaps if s[0] == 'SYSRESETREQ@mcu_reset']
        check('D8-' + kind, 'mcu_reset() (QK_REBOOT) -> LEDs off, WDT stopped, SYSRESETREQ, clean reboot to main loop (no ISP) [E-L11]',
              stop_i is not None and any(x[1] == 'SYSRESETREQ' for x in m.resets) and m.isp is None and m.boot_index >= 1
              and m.first_main_loop_t is not None and not m.faults and snap and not snap[0][2] and snap[0][3] == 0 and not snap[0][4],
              'WDT disabled=%s, resets=%s, boots=%d, ISP=%s, at SYSRESETREQ: selects high=%s PWMIOENB=%#x IRQ16 enabled=%s' % (
                  stop_i is not None, m.resets, m.boot_index + 1, m.isp is not None,
                  snap[0][2] if snap else '?', snap[0][3] if snap else -1, snap[0][4] if snap else '?'))

    m = None
    m = mk('default')
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    usbd = m.sym['USBD1']
    def suspend(mm):
        mm.uc.mem_write(usbd, b'\x05'); mm.note('INJECT: USBD1.state = USB_SUSPENDED')
    def resume(mm):
        mm.uc.mem_write(usbd, b'\x02'); mm.note('INJECT: USBD1.state = USB_READY')
    m.inject = [(300, suspend), (2800, resume)]
    r = m.run(3200)
    feeders = [f for f in m.wdt_feeds if 300 < f[0] * 1e3 < 2800]
    gaps = [b[0] - a[0] for a, b in zip(feeders, feeders[1:])]
    check('D5', 'USB suspend loop (2.5 s) keeps feeding the WDT (suspend_power_down_kb)',
          not m.resets and feeders and all(f[1] == 'suspend_power_down_kb' for f in feeders[1:]),
          '%d feeds while suspended, max gap %.2f ms, feeders=%s, resets=%s' % (
              len(feeders), max(gaps) * 1e3 if gaps else -1, sorted(set(f[1] for f in feeders)), m.resets))


def exit_ok(m, kind_prefix):
    snap = [s for s in m.exit_snaps if s[0].startswith(kind_prefix)]
    if not snap:
        return False, 'no %s snapshot' % kind_prefix
    s = snap[0]
    return (not s[2] and s[3] == 0 and s[5] == 0), 'at %s: selects high=%s PWMIOENB=%#x lit=%d' % (s[0], s[2], s[3], s[5])


def fault_case(tid, name, inj, kind='default', white=True):
    m = None
    m = mk(kind)
    m.keep_reg_writes = m.keep_col_reads = False
    m.inject = [(240, set_white)] if white else []
    m.inject.append((300, inj))
    r = m.run(500)
    i = m.isp or {}
    ok = (r == 'ISP' and any(x[1] == 'SYSRESETREQ' for x in m.resets) and i.get('boot') == 1
          and m.sym.func(i.get('lr', 0)) == 'early_hardware_init_pre'
          and i.get('addr') == 0x1FFF0300 and ram32(m, 0x20001FFC) == 0 and i.get('usb_touched') is None)
    eok, edet = exit_ok(m, 'SYSRESETREQ@')
    lit_before = recent_select_rise(m, 290)
    check(tid, name + ' -> LEDs off, magic + SYSRESETREQ -> early hook -> ROM ISP [E-L11]', ok and eok and (lit_before or not white),
          'result=%s, resets=%s, faults=%s, ISP boot#%s caller=%s, LEDs lit just before=%s, %s' % (
              r, m.resets, [f[2] for f in m.faults][:1], i.get('boot'), m.sym.at(i.get('lr', 0)), lit_before, edet))
    return m


def inj_pend_hf(mm):
    mm.pending.add(EXC_HARDFAULT); mm.note('INJECT: HardFault pended')
def inj_wild_jump(mm):
    mm.uc.reg_write(UC_ARM_REG_PC, 0x30000000); mm.note('INJECT: wild jump')
def inj_bad_load(mm):
    mm.uc.mem_write(0x20001F00, bytes.fromhex('0868'))
    mm.uc.reg_write(UC_ARM_REG_R1, 0xA0000000)
    mm.uc.reg_write(UC_ARM_REG_PC, 0x20001F00); mm.note('INJECT: load from unmapped 0xA0000000')
def inj_undef(mm):
    mm.uc.mem_write(0x20001F00, bytes.fromhex('ffde'))
    mm.uc.reg_write(UC_ARM_REG_PC, 0x20001F00); mm.note('INJECT: undefined instruction')
def inj_irq(mm):
    mm.pend_irq(5); mm.note('INJECT: spurious IRQ5 (no driver, line enabled)')
def inj_halt(mm):
    mm.uc.reg_write(UC_ARM_REG_R0, 0x5800); mm.uc.reg_write(UC_ARM_REG_PC, mm.sym['chSysHalt']); mm.note('INJECT: chSysHalt()')


if want('E'):
    fault_case('E1', 'pended HardFault in main loop (LEDs white)', inj_pend_hf)
    fault_case('E2', 'real fault: instruction fetch from unmapped memory', inj_wild_jump)
    fault_case('E3', 'real fault: data load from unmapped memory', inj_bad_load)
    fault_case('E4', 'real fault: undefined instruction', inj_undef)
    fault_case('E5', 'unhandled interrupt (spurious IRQ5)', inj_irq)
    fault_case('E6-via', 'ChibiOS chSysHalt() (VIA image)', inj_halt, 'via')
    fault_case('E7-via', 'pended HardFault (VIA image)', inj_pend_hf, 'via')
    fault_case('E1-stage1', 'pended HardFault (stage1 image)', inj_pend_hf, 'stage1', white=False)

    combos = [('E8', 'Fn -> RShift -> Esc', [(300, 500, (5, 11)), (310, 500, (4, 13)), (330, 500, (0, 0))]),
              ('E8b', 'RShift -> Fn -> Esc', [(300, 500, (4, 13)), (310, 500, (5, 11)), (330, 500, (0, 0))]),
              ('E8c', 'Fn -> Esc held, then RShift (no boot expected: G80_SBOOT fires on Esc press)',
               [(300, 500, (5, 11)), (310, 500, (0, 0)), (330, 500, (4, 13))])]
    for tid, name, sch in combos:
        m = None
        m = mk('default')
        m.keep_reg_writes = m.keep_col_reads = False
        m.inject = [(240, set_white)]
        m.schedule = sch
        r = m.run(500)
        i = m.isp or {}
        if tid == 'E8c':
            check(tid, 'Fn+Esc pressed before RShift -> no bootloader (stage-1 semantics)', r == 'TIME' and m.isp is None and not m.resets,
                  'result=%s resets=%s' % (r, m.resets))
            continue
        eok, edet = exit_ok(m, 'SYSRESETREQ@bootloader_jump')
        snap = [s for s in m.exit_snaps if s[0] == 'SYSRESETREQ@bootloader_jump']
        check(tid, 'Fn+RShift+Esc (%s; RShift in the Fn layer is G80_BAR, which also registers RShift) -> LEDs off, bootloader_jump -> ROM ISP [E-L11]' % name,
              r == 'ISP' and i.get('boot') == 1 and any(x[1] == 'SYSRESETREQ' for x in m.resets) and eok and snap and not snap[0][4],
              'result=%s resets=%s ISP boot#%s, WDT disabled=%s, IRQ16 disabled=%s, %s' % (
                  r, m.resets, i.get('boot'), any('WDT disabled' in s for _, _, s in m.log), bool(snap) and not snap[0][4], edet))
    m = None
    m = mk('default')
    m.schedule = [(300, 500, (5, 11)), (330, 500, (0, 0))]
    r = m.run(500)
    check('E9', 'Fn+Esc WITHOUT RShift -> no bootloader', r == 'TIME' and m.isp is None and not m.resets, 'result=%s resets=%s' % (r, m.resets))

if want('F'):
    w = open(IMG['via'][0], 'rb').read()
    mv2 = mk('via')
    a = mv2.sym['bootmagic_reset_eeprom'] & ~1
    check('F1-via', 'bootmagic_reset_eeprom() is "bx lr" (no flash write before the bootmagic jump)',
          w[a:a + 2] == bytes.fromhex('7047'), 'bytes @%#x = %s' % (a, w[a:a + 2].hex()))

# =====================================================================
# Part 2 - E-L1 .. E-L16
# =====================================================================
if want('E-L1') or want('E-L2') or want('E-L3') or want('E-L4') or want('E-L5') or want('E-L6') or want('E-L7'):
    m = None
    m = mk('default')
    m.keep_reg_writes = False
    m.keep_col_reads = False
    r = m.run(300)
    R = lambda off: m.reg[CT16B1 + off]
    cens = [e for e in m.ct1_events if e[0] == 'CEN' and e[1] > 250]
    pwm_vals = set()
    regs = dict(AHBCLKEN_b7=(m.reg[SYS1] >> 7) & 1, PRE=R(0x08), MR24=R(0x80), MCTRL=R(0x14), MCTRL2=R(0x18), MCTRL3=R(0x1C),
                PFPA=m.reg[PFPA], PWMCTRL=R(0x94), PWMCTRL2=R(0x98))
    exp = dict(AHBCLKEN_b7=1, PRE=0x17, MR24=0xFF, MCTRL=0, MCTRL2=0, MCTRL3=0x7000, PFPA=0x9FFFB0, PWMCTRL=0x55554500, PWMCTRL2=0x4155)
    attached = all(e[3][2] and e[3][3] == 0x9FFFB0 and e[3][4] == 0x9FFFB0 for e in cens)
    ok = all(regs[k] == exp[k] for k in exp) and attached and r == 'TIME' and not m.violations
    check('E-L1', 'CT16B1 registers as stock (+ PWM mode 2 on exactly the 17 column channels); PWMENB = PWMIOENB = 0x9FFFB0 at every phase start',
          ok, 'regs=%s expected=%s; phase starts checked=%d, all with PWMENB=PWMIOENB=0x9FFFB0=%s; violations=%s' % (
              {k: hex(v) for k, v in regs.items()}, {k: hex(v) for k, v in exp.items()}, len(cens), attached, m.violations[:2]))
    # period
    cyc = [b[2] - a[2] for a, b in zip(cens, cens[1:])]
    isr = m.isr_durations[-len(cyc):] if cyc else []
    per_ms = (sum(cyc) / len(cyc)) / 48e3 if cyc else 0
    check('E-L2', 'phase period = 256 ticks @ 2 MHz (6144 cycles) + ISR; frame of 21 phases < 4 ms',
          cyc and min(cyc) >= 6144 and per_ms * 21 < 4.0,
          'phase period min/mean/max = %d/%.0f/%d cycles (%.1f us mean) -> %.0f ISR/s, frame %.2f ms (%.0f Hz); ISR mean %.0f cycles' % (
              min(cyc), sum(cyc) / len(cyc), max(cyc), per_ms * 1e3, 1 / (per_ms / 1e3), per_ms * 21, 1e3 / (per_ms * 21),
              sum(isr) / max(1, len(isr))))
    T('   mode-1 note: the stock firmware uses PWM mode 1 (PWMCTRL never written); this build uses mode 2 (design D3).')

    # E-L3 static tables
    kb = json.load(open(KBD + '/keyboard.json'))
    lay = kb['rgb_matrix']['layout']
    exp_map = [[0xFF] * 17 for _ in range(7)]
    bar = 0
    for i, e in enumerate(lay):
        if 'matrix' in e:
            rr, cc = e['matrix']
        else:
            rr, cc = 6, 12 + bar
            bar += 1
        exp_map[rr][cc] = i
    fw_chan = list(m.flash[m.sym['g80_col_chan']: m.sym['g80_col_chan'] + 17])
    fw_pos = list(m.flash[m.sym['g80_led_pos']: m.sym['g80_led_pos'] + 93])
    pos_ok = len(set(fw_pos)) == 93 and all((p >> 5) < 7 and (p & 31) < 17 for p in fw_pos)
    fw_map = [[0xFF] * 17 for _ in range(7)]   # the map implied by g80_led_pos
    for i, p in enumerate(fw_pos):
        if pos_ok:
            fw_map[p >> 5][p & 31] = i
    glc = m.sym['g_led_config']
    mco = [list(m.ram_read(glc + 17 * rr, 17)) for rr in range(6)]
    mco_ok = all((mco[rr][cc] if mco[rr][cc] != 0xFF else 0xFF) == exp_map[rr][cc] for rr in range(6) for cc in range(17))
    stock_slots = [21 * rr + cc for rr in range(7) for cc in range(17) if exp_map[rr][cc] != 0xFF]
    stock_ok = sorted(stock_slots) == sorted(set(stock_slots)) and set(range(138, 143)) <= set(stock_slots) and 75 not in stock_slots
    check('E-L3a', 'LED tables in flash: g80_led_pos (LED -> LED row/col, one slot each) == keyboard.json layout (+bar at LED row 6, col 12..16), '
          'g80_col_chan == stock 04-luci-led.md 1.1, g_led_config.matrix_co == map rows 0-5, stock slots 21*row+col unique (138..142 = bar, 75 excluded)',
          fw_map == exp_map and fw_chan == COL_CHAN and pos_ok and mco_ok and stock_ok,
          'map ok=%s chan=%s ok=%s pos ok=%s matrix_co ok=%s stock slots ok=%s' % (fw_map == exp_map, fw_chan, fw_chan == COL_CHAN, pos_ok, mco_ok, stock_ok))

    # E-L3 dynamic: unique colour per LED through rgb_matrix_set_color, then one full frame
    vals = {i: (2 + i, 100 + i, 252 - i) for i in range(93)}
    def paint(mm):
        mm.call('rgb_matrix_disable_noeeprom')
    def paint2(mm):
        for i in range(93):
            mm.call('rgb_matrix_set_color', i, *vals[i])
        mm.ct1_events.clear()
        mm.note('INJECT: 93 unique colours written')
    m.inject = [(310, paint), (340, paint2)]
    m.keep_ct1_events = True
    m.run(360)
    cens = [e for e in m.ct1_events if e[0] == 'CEN']
    seen = {}
    errs = []
    for ev in cens:
        mrs, sh, att = ev[3][:3]
        if len(sh) != 1:
            errs.append(('selects', ev[1], sh))
            continue
        p = sh[0]
        row, ch = p % 7, p // 7
        for c in range(17):
            idx = exp_map[row][c]
            # side bar (88..92): R and G swapped on the PCB, g80_put() swaps them (verified on hardware)
            cc = (1 - ch) if (idx != 0xFF and idx >= 88 and ch < 2) else ch
            e = 256 - vals[idx][cc] if idx != 0xFF else 256
            if mrs[c] != e:
                errs.append(('mr', p, c, mrs[c], e))
        seen[p] = seen.get(p, 0) + 1
    check('E-L3b', 'phase/LED mapping, dynamic: 93 unique colours via rgb_matrix_set_color -> in every phase exactly the stock select line '
          '(R rows 0-6, G rows 0-6, B rows 0-6 = switches 0x4D8C/0x4E80) and MR[chan[col]] = 256 - value of LED map[row][col] '
          '(side bar: R<->G swapped, PCB)',
          not errs and len(seen) == 21, 'phases seen=%d (%d phase starts), errors=%d %s' % (len(seen), len(cens), len(errs), errs[:4]))

    # E-L4 / E-L5 / E-L6 / E-L7 with LEDs white
    m.inject = [(370, set_white)]
    m.run(380)
    m.mr_low_max = 0
    m.mr_min_seen = None
    m.match_bad = []
    m.read_bad = []
    m.read_checks.clear()
    m.sel_bad = []
    m.sel_edges.clear()
    nv = len(m.violations)
    m.keep_col_reads = True
    m.schedule = [(390, 460, (rr, cc)) for rr, cc in [(0, 0), (1, 5), (2, 9), (3, 13), (4, 15), (5, 16), (5, 6), (2, 2)]]
    m.run(480)
    check('E-L4', 'every row read in the LED ISR: all 21 selects LOW, PWMIOENB = 0, at most one column HIGH (exactly one for samples) - LEDs white, 8 keys held',
          m.read_checks['bad'] == 0 and m.read_checks['sample'] > 1000,
          'reads=%d (samples %d, discharge %d), bad=%d %s' % (m.read_checks['reads'], m.read_checks['sample'], m.read_checks['release'],
                                                          m.read_checks['bad'], m.read_bad[:3]))
    two = [v for v in m.violations[nv:] if 'two LED select' in v or 'outside the LED ISR' in v]
    check('E-L5', 'no ghosting between LED rows: every select edge happens with all 17 columns HIGH, never two selects HIGH',
          not m.sel_bad and not two and m.sel_edges['rise'] > 100,
          'select rises=%d falls=%d, edges with a column LOW=%d %s, violations=%s' % (
              m.sel_edges['rise'], m.sel_edges['fall'], len(m.sel_bad), m.sel_bad[:3], two[:2]))
    cens = [e for e in m.ct1_events if e[0] == 'CEN' and e[1] > 390]
    cyc = [b[2] - a[2] for a, b in zip(cens, cens[1:])]
    mean_period = sum(cyc) / len(cyc)
    duty_ours = m.mr_low_max * 24 / mean_period / 21
    check('E-L6', 'duty cap with all LEDs (255,255,255): LOW time per phase <= 252 ticks, MR >= 4 (mode 2), average per LED colour <= stock',
          m.mr_low_max <= 252 and (m.mr_min_seen or 0) >= 4 and duty_ours < 255 / 256 / 21,
          'max LOW = %d ticks, min MR = %s, mean phase %.0f cycles -> average per LED colour %.4f (stock upper bound with a 0 us ISR: %.4f)' % (
              m.mr_low_max, m.mr_min_seen, mean_period, duty_ours, 255 / 256 / 21))
    check('E-L7', 'mode 2 return to zero: at every MR24 match (timer stopped, before the ISR) all 17 columns are HIGH = LEDs dark',
          not m.match_bad and not m.lit_at_match,
          'matches checked=%d, with a column LOW=%d %s, LEDs lit while stopped=%d' % (
              sum(1 for e in m.ct1_events if e[0] == 'MATCH'), len(m.match_bad), m.match_bad[:2], len(m.lit_at_match)))
    m.keep_col_reads = False

    # E-L8 stall: IRQs masked for 10 ms with LEDs white
    saved = {}
    def stall_on(mm):
        saved['pc'] = mm.uc.reg_read(UC_ARM_REG_PC)
        # state at the stall start: if the injection lands between an MR24 match (timer stopped,
        # IRQ16 pending) and the ISR entry, there is no further match during the stall
        rt = mm.pwm_routes()
        saved['start'] = dict(t=round(mm.t_ms(), 4), cen=mm.ct1['cen'], pend16=EXC_CT16B1 in mm.pending,
                              lit=mm.lit_leds(rt)[:4], low=[c for c, v in enumerate(mm.col_levels(rt)) if v == 0])
        mm.uc.mem_write(0x20001F00, b'\xfe\xe7')
        mm.uc.reg_write(UC_ARM_REG_PRIMASK, 1)
        mm.uc.reg_write(UC_ARM_REG_PC, 0x20001F00)
        mm.lit_at_match.clear()
        mm.in_stall = True
        mm.stall_matches.clear()
        mm.note('INJECT: IRQs masked (cpsid i) for 10 ms')
    def stall_off(mm):
        mm.in_stall = False
        mm.uc.reg_write(UC_ARM_REG_PC, saved['pc'])
        mm.uc.reg_write(UC_ARM_REG_PRIMASK, 0)
        mm.note('INJECT: IRQs unmasked')
    m.inject = [(500, stall_on), (510, stall_off)]
    m.run(530)
    st = list(m.stall_matches)
    s0 = saved.get('start', {})
    # stage 3: the stall may also start with the timer already stopped (match just before the
    # injection, ISR not yet entered): then 0 matches, and the stopped state must be dark
    stopped_dark = len(st) == 0 and not s0.get('cen', True) and not s0.get('lit') and not s0.get('low')
    check('E-L8a', 'stall, IRQs masked 10 ms with all LEDs white: the timer stops at the end of the phase with every column HIGH -> no LED lit for > 1 phase (128 us)',
          ((len(st) == 1 and not st[0][1] and not st[0][2]) or stopped_dark) and recent_select_rise(m, 495),
          'matches during the stall=%d (the timer stops: STOP), LEDs lit / columns LOW at that match: %s; state at stall start: %s; resumed=%s' % (
              len(st), st[:1], s0, recent_select_rise(m, 512)))
    boot['ram_default'] = m

    mv = mk('via', flash_stall_us=2000)
    mv.keep_reg_writes = mv.keep_col_reads = False
    def via_write(mm):
        # stage 7b: never nest the call inside a flash operation the firmware itself is doing (first-boot
        # EEPROM format still running at 400 ms): VIA commands run in the main thread, so on the keyboard
        # they can not interleave with an efl call. Retry 1 ms later until the thread is outside efl_*.
        # (phase C: also the flash driver's busy wait and the wear-leveling / backing-store layers, which
        # the first-boot format now runs through in more steps)
        if mm.sym.func(mm.cur_pc()).startswith(('efl', 'sn32_flash', 'backing_store', 'wear_leveling')):
            mm.inject.append((mm.t_ms() + 1, via_write))
            return
        mm.in_stall = False
        mm.stall_matches.clear()
        n0 = len(mm.flash_ops)
        for k in range(4):
            mm.call('eeconfig_update_kb', 0x00000010 * (k + 1))
        mm.note('INJECT: 4 eeconfig_update_kb() -> %d flash ops' % (len(mm.flash_ops) - n0))
    mv.inject = [(300, set_white), (400, via_write)]
    r = mv.run(600)
    st = list(mv.stall_matches)
    ops = [o for o in mv.flash_ops if o[-1] >= 399]
    check('E-L8b', 'stall during VIA EEPROM writes (each flash op stalls the CPU 2 ms, timer keeps running) with all LEDs white: no LED lit while stalled',
          ops and st and all(not s[1] and not s[2] for s in st) and r == 'TIME' and not mv.violations,
          'flash ops=%d, phase ends during stalls=%d, any lit=%s, violations=%s' % (len(ops), len(st), any(s[1] for s in st), mv.violations[:2]))

if want('E-L9'):
    for label, setup, pos in (('white', set_white, None), ('off', set_off, [(r, c) for r in range(6) for c in (0, 5, 10, 13, 16)]),
                              ('animated', set_anim, [(r, c) for r in range(6) for c in (1, 6, 11, 12, 14, 15)])):
        m, bad = matrix_sweep('default', positions=pos, setup=setup)
        check('E-L9-' + label, 'matrix positions with RGB %s: each pressed position appears in matrix[] and clears (%d positions)' % (label, len(pos or range(102))),
              not bad and not m.violations, 'failures=%d %s violations=%s' % (len(bad), bad[:3], m.violations[:2]))
    keys20 = [(1, c) for c in range(1, 11)] + [(3, c) for c in range(1, 11)]
    m = None
    m = mk('default')
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.inject = [(150, set_white)]
    m.schedule = [(200, 260, rc) for rc in keys20]
    m.run(240)
    got = struct.unpack('<6I', m.uc.mem_read(m.sym['matrix'], 24))
    wnt = [0] * 6
    for rr, cc in keys20:
        wnt[rr] |= 1 << cc
    check('E-L9-20keys', '20 keys held together with LEDs white: matrix[] shows exactly those 20 (no phantom position)',
          list(got) == wnt, 'got=%s want=%s' % ([hex(x) for x in got], [hex(x) for x in wnt]))

if want('E-L10'):
    m0 = mk('default')
    m0.run(60)
    t_board = min(t for (t, b, pc, a, v) in m0.reg_writes if m0.sym.func(pc) == 'board_init')
    m = None
    m = mk('default')
    m.schedule = [(t_board, 400, (0, 0))]
    r = m.run(400)
    i = m.isp or {}
    snap = [s for s in m.exit_snaps if s[0] == 'SYSRESETREQ@bootloader_jump']
    check('E-L10', 'bootmagic: Esc pressed right after the early check (from board_init, t=%.2f ms) -> first matrix_scan already sees it '
          '(matrix_init waits for the first ISR pass) -> bootloader_jump -> ROM ISP' % t_board,
          r == 'ISP' and i.get('boot') == 1 and snap and not snap[0][2] and snap[0][3] == 0,
          'result=%s resets=%s ISP boot#%s at %s ms, exit snapshot=%s' % (r, m.resets, i.get('boot'), i.get('t_ms'), snap[:1]))

if want('E-L12'):
    m = None
    m = mk('default')
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    s0 = {}
    def hang2(mm):
        s0['steps'] = ram32(mm, mm.sym['g80_isr_steps'])
        hang(mm)
    def probe_steps(mm):
        s0['steps2'] = ram32(mm, mm.sym['g80_isr_steps'])
    m.inject = [(500, hang2), (3000, probe_steps)]
    r = m.run(9300)
    wr = [x for x in m.resets if x[1] == 'WDT']
    feeders = sorted(set(f[1] for f in m.wdt_feeds))
    check('E-L12', 'main loop hung with the LED ISR alive -> the ISR keeps running but never feeds -> WDT reset at ~8.2 s -> normal reboot',
          bool(wr) and 8550 < wr[0][0] < 8850 and m.isp is None and m.first_main_loop_t is not None and s0.get('steps2', 0) > s0.get('steps', 0) + 10000
          and 'Vector80' not in feeders,
          'WDT reset at %s ms, ISR steps during the hang=%d, feeders=%s, ISP=%s' % (
              wr[0][0] if wr else None, s0.get('steps2', 0) - s0.get('steps', 0), feeders, m.isp is not None))

if want('E-L13'):
    m = None
    m = mk('default')
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    def kill(mm):
        mm.irq16_suppressed = True; mm.note('INJECT: IRQ16 line dead')
    m.inject = [(300, kill)]
    m.schedule = [(450, 480, (2, 5))]
    m.run(470)
    fb = ram8(m, 'g80_scan_fallback')
    got = struct.unpack('<6I', m.uc.mem_read(m.sym['matrix'], 24))
    sh = m.selects_high()
    ioen = m.reg[CT16B1 + 0xA0]
    check('E-L13a', 'IRQ16 stops at runtime -> within 100 ms: fallback to the stage-1 polled scan, LEDs off, keys still work',
          fb == 1 and got[2] == 1 << 5 and not sh and ioen == 0 and not m.resets,
          'g80_scan_fallback=%d, matrix row2=%#x (T held), selects high=%s, PWMIOENB=%#x, resets=%s' % (fb, got[2], sh, ioen, m.resets))
    m = None
    m = mk('default')
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.irq16_suppressed = True
    m.schedule = [(300, 330, (3, 4))]
    r = m.run(320)
    fb = ram8(m, 'g80_scan_fallback')
    got = struct.unpack('<6I', m.uc.mem_read(m.sym['matrix'], 24))
    check('E-L13b', 'IRQ16 never fires (e.g. CT16B1 clock assumption wrong) -> matrix_init times out after 10 ms -> polled scan from boot, keys work',
          fb == 1 and got[3] == 1 << 4 and r == 'TIME' and m.first_main_loop_t is not None,
          'fallback=%d, main loop at %s ms, matrix row3=%#x (F held)' % (fb, m.first_main_loop_t, got[3]))

if want('E-L14'):
    for label, kw in (('normal', {}), ('row 5 stuck high', dict(stuck_rows=1 << 6))):
        m = None
        m = mk('default', **kw)
        m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
        m.inject = [(240, set_white)]
        m.run(300)
        m.isr_durations.clear()
        c0 = m.cyc
        m.schedule = [(300, 400, (1, 1)), (300, 400, (3, 7))]
        m.run(400)
        d = m.isr_durations
        load = sum(d) / (m.cyc - c0)
        mx = max(d)
        check('E-L14-' + label.split()[0], 'ISR duration (%s, LEDs white, 2 keys held): worst < 80 us (3840 cycles), CPU load < 20 %%' % label,
              mx < 3840 and load < 0.20,
              'ISRs=%d, mean %.0f cycles (%.1f us), max %d cycles (%.1f us), CPU load %.1f %%' % (
                  len(d), sum(d) / len(d), sum(d) / len(d) / 48, mx, mx / 48, load * 100))

if want('E-L15'):
    def press(m, t, keys, dur=40):
        m.schedule += [(t, t + dur, k) for k in keys]

    def rep_at(m, t):
        m.run(t)
        return report(m)

    # Win-lock
    m = None
    m = mk('default')
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.schedule = []
    press(m, 300, [(5, 1)], 30)                        # LGui alone (lock off)
    press(m, 400, [(5, 11)], 40); press(m, 410, [(5, 1)], 20)   # Fn+LWin -> lock
    press(m, 500, [(5, 1)], 40)                        # LGui
    press(m, 600, [(5, 12)], 40)                       # App
    a = rep_at(m, 320)
    b = rep_at(m, 520)
    c = rep_at(m, 620)
    kc = ram32(m, m.sym['keymap_config']) & 0xFFFF
    check('E-L15-winlock', 'Win-lock (Fn+LWin): before, LGui -> GUI in the report; after, LGui -> nothing, App (0x65) -> nothing (stock)',
          (a[0] & 0x08) and not (b[0] & 0x88) and 0x65 not in c[1] and (kc >> 4) & 1,
          'LGui before: mods=%#x; after: mods=%#x keys=%s; App: keys=%s; keymap_config.no_gui=%d' % (a[0], b[0], sorted(b[1]), sorted(c[1]), (kc >> 4) & 1))

    # Alt+Tab / Alt+F4
    m = None
    m = mk('default')
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.schedule = []
    press(m, 300, [(5, 11)], 40); press(m, 310, [(2, 0)], 20)    # Fn+Tab -> block LAlt+Tab
    press(m, 360, [(5, 11)], 40); press(m, 370, [(3, 0)], 20)    # Fn+Caps -> block LAlt+F4
    press(m, 450, [(5, 2)], 60); press(m, 465, [(2, 0)], 30)     # LAlt + Tab
    press(m, 550, [(5, 10)], 60); press(m, 565, [(2, 0)], 30)    # RAlt + Tab
    press(m, 650, [(5, 2)], 60); press(m, 665, [(0, 4)], 30)     # LAlt + F4
    press(m, 750, [(2, 0)], 30)                                  # Tab alone
    t1 = rep_at(m, 485)
    t2 = rep_at(m, 585)
    t3 = rep_at(m, 685)
    t4 = rep_at(m, 770)
    check('E-L15-alttab', 'LAlt+Tab and LAlt+F4 blocked after Fn+Tab / Fn+Caps; RAlt+Tab and Tab alone still work (left Alt only, as stock)',
          (t1[0] & 0x04) and 0x2B not in t1[1] and (t2[0] & 0x40) and 0x2B in t2[1] and (t3[0] & 0x04) and 0x3D not in t3[1] and 0x2B in t4[1],
          'LAlt+Tab=%s/%s RAlt+Tab=%s/%s LAlt+F4=%s/%s Tab=%s' % (hex(t1[0]), sorted(t1[1]), hex(t2[0]), sorted(t2[1]), hex(t3[0]), sorted(t3[1]), sorted(t4[1])))

    # Win/Mac switch P0.2
    res = {}
    for lvl in (0, 1):
        m = None
        m = mk('default', ext_pins={(0, 2): lvl})
        m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
        m.schedule = []
        press(m, 300, [(5, 2)], 40)
        res[lvl] = rep_at(m, 320)
    m = None
    m = mk('default', ext_pins={(0, 2): 1})
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.schedule = []
    def flip(mm):
        mm.ext_pins[(0, 2)] = 0; mm.note('INJECT: P0.2 -> LOW (Mac)')
    m.inject = [(300, flip)]
    press(m, 305, [(5, 2)], 25)       # inside the 40 ms debounce window: still Win
    press(m, 420, [(5, 2)], 40)       # after: Mac
    e1 = rep_at(m, 322)
    e2 = rep_at(m, 440)
    flash = ram8(m, 'g80_fl') if m.sym.get('g80_fl') else None
    check('E-L15-mac', 'Win/Mac from P0.2 (Mac = LOW): LAlt -> LGui on Mac, LAlt on Win; runtime change applied after the ~40 ms debounce; never stored',
          (res[0][0] & 0x08) and not (res[0][0] & 0x04) and (res[1][0] & 0x04) and (e1[0] & 0x04) and (e2[0] & 0x08)
          and not [o for o in m.flash_ops],
          'P0.2 LOW: mods=%#x, HIGH: mods=%#x; runtime flip: +22 ms mods=%#x, +140 ms mods=%#x; flash ops=%d' % (
              res[0][0], res[1][0], e1[0], e2[0], len(m.flash_ops)))

    # factory reset
    for label, hold, kind in (('2.5s', 2500, 'default'), ('3.3s', 3300, 'default'), ('3.3s-via', 3300, 'via')):
        m = None
        m = mk(kind)
        m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
        m.schedule = [(300, 300 + hold, (5, 11)), (310, 300 + hold, (1, 13))]
        r = m.run(300 + 3000 + 1700 + 600)
        rs = [x for x in m.resets if x[1] == 'SYSRESETREQ']
        snap = [s for s in m.exit_snaps if s[0] == 'SYSRESETREQ@mcu_reset']
        if hold < 3000:
            check('E-L15-frst-' + label, 'Fn+Backspace held 2.5 s -> no factory reset', not m.resets and r == 'TIME', 'resets=%s' % m.resets)
        else:
            bad_ops = [o for o in m.flash_ops if not (0xF6C0 <= o[1] < 0xFFC0)]
            check('E-L15-frst-' + label, 'Fn+Backspace held 3.3 s (%s) -> 7 white flashes -> eeconfig_disable -> mcu_reset (LEDs off) -> clean reboot, no ISP; flash ops only in 0xF6C0..0xFFBF' % kind,
                  rs and 4400 < rs[0][0] < 5200 and m.isp is None and m.boot_index >= 1 and not bad_ops and snap and not snap[0][2] and snap[0][3] == 0,
                  'reset at %s ms, boots=%d, ISP=%s, flash ops=%d (outside=%d), exit=%s' % (
                      rs[0][0] if rs else None, m.boot_index + 1, m.isp is not None, len(m.flash_ops), len(bad_ops), snap[:1]))

    # WASD swap
    m = None
    m = mk('default')
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.schedule = []
    press(m, 300, [(2, 2)], 30)                                   # W
    press(m, 380, [(5, 11)], 40); press(m, 390, [(3, 5)], 20)     # Fn+G
    press(m, 460, [(2, 2)], 30)                                   # W -> Up
    press(m, 520, [(4, 15)], 30)                                  # Up -> W
    w1 = rep_at(m, 320)
    w2 = rep_at(m, 480)
    w3 = rep_at(m, 540)
    w4 = rep_at(m, 580)
    check('E-L15-wasd', 'WASD <-> arrows (Fn+G): W -> W before, W -> Up and Up -> W after; nothing stuck after release',
          0x1A in w1[1] and 0x52 in w2[1] and 0x1A not in w2[1] and 0x1A in w3[1] and 0x52 not in w3[1] and not (w4[1] & {0x1A, 0x52}),
          'before=%s after: W=%s Up=%s released=%s' % (sorted(w1[1]), sorted(w2[1]), sorted(w3[1]), sorted(w4[1])))

    # key backlight off with indicators: Win-lock LED stays
    m = None
    m = mk('default')
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.inject = [(250, set_white)]
    m.schedule = []
    press(m, 300, [(5, 11)], 40); press(m, 310, [(5, 1)], 20)     # Win-lock on
    press(m, 380, [(5, 11)], 40); press(m, 390, [(2, 5)], 20)     # Fn+T keys off
    m.run(470)
    fr = m.ram_read(m.sym['g80_frame'], 21 * 17)
    on_keys = {(p, c) for p in range(21) for c in range(17) if fr[p * 17 + c] and p % 7 < 6}
    exp_on = {(ch * 7 + 5, 1) for ch in range(3)}
    bar_on = sum(1 for p in range(21) for c in range(17) if fr[p * 17 + c] and p % 7 == 6)
    check('E-L15-keysoff', 'Fn+T (keys off): every key LED dark except the Win-lock indicator (LGui white), side bar unaffected',
          on_keys == exp_on and bar_on > 0, 'lit key cells=%s expected=%s, bar cells lit=%d' % (sorted(on_keys)[:6], sorted(exp_on), bar_on))

if want('E-L16'):
    mm = boot.get('ram_default') or mk('default')
    budget = {}
    for kind in ('default', 'via', 'stage1'):
        sy = Syms(IMG[kind][1])
        b = open(IMG[kind][0], 'rb').read()
        elf = IMG[kind][0][:-4] + '.elf'
        budget[kind] = dict(heap=sy['__heap_end__'] - sy['__heap_base__'], flag=b[0xFFFC:].hex(), eeprom_zero=not any(b[0xF6C0:0xFFC0]),
                            last_code=max(i for i in range(0xF6C0) if b[i]) + 1)
    ok = all(v['heap'] >= 512 and v['flag'] == '5555aaaa' and v['eeprom_zero'] and v['last_code'] <= 0xF6C0 for v in budget.values())
    check('E-L16a', 'memory: code+data below 0xF6C0, wear-leveling aux sectors + EEPROM area blank in the image, flag 0xAAAA5555 at 0xFFFC, free RAM >= 512 B (all images)', ok,
          ' '.join('%s: last code byte %#x, free RAM %d B;' % (k, v['last_code'], v['heap']) for k, v in budget.items()))
    m = None
    m = mk('default')
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    def rainbow(mm):
        mm.call('rgb_matrix_enable_noeeprom')
        mm.call('rgb_matrix_mode_eeprom_helper', FX_UPDOWN, 0)
    m.inject = [(250, rainbow)]
    m.schedule = [(300 + 37 * k, 300 + 37 * k + 25, (k % 6, (k * 7) % 17)) for k in range(70)]
    m.run(3000)
    def free_words(lo, hi):
        n = 0
        for a in range(lo, hi, 4):
            if ram32(m, a) == 0x55555555:
                n += 4
            else:
                break
        return n
    msb, mse = m.sym['__main_stack_base__'], m.sym['__main_stack_end__']
    psb, pse = m.sym['__process_stack_base__'], m.sym['__process_stack_end__']
    fm, fp = free_words(msb, mse), free_words(psb, pse)
    check('E-L16b', 'stack painting after 3 s with the rainbow effect and 70 key presses: >= 256 B never touched in both stacks',
          fm >= 256 and fp >= 256 and not m.resets and not m.faults,
          'main stack (exceptions, %d B): %d B untouched; process stack (main thread, %d B): %d B untouched' % (mse - msb, fm, pse - psb, fp))

# =====================================================================
# Part 3 - stage 3 features (test_features.py)
# =====================================================================
if want('S3'):
    import test_features
    test_features.run(dict(mk=mk, check=check, T=T, report=report, ram32=ram32, IMG=IMG, FW=FW, EFFECT_IDS=EFFECT_IDS))

# =====================================================================
# Part 4 - stage 4 hardware-report fixes (test_via_eeprom.py)
# =====================================================================
if want('S4'):
    import test_via_eeprom
    test_via_eeprom.run(dict(mk=mk, check=check, T=T, report=report, ram32=ram32, IMG=IMG, FW=FW, EFFECT_IDS=EFFECT_IDS,
                      only=[o for o in ONLY if o.startswith('S4')] if ONLY else None))

json.dump(results, open(OUT + '/results.json', 'w'), indent=1)
open(OUT + '/trace.txt', 'w', encoding='utf-8').write('\n'.join(trace) + '\n')
print('\nSUMMARY: %d PASS, %d FAIL' % (sum(r['result'] == 'PASS' for r in results), sum(r['result'] == 'FAIL' for r in results)))
