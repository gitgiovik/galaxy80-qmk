# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared helpers of the power-cut suites (suite_powercut*.py): flash-op recording, offline cut images
(plain and torn), boots of cut images, and a Python model of the wear-leveling read path including the
boot recovery of g80_wear_leveling.c (state log, scratch restore, PHASE2 finish, trusted main).

Flash layout (VIA image): 0xF6C0 / 0xF700 scratch sectors, 0xF740 / 0xF780 state log (32 raw words),
0xF7C0..0xFFBF wear-leveling area (stored complemented: 1024 B data, 8 B hash, 1016 B log).
"""
import os, sys, struct, random, gc
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *
from g80emu import torn_op
from via_host import G80Via, run_until, via_sync, boot_to_loop

AUX0, ST0, WL0, WL1 = 0xF6C0, 0xF740, 0xF7C0, 0xFFC0
LOG_START = 1032
ST_BEGIN, ST_PHASE2 = 0x5A17C0DE, 0xA5E81F21
FNV_INIT = 0xcbf29ce484222325
DEF_FLAG = b'\x55\x55\xaa\xaa'

REGIONS = [('eeconfig_magic', 0, 2), ('default_layer', 3, 4), ('keymap_config', 4, 6), ('kb', 15, 19), ('user', 19, 23),
           ('rgb_matrix', 23, 31), ('via_magic', 37, 40), ('layout_opt', 40, 41), ('pk_layout_byte', 41, 42),
           ('pk_table', 42, 228), ('keymaps', 228, 840), ('macros', 840, 1024)]


def fnv64a(b, h=FNV_INIT):
    for x in b:
        h ^= x
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def words(flash, a, n):
    return list(struct.unpack_from('<%dI' % n, flash, a))


def logical(flash, a, n):
    """n stored (complemented) lines at a -> logical bytes"""
    return b''.join(struct.pack('<I', ~w & 0xFFFFFFFF) for w in words(flash, a, n))


# ------------------------------------------------------------------ offline model
def playback(cache, area_words):
    """QMK log playback over cache (bytearray); area_words = logical words of the 2048 B area.
    -> (end address, failed)"""
    a = LOG_START
    while a < 2048:
        v = area_words[a // 4]
        if v == 0:
            return a, False
        a += 4
        b = struct.pack('<I', v)
        if b[0] >> 6 != 0:
            return a, True
        ad = ((b[0] & 7) << 16) | (b[1] << 8) | b[2]
        ln = (b[0] >> 3) & 7
        val = bytes([b[3]])
        if ln > 1:
            if a >= 2048:
                return a, True
            val += struct.pack('<I', area_words[a // 4])
            a += 4
        if ad + ln > 1024:
            return a, True
        cache[ad:ad + ln] = val[:ln]
    return a, False


def copy_word(flash, i):
    scr = AUX0 + 64 * (i & 1)
    return 0xC6 << 24 | i << 20 | (fnv64a(logical(flash, scr, 16), FNV_INIT ^ i) & 0xFFFFF)


def model_boot(flash):
    """What the firmware's wear_leveling_init() ends with (cache before any re-consolidation, which does not
    change it): -> dict(cache, path, hash_ok, failed, log_end)"""
    fl = bytearray(flash)
    st = words(fl, ST0, 32)
    area = [~w & 0xFFFFFFFF for w in words(fl, WL0, 512)]
    main = b''.join(struct.pack('<I', w) for w in area[:256])
    stored = area[256] | area[257] << 32
    h = fnv64a(main)
    path = 'plain'
    trust = False
    if st[0] == ST_BEGIN:
        if h == stored:
            path = 'st-clear'
        elif ST_PHASE2 in st[1:]:
            path = 'phase2-finish'
            return dict(cache=main, path=path, hash_ok=True, failed=False, log_end=LOG_START)
        else:
            last = 0
            for w in st[1:]:
                if w >> 24 == 0xC6:
                    last = w
            path = 'phase1-trust'
            if last:
                i = last >> 20 & 15
                if copy_word(fl, i) == last and fl[AUX0 + 64 * (i & 1):AUX0 + 64 * (i & 1) + 64] != fl[WL0 + 64 * i:WL0 + 64 * i + 64]:
                    fl[WL0 + 64 * i:WL0 + 64 * i + 64] = fl[AUX0 + 64 * (i & 1):AUX0 + 64 * (i & 1) + 64]
                    path = 'phase1-restore'
            area = [~w & 0xFFFFFFFF for w in words(fl, WL0, 512)]
            main = b''.join(struct.pack('<I', w) for w in area[:256])
            trust = True
    ok = trust or fnv64a(main) == stored
    cache = bytearray(main if ok else bytes(1024))
    end, failed = playback(cache, area)
    return dict(cache=bytes(cache), path=path, hash_ok=ok, failed=failed, log_end=end)


def apply_ops(flash, ops, torn=None):
    """apply recorded ops ('E'|'P', addr, words, ...) to a bytearray copy of the flash; torn = (op, rnd):
    one more op, cut half-way (g80emu.torn_op)"""
    f = bytearray(flash)
    for o in ops:
        if o[0] == 'E':
            p = o[1] & ~63
            f[p:p + 64] = b'\xff' * 64
        else:
            for i, w in enumerate(o[2]):
                p = o[1] + 4 * i
                struct.pack_into('<I', f, p, struct.unpack_from('<I', f, p)[0] & w)
    if torn is not None:
        o, rnd = torn
        torn_op(f, 'ERASE' if o[0] == 'E' else 'PROGRAM', o[1], o[2], rnd)
    return f


# ------------------------------------------------------------------ emulator with op recording
class Rec(G80Via):
    """G80Via that records every flash op with its data and the wear-leveling cache at that moment"""
    def __init__(self, *a, snap_cache=False, **k):
        self.oprec = []
        self.snap_cache = snap_cache
        super().__init__(*a, **k)

    def fmc_start(self, pc):
        ctrl = self.reg[FMC + 8]
        addr = self.reg[FMC + 0x10]
        buf = list(self.flash_buf)
        n0 = len(self.flash_ops)
        cache = self.wl_cache() if self.snap_cache else None
        super().fmc_start(pc)
        if len(self.flash_ops) > n0:
            ctx = self.flash_ctx() if self.stress else None
            if ctrl & 2:
                self.oprec.append(('E', addr & ~63, [], round(self.t_ms(), 3), ctx, cache))
            else:
                self.oprec.append(('P', addr, buf, round(self.t_ms(), 3), ctx, cache))


def mk(path, nm, stress=False, prog_us=None, erase_us=None, **kw):
    gc.collect()
    m = Rec(path, nm, stress=stress, flash_prog_us=prog_us, flash_erase_us=erase_us, **kw)
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    return m


def write_img(path, flash):
    with open(path, 'wb') as f:
        f.write(bytes(flash))
    return path


def boot_img(path, nm, flash, extra_ms=30, **kw):
    """boot a flash image to the main loop (+extra_ms); -> machine (caller drops it)"""
    write_img(path, flash)
    m = mk(path, nm, **kw)
    run_until(m, 6000, lambda mm: mm.first_main_loop_t is not None and mm.t_ms() > mm.first_main_loop_t + extra_ms, step=10.0)
    return m


def boot_ok(m):
    return m.first_main_loop_t is not None and not m.faults and m.isp is None and not m.lockup and not m.resets


def boot_twice(tmp, nm, flash, extra_ms=30):
    """boot a cut image, then boot again from what the first boot left: -> dict"""
    m = boot_img(tmp, nm, flash, extra_ms)
    r1 = dict(ok=boot_ok(m), ops=len(m.oprec), cache=m.wl_cache(), flash=bytes(m.flash), viol=list(m.violations),
              erases=sum(o[0] == 'E' for o in m.oprec), aux_ops=sum(o[1] < WL0 for o in m.oprec), oprec=m.oprec)
    m = None
    m = boot_img(tmp, nm, r1['flash'], extra_ms)
    r2 = dict(ok=boot_ok(m), ops=len(m.oprec), cache=m.wl_cache(), viol=list(m.violations), flash=bytes(m.flash))
    m = None
    gc.collect()
    return r1, r2


def wl_addr(m):
    return struct.unpack('<I', m.uc.mem_read(m.sym['wear_leveling'] + 1024, 4))[0]


def log_free(m):
    return 2048 - wl_addr(m)


def prefill(m, left_max):
    """fill the write log with eeconfig_kb write pairs (changed, then back: the data ends unchanged, 16 B
    of log per pair) until less than left_max + 16 bytes are free"""
    kb0 = struct.unpack('<I', bytes(m.uc.mem_read(m.sym['wear_leveling'] + 15, 4)))[0]
    n = 0
    while log_free(m) >= left_max + 16 and n < 4000:
        m.call('eeconfig_update_kb', kb0 ^ 0x55000000, max_insn=50000000)
        m.call('eeconfig_update_kb', kb0, max_insn=50000000)
        n += 2
    m.pending_stall = 0
    return n


def classify_mix(R, C, L):
    """R = cache after the cut, C = cache at the consolidation start, L = main + log at the start (what a
    cut just before the consolidation gives: C without the part of the write that filled the log which
    did not fit in the log any more).
    -> 'C' | 'L' | 'TAIL-MIX(n)' (n bytes of that not-logged tail old, every other byte = C) | 'BAD'"""
    if R == C:
        return 'C'
    if R == L:
        return 'L'
    n = 0
    for r, c, l in zip(R, C, L):
        if r == c:
            continue
        if r == l:
            n += 1
            continue
        return 'BAD'
    return 'TAIL-MIX(%d)' % n


def regions_diff(a, b):
    return [n for n, s, e in REGIONS if a[s:e] != b[s:e]]


# ------------------------------------------------------------------ trigger bench shared by the suites
class Bench:
    """Base images and the consolidation triggers of the power-cut suites.
    D = defaults (first boot from a blank area), P0 = user data (3 VIA remaps) on top of it."""
    def __init__(self, fw, out):
        self.fw, self.out = fw, out
        self.nm = os.path.join(fw, 'nm_via.txt')
        self.bin = os.path.join(fw, 'epomaker_galaxy80_via.bin')
        self.tmp = os.path.join(out, 'pc_tmp_%d.bin' % os.getpid())
        img = bytearray(open(self.bin, 'rb').read())
        img[AUX0:WL1] = b'\xff' * (WL1 - AUX0)
        self.blank = bytes(img)
        m = self.booted(self.blank, 300)
        self.D = m.wl_cache()
        self.first_boot_ops = len(m.oprec)
        self.f_def = bytes(m.flash)
        m = None
        m = self.booted(self.f_def, 50)
        for (l, r, c, kc) in ((0, 2, 3, 0x0004), (1, 2, 4, 0x0005), (2, 3, 5, 0x0029)):
            m.call('dynamic_keymap_set_keycode', l, r, c, kc, max_insn=50000000)
        run_until(m, m.t_ms() + 20)
        self.P0 = bytes(m.flash)
        m = None

    def booted(self, flash, extra=300, **kw):
        return boot_img(self.tmp, self.nm, flash, extra, **kw)

    def boot_twice(self, flash, extra=30):
        return boot_twice(self.tmp, self.nm, flash, extra)

    def via_m(self, flash, prog_us=None, erase_us=None):
        m = mk(write_img(self.tmp, flash), self.nm, stress=True, snap_cache=True, prog_us=prog_us, erase_us=erase_us)
        boot_to_loop(m, 100)
        return m

    @staticmethod
    def paint(m, n, seed):
        rnd = random.Random(seed)
        for led in sorted(rnd.sample(range(88), n)):
            via_sync(m, [[0x07, 0, 1, led, 1, rnd.randrange(256), rnd.choice((255, 170, 85))], [0x09, 0]])

    @staticmethod
    def every_sector(m):
        """one byte changed in each logical sector 4..15 (keymaps / macros), as log entries"""
        for s in range(4, 16):
            a = 64 * s + 60
            v = bytes(m.uc.mem_read(m.sym['wear_leveling'] + a, 1))[0]
            m.call('eeprom_update_byte', a, v ^ 0x5A, max_insn=50000000)

    def trig_apply(self, n_keys, free, seed, extra=None, lighting=False, prog_us=None, erase_us=None, keep=False, base=None):
        """-> (flash before, recorded ops[, machine]); base = start image (default P0)"""
        m = self.via_m(self.P0 if base is None else base, prog_us, erase_us)
        if extra:
            extra(m)
        prefill(m, free)
        run_until(m, m.t_ms() + 20)
        pre = bytes(m.flash)
        n0 = len(m.oprec)
        if lighting:
            via_sync(m, [[0x07, 3, 2, 3], [0x09, 3]])          # effect -> deferred save
            run_until(m, m.t_ms() + 1600)
        self.paint(m, n_keys, seed)
        m.t_apply = m.t_ms()
        m.n_apply = len(m.oprec)
        via_sync(m, [[0x07, 0, 24, 1], [0x09, 0]])
        run_until(m, m.t_ms() + 4000)
        ops = m.oprec[n0:]
        if keep:
            return pre, ops, m
        m = None
        return pre, ops

    def trig_save_fill(self):
        m = self.via_m(self.P0)
        prefill(m, 4)
        run_until(m, m.t_ms() + 20)
        pre = bytes(m.flash)
        n0 = len(m.oprec)
        via_sync(m, [[0x07, 3, 1, 77], [0x09, 3]])              # brightness -> deferred save
        via_sync(m, [[0x07, 3, 4, 33, 200], [0x09, 3]])         # colour
        run_until(m, m.t_ms() + 4000)
        ops = m.oprec[n0:]
        m = None
        return pre, ops

    def trig_boot(self, flash):
        m = mk(write_img(self.tmp, flash), self.nm, snap_cache=True)
        run_until(m, 6000, lambda mm: mm.first_main_loop_t is not None and mm.t_ms() > mm.first_main_loop_t + 100, step=10.0)
        ops = list(m.oprec)
        m = None
        return bytes(flash), ops

    def b4_image(self):
        m = self.booted(self.P0, 30)
        wa = wl_addr(m)
        m = None
        b4 = bytearray(self.P0)
        struct.pack_into('<I', b4, WL0 + wa, ~0x000000C1 & 0xFFFFFFFF)             # log entry type 3: invalid
        struct.pack_into('<I', b4, WL0 + wa + 4, ~0x01020304 & 0xFFFFFFFF)         # a valid-looking entry after it
        return bytes(b4)


def window(ops):
    """consolidation window of a recorded op list: first op (an erase or an aux-sector op) .. the state-log
    clear after the hash (erase of the 2nd state sector)"""
    i0 = next((i for i, o in enumerate(ops) if o[0] == 'E' or o[1] < WL0), None)
    if i0 is None:
        return None, None
    ends = [i for i, o in enumerate(ops) if i > i0 and o[0] == 'E' and o[1] == ST0 + 64]
    return i0, (ends[0] if ends else len(ops) - 1)


def outcome(R, C, L, D):
    c = classify_mix(R, C, L)
    if R == D or R[0:2] != C[0:2] or R[37:42] != C[37:42]:
        return 'FULL_RESET', c
    return {'C': 'KEPT', 'L': 'LAST_LOST', 'BAD': 'BAD'}.get(c, 'PARTIAL' if c.startswith('TAIL') else c), c
