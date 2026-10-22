# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Deep sleep / idle countdown / LVD (g80_lpm.c) on the emulator with the PMU / clock / GPIO-IRQ model
of emu_lpm.py and a simulated radio module (ACKs every frame, can send its own frames).
usage: python suite_sleep.py <fw_dir>

T-SLEEP-1   countdown values per event (key unlinked 150, linked 3000/6000/18000/36000 by option = 5/10/30/60 min,
            5B 32 = the option time, 5B 33 150, 5B 22 150), 1 tick per 100 ms, frozen with option "never", while
            pairing and while a key is held; factory default option = never
T-SLEEP-2   each guard blocks sleep at zero (USB not stopped, save pending, TX busy, pairing) and sleep
            follows once it clears (factory-reset guard: not injectable, the flag starts the reset) and sleep follows once it clears; a charging cable does not block
T-SLEEP-CR6 critical battery (5B 22) + pending save: sleeps anyway, the save stays in RAM (no flash op, also after the wake) until 5B 23
            during the sleep, the save is written after the wake
T-SLEEP-3   register trace of the sleep entry / wake = the stock order and values (hand-written list from
            the analysis of stock v1.10, FUN_194 0x0244..0x02F0, FUN_A04, FUN_BA0, FUN_662A), the
            watchdog restarted before SysTick and the LED engine
T-SLEEP-4   state at WFI (PMU 2, ILRC, AHBCP 2, LPCTRL 2, IHRC off, PRIMASK, WDT off, SysTick off, LEDs
            off, only IRQ28/31 enabled) and after the wake (48 MHz, LPCTRL 5, WDT on, SysTick back, LED
            engine running, P3/P0 IRQs off, NVIC as before); stale UART input dropped
T-SLEEP-5   row-edge wake: the wake key goes out (A1 press + release), nothing stuck
T-SLEEP-6   no flash operation and no clock/flash violation inside any sleep window (all sleeps)
T-SLEEP-7   slider OFF while asleep: P0.7 wakes the keyboard, USB mode (A6 11, USB restarted)
T-SLEEP-8   bounded waits: IHRC never ready on wake / clock switch never done (entry and wake) -> reset
            (AIRCR) within the bound, then a normal boot (no ISP, no fault)
T-SLEEP-9   5B 33 (link lost) -> sleep -> wake key: A6 00 A6 before the A1
T-SLEEP-10  a pending deferred save is written before the sleep, never during it
T-SLEEP-11  wake sources do not read back armed (P3 IE stuck at 0): no WFI, abort counted, clocks
            restored, keys still work
T-SLEEP-12  key held, no key event for 10 min: empty report, sleep; release wakes, nothing stuck
T-SLEEP-ISP Fn+RShift+Esc after a wake still enters the ROM ISP (watchdog off)
T-LVD       SYS0_LVDCTRL written exactly once with 0xC002 (via, default); never in the minimal image
T-SLEEP-PAIR P0-5: pairing (5B 31) freezes the countdown inside the 20 s window; at its end the countdown is set
            to 30 ticks once and the keyboard sleeps ~3 s later (a failed pairing does not keep it awake); link
            lost (5B 33): at the end of the 10 s reconnect window 5 ticks, sleep ~0.5 s later
T-SLEEP-VEC wake vectors 0xB0 (P3, IRQ28) / 0xBC (P0, IRQ31) point to a real handler, not _unhandled_exception;
            both IRQs taken while awake (a slip that left them enabled): sources disarmed, no fault / ISP /
            reset, keys still work
T-SLEEP-ACT a key the on-board editor swallows (no key event) still reloads the countdown (matrix activity)
T-SLEEP-LED LEDs off after 1 min, deep sleep after 5 min (wireless): after 61 s idle the LEDs are dark (rgb_matrix
            suspended, frame black) and the keyboard is awake with the countdown still running; at the end of the
            countdown it deep-sleeps, the wake key goes out, the LEDs come back and the countdown is reloaded
"""
import sys, os, struct, gc, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *
from emu_lpm import G80Lpm, PMU_CTRL, P0, P3

FW = sys.argv[1]
FN, A_, S_, K_, ESC, RSFT = (5, 11), (3, 1), (3, 2), (3, 8), (0, 0), (4, 13)
USB_STOP = 1
LINK_OFF = 30            # g80_radio_stats.link (7 x uint32 + battery + host_leds)
PRESS_A = (0xA1, 0, 0, 0x04, 0, 0, 0, 0, 0, 0xA5)
EMPTY = (0xA1, 0, 0, 0, 0, 0, 0, 0, 0, 0xA1)
WAKE_CMD = (0xA6, 0x00, 0xA6)
WAIT_MAX = 0xFFFF
results = []


def check(tid, ok, detail):
    results.append(ok)
    print('%s  %-12s %s' % ('PASS' if ok else 'FAIL', tid, detail), flush=True)


class Module:
    """simulated radio module: parses the keyboard's frames, ACKs them in time, can send frames"""
    LEN = {0xA1: 10, 0xA3: 4, 0xA6: 3, 0xA8: 7, 0x61: 3}

    def __init__(self, m):
        self.m, self.pos, self.frames, self.ack, self.buf, self.last_t = m, 0, [], True, [], 0
        m.tx_listener = lambda mm: self.poll()

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
        for i, b in enumerate(frame):
            bisect.insort(self.m.uart_rx, (t + 0.09 * i, b))


def mk(kind='via', slider=True, **kw):
    gc.collect()  # unicorn segfaults in mem_map with many live engines
    # stress=True: flash content changes are applied between chunks (writing the code page from inside
    # a hook while unicorn emulates crashes it); also gives the raw HID injection
    kw.setdefault('stress', True)
    m = G80Lpm(FW + '/epomaker_galaxy80_%s.bin' % kind, FW + '/nm_%s.txt' % kind,
               ext_pins={(0, 7): 1 if slider else 0}, **kw)
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.schedule = []
    return m, Module(m)


def rd(m, sym, n, off=0):
    return bytes(m.uc.mem_read(m.sym[sym] + off, n))


def wr(m, sym, data, off=0):
    m.uc.mem_write(m.sym[sym] + off, bytes(data))


def cd(m):
    return struct.unpack('<H', rd(m, 'g80_lpm_countdown', 2))[0]


def set_cd(m, v):
    wr(m, 'g80_lpm_countdown', struct.pack('<H', v))


def get_opt(m):
    return (struct.unpack('<I', rd(m, 'g80_ucfg', 4))[0] >> 13) & 7     # g80_ucfg.deep_sleep, bits 13..15


def set_opt(m, v):
    u = struct.unpack('<I', rd(m, 'g80_ucfg', 4))[0]
    wr(m, 'g80_ucfg', struct.pack('<I', (u & ~(7 << 13)) | (v << 13)))


def tjump(m, ms):
    """timer_read32() jumps forward by ms (chibios ms_offset); below 65 s so that 16-bit timers cannot wrap"""
    a = m.sym['ms_offset']
    m.uc.mem_write(a, struct.pack('<I', (struct.unpack('<I', bytes(m.uc.mem_read(a, 4)))[0] + ms) & 0xFFFFFFFF))


def patch_getter(m, name, value):
    """option getter (g80_battery.c, noinline) replaced by movs r0,#value / bx lr"""
    m.uc.mem_write(m.sym[name] & ~1, bytes([value & 0xFF, 0x20, 0x70, 0x47]))
    m.uc.ctl_flush_tb()


def stats(m):
    s, a, b, g = struct.unpack('<HHHB', rd(m, 'g80_lpm_stats', 7))
    return dict(sleeps=s, aborts=a, blocked=b, guard=g)


def link(m):
    return rd(m, 'g80_radio_stats', 1, LINK_OFF)[0]


def usb_state(m):
    return m.uc.mem_read(m.sym['USBD1'], 1)[0]


def tap(m, t, k, hold=60):
    m.schedule.append((t, t + hold, k))


def fn(m, t, key, hold=60):
    m.schedule += [(t, t + hold + 40, FN), (t + 10, t + 10 + hold, key)]


def frames(mod, t0=0, t1=1e9, kind=None):
    return [f for t, f, st in mod.frames if t0 <= t < t1 and (kind is None or f[0] == kind)]


def slept(m, n0):
    return [s for s in m.sleeps[n0:] if not (s['cause'] or '').startswith('pending')]   # incl. one in progress


def boot_linked(opt=1, **kw):
    m, mod = mk(**kw)
    m.run(1500)
    mod.send(1500, (0x5B, 0x32, 0x8D))
    m.run(1600)
    if opt is not None:
        set_opt(m, opt)
    return m, mod


def wake_with_a(m, t):
    tap(m, t, A_, 60)
    m.run(t + 200)


all_bad, all_over, all_viol, sleep_flash = [], [], [], []


def collect(m, mod):
    """whole-run checks shared by every engine (T-SLEEP-6, no garbled frame, no TX overwrite)"""
    all_bad.extend(x for x in mod.frames if x[2] != 'OK')
    all_over.extend(m.uart_overwrites)
    all_viol.extend(m.violations + m.lpm_viol)
    for s in m.sleeps:
        end = s['wake_t'] if s['wake_t'] is not None else m.t_ms()
        for op in m.flash_ops:
            if s['t'] - 5 <= op[-1] <= end + 0.5:
                sleep_flash.append((s['t'], op))


# ======================================================================== E1: main wireless engine
m = None
m, mod = boot_linked(opt=None)
opt0 = get_opt(m)
vals = {}
tap(m, 1700, A_)
m.run(1790)
vals['opt0'] = cd(m)
for o, exp in ((1, 3000), (2, 6000), (3, 18000), (4, 36000)):
    set_opt(m, o)
    t = m.t_ms() + 20
    tap(m, t, A_)
    m.run(t + 75)
    vals['opt%d' % o] = cd(m)
set_opt(m, 1)
c0 = cd(m)
t = m.t_ms()
m.run(t + 2000)
rate = c0 - cd(m)
mod.send(m.t_ms(), (0x5B, 0x33, 0x8E))
m.run(m.t_ms() + 20)
vals['5B33'] = cd(m)
t = m.t_ms() + 10
tap(m, t, A_)
m.run(t + 75)
vals['key_unlinked'] = cd(m)
set_opt(m, 3)
mod.send(m.t_ms(), (0x5B, 0x32, 0x8D))
m.run(m.t_ms() + 20)
vals['5B32'] = cd(m)                         # the option time (30 min), not a fixed 5 min
set_opt(m, 1)
mod.send(m.t_ms(), (0x5B, 0x22, 0x7D))
m.run(m.t_ms() + 20)
vals['5B22'] = cd(m)
mod.send(m.t_ms(), (0x5B, 0x23, 0x7E))
mod.send(m.t_ms() + 5, (0x5B, 0x32, 0x8D))
m.run(m.t_ms() + 30)
# frozen: option never, pairing, key held
set_opt(m, 0)
c1 = cd(m); m.run(m.t_ms() + 1000); fr_never = c1 - cd(m)
set_opt(m, 1)
mod.send(m.t_ms(), (0x5B, 0x31, 0x8C))
m.run(m.t_ms() + 20)
c1 = cd(m); m.run(m.t_ms() + 1000); fr_pair = c1 - cd(m)
mod.send(m.t_ms(), (0x5B, 0x32, 0x8D))
m.run(m.t_ms() + 20)
t = m.t_ms() + 10
m.schedule.append((t, t + 1300, A_))
m.run(t + 200)
c1 = cd(m); m.run(t + 1200); fr_held = c1 - cd(m)
m.run(t + 1400)
ok = (opt0 == 0 and vals['opt0'] == 0 and abs(vals['opt1'] - 3000) <= 1 and abs(vals['opt2'] - 6000) <= 1
      and abs(vals['opt3'] - 18000) <= 1 and abs(vals['opt4'] - 36000) <= 1 and 19 <= rate <= 21 and vals['5B33'] == 150
      and abs(vals['key_unlinked'] - 150) <= 1 and abs(vals['5B32'] - 18000) <= 1 and 149 <= vals['5B22'] <= 150 and fr_never == 0 and fr_pair == 0 and fr_held == 0 and not m.sleeps)
check('T-SLEEP-1', ok, 'factory option=%d, values %s, ticks in 2 s=%d, frozen never/pairing/held=%d/%d/%d, sleeps=%d' % (
    opt0, vals, rate, fr_never, fr_pair, fr_held, len(m.sleeps)))

# ---- first sleep: trace, state, wake key (T-SLEEP-3/4/5)
pre = {}


def on_enter(mm):
    pre['iser'] = mm.nvic_iser
    pre['csr'] = mm.reg[0xE000E010] & 7
    pre['t'] = mm.t_ms()
    pre['isr'] = struct.unpack('<I', bytes(mm.uc.mem_read(mm.sym['g80_isr_steps'], 4)))[0]
    mod.send(mm.t_ms() + 0.01, (0x5A,))      # a partial frame that sits in the UART across the sleep


m.on_sleep_enter = on_enter
n_tr = len(m.lpm_trace)
n_sl = len(m.sleeps)
set_cd(m, 3)
m.run(m.t_ms() + 400)
s_list = slept(m, n_sl)
asleep_now = m._asleep is not None
t_w = m.t_ms() + 50
tap(m, t_w, A_, 80)
mod_n = len(mod.frames)
m.run(t_w + 0.2)
mod.send(m.t_ms() + 0.1, (0x5B, 0x32, 0x8D))   # arrives within 1 ms of the wake: must parse
m.run(t_w + 300)
m.on_sleep_enter = None
s_list = slept(m, n_sl)
tr = [(e[2], e[3], e[4]) for e in m.lpm_trace[n_tr:]]
i0 = next((i for i, x in enumerate(tr) if x[0] == 'WDT_CFG' and not x[1] & 1 and abs(m.lpm_trace[n_tr + i][0] - pre.get('t', -9)) < 0.01), len(tr))
tr = tr[i0:]                      # from the first register write of the sleep sequence

# hand-written stock order (analysis of stock v1.10): entry = FUN_194 @0x02A4..0x02EE with FUN_A04 and
# FUN_BA0; wake = FUN_662A. Predicates on (name, value). Only these registers are compared, in order.
STOCK_ENTRY = [
    ('WDT_CFG', lambda v: v == 0x5AFA0000),                 # watchdog off (@0x02B6)
    ('SYST_CSR', lambda v: v == 0),                         # SysTick off (@0x02C6)
    ('P3_IS', lambda v: v == 0), ('P3_IBS', lambda v: v == 0xFC0), ('P3_IEV', lambda v: v == 0),
    ('P3_IC', lambda v: v == 0xFFFF), ('P3_IE', lambda v: v == 0xFC0),          # FUN_A04
    ('ICPR', lambda v: v == 1 << 28), ('ISER', lambda v: v == 1 << 28),
    ('USB_CFG', lambda v: (v & 0x48000000) == 0),           # &= 0xB7FFFFFF (@0x02D8)
    ('CLKCFG', lambda v: v == 1), ('AHBCP', lambda v: v == 2), ('FLASH_LPCTRL', lambda v: v == 0x5AFA0002),
    ('ANBCTRL', lambda v: v == 0),                          # FUN_BA0
    ('USB_CFG', lambda v: v == 0),                          # @0x02E6
    ('PMU_CTRL', lambda v: v == 2), ('PMU_CTRL', lambda v: v == 0),   # WFI between (@0x02EA..0x02F0)
    ('FLASH_LPCTRL', lambda v: v == 0x5AFA0004), ('FLASH_LPCTRL', lambda v: v == 0x5AFA0005),
    ('ANBCTRL', lambda v: v == 1), ('CLKCFG', lambda v: v == 0), ('AHBCP', lambda v: (v & 7) == 0),  # FUN_662A
]
CMP = {'WDT_CFG', 'SYST_CSR', 'P3_IS', 'P3_IBS', 'P3_IEV', 'P3_IC', 'P3_IE', 'USB_CFG', 'CLKCFG', 'AHBCP',
       'FLASH_LPCTRL', 'ANBCTRL', 'PMU_CTRL'}
seq = []
for name, v, f in tr:
    if name in ('ICPR', 'ISER') and v == 1 << 28:
        seq.append((name, v))
    elif name in CMP:
        seq.append((name, v))
# cut at the end of the clock restore (first AHBCP write after the second PMU_CTRL write)
cut = None
pm = [i for i, x in enumerate(seq) if x[0] == 'PMU_CTRL']
if len(pm) >= 2:
    for i in range(pm[1], len(seq)):
        if seq[i][0] == 'AHBCP':
            cut = i + 1
            break
entry = seq[:cut] if cut else seq
match = len(entry) == len(STOCK_ENTRY) and all(n == sn and p(v) for (n, v), (sn, p) in zip(entry, STOCK_ENTRY))
# after the clock restore: watchdog enabled before SysTick restore and before the CT16B1 clock
rest = tr[[i for i, x in enumerate(tr) if x[0] == 'AHBCP'][-1] + 1:] if cut else []
i_wdt = next((i for i, x in enumerate(rest) if x[0] == 'WDT_CFG' and x[1] & 1), None)
i_st = next((i for i, x in enumerate(rest) if x[0] == 'SYST_CSR'), None)
i_ct = next((i for i, x in enumerate(rest) if x[0] == 'AHBCLKEN' and x[1] & 0x80), None)
order = i_wdt is not None and i_st is not None and i_ct is not None and i_wdt < i_st and i_wdt < i_ct
check('T-SLEEP-3', match and order, 'entry+wake trace %s stock list (%d vs %d writes)%s; after the clock restore: WDT on #%s, SysTick #%s, '
      'CT16B1 clock #%s' % ('==' if match else '!=', len(entry), len(STOCK_ENTRY), '' if match else ' got %s' % [(n, hex(v)) for n, v in entry],
                            i_wdt, i_st, i_ct))

s0 = s_list[0] if s_list else None
isr_after = struct.unpack('<I', rd(m, 'g80_isr_steps', 4))[0]
ok_at = s0 is not None and s0['pmu'] == 2 and s0['clk_st'] == 1 and s0['ahbcp'] == 2 and s0['lpctrl'] == 2 and s0['anbctrl'] == 0 \
    and s0['primask'] == 1 and not s0['wdt_en'] and s0['syst_csr'] == 0 and s0['iser'] == (1 << 28 | 1 << 31) \
    and s0['p3_ie'] == 0xFC0 and s0['p3_ibs'] == 0xFC0 and s0['p0_ie'] & 0x80 and not s0['ct1clk'] and not s0['selects'] \
    and all(c == 1 for c in s0['cols']) and s0['usb_cfg'] == 0 and s0['func'] == 'g80_lpm_sleep'
ok_after = asleep_now and m.hclk() == 48e6 and (m.reg[0x40062000] & 0xF) == 5 and (m.reg[0x40060000] & 1) and m.wdt['en'] \
    and (m.reg[0xE000E010] & 7) == pre.get('csr') and m.nvic_iser == pre.get('iser', -1) | (1 << 16) and m.reg[P3 + 0x18] == 0 \
    and not (m.reg[P0 + 0x18] & 0x80) and not ({16 + 28, 16 + 31} & m.pending) and isr_after > pre.get('isr', 1 << 31) + 1000 \
    and link(m) == 0x32
check('T-SLEEP-4', ok_at and ok_after, 'at WFI: %s | after: HCLK %.0f MHz LPCTRL=%d WDT=%s CSR %s->%s ISER %#x->%#x (+CT16B1) P3 IE=%#x, ISR steps +%d, '
      '5B 32 right after the wake parsed (partial 5A dropped): link=%#x' % (
          {k: s0[k] for k in ('pmu', 'clk_st', 'ahbcp', 'lpctrl', 'anbctrl', 'primask', 'wdt_en', 'syst_csr', 'iser', 'p3_ie', 'p0_ie',
                              'ct1clk', 'selects', 'usb_cfg', 'func', 'cause')} if s0 else None,
          m.hclk() / 1e6, m.reg[0x40062000] & 0xF, m.wdt['en'], pre.get('csr'), m.reg[0xE000E010] & 7, pre.get('iser', 0), m.nvic_iser,
          m.reg[P3 + 0x18], isr_after - pre.get('isr', 0), link(m)))
f = frames(mod, t_w - 1, t_w + 300, 0xA1)
check('T-SLEEP-5', s0 is not None and s0['cause'] == 'IRQ28' and f.count(PRESS_A) == 1 and f[-1:] == [EMPTY] and not m.faults and not m.resets,
      'wake cause %s at %.1f ms (key at %.1f), A1 after the wake %s' % (s0 and s0['cause'], s0['wake_t'] if s0 else -1, t_w, f))

# ---- T-SLEEP-9: link lost -> sleep -> wake key: A6 00 before the A1
mod.send(m.t_ms(), (0x5B, 0x33, 0x8E))
m.run(m.t_ms() + 30)
c33 = cd(m)
set_cd(m, 3)
n_sl = len(m.sleeps)
m.run(m.t_ms() + 500)
t_w = m.t_ms() + 20
tap(m, t_w, A_, 60)
m.run(t_w + 250)
f = frames(mod, t_w - 1, t_w + 250)
ok9 = len(slept(m, n_sl)) == 1 and WAKE_CMD in f and PRESS_A in f and f.index(WAKE_CMD) < f.index(PRESS_A) and 149 <= c33 <= 150
check('T-SLEEP-9', ok9, 'countdown after 5B 33=%d, sleeps=%d, frames after the wake key %s' % (c33, len(slept(m, n_sl)), f[:4]))
mod.send(m.t_ms(), (0x5B, 0x32, 0x8D))
m.run(m.t_ms() + 30)

# ---- T-SLEEP-2 guards
g_res = {}


def guard_case(name, setup, clear, expect_guard=None, expect_block=True):
    n_sl = len(m.sleeps)
    b0 = stats(m)['blocked']
    setup()
    set_cd(m, 3)
    m.run(m.t_ms() + 700)
    blocked = len(slept(m, n_sl)) == 0
    st = stats(m)
    clear()
    m.run(m.t_ms() + 1300)
    after = len(slept(m, n_sl)) == 1
    ok = blocked == expect_block and after and (expect_guard is None or (st['guard'] == expect_guard and st['blocked'] > b0))
    g_res[name] = (ok, 'blocked=%s guard=%d' % (blocked, st['guard']))
    t = m.t_ms() + 10
    wake_with_a(m, t)
    return ok


usbd = m.sym['USBD1']
guard_case('usb', lambda: m.uc.mem_write(usbd, bytes([3])), lambda: m.uc.mem_write(usbd, bytes([USB_STOP])), 2)
guard_case('tx', lambda: setattr(m, 'uart_temt_stuck', True), lambda: setattr(m, 'uart_temt_stuck', False), 7)
m.ext_pins[(3, 5)] = 0     # charging (P3.5 low): must not block
guard_case('charging', lambda: None, lambda: None, expect_block=False)
m.ext_pins.pop((3, 5))
m.run(m.t_ms() + 100)
# deferred save pending (Fn+S = SOCD toggle, written 1 s later, G80_SAVE_DELAY_MS): sleep waits for it (T-SLEEP-10)
n_ops = len(m.flash_ops)
n_sl = len(m.sleeps)
t = m.t_ms() + 10
fn(m, t, S_)
m.run(t + 150)
set_cd(m, 3)
m.run(t + 900)                 # countdown expired, the save (due >= t + 1015 ms) still pending
blocked_save = len(slept(m, n_sl)) == 0 and stats(m)['guard'] == 6
m.run(t + 4800)
sl = slept(m, n_sl)
ops = m.flash_ops[n_ops:]
ok10 = blocked_save and len(sl) == 1 and ops and all(op[-1] < sl[0]['t'] for op in ops)
g_res['save'] = (ok10, 'blocked while dirty=%s, flash ops %d all before the sleep at %.0f ms' % (
    blocked_save, len(ops), sl[0]['t'] if sl else -1))
wake_with_a(m, m.t_ms() + 10)
check('T-SLEEP-10', ok10, g_res['save'][1])
# pairing: the countdown does not run
mod.send(m.t_ms(), (0x5B, 0x31, 0x8C))
n_sl = len(m.sleeps)
m.run(m.t_ms() + 20)
set_cd(m, 3)
m.run(m.t_ms() + 800)
pair_blocked = len(slept(m, n_sl)) == 0 and cd(m) == 3
mod.send(m.t_ms(), (0x5B, 0x32, 0x8D))
m.run(m.t_ms() + 30)
g_res['pairing'] = (pair_blocked, 'countdown held at %d, sleeps %d' % (cd(m), len(slept(m, n_sl))))
check('T-SLEEP-2', all(v[0] for v in g_res.values()), ', '.join('%s: %s %s' % (k, 'ok' if v[0] else 'BAD', v[1]) for k, v in g_res.items()))

# ---- CR-6: critical battery + pending save -> sleeps; the save waits for 5B 23 (I-F5)
mod.send(m.t_ms(), (0x5B, 0x22, 0x7D))
m.run(m.t_ms() + 30)
n_ops = len(m.flash_ops)
n_sl = len(m.sleeps)
dirty = {}
m.on_sleep_enter = lambda mm: dirty.setdefault('d', bytes(mm.uc.mem_read(mm.sym['g80_save_dirty'], 1))[0])
t = m.t_ms() + 10
fn(m, t, S_)
m.run(t + 120)
set_cd(m, 3)
m.run(t + 800)
sl = slept(m, n_sl)
ops_before = list(m.flash_ops[n_ops:])
t_w = m.t_ms() + 20
wake_with_a(m, t_w)
m.run(t_w + 3600)
# still critical after the wake: the save stays inhibited (I-F5, g80_battery.c) until 5B 23
ops_crit = m.flash_ops[n_ops + len(ops_before):]
m.on_sleep_enter = None
t_n = m.t_ms()
mod.send(t_n, (0x5B, 0x23, 0x7E))
m.run(t_n + 3600)
ops_after = m.flash_ops[n_ops + len(ops_before) + len(ops_crit):]
ok6 = bool(len(sl) == 1 and dirty.get('d') == 1 and not ops_before and not ops_crit and ops_after and all(op[-1] > t_n for op in ops_after))
check('T-SLEEP-CR6', ok6, 'sleeps=%d, save pending at sleep=%s, flash ops before the wake=%d, after the wake while critical=%d, after 5B 23=%d' % (
    len(sl), dirty.get('d'), len(ops_before), len(ops_crit), len(ops_after)))

# ---- T-SLEEP-12: key held without other events for 10 min
set_opt(m, 1)
n_sl = len(m.sleeps)
n_f = len(mod.frames)
t = m.t_ms() + 10
m.schedule.append((t, t + 3000, A_))
m.run(t + 300)
kt = struct.unpack('<I', rd(m, 'g80_lpm_key_t', 4))[0]
wr(m, 'g80_lpm_key_t', struct.pack('<I', (kt - 600000) & 0xFFFFFFFF))
m.run(t + 1000)
sl = slept(m, n_sl)
f_before = [x for x in frames(mod, t, t + 1000, 0xA1)]
m.run(t + 3300)                              # release at t + 3000 wakes it
sl2 = slept(m, n_sl)
f_after = frames(mod, t + 2990, t + 3300, 0xA1)
ok12 = len(sl) == 1 and f_before[:1] == [PRESS_A] and f_before[-1:] == [EMPTY] and len(sl2) == 1 and sl2[0]['wake_t'] and \
    abs(sl2[0]['wake_t'] - (t + 3000)) < 1 and PRESS_A not in f_after
check('T-SLEEP-12', ok12, 'A held: frames before the sleep %s, sleeps %d, woke at %+.1f ms after the release, A1 after %s' % (
    f_before, len(sl), (sl2[0]['wake_t'] - t - 3000) if sl2 and sl2[0]['wake_t'] else -1, f_after))

# ---- T-SLEEP-ISP: Fn+RShift+Esc after a wake
m.run(m.t_ms() + 100)
set_cd(m, 3)
n_sl = len(m.sleeps)
m.run(m.t_ms() + 500)
dbg_isp = (stats(m), cd(m), link(m), [(x['t'], x['cause']) for x in m.sleeps[-2:]], m.resets[-1:])
t = m.t_ms() + 20
wake_with_a(m, t)
woke = len(slept(m, n_sl)) == 1
t = m.t_ms() + 20
m.schedule += [(t, t + 300, FN), (t + 20, t + 250, RSFT), (t + 60, t + 120, ESC)]
r = m.run(t + 400)
check('T-SLEEP-ISP', woke and r == 'ISP' and m.isp is not None and not m.isp['wdt_en'],
      'woke=%s, run -> %s, isp=%s (before: %s)' % (woke, r, {k: m.isp[k] for k in ('t_ms', 'wdt_en')} if m.isp else None, dbg_isp))
total_sleeps_e1 = len(slept(m, 0))
collect(m, mod)

# ======================================================================== E2: slider OFF wakes (P0.7)
m = None
m, mod = boot_linked()
set_cd(m, 3)
m.run(2200)
asleep = m._asleep is not None
m.ext_pins[(0, 7)] = 0
m.run(2210)
s = slept(m, 0)
m.run(3200)
f = frames(mod, 2200, 3200)
tap(m, 3300, A_)
m.run(3500)
check('T-SLEEP-7', asleep and len(s) == 1 and s[0]['cause'] == 'IRQ31' and (0xA6, 0x11, 0xB7) in f and usb_state(m) != USB_STOP
      and not frames(mod, 3300, 3500, 0xA1) and not m.faults and not m.resets,
      'asleep=%s, wake cause %s, frames %s, USB state %d' % (asleep, s[0]['cause'] if s else None, f[:3], usb_state(m)))
collect(m, mod)

# ======================================================================== E3..E5: bounded waits
res8 = []
for name, kw, when in (('IHRC never ready on wake', {}, 'wake'), ('SYSCLKST never ILRC (entry)', dict(sysclk_never=1), 'boot'),
                       ('SYSCLKST never IHRC (wake)', {}, 'wake2')):
    m = None
    m, mod = boot_linked(**kw)
    m.heal_on_reset = True
    ent = {}
    m.on_sleep_enter = lambda mm: ent.setdefault('t', mm.t_ms())
    if when == 'wake':
        m.on_sleep_enter = lambda mm: (ent.setdefault('t', mm.t_ms()), setattr(mm, 'ihrc_never', True))
    elif when == 'wake2':
        m.on_sleep_enter = lambda mm: (ent.setdefault('t', mm.t_ms()), setattr(mm, 'sysclk_never', 0))
    set_cd(m, 3)
    m.run(2200)
    t_w = 2250
    tap(m, t_w, A_, 60)
    t_end = t_w + (WAIT_MAX * 14 / 8000.0) * 1000 + 2000
    m.run(t_w + 1)
    while not m.resets and m.t_ms() < t_end:
        m.run(min(t_end, m.t_ms() + 5000), stop=lambda mm: bool(mm.resets))
    rs = m.resets[:1]
    m.ihrc_never, m.sysclk_never = False, None
    m.on_sleep_enter = None
    h0 = m.main_loop_hits
    if rs:
        m.run(rs[0][0] + 1500)
    rebooted = m.boot_index == 1 and m.main_loop_hits > h0 + 100
    ent_t = ent.get('t')
    t_ref = ent_t if when == 'boot' else t_w
    dt = (rs[0][0] - t_ref) if rs and t_ref is not None else None
    bound = (WAIT_MAX * 14 / 8000.0) * 1000 if when != 'boot' else 50.0
    ok = bool(rs) and rs[0][1] == 'SYSRESETREQ' and dt is not None and 0 <= dt <= bound and m.isp is None and not m.faults \
        and rebooted and m.wdt['en']
    if when != 'boot':
        ok = ok and dt > 1000     # the loop really spun (it is not an early abort)
    res8.append(ok)
    print('      %-30s reset %s %.1f ms after the %s (bound %.0f ms), rebooted to the main loop=%s, ISP=%s, faults=%s' % (
        name, rs[0][1] if rs else None, dt if dt is not None else -1, 'wake key' if when != 'boot' else 'sleep entry', bound,
        rebooted, m.isp is not None, m.faults[:1]))
    collect(m, mod)
check('T-SLEEP-8', all(res8), 'every clock wait ends in a reset within its bound: %s' % res8)

# ======================================================================== E6: I-P4 abort
m = None
m, mod = boot_linked(p3_ie_broken=True)
set_cd(m, 3)
m.run(2300)
st = stats(m)
tap(m, 2400, A_)
m.run(2600)
f = frames(mod, 2400, 2600, 0xA1)
check('T-SLEEP-11', st['aborts'] >= 1 and st['sleeps'] == 0 and not slept(m, 0) and m.hclk() == 48e6 and m.wdt['en']
      and (m.reg[0x40062000] & 0xF) == 5 and f.count(PRESS_A) == 1 and not m.faults and not m.resets,
      'stats %s, WFI sleeps %d, HCLK %.0f MHz, WDT %s, A1 %s' % (st, len(slept(m, 0)), m.hclk() / 1e6, m.wdt['en'], f))
collect(m, mod)

# ======================================================================== E8: LVD
lvd = {}
for kind in ('via', 'default', 'stage1'):
    m = None
    m, mod = mk(kind=kind, slider=False)
    m.run(800)
    lvd[kind] = [(e[3], e[4]) for e in m.lpm_trace if e[2] == 'LVDCTRL']
check('T-LVD', [v for v, f in lvd['via']] == [0xC002] and [v for v, f in lvd['default']] == [0xC002] and lvd['stage1'] == [],
      'LVDCTRL writes: %s' % {k: [(hex(v), f) for v, f in w] for k, w in lvd.items()})

# ======================================================================== E9: pairing / lost windows, wake vectors
m = None
m, mod = boot_linked(opt=1)


def age_link(ms):
    """the link code changed ms ago (g80_link_t moved back)"""
    def f(mm):
        t = mm.call('timer_read32') & 0xFFFFFFFF
        wr(mm, 'g80_link_t', struct.pack('<I', (t - ms) & 0xFFFFFFFF))
    m.inject.append((m.t_ms() + 0.5, f))
    m.run(m.t_ms() + 1)


def watch_cd(t_end, step=10):
    """samples (t, countdown) until t_end or the first sleep"""
    out = []
    n0 = len(m.sleeps)
    while m.t_ms() < t_end and len(m.sleeps) == n0:
        m.run(m.t_ms() + step)
        out.append((m.t_ms(), cd(m)))
    return out


mod.send(1700, (0x5B, 0x31, 0x8C))
m.run(1800)
c0 = cd(m)
m.run(3800)
frozen = cd(m) == c0 and not m.sleeps
age_link(19950)
t_exp = m.t_ms() + 50                       # window end
n_sl = len(m.sleeps)
w = watch_cd(t_exp + 4000)
set_at = next((t for t, v in w if v and v <= 30), None)
first = next((v for t, v in w if v and v <= 30), None)
sl = slept(m, n_sl)
dt_pair = (sl[0]['t'] - t_exp) if sl else None
ok_pair = frozen and first is not None and 29 <= first <= 30 and sl and 2800 <= dt_pair <= 3300 and stats(m)['guard'] != 4
wake_with_a(m, m.t_ms() + 20)
mod.send(m.t_ms(), (0x5B, 0x33, 0x8E))     # link lost
m.run(m.t_ms() + 30)
c33 = cd(m)
age_link(9950)
t_exp2 = m.t_ms() + 50
n_sl = len(m.sleeps)
w2 = watch_cd(t_exp2 + 2000)
first2 = next((v for t, v in w2 if v and v <= 5), None)
sl2 = slept(m, n_sl)
dt_lost = (sl2[0]['t'] - t_exp2) if sl2 else None
ok_lost = 149 <= c33 <= 150 and first2 is not None and 4 <= first2 <= 5 and sl2 and 300 <= dt_lost <= 800
check('T-SLEEP-PAIR', ok_pair and ok_lost and not m.faults and not m.resets,
      'pairing: countdown frozen at %d for 2 s=%s, set to %s at the window end, slept %s ms after it (guard %d); lost: countdown %d '
      'after 5B 33, set to %s at the 10 s window end, slept %s ms after it' % (
          c0, frozen, first, dt_pair, stats(m)['guard'], c33, first2, dt_lost))
wake_with_a(m, m.t_ms() + 20)
mod.send(m.t_ms(), (0x5B, 0x32, 0x8D))
m.run(m.t_ms() + 50)

# wake vectors: table entries and a slip that leaves both IRQs enabled while awake
vt = struct.unpack_from('<32I', bytes(m.flash), 0x40)   # external IRQ vectors 0..31
v28, v31 = vt[28] & ~1, vt[31] & ~1
unh = m.sym['_unhandled_exception'] & ~1
tbl_ok = v28 == (m.sym['VectorB0'] & ~1) and v31 == (m.sym['VectorBC'] & ~1) and unh not in (v28, v31)
e28, e31 = m.exc_count[16 + 28], m.exc_count[16 + 31]


def slip(irq):
    """both wake sources left armed and one IRQ pending (each handler disarms both, so one at a time)"""
    def f(mm):
        mm.reg[P3 + 0x18] = 0xFC0
        mm.reg[P0 + 0x18] |= 0x80
        mm.nvic_iser |= 1 << 28 | 1 << 31
        mm.pend_irq(irq)
    return f


disarmed = True
for irq in (31, 28):
    m.inject.append((m.t_ms() + 0.5, slip(irq)))
    m.run(m.t_ms() + 50)
    disarmed = disarmed and m.reg[P3 + 0x18] == 0 and not (m.reg[P0 + 0x18] & 0x80) and not (m.nvic_iser & (1 << 28 | 1 << 31))
taken = (m.exc_count[16 + 28] - e28, m.exc_count[16 + 31] - e31)
t = m.t_ms() + 20
n_f = len(mod.frames)
tap(m, t, A_, 60)
m.run(t + 200)
f = frames(mod, t - 1, t + 200, 0xA1)
check('T-SLEEP-VEC', tbl_ok and taken == (1, 1) and disarmed and f.count(PRESS_A) == 1 and m.isp is None and not m.faults and not m.resets,
      'vectors 0xB0 %#x 0xBC %#x (_unhandled_exception %#x); taken while awake %s, disarmed %s (P3 IE %#x, P0 IE %#x, ISER %#x), '
      'A1 after %s, ISP %s faults %s resets %s' % (v28, v31, unh, taken, disarmed, m.reg[P3 + 0x18], m.reg[P0 + 0x18], m.nvic_iser,
                                                  f, m.isp is not None, m.faults[:1], m.resets))
collect(m, mod)

# ======================================================================== E10: editor keys, LED off then deep sleep
m = None
m, mod = boot_linked(opt=1)                  # deep sleep after 5 min
patch_getter(m, 'g80_opt_led_off_min', 1)    # LEDs off after 1 min
fn(m, 1700, K_)                              # Fn+K: on-board editor on
m.run(1950)
ed_on = rd(m, 'g80_pk_editing', 1)[0]
set_cd(m, 500)
tap(m, 2000, A_)                             # taken by the editor: no key event, no A1
m.run(2150)
cd_ed = cd(m)
a1_ed = frames(mod, 1990, 2150, 0xA1)
fn(m, 2300, K_)                              # editor off
m.run(2550)
check('T-SLEEP-ACT', ed_on == 1 and abs(cd_ed - 3000) <= 2 and PRESS_A not in a1_ed and rd(m, 'g80_pk_editing', 1)[0] == 0,
      'editor on=%d, countdown 500 -> %d after a swallowed key (A1 frames %s)' % (ed_on, cd_ed, a1_ed))

tap(m, 2600, A_)
m.run(2800)
n_sl = len(m.sleeps)
tjump(m, 61000)                              # 61 s without activity
m.run(3200)
dark = (m.call('rgb_matrix_get_suspend_state') & 1) == 1 and not any(rd(m, 'g80_frame', 21 * 17))
cd_dark = cd(m)
awake_dark = not slept(m, n_sl)
set_cd(m, 3)                                 # the rest of the 5 min
m.run(3800)
sl = slept(m, n_sl)
t_w = m.t_ms() + 20
wake_with_a(m, t_w)
m.run(t_w + 400)
lit = (m.call('rgb_matrix_get_suspend_state') & 1) == 0 and any(rd(m, 'g80_frame', 21 * 17))
f = frames(mod, t_w - 1, t_w + 400, 0xA1)
cd_after = cd(m)
check('T-SLEEP-LED', dark and awake_dark and 2900 <= cd_dark <= 3000 and len(sl) == 1 and sl[0]['cause'] == 'IRQ28' and lit
      and f.count(PRESS_A) == 1 and 2990 <= cd_after <= 3000 and not m.faults and not m.resets,
      'idle 61 s: LEDs dark=%s, awake=%s, countdown %d; deep sleeps %d (cause %s); after the wake key: LEDs lit=%s, A1 %s, countdown %d' % (
          dark, awake_dark, cd_dark, len(sl), sl[0]['cause'] if sl else None, lit, f, cd_after))
collect(m, mod)

# ======================================================================== whole run
check('T-SLEEP-6', not sleep_flash and not all_viol, 'flash ops inside sleep windows %s, violations %s (E1 slept %d times)' % (
    sleep_flash[:3], all_viol[:3], total_sleeps_e1))
check('T-SLEEP-W', not all_bad and not all_over, 'bad/truncated frames %s, TX overwrites %s' % (all_bad[:3], all_over[:3]))
m = None
print('%d/%d PASS' % (sum(results), len(results)))
