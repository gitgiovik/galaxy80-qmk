"""Galaxy80 QMK stage 4: emulator tests for the fixes from the first hardware test report.
Called from run_tests.py (Part 4, prefix S4) with the shared helpers; can also run alone:
    python test_via_eeprom.py <fw_dir> [out_dir] [--only=S4-PAINT,...]
1. VIA json: ASCII only, short effect names, accepted by VIA's own reader package (@the-via/reader,
   node + via_reader/ if present), "li" on every key = rgb_matrix LED of that matrix position.
2. VIA key painter (channel 0 id 1, [index, count, hue, sat]) and editor brush; painter starts from the
   colours on the keys; deferred save; persistence.
3. Stress: hundreds of VIA commands (every id, lighting channel, painter, editor switch) mixed with
   keys; rapid editor enter/exit; SOCD fuzz. Afterwards keys still reach the host, no fault/reset,
   no flash write while VIA is busy, stacks within limits, main loop never starved.
   Watchdog: no feed can keep a hang alive (stuck flash controller during a save, stale USB suspend
   with SOF traffic, hung main loop after a VIA storm); static audit of every feed site.
   Editor indication on the side bar (magenta blink).
4. Esc editable in the editor, exit only Fn+K / VIA switch.
5. Editor / painter snapshot of the running effect (solid purple), also with key backlight off;
   2-byte colour fidelity; editor from "off" keeps the table.
Also: VIA EEPROM layout-version check (stage-3 EEPROM -> stage-4 image), RAM / EEPROM budgets."""
import os, sys, json, struct, tempfile, random, subprocess, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *
from unicorn import UC_HOOK_MEM_WRITE
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
FN, S_, A_, D_, W_, K_, ESC, G_, RSFT, LSFT, PAUSE, T_ = (5, 11), (3, 2), (3, 1), (3, 3), (2, 2), (3, 8), (0, 0), (3, 5), (4, 13), (4, 0), (0, 16), (2, 5)
VIA_JSON = (os.environ.get('G80_KBD') or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'keyboards', 'epomaker', 'galaxy80'))) + '/via_json/galaxy80.json'
NO_LED = 0xFF
LED_MAP = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16],
           [17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33],
           [34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50],
           [51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, NO_LED, 63, NO_LED, NO_LED, NO_LED],
           [64, NO_LED, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, NO_LED, 75, NO_LED, 76, NO_LED],
           [77, 78, 79, NO_LED, NO_LED, NO_LED, 80, NO_LED, NO_LED, NO_LED, 81, 82, 83, 84, 85, 86, 87],
           [NO_LED] * 12 + [88, 89, 90, 91, 92]]
POS = {v: (r, c) for r, row in enumerate(LED_MAP) for c, v in enumerate(row) if v != NO_LED}
NEIGHBOUR = {(3, 12): 63, (4, 1): 64, (4, 12): 75}
LED_A, LED_W, LED_ESC, BAR0 = 52, 36, 0, 88
HUES = [0, 11, 21, 32, 43, 64, 85, 106, 128, 149, 170, 191, 213, 234]
S3REF = os.path.join(HERE, 'ref')   # stage-3 VIA image + symbols (EEPROM upgrade test)
FEEDERS_OK = {'housekeeping_task_kb', 'process_record_kb', 'post_process_record_kb', 'via_command_kb',
              'suspend_power_down_kb', 'g80_wdt_kick', 'g80_wdt_start'}


def hdist(a, b):
    d = abs(a - b) & 0xFF
    return min(d, 256 - d)


def run(ctx):
    mk, check, T, report, IMG = ctx['mk'], ctx['check'], ctx['T'], ctx['report'], ctx['IMG']
    ids = ctx['EFFECT_IDS']
    only = ctx.get('only')
    PK, SOLID, UPDOWN = ids['G80_PER_KEY'], ids['SOLID_COLOR'], ids['CYCLE_UP_DOWN']

    def want(t):
        return not only or any(t.startswith(o) for o in only)

    def mkq(kind, **kw):
        m = None  # free the previous unicorn engine first
        m = mk(kind, **kw)
        m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
        m.schedule = []
        return m

    def press(m, t, keys, dur=20):
        m.schedule += [(t, t + dur, k) for k in keys]

    def fn_combo(m, t, key):
        press(m, t, [FN], 40)
        press(m, t + 10, [key], 20)

    def rd(m, sym, n, off=0):
        return bytes(m.uc.mem_read(m.sym[sym] + off, n))

    def cfg(m):
        return struct.unpack('<I', rd(m, 'g80_config', 4))[0]

    def raw(m, pkt):
        """one VIA packet through the real entry point (raw_hid_receive -> via_command_kb -> ...)"""
        buf = m.sym['__heap_base__']
        p = bytes(pkt) + b'\x00' * (32 - len(pkt))
        m.uc.mem_write(buf, p)
        m.call('raw_hid_receive', buf, 32, max_insn=60000000)
        return bytes(m.uc.mem_read(buf, 32))

    def pk(m, led):
        b = rd(m, 'g80_pk', 2, 2 * led)
        return b[0], b[1] >> 4, b[1] & 15          # hue, sat4, val4

    def code(m, led):
        h, s4, v4 = pk(m, led)
        if v4 == 0:
            return 0
        if s4 * 17 < 64:
            return 15
        return min(range(14), key=lambda i: hdist(h, HUES[i])) + 1

    def frame_rgb(m, led):
        f = rd(m, 'g80_frame', 21 * 17)
        r, c = POS[led]
        return f[r * 17 + c], f[(7 + r) * 17 + c], f[(14 + r) * 17 + c]

    def fw_rgb(m, led, val):
        x = m.call('g80_pk_rgb', led, val)
        return tuple(min(252, (x >> s) & 0xFF) for s in (0, 8, 16))

    def rgb_mode(m):
        return rd(m, 'rgb_matrix_config', 1)[0] >> 2

    def rgb_on(m):
        return rd(m, 'rgb_matrix_config', 1)[0] & 3

    def editing(m):
        return rd(m, 'g80_pk_editing', 1)[0]

    def solid(mm, h, s=255, v=255):
        mm.call('rgb_matrix_enable_noeeprom')
        mm.call('rgb_matrix_mode_eeprom_helper', SOLID, 0)
        mm.call('rgb_matrix_sethsv_eeprom_helper', h, s, v, 0)

    def power_cycle(m, kind, flash=None):
        fd, p = tempfile.mkstemp(suffix='.bin')
        os.write(fd, bytes(flash if flash is not None else m.flash))
        os.close(fd)
        m2 = G80(p, IMG[kind][1])
        m2.keep_reg_writes = m2.keep_col_reads = m2.keep_ct1_events = False
        m2.schedule = []
        os.unlink(p)
        return m2

    def do(m, t, fn):
        """run fn(m) at a clean thread-mode point at/after t (inject), return its result"""
        out = {}
        m.inject.append((t, lambda mm: out.__setitem__('r', fn(mm))))
        m.run(t + 6)
        return out.get('r')

    def stack_hook(m):
        lo = {'msp': 0x20000300, 'psp': 0x20000900}

        def hw(uc, access, addr, size, value, user):
            if addr < 0x20000300:
                if addr < lo['msp']:
                    lo['msp'] = addr
            elif addr < lo['psp']:
                lo['psp'] = addr
        m.uc.hook_add(UC_HOOK_MEM_WRITE, hw, begin=0x20000000, end=0x200008FF)
        return lo

    # ================================================================== 1. VIA json
    if want('S4-JSON'):
        raw_bytes = open(VIA_JSON, 'rb').read()
        bad = [(i, b) for i, b in enumerate(raw_bytes) if b >= 0x80]
        j = json.loads(raw_bytes.decode('ascii', errors='replace'))
        labels = []

        def walk(n):
            if isinstance(n, dict):
                if 'label' in n:
                    labels.append(n['label'])
                c = n.get('content')
                if n.get('type') == 'dropdown':
                    labels.extend(o[0] for o in n['options'])
                for v in n.values():
                    walk(v)
            elif isinstance(n, list):
                for v in n:
                    walk(v)
        walk(j['menus'])
        labels += [k[f] for k in j['customKeycodes'] for f in ('name', 'title', 'shortName')]
        odd = [l for l in labels if not re.fullmatch(r"[A-Za-z0-9 '()+/<>,.:;\-\n]*", l)]
        check('S4-JSON-ascii', 'VIA json: every byte is plain ASCII (no accents, arrows or symbols), every label / option / keycode '
              'name uses only letters, digits and basic punctuation',
              not bad and not odd, 'non-ASCII bytes=%d %s, odd labels=%s, labels checked=%d' % (len(bad), bad[:3], odd[:5], len(labels)))

        # VIA's own definition reader (the package the VIA app uses), when node + via_reader/ exist
        chk = os.path.join(HERE, 'via_reader', 'check_def.js')
        res = None
        try:
            out = subprocess.run(['node', chk, VIA_JSON], capture_output=True, text=True, timeout=120, cwd=os.path.join(HERE, 'via_reader'))
            line = [l for l in out.stdout.splitlines() if l.startswith('{')][-1]
            res = json.loads(line)
        except Exception as e:
            res = {'exception': 'node/via_reader unavailable: %s' % e}
        keys = (res.get('keys') or []) + (res.get('optionKeys') or [])
        wrong = [k for k in keys if k[2] != (LED_MAP[k[0]][k[1]] if LED_MAP[k[0]][k[1]] != NO_LED else NEIGHBOUR.get((k[0], k[1])))]
        types = []

        def walk2(n):
            if isinstance(n, dict):
                if 'type' in n and isinstance(n.get('content'), list):
                    types.append((n['type'], n['content'][1], n['content'][2]))
                for v in n.values():
                    walk2(v)
            elif isinstance(n, list):
                for v in n:
                    walk2(v)
        walk2(j['menus'])
        ch0 = [t for t in types if t[1] == 0]
        ok = (res.get('valid') is True and res.get('ok') and len(res.get('keys', [])) == 84 and len(keys) == 94 and not wrong
              and ('color-palette', 0, 20) in ch0 and not [t for t in ch0 if t[2] == 1] and ('dropdown', 0, 19) in ch0
              and sorted(t[2] for t in ch0) == list(range(2, 35)))
        check('S4-JSON-reader', 'VIA reader (@the-via/reader 1.14.4, node): definition valid; all 84 keys + 10 layout-option keys carry "li" = '
              'their rgb_matrix LED (ISO/JIS extras without LED: the neighbour LED); key painter = color-palette control; channel-0 id 1 '
              'left to the painter protocol; bar mode on id 19; custom ids 2..34', ok,
              'valid=%s exc=%s keys=%d (+options %d) wrong li=%s, channel-0 controls=%s' % (
                  res.get('valid'), res.get('exception'), len(res.get('keys', [])), len(res.get('optionKeys', [])), wrong[:4], sorted(ch0, key=lambda t: t[2])))

    # ================================================================== 2. VIA painter
    if want('S4-PAINT'):
        m = mkq('via')
        m.run(400)
        do(m, 400, lambda mm: solid(mm, 191))            # solid purple, full brightness
        m.run(450)
        purple_frame = frame_rgb(m, LED_W)
        n0 = len(m.flash_ops)
        g0 = do(m, 450, lambda mm: raw(mm, [0x08, 0, 1, LED_A, 1]))   # get A before (defaults: red)
        do(m, 458, lambda mm: (raw(mm, [0x07, 0, 1, LED_A, 1, 85, 255]), raw(mm, [0x09, 0])))  # paint A green + VIA commit
        m.run(520)
        unsaved_ops = len(m.flash_ops) - n0
        st = dict(mode=rgb_mode(m), A=pk(m, LED_A), W=pk(m, LED_W), ESC=pk(m, LED_ESC), snap=rd(m, 'g80_pk_snap', 1)[0])
        hue_keys = [pk(m, i) for i in range(88) if i != LED_A]
        snap_ok = all(hdist(h, 191) <= 3 and s4 == 15 and v4 == 15 for h, s4, v4 in hue_keys)
        g1 = do(m, 520, lambda mm: raw(mm, [0x08, 0, 1, LED_A, 2]))   # count 2: A and the next LED
        fa, fw = frame_rgb(m, LED_A), frame_rgb(m, LED_W)
        m.run(3400)
        early = m.flash_ops[n0:]                     # phase C (P0-9): painting alone never writes
        do(m, 3400, lambda mm: (raw(mm, [0x07, 0, 24, 1]), raw(mm, [0x09, 0])))   # VIA "Apply Key Colors"
        m.run(3700)
        ops = m.flash_ops[n0:]
        bad = [o for o in ops if not (0xF7C0 <= o[1] < 0xFFC0)]
        m2 = power_cycle(m, 'via')
        m2.run(450)
        st2 = dict(mode=rgb_mode(m2), A=pk(m2, LED_A), W=pk(m2, LED_W))
        ok = (g0[5:7] == bytes([0, 255]) and st['mode'] == PK and st['A'] == (85, 15, 15) and snap_ok and st['snap'] == 0
              and g1[5:7] == bytes([85, 255]) and hdist(g1[7], 191) <= 3 and fa == (0, 252, 0)
              and all(abs(a - b) <= 8 for a, b in zip(fw, purple_frame)) and ops and not early and not bad
              and st2['mode'] == PK and st2['A'] == (85, 15, 15) and hdist(st2['W'][0], 191) <= 3)
        check('S4-PAINT', 'VIA key painter (ch 0 id 1): with solid purple on, painting A green -> the other keys keep purple (snapshot), '
              'effect "Per tasto", A green on the LEDs; get [index, count] answers hue/sat at bytes 5..; no flash op until VIA Apply '
              '(id 24, phase C), then only 0xF7C0..0xFFBF; after a power cycle A green, W purple, "Per tasto"', ok,
              'get A before=%s, after: %s, keys with purple (+-3 hue, full sat/val)=%s, get(2)=%s, frame A=%s W=%s (solid was %s), '
              'flash ops=%d early=%d outside=%d, after power cycle %s' % (
                  list(g0[5:7]), st, snap_ok, list(g1[5:9]), fa, fw, purple_frame, len(ops), len(early), len(bad), st2))

        # painter "off" switch + brush
        # phase C: after the 2 green Apply flashes (2 x 300 ms from ~3.4 s)
        m.inject = [(4120, lambda mm: (raw(mm, [0x07, 0, 21, 1]), raw(mm, [0x07, 0, 1, LED_W, 1, 0, 255]), raw(mm, [0x07, 0, 21, 0])))]
        m.run(4180)
        w_off, fw_off = pk(m, LED_W), frame_rgb(m, LED_W)
        g21 = do(m, 4180, lambda mm: raw(mm, [0x08, 0, 21]))[3]
        check('S4-PAINT-off', 'VIA "Pittura: il clic spegne il tasto" (id 21): a painted key is switched off (value 0, LED dark); '
              'the switch reads back 0 afterwards', w_off[2] == 0 and fw_off == (0, 0, 0) and g21 == 0,
              'W=%s frame=%s id21=%d' % (w_off, fw_off, g21))

    # ================================================================== 4/5. editor: snapshot, Esc, bar indication
    if want('S4-EDIT'):
        # 5. snapshot of a solid purple, default image (no EEPROM)
        m = mkq('default')
        m.inject = [(250, lambda mm: solid(mm, 191))]
        m.run(320)
        before = {i: frame_rgb(m, i) for i in (0, LED_A, LED_W, 60, 87)}
        fn_combo(m, 330, K_)
        m.run(420)
        st = dict(ed=editing(m), mode=rgb_mode(m), snap=rd(m, 'g80_pk_snap', 1)[0])
        tab = [pk(m, i) for i in range(88)]
        snap_ok = all(hdist(h, 191) <= 3 and s4 == 15 and v4 == 15 for h, s4, v4 in tab)
        after = {i: frame_rgb(m, i) for i in before}
        close = all(all(abs(a - b) <= 8 for a, b in zip(before[i], after[i])) for i in before)
        press(m, 450, [A_])                              # violet (nearest preset) -> next: magenta
        press(m, 480, [ESC])                             # Esc: an ordinary key, gets the next preset
        m.run(520)
        a_after, esc_after, ed_after_esc = pk(m, LED_A), pk(m, LED_ESC), editing(m)
        s = []
        t = 530
        while t < 1100:                                  # side bar while editing: magenta / table colour every 256 ms
            m.run(t)
            s.append(frame_rgb(m, BAR0))
            t += 32
        mag = sum(1 for x in s if x == (0, 252, 252))    # magenta, R/G swapped in the frame
        tabc = sum(1 for x in s if x != (0, 252, 252))
        fn_combo(m, 1120, K_)                            # exit only with Fn+K
        m.run(1200)
        ed_end, mode_end = editing(m), rgb_mode(m)
        ok = (st['ed'] == 1 and st['mode'] == PK and st['snap'] == 0 and snap_ok and close and a_after == (213, 15, 15)
              and esc_after == (213, 15, 15) and ed_after_esc == 1 and mag >= 5 and tabc >= 5 and ed_end == 0 and mode_end == PK)
        check('S4-EDIT-snapshot', 'editor from a solid purple (default image): Fn+K copies what is on the keys into the table (88 keys hue 191 '
              '+-3, full sat/val), switches to "Per tasto" (LEDs unchanged within 8/252); A -> next preset (violet -> magenta); '
              'Esc is edited like any key and does NOT exit; bar alternates magenta / table colour every 256 ms; Fn+K exits', ok,
              'state=%s purple table=%s frames before=%s after=%s, A=%s Esc=%s editing after Esc=%d, bar samples magenta=%d other=%d, '
              'after Fn+K editing=%d mode=%d' % (st, snap_ok, before, after, a_after, esc_after, ed_after_esc, mag, tabc, ed_end, mode_end))

        # Esc in the editor never reaches the host; VIA switch also exits
        m = mkq('via')
        m.run(300)
        m.inject = [(310, lambda mm: mm.call('rgb_matrix_mode_eeprom_helper', PK, 0))]
        fn_combo(m, 330, K_)
        m.run(380)
        esc0 = pk(m, LED_ESC)
        leaked = set()
        press(m, 400, [ESC], 30)
        for tt in range(402, 440, 2):
            m.run(tt)
            leaked |= report(m)[1]
        ed1 = editing(m)
        esc1 = pk(m, LED_ESC)
        m.inject = [(450, lambda mm: raw(mm, [0x07, 0, 16, 0]))]
        m.run(470)
        ed2 = editing(m)
        press(m, 480, [ESC], 30)
        m.run(495)
        host_esc = 0x29 in report(m)[1]
        check('S4-EDIT-esc', 'editor: Esc changes its own colour (default red -> red-orange), nothing reaches the host, the editor stays on; '
              'VIA switch (id 16 = 0) exits; afterwards Esc reaches the host again',
              esc0 == (0, 15, 15) and esc1 == (11, 15, 15) and not leaked and ed1 == 1 and ed2 == 0 and host_esc,
              'Esc before=%s after=%s, host saw=%s, editing=%d -> VIA off -> %d, Esc after exit reported=%s' % (esc0, esc1, sorted(leaked), ed1, ed2, host_esc))

        # snapshot with the key backlight off (keys dark on purpose): the effect's real colours are captured
        m = mkq('default')
        m.inject = [(250, lambda mm: solid(mm, 85))]
        fn_combo(m, 300, (2, 5))                         # Fn+T: key backlight off
        m.run(360)
        dark = frame_rgb(m, LED_A)
        fn_combo(m, 380, K_)
        m.run(460)
        tab = [pk(m, i) for i in range(88)]
        ok_t = all(hdist(h, 85) <= 3 and v4 == 15 for h, s4, v4 in tab)
        lit = frame_rgb(m, LED_A)                        # the editor shows its table even with the backlight off
        # editor from "all LEDs off" (RM_TOGG): table kept, LEDs switched on with "Per tasto"
        m2 = mkq('default')
        m2.inject = [(250, lambda mm: mm.call('rgb_matrix_disable_noeeprom'))]
        fn_combo(m2, 300, K_)
        m2.run(380)
        st2 = dict(on=rgb_on(m2), mode=rgb_mode(m2), A=code(m2, LED_A), W=code(m2, LED_W), F=code(m2, 55))
        check('S4-EDIT-keysoff', 'snapshot with the key backlight off (Fn+T, keys dark): the table gets the effect colours (green), not black, '
              'and the editor shows them; editor from RGB off (all LEDs off): LEDs on, "Per tasto", stored table kept (defaults A/W red, F white)',
              dark == (0, 0, 0) and ok_t and lit[1] > 200 and st2 == dict(on=1, mode=PK, A=1, W=1, F=15),
              'A dark before=%s, table green=%s, A in editor=%s; from off: %s' % (dark, ok_t, lit, st2))

        # 2-byte fidelity: hues of the whole wheel survive snapshot -> table -> render
        m = mkq('default')
        errs = []
        for i, h in enumerate((5, 40, 100, 150, 200, 250)):
            t0 = 250 + i * 300
            m.inject = [(t0, lambda mm, h=h: solid(mm, h, 200, 255))]
            fn_combo(m, t0 + 40, K_)
            m.run(t0 + 140)
            hh, s4, v4 = pk(m, 60)
            errs.append((h, hh, s4, v4))
            fn_combo(m, t0 + 160, K_)
            m.run(t0 + 230)
        ok = all(hdist(h, hh) <= 3 and abs(s4 - round(200 * 15 / 255)) <= 1 and v4 == 15 for h, hh, s4, v4 in errs)
        check('S4-EDIT-fidelity', 'colour fidelity of the 2-byte table: solid colours at 6 hues (saturation 200) captured by the editor '
              'within +-3 hue steps of 256 and +-1 saturation step of 16 (stage 3: 14 hues only)', ok, 'hue in -> (hue, sat4, val4) stored: %s' % errs)

    # ================================================================== deferred VIA saves
    if want('S4-VIA-deferred'):
        m = mkq('via')
        m.run(400)
        n0 = len(m.flash_ops)

        def storm(mm):
            for k in range(100):
                raw(mm, [0x07, 0, 12, 1 + k % 10]); raw(mm, [0x09, 0])          # debounce slider drag
                raw(mm, [0x07, 3, 1, (k * 7) & 0xFF]); raw(mm, [0x09, 3])     # brightness slider drag
                raw(mm, [0x07, 3, 4, k & 0xFF, 255]); raw(mm, [0x09, 3])      # colour picker drag
        m.inject = [(420, storm)]
        m.run(3300)
        during = [o for o in m.flash_ops[n0:] if o[-1] < 420 + 2900]
        m.run(4200)
        after = m.flash_ops[n0:]
        val = rd(m, 'rgb_matrix_config', 4)[3]
        m2 = power_cycle(m, 'via')
        m2.run(450)
        v2, d2, h2 = rd(m2, 'rgb_matrix_config', 4)[3], rd(m2, 'g80_debounce_ms', 1)[0], rd(m2, 'rgb_matrix_config', 4)[1]
        check('S4-VIA-deferred', '600 VIA commands (100 x debounce slider + brightness slider + colour picker, each with its "save", channels 0 '
              'and 3): no flash write while VIA is busy and for 2.9 s after; then one save (few ops); values persist across a power cycle',
              not during and 0 < len(after) < 80 and v2 == val and d2 == 10 and h2 == 99,
              'flash ops during+2.9 s=%d, total after=%d, brightness %d -> after power cycle %d, debounce %d, hue %d' % (len(during), len(after), val, v2, d2, h2))

    # ================================================================== EEPROM layout version
    if want('S4-VIA-layout'):
        s3bin, s3nm = os.path.join(S3REF, 'epomaker_galaxy80_via_s3.bin'), os.path.join(S3REF, 'nm_via_s3.txt')
        if os.path.exists(s3bin):
            m3 = G80(s3bin, s3nm)
            m3.keep_reg_writes = m3.keep_col_reads = m3.keep_ct1_events = False
            m3.run(400)
            do(m3, 400, lambda mm: (mm.call('dynamic_keymap_set_keycode', 0, 2, 5, 0x05),     # stage 3 VIA: T -> B on layer 0
                                    mm.call('dynamic_keymap_set_keycode', 3, 2, 5, 0x06)))    # and something on layer 3
            m3.run(450)
            ee = bytes(m3.flash[0xF7C0:0xFFC0])
            img = bytearray(open(IMG['via'][0], 'rb').read())
            img[0xF7C0:0xFFC0] = ee
            m = power_cycle(None, 'via', flash=img)
            m.run(500)
            kc, lay, mac, ver = do(m, 500, lambda mm: (mm.call('dynamic_keymap_get_keycode', 0, 2, 5), mm.call('dynamic_keymap_get_layer_count'),
                                                       mm.call('dynamic_keymap_macro_get_buffer_size'), mm.call('via_eeprom_is_valid') & 0xFF))
            st = dict(T=kc, layers=lay, macro_bytes=mac, via_valid=ver, A=code(m, LED_A), F=code(m, 55))
            do(m, 510, lambda mm: mm.call('dynamic_keymap_set_keycode', 0, 2, 5, 0x05))       # stage 4 edit must survive the next boot
            m.run(550)
            m2 = power_cycle(m, 'via')
            m2.run(500)
            kc2 = do(m2, 500, lambda mm: mm.call('dynamic_keymap_get_keycode', 0, 2, 5))
            ok = kc == 0x17 and lay == 3 and mac >= 100 and ver == 1 and st['A'] == 1 and st['F'] == 15 and kc2 == 0x05
            check('S4-VIA-layout', 'VIA EEPROM layout check (via_init_kb): stage-3 EEPROM (4 layers, 4-bit colours, keymap edited) under the stage-4 '
                  'image -> keymaps reset to the defaults (T back), per-key defaults, 3 layers, macros >= 100 B, VIA magic valid; a stage-4 '
                  'keymap edit then survives the next power cycle (no reset loop)', ok,
                  'after upgrade %s; edited T=B -> after power cycle %#x' % (st, kc2))
        else:
            check('S4-VIA-layout', 'VIA EEPROM layout check', False, 'missing %s' % s3bin)

    # ================================================================== VIA colour drag while typing (the "glitch")
    if want('S4-VIA-drag'):
        D0 = 3000  # stage 5 backing store: first-boot EEPROM format takes longer with 2 ms stalls
        def drag(img_bin, img_nm):
            m = G80(img_bin, img_nm, flash_stall_us=2000)
            m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
            m.schedule = []
            m.run(D0 + 3500)                                   # first-boot EEPROM format with 2 ms stalls
            t0 = D0 + 3600
            n0 = len(m.flash_ops)
            for k in range(100):                          # 2 s of VIA colour-picker drag: set + save every 20 ms
                m.inject.append((t0 + 20 * k, lambda mm, k=k: (raw(mm, [0x07, 3, 4, (k * 3) & 255, 255]), raw(mm, [0x09, 3]))))
            keys = [(3, c) for c in range(1, 10)]
            hid = [0x04, 0x16, 0x07, 0x09, 0x0A, 0x0B, 0x0D, 0x0E, 0x0F]
            taps = [(t0 + 10 + 50 * k, hid[k % 9]) for k in range(40)]
            for k, (tp, _) in enumerate(taps):
                m.schedule.append((tp, tp + 30, keys[k % 9]))
            seen = [False] * len(taps)
            t = t0
            while t < t0 + 2100:
                m.run(t)
                r = report(m)[1]
                for i, (tp, h) in enumerate(taps):
                    if tp <= t <= tp + 60 and h in r:
                        seen[i] = True
                t += 1
            return len(m.flash_ops) - n0, seen.count(False), m.resets
        ops4, lost4, rs4 = drag(*IMG['via'])
        s3bin = os.path.join(S3REF, 'epomaker_galaxy80_via_s3.bin')
        ops3, lost3, rs3 = drag(s3bin, os.path.join(S3REF, 'nm_via_s3.txt')) if os.path.exists(s3bin) else (None, None, None)
        check('S4-VIA-drag', 'hardware report 3 ("glitch, no input" while changing things in VIA): 2 s of VIA colour-picker drag (set + save every '
              '20 ms, as the VIA app sends them) while typing 40 taps, every flash op stalling the CPU 2 ms. Stage 4: no flash op, no tap lost '
              '(stage 3 for comparison: a flash write per mouse move, wear-leveling consolidations, taps lost while the CPU is stalled)',
              ops4 == 0 and lost4 == 0 and not rs4 and (ops3 is None or (ops3 > 100 and lost3 > 0)),
              'stage 4: flash ops=%d taps lost=%d/40 resets=%s | stage 3: flash ops=%s taps lost=%s/40 resets=%s' % (ops4, lost4, rs4, ops3, lost3, rs3))

    # ================================================================== 3. stress
    if want('S4-STRESS'):
        rnd = random.Random(2026)
        m = mkq('via')
        m.run(400)
        lo = stack_hook(m)
        n0 = len(m.flash_ops)
        f0 = len(m.wdt_feeds)
        t = 420.0
        cmds = 0
        for i in range(700):
            kind = rnd.random()
            if kind < 0.45:
                vid = rnd.choice([2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20, 21])
                pkts = [[0x07, 0, vid, rnd.randrange(1, 13), rnd.randrange(256)], [0x09, 0]]
            elif kind < 0.6:
                pkts = [[0x08, 0, rnd.randrange(1, 23), rnd.randrange(93), 1]]
            elif kind < 0.75:
                pkts = [[0x07, 0, 1, rnd.randrange(93), 1, rnd.randrange(256), rnd.randrange(256)], [0x09, 0]]
            elif kind < 0.8:
                pkts = [[0x07, 0, 16, rnd.randrange(2)], [0x09, 0]]
            else:
                vid = rnd.choice([1, 2, 3, 4])
                vals = [rnd.randrange(256), rnd.randrange(256)] if vid == 4 else [rnd.randrange(30) if vid == 2 else rnd.randrange(256)]
                pkts = [[0x07, 3, vid] + vals, [0x09, 3]]
            cmds += len(pkts)
            m.inject.append((t, lambda mm, p=pkts: [raw(mm, x) for x in p]))
            if i % 5 == 0:
                press(m, t + 0.3, [(rnd.randrange(1, 4), rnd.randrange(1, 11))], 1 + rnd.random() * 3)
            t += 1.5
        hits = []
        tt = 420
        while tt < t + 100:
            h0 = m.main_loop_hits
            m.run(tt + 50)
            hits.append(m.main_loop_hits - h0)
            tt += 50
        during = [o for o in m.flash_ops[n0:] if o[-1] < t + 2900]
        m.inject = [(tt + 5, lambda mm: raw(mm, [0x07, 0, 16, 0]))]      # editor off (it may have been left on)
        m.run(tt + 3600)
        ops = m.flash_ops[n0:]
        press(m, tt + 3700, [T_], 40)
        m.run(tt + 3720)
        k = report(m)[1]
        c = cfg(m)
        feeders = set(f[1] for f in m.wdt_feeds[f0:])
        ok = (0x17 in k and not editing(m) and not m.resets and not m.faults and not m.violations and not during and len(ops) < 200
              and min(hits) >= 10 and 0x20000300 - lo['msp'] < 0x300 * 3 // 4 and 0x20000900 - lo['psp'] < 1536 * 3 // 4
              and (c & 15) < 13 and ((c >> 14) & 15) <= 10 and feeders <= FEEDERS_OK)
        check('S4-STRESS-via', 'stress: %d VIA packets in %.1f s (all channel-0 ids incl. painter get/set and editor switch, lighting channel, '
              'each set followed by its save) + random keys: no flash write during the storm and 2.9 s after, then one bounded save; T reaches '
              'the host afterwards; no fault / reset / violation; main loop >= 10 iterations in every 50 ms (never starved); MSP / PSP use < 75 %%; '
              'settings valid; watchdog fed only from the known sites' % (cmds, (t - 420) / 1000), ok,
              'T in report=%s, editing=%d, resets=%s faults=%s, flash ops during=%d after=%d, main-loop/50 ms min=%d max=%d, MSP used %d/768 B, '
              'PSP used %d/1536 B, cfg=%#x, feeders=%s' % (0x17 in k, editing(m), m.resets, m.faults[:2], len(during), len(ops), min(hits), max(hits),
                                                          0x20000300 - lo['msp'], 0x20000900 - lo['psp'], c, sorted(feeders)))

        # rapid editor enter / exit with keys in between, on-board and from VIA
        m = mkq('via')
        m.run(300)
        lo = stack_hook(m)
        t = 320
        for i in range(120):
            if i % 3 == 2:
                m.inject.append((t, lambda mm: raw(mm, [0x07, 0, 16, 1])))
            else:
                fn_combo(m, t, K_)
            for j in range(3):
                press(m, t + 45 + 8 * j, [(rnd.randrange(0, 5), rnd.randrange(0, 14))], 5)
            if i % 3 == 2:
                m.inject.append((t + 75, lambda mm: raw(mm, [0x07, 0, 16, 0])))
            else:
                fn_combo(m, t + 75, K_)
            t += 130
        m.run(t + 50)
        ed = editing(m)
        if ed:
            fn_combo(m, t + 60, K_)
        m.run(t + 3600)
        press(m, t + 3650, [T_], 40)
        m.run(t + 3670)
        k = report(m)[1]
        tab_ok = all((b >> 4) <= 15 for b in rd(m, 'g80_pk', 186)[1::2])
        check('S4-STRESS-edit', 'stress: 120 editor enter/exit cycles (2/3 Fn+K, 1/3 VIA switch) with keys in between: editor off at the end, T reaches '
              'the host, no stuck key, no fault/reset, stacks < 75 %',
              not editing(m) and k == {0x17} and not m.resets and not m.faults and 0x20000900 - lo['psp'] < 1152,
              'editing at end=%d (before final Fn+K %d), report=%s, resets=%s faults=%s, PSP used %d B, table ok=%s' % (
                  editing(m), ed, sorted(k), m.resets, m.faults[:2], 0x20000900 - lo['psp'], tab_ok))

        # SOCD fuzz: random WASD / arrow presses with SOCD, pair and swap toggles; all released -> nothing stuck
        m = mkq('via')
        m.run(300)
        dirs = [(2, 2), (3, 1), (3, 2), (3, 3), (4, 15), (5, 14), (5, 15), (5, 16)]
        t = 320
        for i in range(300):
            r_ = rnd.random()
            if r_ < 0.05:
                fn_combo(m, t, S_); t += 60
            elif r_ < 0.08:
                fn_combo(m, t, G_); t += 60
            elif r_ < 0.11:
                m.inject.append((t, lambda mm, v=rnd.randrange(8, 12), x=rnd.randrange(2): raw(mm, [0x07, 0, v, x]))); t += 5
            else:
                press(m, t, [rnd.choice(dirs)], rnd.randrange(3, 60)); t += rnd.randrange(1, 20)
        m.run(t + 200)
        k_end = report(m)[1]
        cnt = rd(m, 'g80_dir_cnt', 8)
        swapped = (cfg(m) >> 7) & 1                   # an odd number of Fn+G: A comes out as Left
        press(m, t + 250, [A_], 30)
        m.run(t + 265)
        ka = report(m)[1]
        check('S4-STRESS-socd', 'SOCD fuzz: 300 random WASD/arrow presses overlapped with Fn+S, Fn+G and VIA pair toggles; after all keys are '
              'released nothing is stuck (report empty, per-key counters 0); A then reports A (Left if the WASD swap ended on)',
              not k_end and not any(cnt) and ka == ({0x50} if swapped else {0x04}) and not m.faults,
              'report after release=%s counters=%s, WASD swap=%d, A -> %s' % (sorted(k_end), list(cnt), swapped, sorted(ka)))

    # ================================================================== watchdog: no feed keeps a hang alive
    if want('S4-WDT'):
        # (a) static audit: every call of g80_wdt_kick and every inlined feed (store to WDT_FEED) site
        elf = IMG['via'][0][:-4] + '.elf'
        objdump = os.environ.get('G80_OBJDUMP', 'arm-none-eabi-objdump')
        callers = set()
        cur = None
        try:
            dis = subprocess.run([objdump, '-d', elf], capture_output=True, text=True, timeout=120).stdout
            for line in dis.splitlines():
                mm_ = re.match(r'^[0-9a-f]+ <([^>]+)>:', line)
                if mm_:
                    cur = mm_.group(1)
                elif re.search(r'\bbl\s+[0-9a-f]+ <g80_wdt_kick>', line):
                    callers.add(cur)
        except Exception as e:
            callers = {'objdump failed: %s' % e}
        # phase C (CR-15): also the wear-leveling consolidation loops (32 sector erases, 258 line programs)
        check('S4-WDT-sites', 'static audit: g80_wdt_kick() is called only from g80_pk_store (bounded: index only grows, <= 94 runs), '
              'g80_config_save_now (straight line) and the consolidation loops backing_store_erase (32 sectors) / '
              'backing_store_write_bulk (258 lines); no loop that can spin forever contains a feed',
              callers <= {'g80_pk_store', 'g80_config_save_now', 'backing_store_erase', 'backing_store_write_bulk'} and callers,
              'callers=%s' % sorted(callers))

        # (b) flash controller stuck BUSY inside a deferred save, right after g80_wdt_kick: watchdog resets
        m = mkq('via')
        m.run(400)
        m.inject = [(420, lambda mm: raw(mm, [0x07, 0, 7, 1]))]        # SOCD on -> deferred save 3 s later
        hung = {}

        def stuck(mm):
            mm.fmc_busy_stuck = True
            hung['t'] = mm.t_ms()
        m.inject.append((3300, stuck))
        r = m.run(13500)
        wr = [x for x in m.resets if x[1] == 'WDT']
        last_feed = max((f[0] * 1e3 for f in m.wdt_feeds if f[0] * 1e3 < (wr[0][0] if wr else 1e9)), default=None)
        stuck_in = [f[1] for f in m.wdt_feeds if hung.get('t', 1e9) < f[0] * 1e3 < (wr[0][0] if wr else 1e9)]
        check('S4-WDT-flash', 'flash controller hung BUSY during the deferred save (the save fed the watchdog just before): the busy wait '
              'has no feed -> watchdog reset ~8.2 s after the last feed (not kept alive)',
              bool(wr) and last_feed is not None and 8000 < wr[0][0] - last_feed < 8500,
              'stuck from %.0f ms, last feed %.1f ms (%s), WDT reset at %s, feeds while hung=%s' % (
                  hung.get('t', -1), last_feed or -1, set(stuck_in[-1:]) or '-', wr[:1], len(stuck_in)))

        # (c) USB "suspended" but the host keeps sending SOFs (missed wake-up): feeding stops -> reset
        m = mkq('via')
        m.run(400)
        usbd = m.sym['USBD1']

        def susp(mm, live):
            mm.usb_frmno_live = live
            mm.uc.mem_write(usbd, b'\x05')
        m.inject = [(420, lambda mm: susp(mm, True))]
        m.run(10500)
        wr = [x for x in m.resets if x[1] == 'WDT']
        m2 = mkq('via')
        m2.run(400)
        usbd2 = m2.sym['USBD1']
        m2.inject = [(420, lambda mm: (setattr(mm, 'usb_frmno_live', False), mm.uc.mem_write(usbd2, b'\x05')))]
        m2.run(12500)
        fs = [f for f in m2.wdt_feeds if f[0] * 1e3 > 12000]
        check('S4-WDT-suspend', 'USB suspend loop: state SUSPENDED while SOF frames keep coming (stale suspend, missed wake-up) -> the loop '
              'stops feeding after ~0.5 s -> watchdog reset (~8.7 s); real suspend (no SOF) for 12 s -> still fed, no reset',
              bool(wr) and 8600 < wr[0][0] < 9400 and not m2.resets and fs and all(f[1] == 'suspend_power_down_kb' for f in fs),
              'stale: WDT reset at %s; real suspend: resets=%s, feeds after 12 s=%d by %s' % (wr[:1], m2.resets, len(fs), sorted(set(f[1] for f in fs))))

        # (d) main loop hung after a VIA storm (feeds from VIA commands / keys no longer happen): reset
        m = mkq('via')
        m.run(400)
        m.inject = [(420, lambda mm: [raw(mm, [0x07, 0, 12, 1 + k % 10]) for k in range(200)])]

        def hang(mm):
            mm.uc.mem_write(0x20001F00, b'\xfe\xe7')
            mm.uc.reg_write(UC_ARM_REG_PC, 0x20001F00)
        m.inject.append((500, hang))
        press(m, 600, [T_], 2000)
        r = m.run(9200)
        wr = [x for x in m.resets if x[1] == 'WDT']
        check('S4-WDT-hang', 'main loop hung (endless loop in thread context) right after 200 VIA commands, a key held during the hang: '
              'the LED ISR keeps running but nothing feeds -> watchdog reset at ~8.5 s -> normal reboot',
              bool(wr) and 8400 < wr[0][0] < 8800 and m.isp is None, 'WDT reset at %s, ISP=%s' % (wr[:1], m.isp is not None))

    # ================================================================== budgets
    if want('S4-BUDGET'):
        res = {}
        for kind in ('default', 'via'):
            syms = {}
            for line in open(IMG[kind][1]):
                p = line.split()
                if len(p) == 3:
                    syms[p[2]] = int(p[0], 16)
            res[kind] = dict(heap=syms['__heap_end__'] - syms['__heap_base__'], code_end=hex(max(syms['__textdata_base__'] + syms['__data_end__'] - syms['__data_base__'],
                                                                                                syms.get('__ram0_init_text__', 0))))
        m = mkq('via')
        m.run(400)
        mac, lay = do(m, 400, lambda mm: (mm.call('dynamic_keymap_macro_get_buffer_size'), mm.call('dynamic_keymap_get_layer_count')))
        ok = all(v['heap'] >= 512 and int(v['code_end'], 16) < 0xF7C0 for v in res.values()) and mac >= 100 and lay == 3
        check('S4-BUDGET', 'budgets: free RAM (heap) >= 512 B in default and via; VIA: 3 dynamic layers, macro buffer >= 100 B', ok,
              '%s; VIA layers=%d macro buffer=%d B' % (res, lay, mac))


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    FW = args[0]
    OUT = args[1] if len(args) > 1 else '.'
    only = None
    for a in sys.argv[1:]:
        if a.startswith('--only='):
            only = a[7:].split(',')
    IMG = {k: ('%s/epomaker_galaxy80_%s.bin' % (FW, k), '%s/nm_%s.txt' % (FW, k)) for k in ('default', 'via', 'stage1')}
    results = []

    def check(tid, name, ok, detail):
        results.append(dict(id=tid, name=name, result='PASS' if ok else 'FAIL', detail=detail))
        print('%s  %-17s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail), flush=True)

    def mk(kind, **kw):
        return G80(*IMG[kind], **kw)

    def ram32(m, a):
        return struct.unpack('<I', m.uc.mem_read(a, 4))[0]

    def report(m):
        keys, mods = set(), 0
        kp = m.sym.get('keyboard_report')
        if kp is not None:
            b = m.ram_read(ram32(m, kp), 8)
            mods |= b[0]
            keys |= {k for k in b[2:8] if k}
        np_ = m.sym.get('nkro_report')
        if np_ is not None:
            b = m.ram_read(ram32(m, np_), 32)
            mods |= b[1]
            for i, byte in enumerate(b[2:32]):
                for bit in range(8):
                    if byte >> bit & 1:
                        keys.add(i * 8 + bit)
        return mods, keys

    ids = json.load(open(os.path.join(HERE, 'via_effect_ids.json')))
    run(dict(mk=mk, check=check, T=print, report=report, ram32=ram32, IMG=IMG, FW=FW, EFFECT_IDS=ids, only=only))
    json.dump(results, open(os.path.join(OUT, 's4_results.json'), 'w'), indent=1)
    print('\nS4 SUMMARY: %d PASS, %d FAIL' % (sum(r['result'] == 'PASS' for r in results), sum(r['result'] == 'FAIL' for r in results)))
