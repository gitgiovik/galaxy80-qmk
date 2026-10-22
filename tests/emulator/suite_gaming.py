"""WP-C4 gaming/keys: Snap Click modes, brightness down to off / up back on, MT() hold from VIA
(design 2.2 P1-1, P1-3).
usage: python suite_gaming.py <fw_dir>   (epomaker_galaxy80_<k>.bin + nm_<k>.txt, k = via, default)
Prints PASS / FAIL lines. The existing SOCD tests (test_features.py S3-SOCD*, S3-GAME) are not repeated.

S-SNAP-default   after boot the mode is Off: A+D are both reported (nothing resolved); Off + Fn+S = Last
S-SNAP-<mode>    Last / First / Second / Neutral, 6KRO and NKRO, WASD swap on and off: both keys held,
                 one released, re-press while the other is held; never both keys of a pair in a report
S-SNAP-misc      per-pair mask, pairs independent, invalid mode = Off, game mode + mode
S-BRI            brightness steps 255..0, flash at both ends, up back on after RM_TOGG off
S-TAP-*          MT() key set through VIA: hold after the tapping term (200 ms)
"""
import gc, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

fw = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__))
FN, S_, A_, D_, W_, N_, PAUSE, LSFT = (5, 11), (3, 2), (3, 1), (3, 3), (2, 2), (4, 7), (0, 16), (4, 0)
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
# MT() tap-hold (VIA image, MT key set through VIA)
# ---------------------------------------------------------------------------------------------
def tap_tests():
    MT_A = 0x2000 | (LCTL << 8) | HID['A']   # MT(MOD_LCTL, KC_A) on key A (r3c1)
    m = mkq('via', stress=True)
    m.run(400)
    set_kc(m, 3, 1, MT_A)
    m.run(450)
    # hold alone: Ctrl after the tapping term, no key
    t = 2000
    press(m, t, [A_], 600)
    h_early = at(m, t + 100)
    h_late = at(m, t + 400)
    check('S-TAP-hold', 'MT(Ctrl,A) held alone: nothing before the tapping term (200 ms), Ctrl after', h_early == (0, set()) and h_late == (LCTL, set()),
          'at 100 ms=%s at 400 ms=%s' % (h_early, h_late))
    check('S-TAP-stable', 'no reset or fault during the tap-hold tests', not m.resets and not m.faults, 'resets=%s faults=%s' % (m.resets, m.faults))
    m = None


snap_tests()
brightness_tests()
tap_tests()
print('suite_gaming: %d PASS, %d FAIL' % (sum(results), len(results) - sum(results)))
