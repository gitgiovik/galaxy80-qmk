"""Galaxy80 QMK stage 3: emulator tests for the stage-3 features.
Called from run_tests.py (Part 3, prefix S3) with the shared helpers; can also run alone:
    python test_features.py <fw_dir> [out_dir] [--only=S3-SOCD,...]
Tests: SOCD (NKRO, 6KRO, WASD swap, per-pair, game mode), debounce setting (persistence across a
power cycle, timing 2/8 ms, eager mode), per-key editor (keys never reach the host, colours
stored with the deferred save, rendered, persisted, Fn+RShift+Esc still works), watchdog fed
across a per-key save that forces a wear-leveling consolidation with 2 ms flash stalls,
side-bar overlay, USB bInterval, VIA definition vs. compiled effect ids.
Stage 4 (test_via_eeprom.py has the new tests) changed on purpose, and these tests follow it: the per-key
table has 2 bytes per LED (helpers below decode it to the 16 editor presets), the editor starts from
the colours on the keys (so the editor tests first select "Per tasto" from VIA to keep the stored
defaults), Esc is an editable key (exit with Fn+K), id 17 is the editor brush, every VIA "save" is
deferred (S3-DEB-persist waits for it), the side-bar mode moved to VIA id 19."""
import os, sys, json, struct, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

FN, S_, A_, D_, W_, K_, ESC, G_, RSFT, LSFT, N_, PAUSE, T_ = (5, 11), (3, 2), (3, 1), (3, 3), (2, 2), (3, 8), (0, 0), (3, 5), (4, 13), (4, 0), (4, 7), (0, 16), (2, 5)
HID = dict(A=0x04, D=0x07, W=0x1A, S=0x16, UP=0x52, DOWN=0x51, LEFT=0x50, RIGHT=0x4F, T=0x17, K=0x0E, ESC=0x29)
VIA_JSON = (os.environ.get('G80_KBD') or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'keyboards', 'epomaker', 'galaxy80'))) + '/via_json/galaxy80.json'
LED_A, LED_W, LED_F, BAR0 = 52, 36, 55, 88
NO_LED = 0xFF
# rgb_matrix LED -> (row, col) of g80_frame (10-design-stage2.md sec. 2)
LED_MAP = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16],
           [17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33],
           [34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50],
           [51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, NO_LED, 63, NO_LED, NO_LED, NO_LED],
           [64, NO_LED, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, NO_LED, 75, NO_LED, 76, NO_LED],
           [77, 78, 79, NO_LED, NO_LED, NO_LED, 80, NO_LED, NO_LED, NO_LED, 81, 82, 83, 84, 85, 86, 87],
           [NO_LED] * 12 + [88, 89, 90, 91, 92]]
POS = {v: (r, c) for r, row in enumerate(LED_MAP) for c, v in enumerate(row) if v != NO_LED}


def run(ctx):
    mk, check, T, report, IMG = ctx['mk'], ctx['check'], ctx['T'], ctx['report'], ctx['IMG']
    ids = ctx['EFFECT_IDS']
    only = ctx.get('only')

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

    def rd(m, sym, n):
        return bytes(m.uc.mem_read(m.sym[sym], n))

    def cfg(m):
        return struct.unpack('<I', rd(m, 'g80_config', 4))[0]

    def keys_at(m, t):
        m.run(t)
        return report(m)

    def sample(m, t0, t1, step=1.0):
        out = []
        t = t0
        while t <= t1:
            m.run(t)
            out.append((round(t, 1),) + report(m))
            t += step
        return out

    def via(m, vid, *vals, cmd=0x07):
        """VIA custom value command on channel 0 (id_custom_set_value 0x07 / get 0x08 / save 0x09)"""
        buf = m.sym['__heap_base__']
        pkt = bytes([cmd, 0, vid] + list(vals)) + b'\x00' * 32
        m.uc.mem_write(buf, pkt[:32])
        m.call('via_custom_value_command_kb', buf, 32)
        return bytes(m.uc.mem_read(buf, 32))

    def power_cycle(m, kind):
        """new emulator booting from the flash content of m (EEPROM area included)"""
        fd, p = tempfile.mkstemp(suffix='.bin')
        os.write(fd, bytes(m.flash))
        os.close(fd)
        m2 = G80(p, IMG[kind][1])
        m2.keep_reg_writes = m2.keep_col_reads = m2.keep_ct1_events = False
        m2.schedule = []
        os.unlink(p)
        return m2

    HUES = [0, 11, 21, 32, 43, 64, 85, 106, 128, 149, 170, 191, 213, 234]

    def pk_code(m, led):
        """stage 4: 2 bytes per LED (hue, sat4 << 4 | val4) -> editor preset 0..15 (nearest)"""
        b = rd(m, 'g80_pk', 2 * 93)
        h, s4, v4 = b[2 * led], b[2 * led + 1] >> 4, b[2 * led + 1] & 15
        if v4 == 0:
            return 0
        if s4 * 17 < 64:
            return 15
        return min(range(14), key=lambda i: min((h - HUES[i]) & 255, (HUES[i] - h) & 255)) + 1

    def raw(m, pkt):
        """VIA packet through raw_hid_receive (all channels, incl. the lighting channel 3)"""
        buf = m.sym['__heap_base__']
        m.uc.mem_write(buf, (bytes(pkt) + b'\x00' * 32)[:32])
        m.call('raw_hid_receive', buf, 32, max_insn=60000000)
        return bytes(m.uc.mem_read(buf, 32))

    def frame_rgb(m, led):
        f = rd(m, 'g80_frame', 21 * 17)
        r, c = POS[led]
        return f[r * 17 + c], f[(7 + r) * 17 + c], f[(14 + r) * 17 + c]

    def fw_rgb(m, led, val):
        x = m.call('g80_pk_rgb', led, val)
        return tuple(min(252, (x >> s) & 0xFF) for s in (0, 8, 16))

    def rgb_mode(m):
        return rd(m, 'rgb_matrix_config', 1)[0] >> 2

    # ------------------------------------------------------------------ SOCD
    def socd_seq(m, t, k1, k2, h1, h2):
        """k1 held t..t+200, k2 held t+50..t+150: returns samples every 1 ms"""
        m.schedule += [(t, t + 200, k1), (t + 50, t + 150, k2)]
        s = sample(m, t + 30, t + 230, 1.0)
        both = [x[0] for x in s if h1 in x[2] and h2 in x[2]]
        return dict(before=sorted(keys_of(s, t + 40)), during=sorted(keys_of(s, t + 100)), after=sorted(keys_of(s, t + 180)),
                    end=sorted(keys_of(s, t + 225)), both_ms=len(both))

    def keys_of(s, t):
        return min(s, key=lambda x: abs(x[0] - t))[2]

    if want('S3-SOCD'):
        for label, nkro in (('nkro', True), ('6kro', False)):
            m = mkq('default')
            t = 250
            if not nkro:
                fn_combo(m, t, N_)        # Fn+N: NKRO off
                t += 60
            fn_combo(m, t, S_)            # Fn+S: SOCD on
            m.run(t + 60)
            c = cfg(m)
            kc = struct.unpack('<I', rd(m, 'keymap_config', 4))[0]
            nk = (kc >> 7) & 1
            ad = socd_seq(m, 400, A_, D_, HID['A'], HID['D'])
            da = socd_seq(m, 700, D_, A_, HID['D'], HID['A'])
            fn_combo(m, 1000, G_)         # WASD <-> arrows
            m.run(1060)
            ws = socd_seq(m, 1100, W_, (3, 2), HID['UP'], HID['DOWN'])
            fn_combo(m, 1400, G_)         # swap off
            fn_combo(m, 1460, S_)         # SOCD off
            m.run(1520)
            off = socd_seq(m, 1550, A_, D_, HID['A'], HID['D'])
            ok = ((c >> 9) & 1 and nk == (1 if nkro else 0)
                  and ad['before'] == [4] and ad['during'] == [7] and ad['after'] == [4] and ad['end'] == [] and ad['both_ms'] == 0
                  and da['before'] == [7] and da['during'] == [4] and da['after'] == [7] and da['both_ms'] == 0
                  and ws['before'] == [0x52] and ws['during'] == [0x51] and ws['after'] == [0x52] and ws['end'] == [] and ws['both_ms'] == 0
                  and off['during'] == [4, 7] and off['end'] == [] and not m.resets and not m.faults)
            check('S3-SOCD-' + label, 'SOCD (%s): Fn+S on; A held + D -> only D, D released -> A back, never both; same D->A; with '
                  'WASD swap W+S -> Down wins, Up back; Fn+S off -> A+D both' % label.upper(), ok,
                  'socd bit=%d nkro=%d | A,D: %s | D,A: %s | swap W,S: %s | off: %s' % ((c >> 9) & 1, nk, ad, da, ws, off))

        # per-pair enable from VIA + game mode
        m = mkq('via')
        m.inject = [(300, lambda mm: (via(mm, 7, 1), via(mm, 8, 0)))]
        m.run(320)
        r8 = via(m, 8, cmd=0x08)[3]
        r9 = via(m, 9, cmd=0x08)[3]
        ad = socd_seq(m, 350, A_, D_, HID['A'], HID['D'])
        ws = socd_seq(m, 650, W_, (3, 2), HID['W'], HID['S'])
        check('S3-SOCD-pair', 'VIA: SOCD on (id 7) and pair A/D off (id 8 = 0) -> A+D both reported, W/S still last-input-wins; get returns 0/1',
              ad['during'] == [4, 7] and ws['during'] == [0x16] and ws['after'] == [0x1A] and ws['both_ms'] == 0 and r8 == 0 and r9 == 1,
              'get id8=%d id9=%d | A,D: %s | W,S: %s' % (r8, r9, ad, ws))

        m = mkq('default')
        fn_combo(m, 300, PAUSE)            # game mode
        m.run(360)
        c = cfg(m)
        ad = socd_seq(m, 400, A_, D_, HID['A'], HID['D'])
        press(m, 700, [(5, 1)], 30)        # LGui
        g = keys_at(m, 720)
        press(m, 800, [(5, 2)], 60); press(m, 815, [(2, 0)], 30)   # LAlt+Tab
        at = keys_at(m, 830)
        check('S3-GAME', 'game mode (Fn+Pause): SOCD active (socd_on still 0), LGui blocked, LAlt+Tab blocked',
              (c >> 18) & 1 and not (c >> 9) & 1 and ad['during'] == [7] and ad['both_ms'] == 0 and not (g[0] & 0x88) and 0x2B not in at[1] and (at[0] & 0x04),
              'game bit=%d socd bit=%d | A,D: %s | LGui mods=%#x | LAlt+Tab mods=%#x keys=%s' % ((c >> 18) & 1, (c >> 9) & 1, ad, g[0], at[0], sorted(at[1])))

    # ------------------------------------------------------------------ debounce
    if want('S3-DEB'):
        def first_on(m, t0, t1, rc):
            r, c = rc
            t = t0
            while t <= t1:
                m.run(t)
                mx = struct.unpack('<6I', rd(m, 'matrix', 24))
                if mx[r] >> c & 1:
                    return round(t - t0, 2)
                t += 0.25
            return None

        m = mkq('via')
        d0 = rd(m, 'g80_debounce_ms', 1)[0] if m.run(300) else None
        n0 = len(m.flash_ops)
        m.inject = [(320, lambda mm: (via(mm, 12, 8), via(mm, 0, cmd=0x09)))]
        m.run(340)
        early = len(m.flash_ops) - n0
        m.run(3400)                                   # stage 4: VIA "save" deferred ~3 s
        d1 = rd(m, 'g80_debounce_ms', 1)[0]
        ops = m.flash_ops[n0:]
        bad = [o for o in ops if not (0xF6C0 <= o[1] < 0xFFC0)]
        m2 = power_cycle(m, 'via')
        m2.run(300)
        d2 = rd(m2, 'g80_debounce_ms', 1)[0]
        g2 = via(m2, 12, cmd=0x08)[3]
        c2 = cfg(m2)
        check('S3-DEB-persist', 'debounce from VIA (id 12): default 5 ms; set 8 + save -> written ~1 s later (deferred), flash ops only in 0xF6C0..0xFFBF; after a power cycle 8 ms (eeconfig_kb bits 14..17), VIA get = 8',
              d0 == 5 and d1 == 8 and early == 0 and ops and not bad and d2 == 8 and g2 == 8 and (c2 >> 14) & 15 == 8,
              'boot=%s ms, after set=%d, flash ops within 20 ms=%d, after 3 s=%d (outside=%d), after power cycle=%d ms, VIA get=%d, cfg=%#x' % (d0, d1, early, len(ops), len(bad), d2, g2, c2))

        # timing: a 5 ms press with 8 ms (rebooted image) vs 2 ms, latency defer vs eager
        press(m2, 400, [T_], 5)
        lat8_short = first_on(m2, 400, 420, T_)
        m2.inject = [(430, lambda mm: via(mm, 12, 2))]
        m2.run(440)
        press(m2, 450, [T_], 5)
        lat2_short = first_on(m2, 450, 470, T_)
        m2.inject = [(480, lambda mm: via(mm, 12, 8))]
        m2.run(490)
        press(m2, 500, [T_], 30)
        lat8 = first_on(m2, 500, 540, T_)
        m2.inject = [(560, lambda mm: via(mm, 13, 1))]
        m2.run(570)
        press(m2, 600, [T_], 30)
        lat8e = first_on(m2, 600, 640, T_)
        # eager latency = scan pass + main-loop phase: 1.75..3.0 ms over press offsets with the LEDs rendering (stage 7c,
        # 2026-10-19), the old "< 3" only held for some builds' phase
        check('S3-DEB-timing', 'debounce runtime: 5 ms tap ignored with 8 ms, accepted with 2 ms; 30 ms press seen after >= 8 ms (defer) and '
              '< 3.5 ms with eager (id 13)',
              lat8_short is None and lat2_short is not None and lat8 is not None and lat8 >= 8 and lat8e is not None and lat8e < 3.5,
              '5ms tap @8ms: %s, 5ms tap @2ms: %s ms, 30ms press @8ms defer: %s ms, eager: %s ms (scan pass ~1.15 ms)' % (lat8_short, lat2_short, lat8, lat8e))

    # ------------------------------------------------------------------ per-key editor
    if want('S3-EDIT'):
        m = mkq('via')
        # stage 4: "Per tasto" selected from VIA first (lighting channel, deferred save), so the editor
        # keeps the stored defaults instead of copying the rainbow that is on the keys
        m.inject = [(250, lambda mm: (raw(mm, [0x07, 3, 2, ids['G80_PER_KEY']]), raw(mm, [0x09, 3])))]
        fn_combo(m, 300, K_)                         # enter
        press(m, 400, [A_]); press(m, 450, [A_]); press(m, 500, [A_])   # 3 x forward
        press(m, 540, [LSFT], 60); press(m, 560, [A_])                   # Shift + A: backward
        press(m, 620, [W_]); press(m, 650, [(1, 5)])                     # W (red->red-orange), 5
        press(m, 700, [LSFT], 20)                                        # LShift tapped alone
        press(m, 750, [FN], 40); press(m, 760, [(1, 2)])                 # Fn+2: bar LED 2
        fn_combo(m, 800, (3, 13))                                        # Fn+Enter: Apply (phase C, P0-9)
        fn_combo(m, 860, K_)                                             # exit (stage 4: Fn+K only; exit no longer saves)
        m.run(360)
        ed1 = rd(m, 'g80_pk_editing', 1)[0]
        n0 = len(m.flash_ops)
        s = sample(m, 362, 865, 2.0)
        leaked = sorted(set(k for x in s for k in x[2]))
        mods = sorted(set(x[1] for x in s))
        m.run(910)
        ed2 = rd(m, 'g80_pk_editing', 1)[0]
        codes = dict(A=pk_code(m, LED_A), W=pk_code(m, LED_W), F=pk_code(m, LED_F), LSFT=pk_code(m, 64), BAR1=pk_code(m, BAR0 + 1), K5=pk_code(m, 22))
        mode = rgb_mode(m)
        m.run(3700)
        early = [o for o in m.flash_ops[n0:] if o[-1] < 830 + 240]       # Apply waits for the write gate (250 ms quiet)
        m.run(4200)
        ops = m.flash_ops[n0:]
        bad = [o for o in ops if not (0xF6C0 <= o[1] < 0xFFC0)]
        exp_a = fw_rgb(m, LED_A, 255)
        got_a, got_w, got_f = frame_rgb(m, LED_A), frame_rgb(m, LED_W), frame_rgb(m, LED_F)
        exp_w = fw_rgb(m, LED_W, 255)
        ok = (ed1 == 1 and ed2 == 0 and not leaked and set(mods) <= {0, 0x02}
              and codes == dict(A=3, W=2, F=15, LSFT=0, BAR1=13, K5=0) and mode == ids['G80_PER_KEY']
              and ops and not early and not bad and got_a == exp_a and got_w == exp_w and got_f == (252, 252, 252) and not m.resets)
        check('S3-EDIT-keys', 'editor: Fn+K enters; A x3 (red->yellow) + Shift+A (-> amber), W (red->red-orange), 5 (white->off), LShift tapped alone '
              '(white->off), Fn+2 (bar LED 2 white->off); NO key reaches the host (only the passed-through Shift modifier); Fn+Enter = Apply, '
              'written once the keys are quiet for 250 ms (write gate); Fn+K exits -> effect "Per tasto"; flash ops only in the EEPROM area; '
              'frame shows the new colours', ok,
              'editing=%d->%d, keys seen by host=%s mods=%s, codes=%s, effect=%d, flash ops=%d (early=%d, outside=%d), '
              'frame A=%s (exp %s) W=%s F=%s' % (ed1, ed2, leaked, [hex(x) for x in mods], codes, mode, len(ops), len(early), len(bad), got_a, exp_a, got_w, got_f))

        m2 = power_cycle(m, 'via')
        m2.run(400)
        codes2 = dict(A=pk_code(m2, LED_A), W=pk_code(m2, LED_W), F=pk_code(m2, LED_F), BAR1=pk_code(m2, BAR0 + 1))
        fa = frame_rgb(m2, LED_A)
        check('S3-EDIT-persist', 'per-key colours and the effect survive a power cycle (VIA custom config + rgb eeconfig) and are rendered at boot',
              codes2 == dict(A=3, W=2, F=15, BAR1=13) and rgb_mode(m2) == ids['G80_PER_KEY'] and fa == exp_a,
              'codes=%s effect=%d frame A=%s' % (codes2, rgb_mode(m2), fa))

        # VIA: editor toggle, colour of the last key, reset
        m2.inject = [(420, lambda mm: via(mm, 16, 1))]
        m2.run(430)
        press(m2, 450, [D_])
        m2.run(480)
        m2.inject = [(490, lambda mm: via(mm, 17, 170, 255))]   # blue
        m2.run(500)
        d_blue = pk_code(m2, 54)
        g17 = via(m2, 17, cmd=0x08)[3:5]
        m2.inject = [(510, lambda mm: via(mm, 16, 0)), (520, lambda mm: via(mm, 18, 1))]
        m2.run(530)
        after_reset = dict(A=pk_code(m2, LED_A), W=pk_code(m2, LED_W), D=pk_code(m2, 54))
        check('S3-EDIT-via', 'VIA: editor on (id 16), D pressed, brush colour (id 17, stage 4) hue 170 -> last key D = blue (code 11), get returns hue 170; '
              'reset (id 18) -> defaults (A/W/D red)',
              d_blue == 11 and tuple(g17) == (170, 255) and after_reset == dict(A=1, W=1, D=1),
              'D=%d get=%s after reset=%s' % (d_blue, list(g17), after_reset))

        # safety in the editor: Fn+RShift+Esc still reaches the ROM ISP
        m = mkq('via')
        fn_combo(m, 300, K_)
        press(m, 400, [FN], 200); press(m, 420, [RSFT], 150); press(m, 450, [ESC], 50)
        r = m.run(700)
        ed = rd(m, 'g80_pk_editing', 1)[0] if r != 'ISP' else None
        check('S3-EDIT-boot', 'in the editor Fn + RShift + Esc still enters the ROM ISP (LEDs off first)',
              r == 'ISP' and m.isp is not None, 'result=%s isp=%s' % (r, bool(m.isp)))

        # default image: editor works, nothing written
        m = mkq('default')
        m.inject = [(250, lambda mm: mm.call('rgb_matrix_mode_noeeprom', ids['G80_PER_KEY']))]
        fn_combo(m, 300, K_)
        press(m, 400, [A_])
        fn_combo(m, 450, K_)
        m.run(4000)
        check('S3-EDIT-default', 'default image (transient EEPROM): editor works (A red->orange-red), no flash op at all',
              pk_code(m, LED_A) == 2 and not m.flash_ops and rgb_mode(m) == ids['G80_PER_KEY'],
              'A=%d flash ops=%d effect=%d' % (pk_code(m, LED_A), len(m.flash_ops), rgb_mode(m)))

    # ------------------------------------------------------------------ watchdog across long writes
    if want('S3-WDT'):
        D0 = 3000  # stage 5 backing store: first-boot EEPROM format takes longer with 2 ms stalls
        m = mkq('via', flash_stall_us=2000)
        # first boot formats the EEPROM (~1400 flash ops = ~2.8 s of stalls, before the watchdog
        # is started by design): wait for the main loop
        m.run(D0 + 3500)
        boot_ml = m.first_main_loop_t
        st = {}

        def prefill(mm):
            # fill the wear-leveling write log (wear_leveling.write_address, offset 1024 in the
            # struct) to <= 12 bytes before the end of the 2048 B backing store: the next writes
            # (the per-key save) force a consolidation (32 page erases + 1 KiB rewrite)
            wa = mm.sym['wear_leveling'] + 1024
            n = 0
            while n < 3000:
                left = 2048 - struct.unpack('<I', mm.uc.mem_read(wa, 4))[0]
                if left <= 12:
                    break
                mm.call('eeconfig_update_kb', (0x55000000 | n) if left > 40 else (n & 1), max_insn=50000000)
                n += 1
            st['writes_per_log'] = n
            st['log_left'] = left
            st['erases_prefill'] = sum(1 for o in mm.flash_ops if o[0] == 'ERASE' and o[-1] >= D0 + 3500)
            mm.pending_stall = 0               # the prefill itself is not part of the measurement
        m.inject = [(D0 + 3520, prefill)]
        m.run(D0 + 3540)
        n0 = len(m.flash_ops)
        f0 = len(m.wdt_feeds)
        fn_combo(m, D0 + 3600, K_)
        for k in range(20):                           # 20 changes (keys 1..=, twice for 8 of them)
            press(m, D0 + 3650 + 25 * k, [(1, 1 + (k % 12))], 15)
        fn_combo(m, D0 + 4150, (3, 13))               # Fn+Enter: Apply (phase C: exit no longer saves)
        fn_combo(m, D0 + 4200, K_)
        r = m.run(D0 + 9000)
        ops = m.flash_ops[n0:]
        er = sum(1 for o in ops if o[0] == 'ERASE')
        feeds = [f[0] * 1e3 for f in m.wdt_feeds[f0:]]
        gaps = [b - a for a, b in zip(feeds, feeds[1:])]
        mx = max(gaps) if gaps else None
        save_t = (ops[0][-1], ops[-1][-1]) if ops else None
        wdt_resets = [x for x in m.resets if x[1] == 'WDT']
        feeders = sorted(set(f[1] for f in m.wdt_feeds[f0:]))
        check('S3-WDT', 'per-key save that forces a wear-leveling consolidation, every flash op stalling the CPU 2 ms: watchdog fed '
              'before each run, longest feed gap < 1000 ms (worst-case WDT ~1 s), no WDT reset, all ops in the EEPROM area',
              boot_ml is not None and r == 'TIME' and er > 0 and mx is not None and mx < 1000 and not wdt_resets and not m.violations,
              'main loop at %s ms, prefill writes=%s (log bytes left %s), prefill erases=%s, save ops=%d (erases=%d) from %s ms, longest feed gap %.1f ms, feeders=%s, resets=%s, violations=%s' % (
                  boot_ml, st.get('writes_per_log'), st.get('log_left'), st.get('erases_prefill'), len(ops), er, save_t, mx or -1, feeders, m.resets, m.violations[:2]))

    # ------------------------------------------------------------------ side bar overlay
    if want('S3-BAR'):
        m = mkq('default')
        def solid_red(mm):
            mm.call('rgb_matrix_enable_noeeprom')
            mm.call('rgb_matrix_mode_eeprom_helper', ids['SOLID_COLOR'], 0)
            mm.call('rgb_matrix_sethsv_eeprom_helper', 0, 255, 128, 0)
            mm.call('usb_device_state_set_leds', 2)   # Caps Lock on
        m.inject = [(250, solid_red)]
        fn_combo(m, 300, S_)                          # SOCD on
        m.run(500)
        b0, b1, b4 = frame_rgb(m, BAR0), frame_rgb(m, BAR0 + 1), frame_rgb(m, BAR0 + 4)
        press(m, 600, [FN], 100)
        m.run(680)
        b4h = frame_rgb(m, BAR0 + 4)
        # bar LEDs are R/G swapped on the PCB: frame (R,G,B) = (g, r, b) of the colour
        check('S3-BAR', 'side-bar overlay (bar mode 0 + overlay): LED1 white = Caps Lock, LED2 cyan = SOCD, LED5 = layer (red while Fn held, '
              'R/G swapped in the frame)',
              b0 == (252, 252, 252) and b1 == (252, 0, 252) and b4h == (0, 252, 0),
              'bar1=%s bar2=%s bar5 idle=%s bar5 Fn held=%s' % (b0, b1, b4, b4h))

    # ------------------------------------------------------------------ optional features
    if want('S3-OPT'):
        m = mkq('via')
        fn_combo(m, 300, (4, 4))          # Fn+C: Caps Word
        press(m, 400, [T_])
        cw = keys_at(m, 410)
        fn_combo(m, 450, S_)              # SOCD on: A handled by the SOCD path
        press(m, 520, [A_])
        cwa = keys_at(m, 530)
        bar = frame_rgb(m, BAR0)
        press(m, 560, [(5, 6)])           # Space ends Caps Word
        m.run(600)
        press(m, 620, [T_])
        nocw = keys_at(m, 630)
        # Repeat Key on Backslash (2,13), Mouse button 1 on Del (2,14), from the VIA dynamic keymap
        m.inject = [(650, lambda mm: (mm.call('dynamic_keymap_set_keycode', 0, 2, 13, 0x7C79), mm.call('dynamic_keymap_set_keycode', 0, 2, 14, 0x00D1)))]
        m.run(660)
        press(m, 700, [(3, 4)])           # F
        m.run(740)
        press(m, 760, [(2, 13)])          # Repeat -> F again
        rep_ = keys_at(m, 770)
        press(m, 800, [(2, 14)], 40)      # mouse button 1
        m.run(815)
        mr = rd(m, 'mouse_report', 6)
        check('S3-OPT', 'optional features: Caps Word (Fn+C) shifts T and also A on the SOCD path, bar LED 1 orange (R/G swapped), Space ends it; '
              'Repeat Key (QK_REP from VIA) repeats F; Mouse Keys button 1 (MS_BTN1 from VIA) in the mouse report',
              (cw[0] & 0x22) and 0x17 in cw[1] and (cwa[0] & 0x22) and 0x04 in cwa[1] and bar == (128, 252, 0)
              and not (nocw[0] & 0x22) and 0x17 in nocw[1] and 0x09 in rep_[1] and (mr[1] & 1),
              'CW T: mods=%#x keys=%s; CW A (SOCD): mods=%#x keys=%s; bar1=%s; after Space T: mods=%#x; repeat: %s; mouse_report=%s' % (
                  cw[0], sorted(cw[1]), cwa[0], sorted(cwa[1]), bar, nocw[0], sorted(rep_[1]), mr.hex()))

    # ------------------------------------------------------------------ static checks
    if want('S3-USB'):
        res = {}
        for kind in ('default', 'via'):
            b = open(IMG[kind][0], 'rb').read()
            i = 0
            eps = None
            while True:
                i = b.find(b'\x09\x02', i)
                if i < 0:
                    break
                tl = b[i + 2] | b[i + 3] << 8
                if 30 < tl < 300 and b[i + 4] < 8:
                    d, j, eps = b[i:i + tl], 0, []
                    while j < len(d) and d[j]:
                        if d[j + 1] == 5:
                            eps.append((d[j + 2], d[j + 3], d[j + 6]))
                        j += d[j]
                    break
                i += 2
            res[kind] = eps
        ok = all(e and all(a == 3 and iv == 1 for (_, a, iv) in e) for e in res.values())
        check('S3-USB', 'USB: every interrupt endpoint (keyboard, NKRO/shared, VIA raw) has bInterval = 1 (full speed: 1 ms = 1000 Hz)', ok,
              ' '.join('%s: %s;' % (k, ['EP%02x int=%d' % (ep, iv) for ep, a, iv in v]) for k, v in res.items()))

    if want('S3-VIA'):
        j = json.load(open(VIA_JSON, encoding='utf-8'))
        fx = None
        vids = set()
        def walk(n):
            nonlocal fx
            if isinstance(n, dict):
                c = n.get('content')
                if isinstance(c, list) and len(c) == 3 and isinstance(c[0], str):
                    if c[0] == 'id_qmk_rgb_matrix_effect':
                        fx = n['options']
                    if c[1] == 0:
                        vids.add(c[2])
                for v in n.values():
                    walk(v)
            elif isinstance(n, list):
                for v in n:
                    walk(v)
        walk(j['menus'])
        nfx = max(ids.values()) + 1
        ok = (j['vendorId'] == '0x4758' and j['productId'] == '0x0080' and fx and [o[1] for o in fx] == list(range(nfx))
              and all(len(o[0]) <= 18 for o in fx) and fx[ids['G80_PER_KEY']][0] == 'Per-key' and vids == set(range(2, 35))
              and len(j['customKeycodes']) == 14)
        check('S3-VIA', 'VIA definition: 4758:0080, effect dropdown = ids 0..%d in compiled order with short names (<= 18 chars), '
              'custom value ids 2..34 (id 1 = VIA key painter protocol, stage 4; 24..34 phase C), 14 custom keycodes (G80_SBOOT..G80_GAME)' % (nfx - 1), ok,
              'effects=%s; value ids=%s; keycodes=%d' % ([o[0] for o in fx] if fx else None, sorted(vids), len(j['customKeycodes'])))


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
        print('%s  %-16s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail), flush=True)

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

    ids = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'via_effect_ids.json')))
    run(dict(mk=mk, check=check, T=print, report=report, ram32=ram32, IMG=IMG, FW=FW, EFFECT_IDS=ids, only=only))
    json.dump(results, open(os.path.join(OUT, 's3_results.json'), 'w'), indent=1)
    print('\nS3 SUMMARY: %d PASS, %d FAIL' % (sum(r['result'] == 'PASS' for r in results), sum(r['result'] == 'FAIL' for r in results)))
