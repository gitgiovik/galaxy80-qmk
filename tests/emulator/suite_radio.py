"""WP-C2 radio suite (g80_radio.c) on the VIA image, with a simulated radio module.
usage: python suite_radio.py <fw_dir> [kind=via]

T-NKRO-1..3  hybrid NKRO with 7 / 10 / 15 keys: A1 = first 5 keys in press order (k6 = 0), A2 = bitmap
             of the rest (usages < 0x70), 16 bytes, valid checksum
T-NKRO-4     release order: A1 keys released leave the extras alone, extras released update A2
T-NKRO-5     A2 sent only on change, the all-zero A2 after the last extra key
T-NKRO-6     usage >= 0x70 goes through A1 only (dropped when A1 is full), never into A2
T-NKRO-7     runtime option off: 6-key A1 fold, no A2; switching it off with extras held clears A2
T-NKRO-8     modifiers travel in A1
T-NKRO-9     the weak default of the option reads "on"
T-A4         system control: A4 u cs for Power / Sleep / Wake and release, coexisting with A3
T-LINK-1     5B 31 / 5B 33 clear the host LEDs (no stuck Caps LED), 5B 32 keeps them
T-LINK-2     reconnect blink phases are 256 ms
T-LINK-3     indication windows: pairing 20 s, link down 10 s, connected steady 3 s; wake restarts the 3 s
T-POLL-1     battery request: none in the first seconds (stock was 5 s), then every 60 s while linked
T-POLL-2     poll on wake (when linked; after the link comes back otherwise); half frame dropped on wake
T-API-1      g80_radio_idle(): false while a frame is queued / in flight / owed an ACK, true otherwise
T-API-2      5C percentage (raw byte of g80_radio_stats), low / critical state as the battery code sees it (Fn
             warning running, saves inhibited), charge edge ends low
T-RADIO-WL   whole run: only whitelisted frames (A1 A2 A3 A4 A6{00 11 30 31 32 33 51 53} A8 + ACK), valid
             checksums, no AB frame, one byte per THRE (no TX overwrite), no TX timeout, no fault / reset
"""
import gc
import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *

FW = sys.argv[1]
KIND = sys.argv[2] if len(sys.argv) > 2 else 'via'
BIN = FW + '/epomaker_galaxy80_%s.bin' % KIND
NM = FW + '/nm_%s.txt' % KIND
results = []

# matrix positions (row, col) of the keys used and their HID usages
KEY = {
    'Q': ((2, 1), 0x14), 'W': ((2, 2), 0x1A), 'E': ((2, 3), 0x08), 'R': ((2, 4), 0x15), 'T': ((2, 5), 0x17),
    'Y': ((2, 6), 0x1C), 'U': ((2, 7), 0x18), 'I': ((2, 8), 0x0C), 'O': ((2, 9), 0x12), 'P': ((2, 10), 0x13),
    'A': ((3, 1), 0x04), 'S': ((3, 2), 0x16), 'D': ((3, 3), 0x07), 'F': ((3, 4), 0x09), 'G': ((3, 5), 0x0A),
    'RO': ((4, 12), 0x87), 'LSHIFT': ((4, 0), None),
}
NKRO_PRESS_ORDER = 'QWERTYUIOPASDFG'
RADIO_WL = {0x00, 0x11, 0x30, 0x31, 0x32, 0x33, 0x51, 0x53}
# g80_radio_stats_t offsets (g80_radio.h): 7 x u32 (rx_bytes, rx_frames, rx_bad, ...), battery, host_leds, link,
# log_head, log[8][4], 3 x u16 (tx_dropped, tx_lost, tx_stuck)
ST_RXBAD, ST_BAT, ST_LEDS, ST_LINK, ST_TXSTUCK = 8, 28, 29, 30, 68


def check(tid, ok, detail):
    results.append(ok)
    print('%s  %-10s %s' % ('PASS' if ok else 'FAIL', tid, detail), flush=True)


class Module:
    """parses what the keyboard sends, ACKs it, can inject frames"""
    LEN = {0xA1: 10, 0xA2: 16, 0xA3: 4, 0xA4: 3, 0xA6: 3, 0xA8: 7, 0x61: 3}

    def __init__(self, m):
        self.m, self.pos, self.frames, self.ack, self.buf, self.last_t = m, 0, [], True, [], 0

    def poll(self):
        tx = self.m.uart_tx
        while self.pos < len(tx):
            t, b = tx[self.pos]
            self.pos += 1
            if self.buf and t - self.last_t > 1.0:
                self.frames.append((self.buf_t, tuple(self.buf), 'TRUNC'))
                self.buf = []
            if not self.buf:
                self.buf_t = t
            self.buf.append(b)
            self.last_t = t
            n = self.LEN.get(self.buf[0])
            if n is None:
                self.frames.append((t, tuple(self.buf), 'BAD'))
                self.buf = []
            elif len(self.buf) == n:
                f = tuple(self.buf)
                ok = (sum(f[:-1]) & 0xFF) == f[-1] if f[0] != 0x61 else f == (0x61, 0x0D, 0x0A)
                self.frames.append((self.buf_t, f, 'OK' if ok else 'CKSUM'))
                self.buf = []
                if self.ack and f[0] != 0x61:
                    self.send(t + 0.3, (0x61, 0x0D, 0x0A))

    def send(self, t, frame):
        self.m.uart_rx += [(t + 0.09 * i, b) for i, b in enumerate(frame)]


def frame3(a, b):
    return (a, b, (a + b) & 0xFF)


def mk(path=None):
    gc.collect()  # unicorn segfaults in mem_map with many live engines
    m = G80(path or BIN, NM, ext_pins={(0, 7): 1})
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.schedule = []
    return m, Module(m)


def run(m, mod, t_end, step=1.0):
    t = m.t_ms()
    while t < t_end:
        t = min(t_end, t + step)
        m.run(t)
        mod.poll()


def frames(mod, t0=0, t1=1e9, kind=None):
    return [f for t, f, st_ in mod.frames if t0 <= t < t1 and (kind is None or f[0] == kind)]


def a1(mods, keys):
    f = [0xA1, mods, 0] + list(keys) + [0] * (6 - len(keys))
    return tuple(f + [sum(f) & 0xFF])


def a2(usages_):
    b = [0] * 14
    for u in usages_:
        b[u >> 3] |= 1 << (u & 7)
    f = [0xA2] + b
    return tuple(f + [sum(f) & 0xFF])


def hold(m, t_down, t_up, names, dt=100):
    """press the keys one after the other (dt apart), all released at t_up"""
    for i, n in enumerate(names):
        m.schedule.append((t_down + i * dt, t_up, KEY[n][0]))


def usages(names):
    return [KEY[n][1] for n in names]


def rd(m, sym, n):
    return bytes(m.uc.mem_read(m.sym[sym], n))


def st(m, off, n=1):
    return int.from_bytes(rd(m, 'g80_radio_stats', off + n)[off:off + n], 'little')


CUR = {}  # the engine and module of the running scenario


def inject(fn, timeout=12.0):
    """run fn(m) at a clean thread-mode point of the firmware (the emulator's inject mechanism), return its result"""
    m, mod = CUR['m'], CUR['mod']
    res = []
    m.inject.append((m.t_ms(), lambda mm: res.append(fn(mm))))
    t0 = m.t_ms()
    while not res:
        assert m.t_ms() - t0 < timeout, 'inject not executed'
        run(m, mod, m.t_ms() + 0.25, 0.25)
    return res[0]


def call(name, *args):
    return inject(lambda mm: mm.call(name, *args))


def now32():
    return call('timer_read32')


def indicator():
    """(led, r, g, b) of g80_wl_indicator(), None when it draws nothing. Out parameters live in RAM below
    the stack and are restored afterwards."""
    def f(mm):
        addr = (mm.uc.reg_read(UC_ARM_REG_SP) - 0x300) & ~3
        old = bytes(mm.uc.mem_read(addr, 4))
        ret = mm.call('g80_wl_indicator', addr, addr + 1, addr + 2, addr + 3) & 0xFF
        out = bytes(mm.uc.mem_read(addr, 4))
        mm.uc.mem_write(addr, old)
        return tuple(out) if ret else None
    return inject(f)


def patch_option_stub(tmpdir):
    """Copy of the image whose g80_wl_nkro_enabled() reads a flag byte in free flash (the test flips it
    through the emulator's memory): B.W to a stub  adr r0,flag ; ldrb r0,[r0] ; bx lr ; nop ; flag."""
    img = bytearray(open(BIN, 'rb').read())
    sym = Syms(NM)
    f = sym['g80_wl_nkro_enabled'] & ~1
    # unused flash between the end of the code and the wear-leveling aux sectors at 0xF6C0 (zero-filled;
    # 0xF6C0..0xFFBF is erased / programmed by the wear-leveling driver, a stub there would be wiped)
    stub = (max(i for i in range(0xF6C0) if img[i]) + 1 + 15) & ~15
    assert stub + 16 <= 0xF6C0 and img[stub:stub + 16] == bytes(16)
    img[stub:stub + 8] = bytes([0x01, 0xA0, 0x00, 0x78, 0x70, 0x47, 0xC0, 0x46])
    img[stub + 8] = 1
    off = stub - (f + 4)
    s = (off >> 24) & 1
    i1, i2 = (off >> 23) & 1, (off >> 22) & 1
    j1, j2 = (~(i1 ^ s)) & 1, (~(i2 ^ s)) & 1
    hw1 = 0xF000 | (s << 10) | ((off >> 12) & 0x3FF)
    hw2 = 0x9000 | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7FF)
    img[f:f + 4] = struct.pack('<HH', hw1, hw2)
    path = os.path.join(tmpdir, 'radio_patched.bin')
    open(path, 'wb').write(img)
    return path, stub + 8


all_bad, all_over, all_stuck, all_sys = [], [], [], []


def harvest(m, mod):
    all_bad.extend(x for x in mod.frames if x[2] != 'OK' or (x[1][0] == 0xA6 and x[1][1] not in RADIO_WL))
    mod.frames = [x for x in mod.frames]  # (kept: later tests look at the full history)
    all_over.extend(m.uart_overwrites)
    if st(m, ST_TXSTUCK, 2):
        all_stuck.append(st(m, ST_TXSTUCK, 2))
    if m.faults or m.resets or m.isp is not None:
        all_sys.append((m.faults[:1], m.resets, m.isp))


tmp = tempfile.mkdtemp(prefix='suite_radio_')
try:
    patched, flag_addr = patch_option_stub(tmp)

    # ------------------------------------------------------------------ E1: patched image, wireless on
    m = None  # free the previous engine first
    m, mod = mk(patched)
    CUR.update(m=m, mod=mod)
    run(m, mod, 1500)
    p_none = st(m, ST_BAT)
    check('T-API-0', p_none == 0xFF, 'battery percentage before any 5C = %#x' % p_none)
    mod.send(m.t_ms(), (0x5B, 0x32, 0x8D))  # connected
    run(m, mod, 1700)
    check('T-LINK-0', st(m, ST_LINK) == 0x32, 'link %#x' % st(m, ST_LINK))

    # --- T-NKRO-1..3: 7, 10 and 15 keys pressed 100 ms apart, all released together
    t0 = 2000
    for tid, names in (('T-NKRO-1', 'QWERTYU'), ('T-NKRO-2', 'QWERTYUIOP'), ('T-NKRO-3', NKRO_PRESS_ORDER)):
        t_up = t0 + len(names) * 100 + 500
        hold(m, t0, t_up, names)
        run(m, mod, t_up - 100)
        f1 = frames(mod, t0, t_up - 100, 0xA1)
        f2 = frames(mod, t0, t_up - 100, 0xA2)
        want1 = a1(0, usages(names[:5]))
        want2 = a2(usages(names[5:]))
        check(tid, f1[-1:] == [want1] and f2[-1:] == [want2] and all(len(f) == 16 for f in f2),
              '%d keys: last A1 %s (want %s), last A2 %s (want %s), A2 frames %d' % (len(names), f1[-1:], want1, f2[-1:], want2, len(f2)))
        run(m, mod, t_up + 500)
        f1 = frames(mod, t_up, t_up + 500, 0xA1)
        f2 = frames(mod, t_up, t_up + 500, 0xA2)
        t0 = t_up + 700
    check('T-NKRO-5a', f1[-1:] == [a1(0, [])] and f2[-1:] == [a2([])], 'after releasing 15 keys at once: last A1 %s, last A2 %s' % (f1[-1:], f2[-1:]))

    # --- T-NKRO-4 / T-NKRO-5: release order, A2 only on change
    ta = t0
    rel = {'E': ta + 1500, 'U': ta + 2000, 'Y': ta + 2500}
    for i, n in enumerate('QWERTYU'):
        m.schedule.append((ta + i * 100, rel.get(n, ta + 3200), KEY[n][0]))
    run(m, mod, ta + 1900)
    f1b = frames(mod, ta + 1490, ta + 1900, 0xA1)
    f2b = frames(mod, ta + 1490, ta + 1900, 0xA2)
    run(m, mod, ta + 2400)
    f2c = frames(mod, ta + 1990, ta + 2400, 0xA2)
    run(m, mod, ta + 2900)
    f2d = frames(mod, ta + 2490, ta + 2900, 0xA2)
    run(m, mod, ta + 3700)
    f1e = frames(mod, ta + 3190, ta + 3700, 0xA1)
    f2all = frames(mod, ta, ta + 3700, 0xA2)
    check('T-NKRO-4', f1b[-1:] == [a1(0, usages('QWRT'))] and not f2b and f2c[-1:] == [a2(usages('Y'))]
          and f2d[-1:] == [a2([])] and f1e[-1:] == [a1(0, [])],
          'E (A1) released: A1 %s, no A2 change %s; U (extra) released: %s; Y released: %s; all up: %s' % (f1b[-1:], f2b, f2c[-1:], f2d[-1:], f1e[-1:]))
    check('T-NKRO-5', [f[4] for f in f2all] == [0x10, 0x11, 0x10, 0x00] and len(f2all) == 4,
          'A2 frames over the whole scenario (byte 3 of the bitmap): %s' % [hex(f[4]) for f in f2all])

    # --- T-NKRO-6: usage >= 0x70 (the Ro key, 0x87)
    t0 = ta + 4000
    m.schedule.append((t0, t0 + 400, KEY['RO'][0]))
    run(m, mod, t0 + 800)
    fa = frames(mod, t0, t0 + 800, 0xA1)
    f2x = frames(mod, t0, t0 + 800, 0xA2)
    t1 = t0 + 1000
    hold(m, t1, t1 + 900, 'QWERT')
    m.schedule.append((t1 + 600, t1 + 800, KEY['RO'][0]))
    run(m, mod, t1 + 1500)
    fb = frames(mod, t1, t1 + 1500, 0xA1)
    f2y = frames(mod, t1, t1 + 1500, 0xA2)
    check('T-NKRO-6', a1(0, [0x87]) in fa and not f2x and not any(0x87 in f for f in fb) and not f2y,
          'Ro alone: A1 %s; Ro as the 6th key: dropped (A1 frames with 0x87: %d), A2 frames %d' % (fa[:1], sum(0x87 in f for f in fb), len(f2x) + len(f2y)))

    # --- T-NKRO-8: modifiers
    t0 = t1 + 2000
    m.schedule.append((t0, t0 + 600, KEY['LSHIFT'][0]))
    hold(m, t0 + 100, t0 + 600, 'QW')
    run(m, mod, t0 + 500)
    f = frames(mod, t0, t0 + 500, 0xA1)
    run(m, mod, t0 + 1000)
    check('T-NKRO-8', f[-1:] == [a1(0x02, usages('QW'))], 'LShift+Q+W: last A1 %s' % f[-1:])

    # --- T-NKRO-7: runtime option off
    t0 += 1500
    hold(m, t0, t0 + 2500, 'QWERTYU')
    run(m, mod, t0 + 900)
    f2z = frames(mod, t0, t0 + 900, 0xA2)
    m.uc.mem_write(flag_addr, bytes([0]))  # option off
    m.schedule.append((t0 + 1000, t0 + 2500, KEY['P'][0]))
    run(m, mod, t0 + 1500)
    fo1 = frames(mod, t0 + 950, t0 + 1500, 0xA1)
    fo2 = frames(mod, t0 + 950, t0 + 1500, 0xA2)
    run(m, mod, t0 + 3200)
    fo3 = frames(mod, t0 + 2600, t0 + 3200, 0xA1)
    fo4 = frames(mod, t0 + 2600, t0 + 3200, 0xA2)
    m.uc.mem_write(flag_addr, bytes([1]))
    check('T-NKRO-7', f2z and fo1[-1:] == [a1(0, sorted(usages('QWERTYUP'))[:6])] and fo2 == [a2([])] and not fo4,
          'option off with extras held: A1 %s (want the 6 lowest), A2 frames since: %s; after release A1 %s' % (fo1[-1:], fo2, fo3[-1:]))
    harvest(m, mod)

    # --- T-A4
    t0 += 3600
    run(m, mod, t0)
    for u in (0x81, 0, 0x82, 0, 0x83, 0):
        call('host_system_send', u)
        run(m, mod, m.t_ms() + 40)
    got = frames(mod, t0, m.t_ms(), 0xA4)
    want = [frame3(0xA4, 1), frame3(0xA4, 0), frame3(0xA4, 2), frame3(0xA4, 0), frame3(0xA4, 4), frame3(0xA4, 0)]
    # both kinds queued together (module silent): A3 and A4 must both survive
    mod.ack = False
    t1 = m.t_ms() + 100
    run(m, mod, t1)
    inject(lambda mm: (mm.call('host_consumer_send', 0xEA), mm.call('host_system_send', 0x81)))
    mod.ack = True
    run(m, mod, t1 + 80)
    g3 = frames(mod, t1, t1 + 80, 0xA3)
    g4 = frames(mod, t1, t1 + 80, 0xA4)
    call('host_consumer_send', 0)
    call('host_system_send', 0)
    run(m, mod, m.t_ms() + 100)
    check('T-A4', got == want and (0xA3, 0xEA, 0x00, 0x8D) in g3 and frame3(0xA4, 1) in g4,
          'A4 frames %s (want %s); A3+A4 queued together: A3 %s A4 %s' % (got, want, g3[:1], g4[:1]))
    harvest(m, mod)

    # --- T-LINK-1
    def leds_after(second):
        mod.send(m.t_ms() + 1, (0x5A, 0x02, 0x5C))
        run(m, mod, m.t_ms() + 30)
        a = st(m, ST_LEDS)
        mod.send(m.t_ms() + 1, second)
        run(m, mod, m.t_ms() + 30)
        return a, st(m, ST_LEDS), st(m, ST_LINK)
    r33 = leds_after((0x5B, 0x33, 0x8E))
    r31 = leds_after((0x5B, 0x31, 0x8C))
    r32 = leds_after((0x5B, 0x32, 0x8D))
    check('T-LINK-1', r33 == (2, 0, 0x33) and r31 == (2, 0, 0x31) and r32 == (2, 2, 0x32),
          'Caps LED set then 5B 33 -> %s, then 5B 31 -> %s, then 5B 32 -> %s (host_leds before, after, link)' % (r33, r31, r32))

    # --- T-LINK-2 blink phase and T-LINK-3 windows
    mod.send(m.t_ms() + 1, (0x5B, 0x33, 0x8E))
    run(m, mod, m.t_ms() + 20)
    samples = []
    for i in range(80):
        run(m, mod, m.t_ms() + 16)
        ind = indicator()
        samples.append((m.t_ms(), ind is not None and (ind[2] or ind[3]) != 0, ind))
    edges = [samples[i][0] for i in range(1, len(samples)) if samples[i][1] != samples[i - 1][1]]
    runs = [round(b - a) for a, b in zip(edges, edges[1:])]
    led_ok = samples[0][2] is not None and samples[0][2][0] == 35
    check('T-LINK-2', len(edges) >= 4 and all(256 - 48 <= r <= 256 + 48 for r in runs) and led_ok,
          'link down: %d edges, phase lengths %s ms (want 256), LED %s' % (len(edges), runs, samples[0][2]))

    def window(code, age_ms):
        """indication shown with the link code's window age_ms old, and 300 ms later (window over)"""
        mod.send(m.t_ms() + 1, (0x5B, code, 0x5B + code))
        run(m, mod, m.t_ms() + 20)
        t = now32()
        m.uc.mem_write(m.sym['g80_link_t'], struct.pack('<I', (t - age_ms) & 0xFFFFFFFF))
        n = [indicator() is not None]
        run(m, mod, m.t_ms() + 300)
        n.append(indicator() is not None)
        return n
    w_lost = window(0x33, 9800)
    w_pair = window(0x31, 19800)
    w_conn = window(0x32, 2800)
    check('T-LINK-3a', w_lost == [True, False] and w_pair == [True, False] and w_conn == [True, False],
          'indicator shown / still shown 300 ms later: link down at 9.8 s %s, pairing at 19.8 s %s, connected at 2.8 s %s' % (w_lost, w_pair, w_conn))
    # wake: the link state is stale (connected), the steady 3 s start again
    t = now32()
    m.uc.mem_write(m.sym['g80_link_t'], struct.pack('<I', (t - 5000) & 0xFFFFFFFF))
    before = indicator()
    call('g80_radio_on_wake')
    after = indicator()
    age = (now32() - struct.unpack('<I', rd(m, 'g80_link_t', 4))[0]) & 0xFFFFFFFF
    check('T-LINK-3b', before is None and after is not None and after[0] == 35 and after[3] == 255 and age < 50,
          'steady channel indication after the wake call: before %s, after %s, link age %d ms' % (before, after, age))
    harvest(m, mod)

    # --- T-POLL: cadence and wake
    def n53(a, b):
        return [f for f in frames(mod, a, b, 0xA6) if f[1] == 0x53]
    run(m, mod, m.t_ms() + 100)  # the wake call above asked for a request: sent here
    t = now32()
    m.uc.mem_write(m.sym['g80_bat_time'], struct.pack('<I', (t - 59000) & 0xFFFFFFFF))
    ta = m.t_ms()
    run(m, mod, ta + 800)
    early = len(n53(ta, ta + 800))
    run(m, mod, ta + 1400)
    on_time = n53(ta + 800, ta + 1400)
    run(m, mod, ta + 2200)
    again = len(n53(ta + 1400, ta + 2200))
    bt = (struct.unpack('<I', rd(m, 'g80_bat_time', 4))[0] - t) & 0xFFFFFFFF
    check('T-POLL-1', early == 0 and len(on_time) == 1 and again == 0 and abs(bt - 1000) < 200,
          'request due 1 s after "now - 59 s": before it %d, in the window %d, after it %d (60 s period); timer restarted %d ms after now' % (early, len(on_time), again, bt))

    tw = m.t_ms()
    m.uc.mem_write(m.sym['g80_bat_time'], struct.pack('<I', now32()))  # nothing due
    call('g80_radio_on_wake')
    run(m, mod, tw + 120)
    w1 = len(n53(tw, tw + 120))
    mod.send(m.t_ms() + 1, (0x5B, 0x33, 0x8E))  # link lost: no request until the link is back
    run(m, mod, m.t_ms() + 30)
    tw2 = m.t_ms()
    call('g80_radio_on_wake')
    run(m, mod, tw2 + 200)
    w2 = len(n53(tw2, tw2 + 200))
    mod.send(m.t_ms() + 1, (0x5B, 0x32, 0x8D))
    run(m, mod, m.t_ms() + 200)
    w3 = len(n53(tw2 + 200, 1e9))
    # a half frame in the receiver at the wake call is dropped
    t4 = m.t_ms() + 1
    mod.send(t4, (0x5B, 0x33))               # first two bytes of 5B 33 8E
    run(m, mod, t4 + 0.6)
    bad0 = st(m, ST_RXBAD, 4)
    call('g80_radio_on_wake')
    mod.send(m.t_ms() + 0.2, (0x8E,))
    run(m, mod, m.t_ms() + 30)
    check('T-POLL-2', w1 == 1 and w2 == 0 and w3 == 1 and st(m, ST_LINK) == 0x32 and st(m, ST_RXBAD, 4) == bad0 + 1,
          'wake while linked: %d request; wake with the link lost: %d; after the link is back: %d; half frame dropped (link %#x, rx_bad %d -> %d)' % (
              w1, w2, w3, st(m, ST_LINK), bad0, st(m, ST_RXBAD, 4)))
    harvest(m, mod)

    # --- T-API-2: battery
    def bat_state():
        """(low, critical) as the battery code sees them: Fn warning running, saves inhibited"""
        return (int(call('g80_batt_fn_state') & 0xFF != 0xFF), call('g80_save_inhibited') & 0xFF)
    pcts = []
    for v in (0x50, 0x70, 0xFF):
        mod.send(m.t_ms() + 1, (0x5C, v, (0x5C + v) & 0xFF))
        run(m, mod, m.t_ms() + 30)
        pcts.append(st(m, ST_BAT))
    states = []
    for code in (0x21, 0x22, 0x23, 0x21):
        mod.send(m.t_ms() + 1, (0x5B, code, 0x5B + code))
        run(m, mod, m.t_ms() + 30)
        states.append(bat_state())
    check('T-API-2a', pcts == [0x50, 0x70, 0xFF] and states == [(1, 0), (1, 1), (0, 0), (1, 0)],
          'percentage byte for 5C 50 / 70 / FF = %s, (low, critical) after 5B 21/22/23/21 = %s' % (pcts, states))
    # charge edge (P3.5 low = charging): ends the low state like 5B 23; the input debounce takes a while
    m.ext_pins[(3, 5)] = 0
    run(m, mod, m.t_ms() + 1500)
    low_after = bat_state()[0]
    m.ext_pins[(3, 5)] = 1
    run(m, mod, m.t_ms() + 600)
    check('T-API-2b', states[-1] == (1, 0) and low_after == 0, 'low state before the charge edge %s, after %d' % (states[-1], low_after))
    harvest(m, mod)

    # --- T-API-1: idle, sampled at clean points while a key frame goes out
    run(m, mod, m.t_ms() + 100)
    idle0 = call('g80_radio_idle') & 0xFF
    tk = m.t_ms() + 20
    m.schedule.append((tk, tk + 200, KEY['A'][0]))
    obs = []
    for i in range(160):
        m.inject.append((tk - 10 + i * 0.5, lambda mm, i=i: obs.append((mm.t_ms(), mm.call('g80_radio_idle') & 0xFF))))
    run(m, mod, tk + 400)
    busy = [o for o in obs if o[1] == 0]
    idle1 = call('g80_radio_idle') & 0xFF
    check('T-API-1', idle0 == 1 and idle1 == 1 and len(obs) == 160 and busy and busy[0][0] - tk < 20,
          'idle at rest %d, %d of %d samples busy (first %.1f ms after the press), idle after %d' % (idle0, len(busy), len(obs), (busy[0][0] - tk) if busy else -1, idle1))
    harvest(m, mod)

    # ------------------------------------------------------------------ E2: unpatched image
    m = None
    CUR.clear()
    m, mod = mk()
    CUR.update(m=m, mod=mod)
    run(m, mod, 1000)
    dflt = call('g80_wl_nkro_enabled') & 0xFF
    mod.send(m.t_ms(), (0x5B, 0x32, 0x8D))
    run(m, mod, 1200)
    hold(m, 1500, 3000, 'QWERTYU')
    run(m, mod, 2500)
    f2 = frames(mod, 1500, 2500, 0xA2)
    check('T-NKRO-9', dflt == 1 and f2[-1:] == [a2(usages('YU'))], 'weak default of g80_wl_nkro_enabled() = %d, A2 with 7 keys %s' % (dflt, f2[-1:]))
    # --- T-POLL-1b: no request in the first seconds of a link
    run(m, mod, 8000)
    n = [f for f in frames(mod, 1200, 8000, 0xA6) if f[1] == 0x53]
    check('T-POLL-1b', not n, 'A6 53 between 1.2 s and 8 s of a linked wireless session: %d (the stock cadence would be 5 s)' % len(n))
    harvest(m, mod)
finally:
    CUR.clear()
    m = None
    shutil.rmtree(tmp, ignore_errors=True)

check('T-RADIO-WL', not all_bad and not all_over and not all_stuck and not all_sys,
      'bad / unknown / truncated frames %s, TX overwrites %s, TX timeouts %s, faults/resets/ISP %s' % (all_bad[:3], all_over[:3], all_stuck, all_sys[:1]))
print('%d/%d PASS' % (sum(results), len(results)))
