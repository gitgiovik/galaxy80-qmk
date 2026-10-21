"""WP-C4 gaming/keys: Snap Click modes, OS shortcut keycodes, brightness down to off / up back on,
Chordal Hold + Flow Tap (design 2.2 P1-1, P1-3, P1-4, P1-6).
usage: python suite_gaming.py <fw_dir>   (epomaker_galaxy80_<k>.bin + nm_<k>.txt, k = via, default)
Prints PASS / FAIL lines. The existing SOCD tests (test_features.py S3-SOCD*, S3-GAME) are not repeated.

S-SNAP-default   after boot the mode is Off: A+D are both reported (nothing resolved); Off + Fn+S = Last
S-SNAP-<mode>    Last / First / Second / Neutral, 6KRO and NKRO, WASD swap on and off: both keys held,
                 one released, re-press while the other is held; never both keys of a pair in a report
S-SNAP-misc      per-pair mask, pairs independent, invalid mode = Off, game mode + mode
S-BRI            brightness steps 255..0, flash at both ends, up back on after RM_TOGG off
S-OS-*           Mission Control / Launchpad (consumer usages), Siri (Cmd+Space held 500 ms), Task View,
                 File Explorer, Snip report sequences, Win-lock blocks the Win chords, VIA JSON fragment
S-TAP-*          Chordal Hold (same hand = tap, other hand = hold) and Flow Tap, MT keys set through VIA
"""
import gc, json, os, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

fw = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__))
KBD = os.environ.get('G80_KBD') or os.path.normpath(os.path.join(HERE, '..', '..', 'keyboards', 'epomaker', 'galaxy80'))
FN, S_, A_, D_, W_, N_, PAUSE, LSFT, E_, J_ = (5, 11), (3, 2), (3, 1), (3, 3), (2, 2), (4, 7), (0, 16), (4, 0), (2, 3), (3, 7)
FN_UP, FN_DOWN, FN_INS, FN_LWIN = (4, 15), (5, 15), (1, 14), (5, 1)
HID = dict(A=0x04, D=0x07, E=0x08, J=0x0D, S=0x16, W=0x1A, TAB=0x2B, SPACE=0x2C, UP=0x52, DOWN=0x51, LEFT=0x50, RIGHT=0x4F)
LGUI, LSHIFT, LCTL = 0x08, 0x02, 0x01
results = []


def check(tid, name, ok, detail):
    results.append(ok)
    print('%s  %-16s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail), flush=True)


def mkq(kind, stress=False):
    """stress=True for the tests that write the VIA EEPROM (set_kc): the flash content then reaches unicorn
    between run() chunks, not from inside a hook (g80emu code_write; unicorn crashes on the latter)"""
    gc.collect()  # unicorn: free the previous engine first (callers set m = None)
    m = G80(fw + '/epomaker_galaxy80_%s.bin' % kind, fw + '/nm_%s.txt' % kind, stress=stress)
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.schedule = []
    return m


def rd(m, sym, n):
    return bytes(m.uc.mem_read(m.sym[sym], n))


def set_snap(m, mode):
    """Snap Click mode = g80_ucfg.snap_mode (bits 5..7 of eeconfig_user, RAM copy)."""
    b = rd(m, 'g80_ucfg', 1)[0]
    wr(m, 'g80_ucfg', [(b & 0x1F) | ((mode & 7) << 5)])


def wr(m, sym, data):
    m.uc.mem_write(m.sym[sym], bytes(data))


def ram32(m, a):
    return struct.unpack('<I', m.uc.mem_read(a, 4))[0]


def report(m):
    """(mods, set of HID codes) from both the 6KRO and the NKRO report structures"""
    keys = set()
    mods = 0
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


def at(m, t):
    m.run(t)
    return report(m)


def keys_at(m, t):
    return sorted(at(m, t)[1])


def press(m, t, keys, dur=20):
    m.schedule += [(t, t + dur, k) for k in keys]


def fn_combo(m, t, key, dur=20):
    press(m, t, [FN], 40 + dur)
    press(m, t + 10, [key], dur)


def cfg(m):
    return struct.unpack('<I', rd(m, 'g80_config', 4))[0]


def set_cfg_bit(m, bit, on):
    c = cfg(m)
    c = (c | (1 << bit)) if on else (c & ~(1 << bit))
    wr(m, 'g80_config', struct.pack('<I', c))


def raw(m, pkt):
    buf = m.sym['__heap_base__']
    m.uc.mem_write(buf, (bytes(pkt) + b'\x00' * 32)[:32])
    m.call('raw_hid_receive', buf, 32, max_insn=60000000)
    return bytes(m.uc.mem_read(buf, 32))


def set_kc(m, row, col, kc, layer=0):
    """VIA dynamic_keymap_set_keycode (0x05). The firmware defers VIA EEPROM writes to the main loop
    (g80_via_task, V-4) and answers only once they are in flash; like the VIA app, wait for that
    before the next command (a command sent earlier is answered busy)."""
    raw(m, [0x05, layer, row, col, kc >> 8, kc & 0xFF])
    t_end = m.t_ms() + 1000
    while rd(m, 'g80_vq_busy', 1)[0] and m.t_ms() < t_end:
        m.run(m.t_ms() + 2)


def consumer(m):
    return struct.unpack('<H', rd(m, 'last_consumer_usage', 2))[0]


def enum_keycodes():
    """name -> keycode of enum galaxy80_keycodes (QK_KB_0 = 0x7E00 + index), parsed from galaxy80.h"""
    s = open(os.path.join(KBD, 'galaxy80.h'), encoding='utf-8').read()
    body = s[s.index('enum galaxy80_keycodes'):]
    body = body[body.index('{') + 1:body.index('};')]
    body = re.sub(r'/\*.*?\*/', '', body, flags=re.S)
    names = [x.split('=')[0].strip() for x in body.split(',') if x.strip()]
    return {n: 0x7E00 + i for i, n in enumerate(names)}


# ---------------------------------------------------------------------------------------------
# Snap Click
# ---------------------------------------------------------------------------------------------
SC_OFF, SC_LAST, SC_FIRST, SC_SECOND, SC_NEUTRAL = 0, 1, 2, 3, 4
MODE_NAMES = {SC_LAST: 'Last', SC_FIRST: 'First', SC_SECOND: 'Second', SC_NEUTRAL: 'Neutral'}


def expect_pair(mode, lo_held, hi_held, last_hi):
    """keys expected for a pair, as ('lo'|'hi') list"""
    if lo_held and hi_held:
        if mode == SC_FIRST:
            return ['lo']
        if mode == SC_SECOND:
            return ['hi']
        if mode == SC_NEUTRAL:
            return []
        return ['hi'] if last_hi else ['lo']
    return (['lo'] if lo_held else []) + (['hi'] if hi_held else [])


def run_snap_script(m, t0, klo, khi, hlo, hhi):
    """A (lo) down t0, D (hi) down +50, D up +100, D down +150, D up +200, A up +250. Samples after each event.
    Returns the list of key lists at t0+30 / +75 / +125 / +175 / +225 / +270."""
    m.schedule += [(t0, t0 + 250, klo), (t0 + 50, t0 + 100, khi), (t0 + 150, t0 + 200, khi)]
    out = []
    for dt in (30, 75, 125, 175, 225, 270):
        k = keys_at(m, t0 + dt)
        out.append(sorted(x for x in k if x in (hlo, hhi)))
    return out


def expected_script(mode, first_is_hi):
    """expected samples for the script, first key = the one held the whole time"""
    exp = []
    held = [(True, False), (True, True), (True, False), (True, True), (True, False), (False, False)]
    # the second key is the one re-pressed: 'last' = second key whenever both are held
    res = []
    for (f, s) in held:
        if f and s:
            # both: last pressed = the second key
            if mode in (SC_LAST, SC_OFF):
                res.append(['second'])
            elif mode == SC_NEUTRAL:
                res.append([])
            else:
                res.append(['lo' if mode == SC_FIRST else 'hi'])
        else:
            res.append((['first'] if f else []) + (['second'] if s else []))
    # map to concrete keys: first key is hi or lo
    out = []
    for r in res:
        o = []
        for x in r:
            if x == 'first':
                o.append('hi' if first_is_hi else 'lo')
            elif x == 'second':
                o.append('lo' if first_is_hi else 'hi')
            else:
                o.append(x)
        out.append(sorted(o))
    return out


def snap_tests():
    # --- default Off ---
    m = None
    m = mkq('default')
    m.run(300)
    mode0 = rd(m, 'g80_ucfg', 1)[0] >> 5
    press(m, 400, [A_, D_], 100)
    both = keys_at(m, 450)
    after = keys_at(m, 560)
    fn_combo(m, 700, S_)  # Fn+S: SOCD on, mode Off -> resolves as Last
    m.schedule += [(800, 1000, A_), (850, 950, D_)]
    last_d = keys_at(m, 900)
    last_a = keys_at(m, 975)
    check('S-SNAP-default', 'mode is Off after boot: A+D both reported; Fn+S on with mode Off = Last input wins',
          mode0 == 0 and both == [HID['A'], HID['D']] and after == [] and last_d == [HID['D']] and last_a == [HID['A']],
          'mode=%d | A+D: %s | after: %s | Fn+S: D pressed last: %s, D released: %s' % (mode0, both, after, last_d, last_a))
    m = None

    # --- every mode x 6KRO/NKRO x swap ---
    for mode in (SC_LAST, SC_FIRST, SC_SECOND, SC_NEUTRAL):
        bad = []
        nfail = 0
        for nkro in (True, False):
            for swap in (False, True):
                m = None
                m = mkq('default')
                m.run(250)
                t = 260
                if not nkro:
                    fn_combo(m, t, N_)
                    t += 60
                if swap:
                    fn_combo(m, t, (3, 5))  # Fn+G: WASD <-> arrows
                    t += 60
                m.run(t + 40)
                set_snap(m, mode)
                if swap:
                    # physical W / S -> outputs Down / Up? (output = source ^ 4: W->Up, S->Down)
                    lo_key, hi_key, hlo, hhi = W_, S_, HID['UP'], HID['DOWN']
                else:
                    lo_key, hi_key, hlo, hhi = A_, D_, HID['A'], HID['D']
                base = t + 100
                got1 = run_snap_script(m, base, lo_key, hi_key, hlo, hhi)           # lo first, hi second
                e1 = expected_script(mode, False)
                got2 = run_snap_script(m, base + 400, hi_key, lo_key, hhi, hlo)     # hi first, lo second
                e2 = expected_script(mode, True)
                conv = lambda lst: [[{'lo': hlo, 'hi': hhi}[x] for x in s_] for s_ in lst]
                e1c, e2c = [sorted(x) for x in conv(e1)], [sorted(x) for x in conv(e2)]
                both_ever = any(hlo in g and hhi in g for g in got1 + got2)
                ok = got1 == e1c and got2 == e2c and not both_ever and not m.resets and not m.faults
                if not ok:
                    nfail += 1
                    bad.append('nkro=%d swap=%d got1=%s exp1=%s got2=%s exp2=%s' % (nkro, swap, got1, e1c, got2, e2c))
                m = None
        check('S-SNAP-' + MODE_NAMES[mode], 'Snap Click %s: A then D / D then A, release and re-press while the other is held (6KRO + NKRO, '
              'with and without WASD swap); never both keys of a pair' % MODE_NAMES[mode], nfail == 0,
              'failing combinations: %d %s' % (nfail, bad[:2]))

    # --- misc: pair mask, independence, invalid mode, game mode ---
    m = mkq('default')
    m.run(300)
    set_snap(m, SC_NEUTRAL)
    set_cfg_bit(m, 11, True)  # socd_pair_off bit 1 (A/D) -> A/D not resolved
    m.schedule += [(400, 600, A_), (420, 580, D_), (400, 600, W_), (420, 580, S_)]
    ad_w = keys_at(m, 500)
    set_cfg_bit(m, 11, False)
    m.schedule += [(700, 900, A_), (720, 880, D_), (730, 880, W_)]  # W alone while A+D neutral
    ind = keys_at(m, 800)
    # all three of W, A, D then S: W/S neutral -> none of W/S; A/D neutral -> none
    m.schedule += [(1000, 1200, A_), (1010, 1190, D_), (1020, 1190, W_), (1030, 1190, S_)]
    quad = keys_at(m, 1100)
    set_snap(m, 7)  # invalid = Off, SOCD switch off -> nothing resolved
    m.schedule += [(1300, 1400, A_), (1310, 1390, D_)]
    inv = keys_at(m, 1350)
    check('S-SNAP-misc', 'pair mask (A/D off) leaves A+D both while W/S neutral; pairs are independent (W alone with A+D neutral); '
          'invalid mode 7 = Off', ad_w == [HID['A'], HID['D']] and ind == [HID['W']] and quad == [] and inv == [HID['A'], HID['D']] and not m.faults,
          'mask: %s | independent: %s | W,A,D,S neutral: %s | invalid: %s' % (ad_w, ind, quad, inv))
    m = None
    m = mkq('default')
    m.run(300)
    set_snap(m, SC_FIRST)
    fn_combo(m, 400, PAUSE)  # game mode with mode First: D pressed last, A (first key) wins
    m.schedule += [(500, 700, A_), (550, 650, D_)]
    g = keys_at(m, 600)
    check('S-SNAP-game', 'game mode + Snap Click First: A wins over a later D', g == [HID['A']], 'keys: %s' % g)
    m = None


# ---------------------------------------------------------------------------------------------
# Brightness
# ---------------------------------------------------------------------------------------------
def brightness_tests():
    m = mkq('default')
    m.run(300)

    def state():
        c = rd(m, 'rgb_matrix_config', 6)
        return c[0] & 3, c[3]  # enable, val

    def flash():
        return rd(m, 'g80_fl', 1)[0]  # target

    en0, v0 = state()
    seq, flashes = [], []
    t = 400
    for _ in range(6):  # 5 down presses: 191 127 63 0, 0 (flash)
        fn_combo(m, t, FN_DOWN)
        m.run(t + 60)
        seq.append(state()[1])
        flashes.append(flash())
        t += 80
    m.run(t + 3000)  # flash over
    down = seq
    # a flash is shown at the limit only
    ok_down = down[:4] == [191, 127, 63, 0] and down[4:] == [0, 0] and flashes[:4] == [0, 0, 0, 0] and flashes[4] != 0
    seq2, fl2 = [], []
    t = m.t_ms() + 100
    for _ in range(6):  # up: 63 127 191 255, 255 (flash)
        fn_combo(m, t, FN_UP)
        m.run(t + 60)
        seq2.append(state()[1])
        fl2.append(flash())
        t += 80
    m.run(t + 3000)
    ok_up = seq2[:4] == [64, 128, 192, 255] and seq2[4:] == [255, 255] and fl2[:4] == [0, 0, 0, 0] and fl2[4] != 0
    en1, v1 = state()
    # RM_TOGG off, then brightness up switches back on without changing the level
    t = m.t_ms() + 100
    fn_combo(m, t, FN_DOWN); fn_combo(m, t + 80, FN_DOWN)  # 255 -> 191 -> 127
    fn_combo(m, t + 160, FN_INS)
    m.run(t + 250)
    en_off, v_off = state()
    fn_combo(m, t + 300, FN_DOWN)       # while off: limit flash, no change
    m.run(t + 360)
    en_off2, v_off2 = state()
    fn_combo(m, t + 400, FN_UP)
    m.run(t + 460)
    en_on, v_on = state()
    # off at level 0, up switches on and lights the first level
    t = m.t_ms() + 3000
    for i in range(4):
        fn_combo(m, t + 80 * i, FN_DOWN)
    fn_combo(m, t + 400, FN_INS)
    m.run(t + 500)
    en_z, v_z = state()
    fn_combo(m, t + 600, FN_UP)
    m.run(t + 700)
    en_z2, v_z2 = state()
    # Shift inverts (Shift+Fn+Up = down)
    t = m.t_ms() + 100
    press(m, t, [LSFT], 200)
    fn_combo(m, t + 20, FN_UP)
    m.run(t + 150)
    en_s, v_s = state()
    ok = (en0 == 1 and v0 == 255 and ok_down and ok_up and en1 == 1 and v1 == 255
          and (en_off, v_off) == (0, 127) and (en_off2, v_off2) == (0, 127) and (en_on, v_on) == (1, 127)
          and (en_z, v_z) == (0, 0) and (en_z2, v_z2) == (1, 64) and v_s == 0 and not m.faults)
    check('S-BRI', 'brightness: down 255/191/127/63/0 (QMK step 64, saturating) with a flash only at the limit, up 64/128/192/255 (flash at the top); Fn+Ins off then up = on at the '
          'same level, down while off = no change; off at level 0, up = first level; Shift inverts', ok,
          'start=%d/%d down=%s fl=%s up=%s fl=%s | toggle off=%s,%s while off down=%s,%s up=%s,%s | zero: off=%s,%s up=%s,%s | shift+up=%s,%s' % (
              en0, v0, down, flashes, seq2, fl2, en_off, v_off, en_off2, v_off2, en_on, v_on, en_z, v_z, en_z2, v_z2, en_s, v_s))
    m = None


# ---------------------------------------------------------------------------------------------
# OS keycodes (VIA image)
# ---------------------------------------------------------------------------------------------
def os_tests():
    kc = enum_keycodes()
    names = ['G80_MCTRL', 'G80_LNPAD', 'G80_SIRI', 'G80_TASK', 'G80_FILE', 'G80_SNIP']
    pos = {'G80_MCTRL': (0, 11), 'G80_LNPAD': (0, 12), 'G80_SIRI': (0, 13), 'G80_TASK': (0, 14), 'G80_FILE': (0, 15), 'G80_SNIP': (0, 16)}
    # VIA JSON: the 6 OS keycodes sit in customKeycodes at their enum index (keycode - QK_KB_0)
    frag_path = os.path.join(KBD, 'via_json', 'galaxy80.json')
    try:
        ck = json.load(open(frag_path, encoding='utf-8'))['customKeycodes']
        frag = [ck[kc[n] - 0x7E00] for n in names]
        short = ['MCtrl', 'LPad', 'Siri', 'Task', 'Files', 'Snip']
        frag_ok = [f.get('shortName') for f in frag] == short and all(f.get('name') and f.get('title') for f in frag)
        order_ok = [kc[n] for n in names] == sorted(kc[n] for n in names) and kc['G80_SNIP'] - kc['G80_MCTRL'] == 5
    except Exception as e:  # noqa
        frag_ok, order_ok = False, False
        print('fragment error: %s' % e)
    check('S-OS-json', 'VIA JSON customKeycodes hold the 6 OS keycodes (name, title, shortName) at their enum index, consecutive in the enum',
          frag_ok and order_ok, 'fragment=%s codes=%s' % (frag_path, {n: hex(kc[n]) for n in names}))

    m = mkq('via', stress=True)
    m.run(400)
    for n in names:
        set_kc(m, *pos[n], kc[n])
    m.run(450)
    t = 500
    # consumer keys
    press(m, t, [pos['G80_MCTRL']], 100)
    m.run(t + 50)
    c1d = consumer(m)
    m.run(t + 150)
    c1u = consumer(m)
    t = 800
    press(m, t, [pos['G80_LNPAD']], 100)
    m.run(t + 50)
    c2d = consumer(m)
    m.run(t + 150)
    c2u = consumer(m)
    check('S-OS-mac-consumer', 'Mission Control = consumer 0x29F while held, 0 after; Launchpad = 0x2A0',
          c1d == 0x29F and c1u == 0 and c2d == 0x2A0 and c2u == 0, 'MCTRL %#x -> %#x, LNPAD %#x -> %#x' % (c1d, c1u, c2d, c2u))

    # Siri: Cmd+Space held 500 ms after a short tap; a second press extends
    t = 1100
    press(m, t, [pos['G80_SIRI']], 30)
    s_before = at(m, t - 10)
    s_early = at(m, t + 100)   # key already up, still held
    s_mid = at(m, t + 450)
    s_end = at(m, t + 650)
    t = 1900
    press(m, t, [pos['G80_SIRI']], 30)
    press(m, t + 300, [pos['G80_SIRI']], 30)
    s2_a = at(m, t + 250)
    s2_b = at(m, t + 600)    # 500 ms after the first press would be over; the second extends to t+800
    s2_c = at(m, t + 1000)
    check('S-OS-siri', 'Siri key: Cmd+Space registered on press, kept ~500 ms after a short tap, released afterwards; a second press extends the hold',
          s_before == (0, set()) and s_early == (LGUI, {HID['SPACE']}) and s_mid == (LGUI, {HID['SPACE']}) and s_end == (0, set())
          and s2_a == (LGUI, {HID['SPACE']}) and s2_b == (LGUI, {HID['SPACE']}) and s2_c == (0, set()),
          'before=%s +100=%s +450=%s +650=%s | double: %s %s %s' % (s_before, s_early, s_mid, s_end, s2_a, s2_b, s2_c))

    # Windows chords
    t = 3200
    res = {}
    for n, exp in (('G80_TASK', (LGUI, {HID['TAB']})), ('G80_FILE', (LGUI, {HID['E']})), ('G80_SNIP', (LGUI | LSHIFT, {HID['S']}))):
        press(m, t, [pos[n]], 100)
        d = at(m, t + 50)
        u = at(m, t + 150)
        res[n] = (d, u, d == exp and u == (0, set()))
        t += 300
    check('S-OS-win', 'Task View = Win+Tab, File Explorer = Win+E, Snip = Win+Shift+S while held, all released after',
          all(v[2] for v in res.values()), str({k: v[:2] for k, v in res.items()}))

    # Win-lock (Fn+LWin): Win chords and Siri are blocked; the release does not release a real key
    t = 4200
    fn_combo(m, t, FN_LWIN)
    m.run(t + 100)
    press(m, t + 200, [E_], 400)                  # a real E held
    press(m, t + 250, [pos['G80_FILE']], 100)     # File Explorer blocked: no Win, no extra E release
    blk_file = at(m, t + 300)
    blk_after = at(m, t + 400)
    press(m, t + 700, [pos['G80_TASK']], 100)
    blk_task = at(m, t + 750)
    press(m, t + 900, [pos['G80_SIRI']], 50)
    blk_siri = at(m, t + 950)
    blk_siri_end = at(m, t + 1700)
    check('S-OS-winlock', 'Win-lock on: Win+E / Win+Tab / Siri produce nothing, and the released blocked key does not release a held real E',
          blk_file == (0, {HID['E']}) and blk_after == (0, {HID['E']}) and blk_task == (0, set()) and blk_siri == (0, set()) and blk_siri_end == (0, set()),
          'file=%s after-release=%s task=%s siri=%s' % (blk_file, blk_after, blk_task, blk_siri))
    check('S-OS-stable', 'no reset or fault during the OS keycode tests', not m.resets and not m.faults, 'resets=%s faults=%s' % (m.resets, m.faults))
    m = None


# ---------------------------------------------------------------------------------------------
# Chordal Hold + Flow Tap (VIA image, MT keys set through VIA)
# ---------------------------------------------------------------------------------------------
def tap_tests():
    MT_A = 0x2000 | (LCTL << 8) | HID['A']   # MT(MOD_LCTL, KC_A) on key A (r3c1)
    m = mkq('via', stress=True)
    m.run(400)
    set_kc(m, 3, 1, MT_A)
    m.run(450)
    # 1. same hand: A (MT) held, S (left hand) tapped inside the tapping term -> tap 'a' then 's', no Ctrl ever
    t = 600
    press(m, t, [A_], 150)
    press(m, t + 30, [S_], 40)
    seen = []
    for dt in range(0, 260, 5):
        seen.append(at(m, t + dt))
    mods_seen = {x[0] for x in seen}
    keys_seen = set().union(*[x[1] for x in seen])
    check('S-TAP-chord-same', 'Chordal Hold: MT(Ctrl,A) + S (same hand) inside the tapping term = a, s taps, never Ctrl',
          LCTL not in mods_seen and HID['A'] in keys_seen and HID['S'] in keys_seen, 'mods seen=%s keys seen=%s' % (mods_seen, sorted(keys_seen)))
    # 2. other hand: A (MT) + J (right hand) pressed and released inside the tapping term -> Ctrl+J
    t = 1200
    press(m, t, [A_], 150)
    press(m, t + 30, [J_], 60)
    seen = []
    for dt in range(0, 260, 5):
        seen.append(at(m, t + dt))
    ctrl = [x for x in seen if x[0] & LCTL]
    a_tap = [x for x in seen if HID['A'] in x[1]]
    check('S-TAP-chord-other', 'Chordal Hold: MT(Ctrl,A) + J (other hand) = hold (Ctrl), no "a"', bool(ctrl) and not a_tap,
          'Ctrl samples=%d, "a" samples=%d' % (len(ctrl), len(a_tap)))
    # 3. hold alone: Ctrl after the tapping term, no key
    t = 2000
    press(m, t, [A_], 600)
    h_early = at(m, t + 100)
    h_late = at(m, t + 400)
    check('S-TAP-hold', 'MT(Ctrl,A) held alone: nothing before the tapping term (200 ms), Ctrl after', h_early == (0, set()) and h_late == (LCTL, set()),
          'at 100 ms=%s at 400 ms=%s' % (h_early, h_late))
    # 4. Flow Tap: S then A within 150 ms -> A is a tap at once (before the tapping term), held or not
    t = 3000
    press(m, t, [S_], 40)
    press(m, t + 100, [A_], 600)        # 60 ms after S was released: inside FLOW_TAP_TERM (150)
    f_a = at(m, t + 140)
    f_b = at(m, t + 500)
    check('S-TAP-flow', 'Flow Tap: A (MT Ctrl) pressed 100 ms after S is a tap at once (a, no Ctrl), also when kept held',
          HID['A'] in f_a[1] and f_a[0] & LCTL == 0 and f_b[0] & LCTL == 0, 'at +40 ms=%s at +400 ms=%s' % (f_a, f_b))
    # 5. no previous key for > 150 ms: normal tap-hold (hold after 200 ms)
    t = 4500
    press(m, t, [S_], 40)
    press(m, t + 400, [A_], 600)
    n_a = at(m, t + 500)
    n_b = at(m, t + 800)
    check('S-TAP-noflow', 'Flow Tap off when the previous key is older than FLOW_TAP_TERM: A (MT Ctrl) held 400 ms = Ctrl',
          n_a == (0, set()) and n_b == (LCTL, set()), 'at +100 ms=%s at +400 ms=%s' % (n_a, n_b))
    check('S-TAP-stable', 'no reset or fault during the tap-hold tests', not m.resets and not m.faults, 'resets=%s faults=%s' % (m.resets, m.faults))
    m = None


snap_tests()
brightness_tests()
os_tests()
tap_tests()
print('suite_gaming: %d PASS, %d FAIL' % (sum(results), len(results) - sum(results)))
