"""Galaxy80 QMK full-system emulator (Unicorn, Cortex-M0 model) - stage 2 edition.

Runs a built image from the reset vector through crt0, ChibiOS, QMK init and the
QMK main loop, with a peripheral model of the SN32F240B/SN32F248B:
  SYS0/SYS1 (clock ready bits), FLASH controller, GPIO P0..P3 + key matrix,
  CT16B0 (ChibiOS ST counter), SysTick (ChibiOS ST alarm), WDT, USB (idle),
  NVIC (ISER/ICER/ISPR/ICPR, IRQ enable respected), SCB (ICSR, AIRCR SYSRESETREQ),
  and (stage 2) CT16B1 as described in the SN32F240B datasheet ch. 8:
    PRE/TC, MR0..MR24, MCTRL/2/3 (IE/RST/STOP), PWMCTRL/PWMCTRL2 (mode 1 / mode 2 /
    forced 0 / forced 1), PWMENB, PWMIOENB, RIS/IC, PFPA routing of PWMn to its A or B pin,
    IRQ16 pending (level) while RIS.MR24IF is set and the NVIC line is enabled.
    PWM output (combinational, edge-aligned up-counting):
      mode 1: LOW while TC < MR (MR = 0 -> always HIGH); mode 2: HIGH while TC < MR
      (MR = 0 -> always LOW; MR > 255 never matches -> HIGH). After the MR24 match with
      RST+STOP the counter sits at TC = 0 until CEN is set again.
    Pin level = PWM output if PWMIOENB routes a channel to the pin, otherwise GPIO latch.
Exceptions (SysTick, NMI, HardFault, IRQs) are entered/returned by this harness with real
ARMv6-M semantics (8-word frame, 8-byte alignment, EXC_RETURN F1/F9/FD, MSP/PSP).

No hardware is touched. Library module; see run_tests.py.
"""
import struct, bisect
from collections import defaultdict, Counter
from unicorn import *
from unicorn.arm_const import *

FLASH_SIZE = 0x10000
# runtime flash writes allowed: wear-leveling aux sectors (0xF6C0..0xF7BF: scratch + state log of the
# power-fail-safe consolidation) and the EEPROM area 0xF7C0..0xFFBF
FLASH_RW_LO, FLASH_RW_HI = 0xF6C0, 0xFFC0
RAM_BASE, RAM_SIZE = 0x20000000, 0x2000
ROM_BASE, ROM_SIZE = 0x1FFF0000, 0x10000
ISP_ENTRY = 0x1FFF0301
UNTIL = 0xEFFFFFFE  # never reached
CALL_RET = 0x20001F80  # return trampoline used by G80.call()

GPIO = {0x40044000: 0, 0x40046000: 1, 0x40048000: 2, 0x4004A000: 3}
GPIO_BASE = {v: k for k, v in GPIO.items()}
COLS = [(2, 12), (2, 11), (2, 10), (2, 9), (2, 8), (2, 7), (2, 6), (2, 5), (2, 4), (2, 3), (2, 2),
        (2, 1), (2, 0), (2, 15), (1, 15), (1, 13), (1, 12)]
ROWS = [11, 10, 9, 8, 7, 6]  # P3 bits, row 0..5
LED_PINS = {0: 0xFF00, 1: 0x4FDF, 3: 0x0008}
COL_MASK = {2: 0x9FFF, 1: 0xB000}
ROW_MASK = 0x0FC0
# stock phase -> select pin (port, bit): R rows 0-6, G rows 0-6, B rows 0-6 (04-luci-led.md 1.2,
# decoded switches 0x4D8C / 0x4E80)
PHASE_SEL = [(0, 8), (0, 11), (0, 14), (1, 1), (1, 4), (3, 3), (1, 6),
             (0, 10), (0, 13), (1, 0), (1, 3), (1, 8), (1, 10), (1, 9),
             (0, 9), (0, 12), (0, 15), (1, 2), (1, 7), (1, 14), (1, 11)]
SEL_PINS = list(PHASE_SEL)
# LED column -> CT16B1 channel (04-luci-led.md 1.1)
COL_CHAN = [20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8, 23, 7, 5, 4]

SYS0, SYS1, FMC, WDT, CT16B0, USB = 0x40060000, 0x4005E000, 0x40062000, 0x40010000, 0x40000000, 0x4005C000
UART2 = 0x40012000            # stage 7: radio module link (RB/TH 0x00, IE 0x04, II 0x08, LC 0x0C, LS 0x14)
IRQ_UART2 = 14
CT16B1, PFPA = 0x40002000, 0x40042000
SYST_CSR, SYST_RVR, SYST_CVR = 0xE000E010, 0xE000E014, 0xE000E018
ICSR, AIRCR = 0xE000ED04, 0xE000ED0C
NVIC_ISER, NVIC_ICER, NVIC_ISPR, NVIC_ICPR = 0xE000E100, 0xE000E180, 0xE000E200, 0xE000E280

EXC_NMI, EXC_HARDFAULT, EXC_SYSTICK = 2, 3, 15
EXC_CT16B1 = 16 + 16


def pwm_pin(ch, pfpa_b):
    """(port, bit) driven by CT16B1 PWMch: datasheet pin table / PFPA_CT16B1."""
    if pfpa_b:
        return (1, 8 + ch) if ch < 8 else (2, ch - 8)
    return (0, ch) if ch < 16 else (1, ch - 16)


def torn_op(flash, kind, addr, words, rnd):
    """A power cut inside one flash op (raw cells, erased = 1): a torn PROGRAM clears a random subset of
    the 0-bits it was writing; a torn ERASE sets a random subset of the sector's bits to 1."""
    if kind == 'ERASE':
        page = addr & ~63
        for o in range(page, page + 64, 4):
            old = struct.unpack_from('<I', flash, o)[0]
            struct.pack_into('<I', flash, o, old | rnd.getrandbits(32))
    else:
        for i, w in enumerate(words):
            o = addr + 4 * i
            old = struct.unpack_from('<I', flash, o)[0]
            zeros = ~w & 0xFFFFFFFF & old          # bits this program would clear
            struct.pack_into('<I', flash, o, old & ~(zeros & rnd.getrandbits(32)) & 0xFFFFFFFF)


class Syms:
    def __init__(self, nm_path):
        self.by_name = {}
        funcs = []
        for line in open(nm_path):
            p = line.split()
            if len(p) == 3:
                a = int(p[0], 16)
                self.by_name.setdefault(p[2], a)
                if p[1] in 'tTW' and a < FLASH_SIZE:
                    funcs.append((a, p[2]))
        funcs.sort()
        self.addrs = [a for a, _ in funcs]
        self.names = [n for _, n in funcs]

    def __getitem__(self, n):
        return self.by_name[n]

    def get(self, n, d=None):
        return self.by_name.get(n, d)

    def at(self, pc):
        i = bisect.bisect_right(self.addrs, pc & ~1) - 1
        if i < 0 or pc >= FLASH_SIZE:
            return '?%#x' % pc
        return '%s+%#x' % (self.names[i], pc - self.addrs[i])

    def func(self, pc):
        i = bisect.bisect_right(self.addrs, pc & ~1) - 1
        return self.names[i] if i >= 0 and pc < FLASH_SIZE else '?'

    def range(self, name):
        a = self.by_name[name] & ~1
        i = self.addrs.index(a) if a in self.addrs else bisect.bisect_right(self.addrs, a) - 1
        end = self.addrs[i + 1] if i + 1 < len(self.addrs) else a + 0x1000
        return a, end


class Event(Exception):
    pass


class G80:
    CPI = 1.0  # cycles per instruction (approximation, documented in the report)
    SCAN_FUNCS = ('matrix_scan_custom', 'Vector80')

    def __init__(self, image_path, nm_path, pressed=(), stuck_rows=0, page_erase=True, verbose=False,
                 settle_instr=None, ext_pins=None, flash_stall_us=0, flash_prog_us=None, flash_erase_us=None,
                 stress=False):
        self.img = bytearray(open(image_path, 'rb').read())
        assert len(self.img) == FLASH_SIZE
        self.sym = Syms(nm_path)
        self.pressed = set(pressed)
        self.stuck = stuck_rows
        self.page_erase = page_erase
        self.verbose = verbose
        self.ext_pins = dict(ext_pins or {})   # (port, bit) -> level forced from outside (switches)
        self.flash_stall_us = flash_stall_us    # CPU stall per flash erase/program (VIA stall test)
        # flash timing model by op type (None = use flash_stall_us). One "program" is one START of the
        # FMC = one 4 B line with the SonixQMK EFL driver; one "erase" is one 64 B sector (PER).
        self.flash_prog_us = flash_prog_us
        self.flash_erase_us = flash_erase_us
        # stress instrumentation (opt-in, see init_stress): raw HID injection on the real USB OUT path,
        # raw HID answers, host key reports, ISR/main-loop timing, flash op context, power cut
        self.stress = stress
        self.power_cut_at = None      # index into flash_ops: that op and every later one never happen
        self.power_cut = False
        self.power_cut_torn = None    # None, or a random.Random: the cut op is applied in part (torn_op)
        self.log = []                 # (time_ms, text) high level trace
        self.resets = []              # (time_ms, cause)
        self.isp = None               # dict when ROM ISP entry reached
        self.lockup = None
        self.faults = []
        self.cyc = 0                  # HCLK cycles since power on
        self.t_s = 0.0                # seconds since power on
        self.boot_index = 0
        self.uc = None
        self.flash = bytearray(self.img)
        self.ram = None               # retained across system resets
        self.reads_pc = Counter()     # (pc, addr) -> count
        self.reg_writes = []          # (t_ms, boot, pc, addr, value)
        self.keep_reg_writes = True
        self.violations = []
        self.flash_ops = []
        self.uart_rx = []             # stage 7: (time_ms, byte) the simulated module sends
        # stage 7b: UART2 TX as found on the real chip: a holding register (THR) + shift register, one
        # byte = 10 bits at 115385 baud, NO TX FIFO: a TH write while THR is full overwrites (= the
        # 2026-10-19 hardware bug, frames garbled). uart_tx gets each byte when its shifting starts.
        self.uart_thr = None
        self.uart_tsr_end = 0.0
        self.uart_overwrites = []     # (t_ms, lost byte, new byte, pc function)
        self.uart_tx = []             # (time_ms, byte) written to TH by the firmware
        self.col_reads = []           # (t_ms, set(high columns), rows read, pc, ctx)  -- matrix scan trace
        self.keep_col_reads = True
        self.scan_pcs = Counter()
        self.scan_pcs_one = Counter()
        self.wdt_feeds = []           # time_s of feeds
        self.wdt_cfg_writes = []
        self.max_feed_gap = 0.0
        self.schedule = []            # (t_ms_from, t_ms_to, (r,c)) dynamic presses
        self.inject = []              # (t_ms, callable(self))
        self.stop_at_main_loop = None
        self.main_loop_hits = 0
        self.first_main_loop_t = None
        self.usb_first_write = None
        self.pc_hist = Counter()
        self.settle_instr = settle_instr   # None = ideal pins; N = row reads high only N instr after column high
        self.scan_icount = 0
        self.col_high_icount = {}
        self.read_deltas = Counter()
        self.exc_count = Counter()
        self.wfi_count = 0
        # --- stage 2 instrumentation
        self.irq16_suppressed = False      # model a dead CT16B1 interrupt line (fallback test)
        self.ct1_events = []               # ('CEN'|'MATCH', t_ms, cyc, extra)
        self.keep_ct1_events = True
        self.isr_durations = []            # cycles per CT16B1 ISR
        self.isr_enter_cyc = None
        self.read_checks = Counter()       # E-L4 statistics
        self.read_bad = []
        self.sel_edges = Counter()
        self.sel_bad = []
        self.match_bad = []
        self.lit_at_match = []             # (t_ms, [(phase_sel, col)]) LEDs lit while the timer is stopped
        self.exit_snaps = []               # (kind, t_ms, selects_high, pwmioenb, nvic16)
        self.mr_low_max = 0                # max LOW ticks per phase (duty) seen at CEN
        self.mr_min_seen = None
        self.pending_stall = 0
        self.stall_matches = []
        # --- NVIC/SCB priorities and nesting (review B1: nesting is now modelled)
        self.nesting = True                 # False = old model (no exception preempts another)
        self.prio = {}
        self.nest_events = Counter()        # (outer, inner) -> count
        self.max_nest = 0
        # --- s2-fix knobs
        self.ct1_div_mult = 1               # CT16B1 clock N times slower than PRE says (review S1)
        self.release_instr = None           # pressed key: row stays HIGH this many HCLK cycles
                                            # after its column went LOW (slow discharge, review S3)
        self.col_low_icount = {}
        self.chunk_ic0 = 0
        # --- stage 4 knobs
        self.usb_frmno_live = None          # USB_FRMNO (0x60): True/False forces it; None = follow usb_host
        self.usb_host = True                # stage 7: a running PC sends SOFs (1/ms) unless the bus is
                                            # suspended (USBD1.state 5); False = PC off, 5 V still on
        self.fmc_busy_stuck = False         # FLASH_STATUS.BUSY never clears (hung flash controller)
        self.in_call = False
        if stress:
            self.init_stress()
        self.power_on()

    # ------------------------------------------------------------------ time
    def t_ms(self):
        return self.t_s * 1e3

    def hclk(self):
        pre = self.reg[SYS0 + 0x10] & 7
        sysclk = 48e6 if not (self.reg[SYS0 + 0x0C] & 1) else 32e3
        return sysclk / (1 << pre)

    def note(self, s):
        self.log.append((round(self.t_ms(), 3), self.boot_index, s))
        if self.verbose:
            print('[%9.3f ms #%d] %s' % (self.t_ms(), self.boot_index, s))

    # ------------------------------------------------------------- reset
    def periph_reset(self):
        self.reg = defaultdict(int)
        for b in GPIO:
            self.reg[b + 0x08] = 0x00AAAA80 if GPIO[b] == 3 else 0xAAAAAAAA
        self.latch = {n: 0 for n in range(4)}
        self.reg[SYS0 + 0x00] = 1            # ANBCTRL IHRCEN
        self.reg[SYS0 + 0x10] = 2            # AHBCP /4
        self.reg[SYS0 + 0x14] = 0            # RSTST (set below)
        self.reg[SYS0 + 0x1C] = 1            # EXRSTCTRL RESETDIS=1 (P3.7 GPIO)
        self.reg[SYS0 + 0x20] = 0            # SWDCTRL SWD enabled
        self.reg[SYS1 + 0x00] = 0x0100000F   # AHBCLKEN: WDT + P0..P3
        self.reg[USB + 0x10] = 0x80000000    # USB_CFG reset
        self.wdt = dict(en=False, tc=0xFF, pre_at_start=0, last_feed=None, ie=False)
        self.ct = dict(cen=False, base_cyc=0, base_tc=0, pre=0)
        self.ct1 = dict(cen=False, base_cyc=0, base_tc=0)
        self.systick = dict(next=None)
        self.pending = set()
        self.active = []
        self.flash_buf = []
        self.nvic_iser = 0

    def power_on(self):
        self.periph_reset()
        self.reg[SYS0 + 0x14] = 0x10  # PORRSTF
        self.boot(cold=True)

    def system_reset(self, cause):
        self.resets.append((round(self.t_ms(), 3), cause))
        self.note('SYSTEM RESET (%s)' % cause)
        self.snap_exit('RESET-' + cause)
        self.ram = bytes(self.uc.mem_read(RAM_BASE, RAM_SIZE))
        self.periph_reset()
        self.reg[SYS0 + 0x14] = {'WDT': 0x2, 'SYSRESETREQ': 0x1}.get(cause, 0)
        self.boot_index += 1
        self.usb_first_write = None
        self.boot(cold=False)

    def boot(self, cold):
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M0)
        uc.mem_map(0, FLASH_SIZE, UC_PROT_READ | UC_PROT_EXEC)
        uc.mem_write(0, bytes(self.flash))
        uc.mem_map(ROM_BASE, ROM_SIZE)
        uc.mem_write(ROM_BASE, b'\xfe\xe7' * (ROM_SIZE // 2))
        uc.mem_map(RAM_BASE, RAM_SIZE)
        if self.ram is not None:
            uc.mem_write(RAM_BASE, self.ram)
        else:
            # cold power-on: RAM content pseudo-random (deterministic), never 0xDEADBEEF
            import random
            rnd = random.Random(1234)
            uc.mem_write(RAM_BASE, bytes(rnd.getrandbits(8) for _ in range(RAM_SIZE)))
        uc.mem_map(0x40000000, 0x80000)
        uc.mem_map(0xE000E000, 0x1000)
        uc.hook_add(UC_HOOK_MEM_READ, self.on_read, begin=0x40000000, end=0x4007FFFF)
        uc.hook_add(UC_HOOK_MEM_WRITE, self.on_write, begin=0x40000000, end=0x4007FFFF)
        uc.hook_add(UC_HOOK_MEM_READ, self.on_scs_read, begin=0xE000E000, end=0xE000EFFF)
        uc.hook_add(UC_HOOK_MEM_WRITE, self.on_scs_write, begin=0xE000E000, end=0xE000EFFF)
        uc.hook_add(UC_HOOK_CODE, self.on_rom, begin=ROM_BASE, end=ROM_BASE + ROM_SIZE - 1)
        uc.hook_add(UC_HOOK_INTR, self.on_intr)
        # flash read-back coherence: a flash write made inside a hook is deferred (stress mode / call(),
        # see code_write); the first instruction after the FMC START inside the EFL driver ends the
        # emulation so that the write lands before the firmware can read the cells back (the
        # wear-leveling backend verifies its programs and scans its state log)
        for f in ('efl_lld_program', 'efl_lld_start_erase_sector'):
            if self.sym.get(f) is not None:
                a, end = self.sym.range(f)
                uc.hook_add(UC_HOOK_CODE, self.on_efl_insn, begin=a, end=end - 1)
        ml = self.sym.get('main')
        if ml is not None:
            # main loop back-edge: 'b.n protocol_pre_task' at main+0x2a (verified in disassembly)
            self.main_loop_pc = self.find_main_loop()
            uc.hook_add(UC_HOOK_CODE, self.on_main_loop, begin=self.main_loop_pc, end=self.main_loop_pc)
        for f in self.SCAN_FUNCS:
            if self.sym.get(f) is not None:
                a, end = self.sym.range(f)
                uc.hook_add(UC_HOOK_CODE, self.on_scan_insn, begin=a, end=end - 1)
        self.uc = uc
        if self.stress:
            self.boot_stress_hooks(uc)
        sp, pc = struct.unpack_from('<II', self.flash, 0)
        uc.reg_write(UC_ARM_REG_CONTROL, 0)
        uc.reg_write(UC_ARM_REG_MSP, sp)
        uc.reg_write(UC_ARM_REG_PSP, 0)
        uc.reg_write(UC_ARM_REG_PRIMASK, 0)
        uc.reg_write(UC_ARM_REG_PC, pc & ~1)
        self.pc = pc | 1
        self.note('boot from reset vector: SP=%#x PC=%#x (%s)' % (sp, pc, 'power-on' if cold else 'warm'))

    def find_main_loop(self):
        main = self.sym['main']
        # last instruction of main is 'b.n main+0x1a'; locate the call to protocol_pre_task
        code = self.flash[main:main + 0x40]
        for off in range(0, 0x3E, 2):
            hw = struct.unpack_from('<H', code, off)[0]
            if (hw & 0xF800) == 0xE000:  # unconditional b.n
                return main + off
        raise RuntimeError('main loop not found')

    # ------------------------------------------------------------- pin model
    def cur_pc(self):
        return self.uc.reg_read(UC_ARM_REG_PC)

    def in_isr16(self):
        return bool(self.active) and self.active[-1] == EXC_CT16B1

    def ct1_clk(self):
        return bool((self.reg[SYS1] >> 7) & 1)

    def ct1_tc(self):
        c = self.ct1
        if not c['cen']:
            return c['base_tc'] & 0xFFFF
        div = ((self.reg[CT16B1 + 0x08] & 0xFF) + 1) * self.ct1_div_mult
        return (c['base_tc'] + (self.cyc_now() - c['base_cyc']) // div) & 0xFFFF

    def pwm_mode(self, ch):
        if ch < 16:
            return (self.reg[CT16B1 + 0x94] >> (2 * ch)) & 3
        return (self.reg[CT16B1 + 0x98] >> (2 * (ch - 16))) & 3

    def pwm_level(self, ch, tc=None):
        tc = self.ct1_tc() if tc is None else tc
        mr = self.reg[CT16B1 + 0x20 + 4 * ch] & 0xFFFF
        m = self.pwm_mode(ch)
        if m == 0:
            return 1 if mr == 0 else (0 if tc < mr else 1)
        if m == 1:
            return 0 if mr == 0 else (1 if tc < mr else 0)
        return 0 if m == 2 else 1

    def pwm_routes(self):
        """{(port, bit): channel} for every channel whose PWM drives a pin."""
        r = {}
        if not self.ct1_clk():
            return r
        ioen = self.reg[CT16B1 + 0xA0] & 0xFFFFFF
        pfpa = self.reg[PFPA]
        for ch in range(24):
            if (ioen >> ch) & 1:
                r[pwm_pin(ch, (pfpa >> ch) & 1)] = ch
        return r

    def pin_out(self, p, bit, routes=None, tc=None):
        """level driven on output pin (p, bit), or None if it is an input not driven by PWM"""
        routes = self.pwm_routes() if routes is None else routes
        if (p, bit) in routes:
            return self.pwm_level(routes[(p, bit)], tc)
        if (self.reg[GPIO_BASE[p] + 4] >> bit) & 1:
            return (self.latch[p] >> bit) & 1
        return None

    def col_levels(self, routes=None, tc=None):
        routes = self.pwm_routes() if routes is None else routes
        return [self.pin_out(p, b, routes, tc) for (p, b) in COLS]

    def selects_high(self, routes=None):
        routes = self.pwm_routes() if routes is None else routes
        return [i for i, (p, b) in enumerate(SEL_PINS) if self.pin_out(p, b, routes) == 1]

    def lit_leds(self, routes=None, tc=None):
        routes = self.pwm_routes() if routes is None else routes
        sh = self.selects_high(routes)
        if not sh:
            return []
        cl = self.col_levels(routes, tc)
        return [(s, c) for s in sh for c in range(17) if cl[c] == 0]

    def gpio_in(self, port):
        """pin levels seen by the input buffer"""
        lvl = 0
        cfg = self.reg[GPIO_BASE[port] + 8]
        for b in range(16):
            if ((cfg >> (2 * b)) & 3) == 0:
                lvl |= 1 << b              # internal pull-up
        for (p, b), v in self.ext_pins.items():
            if p == port:
                lvl = (lvl | (1 << b)) if v else (lvl & ~(1 << b))
        if port == 3:
            lvl &= ~ROW_MASK               # rows: external pull-down
            lvl |= self.stuck
            t = self.t_ms()
            pressed = set(self.pressed)
            for (a, b_, rc) in self.schedule:
                if a <= t < b_:
                    pressed.add(rc)
            if pressed:
                routes = self.pwm_routes()
                cl = self.col_levels(routes)
                infn = self.sym.func(self.cur_pc()) in self.SCAN_FUNCS
                for (r, c) in pressed:
                    if cl[c] == 1:
                        if self.settle_instr is not None and infn:
                            if self.scan_icount - self.col_high_icount.get(c, -10**9) < self.settle_instr:
                                continue
                        lvl |= 1 << ROWS[r]
                    elif self.release_instr is not None and infn and                             self.scan_now() - self.col_low_icount.get(c, -10**9) < self.release_instr:
                        lvl |= 1 << ROWS[r]   # row not yet discharged
        return lvl

    def gpio_data(self, port):
        mode = self.reg[GPIO_BASE[port] + 4] & 0xFFFF
        return (self.gpio_in(port) & ~mode & 0xFFFF) | (self.latch[port] & mode)

    def high_cols(self):
        cl = self.col_levels()
        return {c for c in range(17) if cl[c] == 1}

    # ------------------------------------------------------------- hooks
    def on_read(self, uc, access, addr, size, value, user):
        a = addr & ~3
        pc = self.cur_pc()
        self.reads_pc[(pc, a)] += 1
        base, off = a & ~0x1FFF, a & 0x1FFF
        v = self.reg[a]
        if base in GPIO:
            n = GPIO[base]
            if off == 0x00:
                v = self.gpio_data(n)
                if n == 3:
                    f = self.sym.func(pc)
                    if f in self.SCAN_FUNCS:
                        self.scan_pcs[pc] += 1
                        hc = self.high_cols()
                        if len(hc) == 1:
                            self.scan_pcs_one[pc] += 1
                            c = next(iter(hc))
                            self.read_deltas[self.scan_icount - self.col_high_icount.get(c, 0)] += 1
                        if f == 'Vector80':
                            self.check_isr_read(hc, pc)
                        if self.keep_col_reads:
                            self.col_reads.append((round(self.t_ms(), 4), frozenset(hc), v & ROW_MASK, pc, f))
            elif off in (0x20, 0x24, 0x28):
                v = 0
        elif a & ~0xFFF == UART2 and off in (0x00, 0x08, 0x14):
            avail = bool(self.uart_rx) and self.uart_rx[0][0] <= self.t_ms()
            if off == 0x00 and not (self.reg[UART2 + 0x0C] >> 7) & 1:
                v = self.uart_rx.pop(0)[1] if avail else 0          # RB
            elif off == 0x14:
                self.uart_tx_update()
                thre = self.uart_thr is None
                temt = thre and self.t_ms() >= self.uart_tsr_end
                v = (1 if avail else 0) | (int(thre) << 5) | (int(temt) << 6)   # LS: RDR, THRE, TEMT
            elif off == 0x08:
                v = 1                                               # II: no interrupt pending
        elif base == SYS0:
            if off == 0x08:
                v = self.reg[SYS0] & 1                       # CSST.IHRCRDY follows IHRCEN
            elif off == 0x0C:
                s = self.reg[a] & 1
                v = s | (s << 4)                             # SYSCLKST follows SYSCLKSEL
        elif base == SYS1:
            pass
        elif base == WDT:
            if off == 0x00:
                v = (1 if self.wdt['en'] else 0) | (2 if self.wdt['ie'] else 0)
            elif off == 0x08:
                v = self.wdt['tc']
            else:
                v = 0
        elif base == CT16B0:
            if off == 0x00:
                v = self.reg[a] & ~2                         # CRST self-clears
            elif off == 0x04:
                v = self.ct_tc()
        elif base == CT16B1 and off < 0x100:
            if not self.ct1_clk():
                self.violations.append('CT16B1 read with its clock gated (off %#x) at %s' % (off, self.sym.at(pc)))
            if off == 0x00:
                v = (1 if self.ct1['cen'] else 0)            # CRST self-clears
            elif off == 0x04:
                v = self.ct1_tc()
            elif off == 0xA8:
                v = 0
        elif base == FMC:
            if off == 0x04:
                v = self.reg[a] & ~1                         # never busy (operations are instantaneous)
                if self.fmc_busy_stuck:
                    v |= 1                                   # stage 4 knob: controller hung BUSY
        elif base == USB:
            if off == 0x04:
                v = 0                                        # INSTS: no events (no host traffic)
            elif off == 0x60:
                live = self.usb_frmno_live
                if live is None:
                    live = self.usb_host and self.uc.mem_read(self.sym['USBD1'], 1)[0] != 5
                v = int(self.t_ms()) & 0x7FF if live else 0   # FRMNO (SOF frame number)
        uc.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))

    UART_BYTE_MS = 10 / 115385 * 1000

    def uart_tx_update(self):
        now = self.t_ms()
        if self.uart_thr is not None and now >= self.uart_tsr_end:
            self.uart_tx.append((round(self.uart_tsr_end, 3), self.uart_thr))
            self.uart_tsr_end += self.UART_BYTE_MS
            self.uart_thr = None

    def uart_tx_write(self, b, pc):
        self.uart_tx_update()
        now = self.t_ms()
        if self.uart_thr is not None:
            self.uart_overwrites.append((round(now, 3), self.uart_thr, b, self.sym.func(pc)))
            self.uart_thr = b
        elif now >= self.uart_tsr_end:
            self.uart_tx.append((round(now, 3), b))
            self.uart_tsr_end = now + self.UART_BYTE_MS
        else:
            self.uart_thr = b

    def check_isr_read(self, hc, pc):
        """E-L4: every row read in the LED ISR: no select high, PWM detached, <= 1 column high"""
        routes = self.pwm_routes()
        sh = self.selects_high(routes)
        ioen = self.reg[CT16B1 + 0xA0] & 0xFFFFFF if self.ct1_clk() else 0
        self.read_checks['reads'] += 1
        if len(hc) == 1:
            self.read_checks['sample'] += 1
        elif len(hc) == 0:
            self.read_checks['release'] += 1
        bad = []
        if sh:
            bad.append('selects high %s' % sh)
        if ioen:
            bad.append('PWMIOENB=%#x' % ioen)
        if len(hc) > 1:
            bad.append('%d columns high' % len(hc))
        if bad:
            self.read_checks['bad'] += 1
            if len(self.read_bad) < 20:
                self.read_bad.append((round(self.t_ms(), 4), self.sym.at(pc), bad))

    def on_write(self, uc, access, addr, size, value, user):
        a = addr & ~3
        pc = self.cur_pc()
        base, off = a & ~0x1FFF, a & 0x1FFF
        value &= 0xFFFFFFFF
        if size < 4:
            sh = (addr & 3) * 8
            m = ((1 << (size * 8)) - 1) << sh
            value = (self.reg[a] & ~m) | ((value << sh) & m)
        if self.keep_reg_writes:
            self.reg_writes.append((round(self.t_ms(), 4), self.boot_index, pc, a, value))
        if a == UART2 and not (self.reg[UART2 + 0x0C] >> 7) & 1:
            self.uart_tx_write(value & 0xFF, pc)                          # TH
        if base in GPIO:
            n = GPIO[base]
            before = self.selects_high()
            if off == 0x24:
                for c, (p, bit) in enumerate(COLS):
                    if p == n and (value >> bit) & 1 and not (self.latch[n] >> bit) & 1:
                        self.col_high_icount[c] = self.scan_icount
                self.latch[n] |= value & 0xFFFF
            elif off == 0x28:
                for c, (p, bit) in enumerate(COLS):
                    if p == n and (value >> bit) & 1 and (self.latch[n] >> bit) & 1:
                        self.col_low_icount[c] = self.scan_now()
                self.latch[n] &= ~value & 0xFFFF
            elif off == 0x00:
                self.latch[n] = value & 0xFFFF
            else:
                self.reg[a] = value
            if n == 3 and off == 0x04 and (value & ROW_MASK):
                self.violations.append('P3 row pin set as OUTPUT (MODE=%#x) at %s' % (value, self.sym.at(pc)))
            self.check_leds(pc, before)
            return
        if base == SYS0:
            if off == 0x1C:
                self.violations.append('SYS0_EXRSTCTRL written (%#x) at %s' % (value, self.sym.at(pc)))
            if off == 0x14:
                self.reg[a] &= ~value
                return
        if base == SYS1 and off == 0x00 and (value & 0x10) and self.usb_first_write is None:
            self.usb_first_write = (round(self.t_ms(), 4), self.boot_index, 'AHBCLKEN.USBCLKEN', self.sym.at(pc))
        if base == USB and self.usb_first_write is None:
            self.usb_first_write = (round(self.t_ms(), 4), self.boot_index, 'USB reg %#x' % a, self.sym.at(pc))
        if base == WDT:
            self.wdt_write(off, value, pc)
            return
        if base == CT16B0:
            if off == 0x00:
                if value & 2:
                    self.ct.update(base_cyc=self.cyc_now(), base_tc=0)
                if (value & 1) and not self.ct['cen']:
                    self.ct.update(base_cyc=self.cyc_now(), base_tc=self.ct['base_tc'])
                self.ct['cen'] = bool(value & 1)
                self.reg[a] = value & ~2
                return
            if off == 0x08:
                tc = self.ct_tc()
                self.ct.update(base_cyc=self.cyc_now(), base_tc=tc, pre=value & 0xFF)
        if base == CT16B1 and off < 0x100:
            self.ct1_write(off, value, pc)
            return
        if a == PFPA:
            self.reg[a] = value & 0xFFFFFF
            self.check_pwm_routes(pc)
            return
        if base == FMC:
            self.fmc_write(off, value, pc)
            return
        self.reg[a] = value

    # approximate current cycle inside a chunk is not known: use chunk start
    def cyc_now(self):
        return self.cyc

    def scan_now(self):
        # cycle time with instruction resolution inside the scan code (self.cyc moves per chunk)
        return self.cyc + int((self.scan_icount - self.chunk_ic0) * self.CPI)

    def ct_tc(self):
        if not self.ct['cen']:
            return self.ct['base_tc'] & 0xFFFF
        return (self.ct['base_tc'] + (self.cyc_now() - self.ct['base_cyc']) // (self.ct['pre'] + 1)) & 0xFFFF

    # ----------------------------------------------------------- CT16B1 model
    def ct1_write(self, off, value, pc):
        a = CT16B1 + off
        if not self.ct1_clk():
            self.violations.append('CT16B1 write with its clock gated (off %#x) at %s' % (off, self.sym.at(pc)))
            return
        if off == 0x00:
            if value & 2:   # CRST
                self.ct1.update(base_cyc=self.cyc_now(), base_tc=0)
            start = bool(value & 1) and not self.ct1['cen']
            if not (value & 1) and self.ct1['cen']:
                self.ct1.update(base_tc=self.ct1_tc(), base_cyc=self.cyc_now())
            if start:
                self.ct1.update(base_cyc=self.cyc_now())
                self.on_ct1_start(pc)
            self.ct1['cen'] = bool(value & 1)
            self.reg[a] = value & 1
            return
        if off == 0x04:
            self.ct1.update(base_tc=value & 0xFFFF, base_cyc=self.cyc_now())
            return
        if off == 0xA8:   # IC: write 1 to clear RIS
            self.reg[CT16B1 + 0xA4] &= ~value
            return
        if off == 0xA4:
            return
        self.reg[a] = value
        if off == 0xA0 or off == 0x9C:
            self.check_pwm_routes(pc)

    def check_pwm_routes(self, pc):
        for (p, b), ch in self.pwm_routes().items():
            if (p, b) not in COLS:
                self.violations.append('CT16B1 PWM%d routed to non-column pin P%d.%d (PFPA=%#x PWMIOENB=%#x) at %s' % (
                    ch, p, b, self.reg[PFPA], self.reg[CT16B1 + 0xA0], self.sym.at(pc)))

    def on_ct1_start(self, pc):
        mrs = [self.reg[CT16B1 + 0x20 + 4 * ch] & 0xFFFF for ch in COL_CHAN]
        routes = self.pwm_routes()
        attached = bool(routes)
        if attached:
            for ch, mr in zip(COL_CHAN, mrs):
                m = self.pwm_mode(ch)
                low = 0
                if m == 1:   # mode 2: LOW from MR to 255
                    low = 0 if mr == 0 else max(0, 256 - min(mr, 256))
                    if mr == 0:
                        low = 256
                elif m == 0:
                    low = 0 if mr == 0 else min(mr, 256)
                self.mr_low_max = max(self.mr_low_max, low)
                if self.mr_min_seen is None or mr < self.mr_min_seen:
                    self.mr_min_seen = mr
        if self.keep_ct1_events:
            sh = self.selects_high(routes)
            self.ct1_events.append(('CEN', round(self.t_ms(), 4), self.cyc, (tuple(mrs), tuple(sh), attached,
                                                                            self.reg[CT16B1 + 0x9C], self.reg[CT16B1 + 0xA0])))

    def ct1_next_match(self):
        """cycle at which MR24 (with IE) matches, or None"""
        c = self.ct1
        if not c['cen'] or not self.ct1_clk():
            return None
        mctrl3 = self.reg[CT16B1 + 0x1C]
        mr24 = self.reg[CT16B1 + 0x80] & 0xFFFF
        tc = c['base_tc']
        if tc > mr24:
            return None
        div = ((self.reg[CT16B1 + 0x08] & 0xFF) + 1) * self.ct1_div_mult
        return c['base_cyc'] + (mr24 - tc + 1) * div

    def ct1_service(self):
        """process the MR24 match if its time has come (match -> RST/STOP/IE)"""
        nm = self.ct1_next_match()
        if nm is None or self.cyc < nm:
            return
        mctrl3 = self.reg[CT16B1 + 0x1C]
        t_match = (nm / self.hclk()) * 1e3 if False else round(self.t_ms() - (self.cyc - nm) / self.hclk() * 1e3, 4)
        if mctrl3 & (1 << 13):     # RST
            self.ct1.update(base_tc=0, base_cyc=nm)
        if mctrl3 & (1 << 14):     # STOP
            self.ct1['cen'] = False
            self.reg[CT16B1] &= ~1
        if mctrl3 & (1 << 12):     # IE
            self.reg[CT16B1 + 0xA4] |= 1 << 24
        routes = self.pwm_routes()
        tc = self.ct1_tc()
        cl = self.col_levels(routes, tc)
        lit = self.lit_leds(routes, tc)
        if any(v == 0 for v in cl) and routes:
            if len(self.match_bad) < 20:
                self.match_bad.append((t_match, [c for c in range(17) if cl[c] == 0]))
        if lit:
            self.lit_at_match.append((t_match, lit[:4]))
        if self.in_stall:
            self.stall_matches.append((t_match, lit[:4], [c for c in range(17) if cl[c] == 0]))
        if self.keep_ct1_events:
            self.ct1_events.append(('MATCH', t_match, nm, (tuple(cl), len(lit))))

    in_stall = False
    in_emu = False
    emu_flash_pending = []
    flash_hot = 0            # stress only: chunks left with the short size around a stalled flash op
    FLASH_HOT_CHUNK = 16
    efl_stopped = False

    def check_leds(self, pc, before):
        after = self.selects_high()
        if after == before:
            return
        f = self.sym.func(pc)
        rising = [s for s in after if s not in before]
        if rising:
            if f != 'Vector80':
                self.violations.append('LED select line(s) %s driven HIGH outside the LED ISR at %s' % (rising, self.sym.at(pc)))
            if len(after) > 1:
                self.violations.append('two LED select lines HIGH together %s at %s' % (after, self.sym.at(pc)))
            cl = self.col_levels()
            self.sel_edges['rise'] += 1
            if any(v == 0 for v in cl):
                self.sel_bad.append((round(self.t_ms(), 4), 'rise', rising, [c for c in range(17) if cl[c] == 0]))
        else:
            self.sel_edges['fall'] += 1
            cl = self.col_levels()
            if any(v == 0 for v in cl):
                self.sel_bad.append((round(self.t_ms(), 4), 'fall', [s for s in before if s not in after],
                                     [c for c in range(17) if cl[c] == 0]))

    def snap_exit(self, kind):
        routes = self.pwm_routes()
        ioen = self.reg[CT16B1 + 0xA0] & 0xFFFFFF if self.ct1_clk() else 0
        self.exit_snaps.append((kind, round(self.t_ms(), 4), self.selects_high(routes), ioen,
                                bool(self.nvic_iser & (1 << 16)), len(self.lit_leds(routes))))

    # ----------------------------------------------------------- WDT model
    def wdt_period(self):
        pre = (self.reg[SYS1 + 0x08] >> 20) & 7
        return 128 * (1 << pre) / 32000.0

    def wdt_write(self, off, value, pc):
        key_ok = (value >> 16) == 0x5AFA
        if not (self.reg[SYS1] >> 24) & 1:
            self.violations.append('WDT written with WDTCLKEN=0 at %s' % self.sym.at(pc))
        if not key_ok:
            self.violations.append('WDT write without 0x5AFA key off=%#x val=%#x at %s' % (off, value, self.sym.at(pc)))
            return
        if off == 0x00:
            en = bool(value & 1)
            self.wdt_cfg_writes.append((round(self.t_ms(), 3), self.boot_index, value, self.sym.at(pc)))
            if en and not self.wdt['en']:
                self.wdt['last_feed'] = self.t_s
                self.note('WDT enabled: TC=%d PRE=%d -> timeout %.3f s (%s)' % (
                    self.wdt['tc'], (self.reg[SYS1 + 8] >> 20) & 7, (self.wdt['tc'] + 1) * self.wdt_period(), self.sym.at(pc)))
            if not en and self.wdt['en']:
                self.note('WDT disabled (%s)' % self.sym.at(pc))
            self.wdt['en'] = en
            self.wdt['ie'] = bool(value & 2)
        elif off == 0x08:
            self.wdt['tc'] = value & 0xFF
        elif off == 0x0C:
            if (value & 0xFFFF) == 0x55AA:
                if self.wdt['en'] and self.wdt['last_feed'] is not None:
                    self.max_feed_gap = max(self.max_feed_gap, self.t_s - self.wdt['last_feed'])
                self.wdt['last_feed'] = self.t_s
                if len(self.wdt_feeds) < 200000:
                    self.wdt_feeds.append((self.t_s, self.sym.func(pc)))

    def wdt_check(self):
        w = self.wdt
        if w['en'] and not w['ie']:
            to = (w['tc'] + 1) * self.wdt_period()
            if self.t_s - w['last_feed'] > to:
                raise Event('WDT')

    # ----------------------------------------------------------- FLASH model
    def fmc_write(self, off, value, pc):
        a = FMC + off
        if off == 0x00:
            if (value >> 16) != 0x5AFA:
                self.violations.append('FLASH_LPCTRL write without key at %s' % self.sym.at(pc))
            self.reg[a] = value & 0xF
            return
        if off == 0x04:
            self.reg[a] &= ~(value & 4)
            return
        if off == 0x08:
            self.reg[a] = value
            if value & 0x40:
                self.fmc_start(pc)
                self.reg[a] = 0
            return
        if off == 0x10:
            self.reg[a] = value
            self.flash_buf = []
            return
        if off == 0x0C:
            self.flash_buf.append(value)
            self.reg[a] = value
            return
        self.reg[a] = value

    def fmc_start(self, pc):
        ctrl = self.reg[FMC + 8]
        addr = self.reg[FMC + 0x10]
        ok_range = FLASH_RW_LO <= addr < FLASH_RW_HI
        if self.power_cut_at is not None and len(self.flash_ops) >= self.power_cut_at:
            if not self.power_cut:
                self.power_cut = True
                self.note('POWER CUT before flash op #%d (%s %#x)' % (len(self.flash_ops), 'ERASE' if ctrl & 2 else 'PROGRAM', addr))
                if self.power_cut_torn is not None and ctrl & 3:
                    # torn op: the cut lands inside this erase / program (see torn_op)
                    torn_op(self.flash, 'ERASE' if ctrl & 2 else 'PROGRAM', addr, list(self.flash_buf), self.power_cut_torn)
                    self.note('  torn %s at %#x' % ('ERASE' if ctrl & 2 else 'PROGRAM', addr))
            self.flash_buf = []
            self.uc.emu_stop()
            return
        us = None
        if ctrl & 2 and self.flash_erase_us is not None:
            us = self.flash_erase_us
        elif ctrl & 1 and not ctrl & 2 and self.flash_prog_us is not None:
            us = self.flash_prog_us
        if us is not None:
            self.pending_stall += int(us * self.hclk() / 1e6)
        elif self.flash_stall_us:
            self.pending_stall += int(self.flash_stall_us * 48)
        if self.stress:
            self.flash_log.append((round(self.t_ms(), 4), 'ERASE' if ctrl & 2 else 'PROGRAM', addr,
                                   self.flash_ctx(), us if us is not None else self.flash_stall_us))
            if self.pending_stall and not self.in_call:
                # the stall is applied at the end of the chunk; chunks are short around each op
                # (on_efl_entry), so the stall starts right after the op and a pending ISR runs as
                # soon as it ends. (uc.emu_stop() here would re-execute the START store.)
                self.flash_hot = max(self.flash_hot, 2)
        if ctrl & 2:
            page = addr & ~63
            self.flash_ops.append(('ERASE', page, self.sym.at(pc), round(self.t_ms(), 3)))
            if not (FLASH_RW_LO <= page < FLASH_RW_HI):
                self.violations.append('FLASH erase outside EEPROM area: %#x at %s' % (page, self.sym.at(pc)))
            if self.page_erase:
                self.flash[page:page + 64] = b'\xff' * 64
                self.code_write(page, b'\xff' * 64)
        elif ctrl & 1:
            n = len(self.flash_buf)
            self.flash_ops.append(('PROGRAM', addr, n * 4, self.sym.at(pc), round(self.t_ms(), 3)))
            end = addr + 4 * n
            if (addr & ~63) != ((end - 1) & ~63):
                self.reg[FMC + 4] |= 4
            if not (ok_range and end <= FLASH_RW_HI):
                self.violations.append('FLASH program outside EEPROM area: %#x..%#x at %s' % (addr, end, self.sym.at(pc)))
            for i, w in enumerate(self.flash_buf):
                o = addr + 4 * i
                if o + 4 <= FLASH_SIZE:
                    old = struct.unpack_from('<I', self.flash, o)[0]
                    struct.pack_into('<I', self.flash, o, old & w)
                    self.code_write(o, bytes(self.flash[o:o + 4]))
        else:
            self.violations.append('FLASH START with CTRL=%#x at %s' % (ctrl, self.sym.at(pc)))
        self.flash_buf = []

    # ----------------------------------------------------------- SCS
    def on_scs_read(self, uc, access, addr, size, value, user):
        a = addr & ~3
        self.reads_pc[(self.cur_pc(), a)] += 1
        v = self.reg[a]
        if a == SYST_CSR:
            v = self.reg[a] & 7
        elif a == SYST_CVR:
            if self.systick['next'] is not None:
                v = max(0, self.systick['next'] - self.cyc) & 0xFFFFFF
        elif a == ICSR:
            v = (1 << 26) if EXC_SYSTICK in self.pending else 0
        elif a in (NVIC_ISER, NVIC_ICER):
            v = self.nvic_iser
        elif a in (NVIC_ISPR, NVIC_ICPR):
            v = sum(1 << (n - 16) for n in self.pending if n >= 16)
        uc.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))

    def on_scs_write(self, uc, access, addr, size, value, user):
        a = addr & ~3
        pc = self.cur_pc()
        value &= 0xFFFFFFFF
        if self.keep_reg_writes:
            self.reg_writes.append((round(self.t_ms(), 4), self.boot_index, pc, a, value))
        if a == SYST_CSR:
            old = self.reg[a]
            self.reg[a] = value & 7
            if (value & 1) and not (old & 1):
                self.systick['next'] = self.cyc + self.reg[SYST_RVR] + 1
            elif not (value & 1):
                self.systick['next'] = None
            return
        if a == SYST_RVR:
            self.reg[a] = value & 0xFFFFFF
            return
        if a == SYST_CVR:
            if self.reg[SYST_CSR] & 1:
                self.systick['next'] = self.cyc + self.reg[SYST_RVR] + 1
            return
        if a == ICSR:
            if value & (1 << 31):
                self.pending.add(EXC_NMI)
                uc.emu_stop()
            if value & (1 << 25):
                self.pending.discard(EXC_SYSTICK)
            if value & (1 << 26):
                self.pending.add(EXC_SYSTICK)
            return
        if a == AIRCR:
            if (value >> 16) == 0x05FA and (value & 4):
                self.reset_req = 'SYSRESETREQ'
                self.note('AIRCR SYSRESETREQ from %s' % self.sym.at(pc))
                self.snap_exit('SYSRESETREQ@' + self.sym.func(pc))
                uc.emu_stop()
            return
        if a == NVIC_ISER:
            self.nvic_iser |= value
            return
        if a == NVIC_ICER:
            self.nvic_iser &= ~value
            return
        if a == NVIC_ISPR:
            for n in range(32):
                if (value >> n) & 1:
                    self.pending.add(16 + n)
            return
        if a == NVIC_ICPR:
            for n in range(32):
                if (value >> n) & 1:
                    self.pending.discard(16 + n)
            return
        self.reg[a] = value
        if 0xE000E400 <= a < 0xE000E420:
            for k in range(4):
                self.prio[16 + (a - 0xE000E400) + k] = (value >> (8 * k)) & 0xC0
        elif a == 0xE000ED1C:
            self.prio[11] = (value >> 24) & 0xC0
        elif a == 0xE000ED20:
            self.prio[14] = (value >> 16) & 0xC0
            self.prio[15] = (value >> 24) & 0xC0

    def on_rom(self, uc, address, size, user):
        if self.isp is None:
            self.snap_exit('ROM-ISP')
            self.isp = dict(t_ms=round(self.t_ms(), 4), boot=self.boot_index, addr=address,
                            lr=uc.reg_read(UC_ARM_REG_LR), usb_touched=self.usb_first_write,
                            wdt_en=self.wdt['en'], control=uc.reg_read(UC_ARM_REG_CONTROL),
                            primask=uc.reg_read(UC_ARM_REG_PRIMASK),
                            col_mode=(self.reg[0x40048004], self.reg[0x40046004]),
                            ahbclken=self.reg[SYS1], hclk=self.hclk())
            self.note('ROM ISP entered at %#x (caller LR=%s)' % (address, self.sym.at(self.isp['lr'])))
        uc.emu_stop()

    def on_intr(self, uc, intno, user):
        self.last_intr = intno
        uc.emu_stop()

    def on_scan_insn(self, uc, address, size, user):
        self.scan_icount += 1

    def on_main_loop(self, uc, address, size, user):
        self.main_loop_hits += 1
        if self.stress:
            self.loop_tick(self.t_s)
        if self.first_main_loop_t is None:
            self.first_main_loop_t = round(self.t_ms(), 3)
            self.first_main_loop_boot = self.boot_index
            self.note('QMK main loop reached (first iteration complete)')

    # ----------------------------------------------------------- exceptions
    def take_exception(self, num, ret_pc=None):
        uc = self.uc
        ctrl = uc.reg_read(UC_ARM_REG_CONTROL)
        if self.active:
            spreg, excret = UC_ARM_REG_MSP, 0xFFFFFFF1
        elif ctrl & 2:
            spreg, excret = UC_ARM_REG_PSP, 0xFFFFFFFD
        else:
            spreg, excret = UC_ARM_REG_MSP, 0xFFFFFFF9
        sp = uc.reg_read(spreg)
        xpsr = (uc.reg_read(UC_ARM_REG_XPSR) & 0xF0000000) | 0x01000000
        if self.active:
            xpsr |= self.active[-1]
        if sp & 4:
            sp -= 4
            xpsr |= 1 << 9
        sp -= 32
        pc = (ret_pc if ret_pc is not None else uc.reg_read(UC_ARM_REG_PC)) & ~1
        regs = [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                                         UC_ARM_REG_R12, UC_ARM_REG_LR)] + [pc, xpsr]
        try:
            uc.mem_write(sp, struct.pack('<8I', *regs))
        except UcError:
            self.lockup = 'stacking fault for exception %d sp=%#x' % (num, sp)
            raise Event('LOCKUP')
        uc.reg_write(spreg, sp)
        if not self.active and (ctrl & 2):
            uc.reg_write(UC_ARM_REG_CONTROL, ctrl & ~2)
        vec = struct.unpack_from('<I', self.flash, 4 * num)[0]
        uc.reg_write(UC_ARM_REG_LR, excret)
        uc.reg_write(UC_ARM_REG_PC, vec & ~1)
        if self.active:
            self.nest_events[(self.active[-1], num)] += 1
        self.active.append(num)
        self.max_nest = max(self.max_nest, len(self.active))
        self.pc = vec | 1
        self.exc_count[num] += 1
        if num == EXC_CT16B1:
            self.isr_enter_cyc = self.scan_icount
            if self.stress:
                self.isr_tick(self.t_s)

    def exception_return(self, pc):
        uc = self.uc
        excret = {0xFFFFFFF0: 0xFFFFFFF1, 0xFFFFFFF8: 0xFFFFFFF9, 0xFFFFFFFC: 0xFFFFFFFD}.get(pc & ~1, None)
        if excret is None or not self.active:
            raise Event('BAD_EXC_RETURN pc=%#x active=%s' % (pc, self.active))
        num = self.active.pop()
        if num == EXC_CT16B1 and self.isr_enter_cyc is not None:
            if len(self.isr_durations) < 400000:
                self.isr_durations.append(self.scan_icount - self.isr_enter_cyc)
            self.isr_enter_cyc = None
        if excret == 0xFFFFFFF1 and not self.active:
            raise Event('EXC_RETURN F1 with no nested exception')
        if excret == 0xFFFFFFFD:
            spreg = UC_ARM_REG_PSP
        else:
            spreg = UC_ARM_REG_MSP
        sp = uc.reg_read(spreg)
        r0, r1, r2, r3, r12, lr, rpc, xpsr = struct.unpack('<8I', uc.mem_read(sp, 32))
        sp += 32
        if xpsr & (1 << 9):
            sp += 4
        uc.reg_write(spreg, sp)
        if excret == 0xFFFFFFFD:
            uc.reg_write(UC_ARM_REG_CONTROL, uc.reg_read(UC_ARM_REG_CONTROL) | 2)
        for r, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R12, UC_ARM_REG_LR),
                        (r0, r1, r2, r3, r12, lr)):
            uc.reg_write(r, v)
        uc.reg_write(UC_ARM_REG_XPSR, (xpsr & 0xF0000000) | 0x01000000)
        uc.reg_write(UC_ARM_REG_PC, rpc & ~1)
        self.pc = rpc | 1

    def pend_irq(self, n, enable=True):
        """inject a pending external IRQ n; enable=True also sets its NVIC enable bit
        (a spurious IRQ only exists if its line is enabled)"""
        if enable:
            self.nvic_iser |= 1 << n
        self.pending.add(16 + n)

    def exc_prio(self, n):
        if n == EXC_NMI:
            return -2
        if n == EXC_HARDFAULT:
            return -1
        return self.prio.get(n, 0)

    def exec_prio(self):
        p = 256
        for n in self.active:
            p = min(p, self.exc_prio(n))
        if self.uc.reg_read(UC_ARM_REG_PRIMASK) & 1:
            p = min(p, 0)
        return p

    def can_take(self, num):
        if num in (EXC_NMI, EXC_HARDFAULT):
            if num == EXC_NMI and EXC_NMI in self.active:
                return False
            return True
        if num >= 16 and not (self.nvic_iser >> (num - 16)) & 1:
            return False
        if num in self.active:
            return False
        if not self.nesting:
            if self.active:
                return False
            return self.uc.reg_read(UC_ARM_REG_PRIMASK) == 0
        # ARMv6-M: preempt only with a strictly higher priority (lower value); PRIMASK masks all
        return self.exc_prio(num) < self.exec_prio()

    # ----------------------------------------------------------- helpers for tests
    flash_sync_stop = False

    def on_efl_insn(self, uc, address, size, user):
        if (self.in_call and self.call_flash_pending) or (not self.in_call and self.emu_flash_pending):
            self.flash_sync_stop = True
            if not self.in_call:
                self.efl_stopped = True
            uc.emu_stop()

    def code_write(self, addr, data):
        """flash content change -> unicorn memory (deferred while call() runs, see stage-4 note)"""
        if self.in_call:
            self.call_flash_pending.append((addr, bytes(data)))
        elif self.stress and self.in_emu:
            # stress: never write the code page from inside a hook while run() is emulating (Unicorn
            # crashes on it, deterministically in the VIA stress); applied when the chunk ends
            self.emu_flash_pending.append((addr, bytes(data)))
        else:
            self.uc.mem_write(addr, data)

    def call(self, func, *args, max_insn=2000000):
        self.in_call = True
        self.call_flash_pending = []
        try:
            return self._call(func, *args, max_insn=max_insn)
        finally:
            self.in_call = False
            for a, d in self.call_flash_pending:
                self.uc.mem_write(a, d)
            self.call_flash_pending = []

    def _call(self, func, *args, max_insn=2000000):
        """Call a firmware function (thread context) from a quiescent point, e.g. inside an
        inject callback: registers are saved and restored; the timer/ISR do not run during the
        call (the call is short)."""
        uc = self.uc
        assert not self.active, 'call() inside an exception'
        regs = {r: uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                                            UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                                            UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
                                            UC_ARM_REG_R12, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_XPSR,
                                            UC_ARM_REG_PRIMASK)}
        sp = uc.reg_read(UC_ARM_REG_SP)
        old = bytes(uc.mem_read(CALL_RET, 4))
        uc.mem_write(CALL_RET, b'\xfe\xe7\x00\x00')     # b .
        uc.reg_write(UC_ARM_REG_PRIMASK, 1)
        for r, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            uc.reg_write(r, v & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_SP, (sp - 64) & ~7)
        uc.reg_write(UC_ARM_REG_LR, CALL_RET | 1)
        addr = self.sym[func] if isinstance(func, str) else func
        pc = addr | 1
        while True:
            self.flash_sync_stop = False
            uc.emu_start(pc, CALL_RET, count=max_insn)
            if not self.flash_sync_stop:
                break
            for a, d in self.call_flash_pending:     # see on_efl_insn
                uc.mem_write(a, d)
            self.call_flash_pending = []
            pc = uc.reg_read(UC_ARM_REG_PC) | 1
        ret = uc.reg_read(UC_ARM_REG_R0)
        uc.mem_write(CALL_RET, old)
        uc.reg_write(UC_ARM_REG_SP, sp)
        for r, v in regs.items():
            uc.reg_write(r, v)
        return ret

    def ram_read(self, addr, n):
        return bytes(self.uc.mem_read(addr, n))

    # ----------------------------------------------------------- stress instrumentation
    # Opt-in (G80(..., stress=True)). Nothing here runs for the existing tests.
    ISR_GAP_KEEP_MS = 0.5     # ISR entry gaps above this are logged with their start time
    LOOP_GAP_KEEP_MS = 5.0    # main-loop iteration gaps above this are logged

    def init_stress(self):
        from collections import deque
        self.emu_flash_pending = []
        self.hid_queue = deque()      # (ready_ms, 32 B packet) waiting in the USB OUT endpoint
        self.hid_script = deque()     # lock-step host: next packet sent hid_latency_ms after the answer
        self.hid_latency_ms = 1.0     # USB full speed interrupt OUT, bInterval 1 ms
        self.hid_rx = []              # (t_ms, cmd, channel/id byte) packets taken by raw_hid_task
        self.hid_tx = []              # (t_ms, 32 B) answers passed to send_raw_hid
        self.hid_per_task = []        # (t_ms, packets drained) per raw_hid_task call that drained any
        self.hid_cur_task = None
        self.rawhid_active = False
        self.rawhid_t0 = None
        self.rawhid_cmd = None
        self.rawhid_durs = []         # (t_ms start, duration ms, cmd byte)
        self.reports = []             # (t_ms, 'kb' | 'nkro', bytes)
        self.isr_last = None
        self.isr_last_boot = -1
        self.isr_n = 0
        self.isr_gap_max = 0.0
        self.isr_gap_hist = Counter() # gap rounded to 10 us -> count
        self.isr_gaps_big = []        # (t_ms start, gap ms)
        self.loop_last = None
        self.loop_last_boot = -1
        self.loop_gap_max = 0.0
        self.loop_gaps_big = []       # (t_ms start, gap ms)
        self.flash_log = []           # (t_ms, 'ERASE'|'PROGRAM', addr, ctx, stall_us)
        self.hid_sites = None

    @staticmethod
    def _bl_target(code, off, base):
        """Thumb BL at code[off:off+4] -> target address, else None"""
        h1, h2 = struct.unpack_from('<HH', code, off)
        if (h1 & 0xF800) != 0xF000 or (h2 & 0xD000) != 0xD000:
            return None
        s = (h1 >> 10) & 1
        j1, j2 = (h2 >> 13) & 1, (h2 >> 11) & 1
        i1, i2 = 1 - (j1 ^ s), 1 - (j2 ^ s)
        imm = (s << 24) | (i1 << 23) | (i2 << 22) | ((h1 & 0x3FF) << 12) | ((h2 & 0x7FF) << 1)
        if s:
            imm -= 1 << 25
        return base + off + 4 + imm

    def find_bl(self, func, target):
        """address of the first 'bl target' inside func, or None"""
        a, end = self.sym.range(func)
        tgt = self.sym[target] & ~1
        for o in range(a, end - 2, 2):
            if self._bl_target(self.flash, o, 0) == tgt:
                return o
        return None

    def boot_stress_hooks(self, uc):
        g = self.sym.get
        if g('raw_hid_task') is not None and g('usb_endpoint_out_receive') is not None and g('raw_hid_receive') is not None:
            rx = self.find_bl('raw_hid_task', 'usb_endpoint_out_receive')
            call = self.find_bl('raw_hid_task', 'raw_hid_receive')
            if rx is not None and call is not None:
                self.hid_sites = (rx, call)
                uc.hook_add(UC_HOOK_CODE, self.on_hid_rx, begin=rx, end=rx)
                uc.hook_add(UC_HOOK_CODE, self.on_rawhid_ret, begin=call + 4, end=call + 4)
                a = g('raw_hid_task') & ~1
                uc.hook_add(UC_HOOK_CODE, self.on_rht_entry, begin=a, end=a)
                a = g('raw_hid_receive') & ~1
                uc.hook_add(UC_HOOK_CODE, self.on_rawhid_entry, begin=a, end=a)
        self._efl_resume = None
        if self.flash_prog_us is not None or self.flash_erase_us is not None:
            for name in ('efl_lld_program', 'efl_lld_start_erase_sector'):
                if g(name) is not None:
                    a = g(name) & ~1
                    uc.hook_add(UC_HOOK_CODE, self.on_efl_entry, begin=a, end=a)
        for name, kind, n in (('send_raw_hid', 'hid', 32), ('host_keyboard_send', 'kb', 8), ('host_nkro_send', 'nkro', 32)):
            if g(name) is not None:
                a = g(name) & ~1
                uc.hook_add(UC_HOOK_CODE, self.on_out_report, begin=a, end=a, user_data=(kind, n))

    def hid_send(self, pkt, at_ms=None):
        """queue one 32 B raw HID OUT packet; raw_hid_task takes it from the main loop at/after at_ms"""
        p = (bytes(pkt) + b'\x00' * 32)[:32]
        self.hid_queue.append((self.t_ms() if at_ms is None else at_ms, p))

    def hid_send_lockstep(self, pkts, at_ms=None):
        """host that waits for each answer: first packet at at_ms, every next one hid_latency_ms after
        raw_hid_receive returned for the previous one"""
        pkts = [(bytes(p) + b'\x00' * 32)[:32] for p in pkts]
        if not pkts:
            return
        self.hid_send(pkts[0], at_ms)
        self.hid_script.extend(pkts[1:])

    def hid_idle(self):
        return not self.hid_queue and not self.hid_script and not self.rawhid_active

    def on_hid_rx(self, uc, address, size, user):
        if self.hid_cur_task is not None and self.hid_queue and self.hid_queue[0][0] <= self.t_ms():
            _, p = self.hid_queue.popleft()
            buf = uc.reg_read(UC_ARM_REG_R1)
            uc.mem_write(buf, p)
            self.hid_rx.append((round(self.t_ms(), 4), p[0], p[1]))
            self.hid_cur_task[1] += 1
            uc.reg_write(UC_ARM_REG_R0, 1)
            uc.reg_write(UC_ARM_REG_PC, (address + 4) | 1)   # skip the real endpoint read (Thumb bit kept)
        elif self.hid_cur_task is not None:
            if self.hid_cur_task[1]:
                self.hid_per_task.append(tuple(self.hid_cur_task))
            self.hid_cur_task = None

    def on_efl_entry(self, uc, address, size, user):
        """end the chunk at the entry of a flash program/erase function, then run it in short chunks"""
        if self.in_call:
            return
        if self._efl_resume == address:
            self._efl_resume = None
            self.flash_hot = max(self.flash_hot, 12)
            return
        self._efl_resume = address
        self.efl_stopped = True
        uc.emu_stop()

    def on_rht_entry(self, uc, address, size, user):
        self.hid_cur_task = [round(self.t_ms(), 4), 0]

    def on_rawhid_entry(self, uc, address, size, user):
        if self.in_call:
            return
        self.rawhid_active = True
        self.rawhid_t0 = self.t_ms()
        self.rawhid_cmd = uc.mem_read(uc.reg_read(UC_ARM_REG_R0), 1)[0]

    def on_rawhid_ret(self, uc, address, size, user):
        if not self.rawhid_active:
            return
        self.rawhid_active = False
        now = self.t_ms()
        self.rawhid_durs.append((round(self.rawhid_t0, 4), now - self.rawhid_t0, self.rawhid_cmd))
        if self.hid_script:
            self.hid_send(self.hid_script.popleft(), now + self.hid_latency_ms)

    def on_out_report(self, uc, address, size, user):
        kind, n = user
        if self.in_call and kind != 'hid':
            return
        b = bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R0), n))
        if kind == 'hid':
            self.hid_tx.append((round(self.t_ms(), 4), b))
        else:
            self.reports.append((round(self.t_ms(), 4), kind, b))

    def flash_ctx(self):
        if self.active:
            return 'isr'
        if self.first_main_loop_t is None or getattr(self, 'first_main_loop_boot', -1) != self.boot_index:
            return 'boot'
        if self.in_call:
            return 'call'
        return 'rawhid' if self.rawhid_active else 'main'

    def isr_tick(self, t_s):
        if self.isr_last is not None and self.isr_last_boot == self.boot_index:
            g = (t_s - self.isr_last) * 1e3
            self.isr_gap_hist[round(g, 2)] += 1
            if g > self.isr_gap_max:
                self.isr_gap_max = g
            if g > self.ISR_GAP_KEEP_MS:
                self.isr_gaps_big.append((round(self.isr_last * 1e3, 4), g))
        self.isr_last = t_s
        self.isr_last_boot = self.boot_index
        self.isr_n += 1

    def loop_tick(self, t_s):
        if self.loop_last is not None and self.loop_last_boot == self.boot_index:
            g = (t_s - self.loop_last) * 1e3
            if g > self.loop_gap_max:
                self.loop_gap_max = g
            if g > self.LOOP_GAP_KEEP_MS:
                self.loop_gaps_big.append((round(self.loop_last * 1e3, 4), g))
        self.loop_last = t_s
        self.loop_last_boot = self.boot_index

    def stress_mark(self):
        """reset the max/gap statistics (start of a measurement window)"""
        self.isr_gap_max = self.loop_gap_max = 0.0
        self.isr_gap_hist = Counter()
        self.isr_gaps_big, self.loop_gaps_big = [], []

    def wl_cache(self, n=1024):
        """logical EEPROM image as the firmware sees it (wear-leveling RAM cache)"""
        a = self.sym.get('wear_leveling')
        return None if a is None else bytes(self.uc.mem_read(a, n))

    # ----------------------------------------------------------- main run loop
    def run(self, until_ms, stop=None, chunk=4000):
        while self.t_ms() < until_ms:
            if self.power_cut:
                return 'POWERCUT'
            for item in list(self.inject):
                if self.t_ms() >= item[0]:
                    # Injections rewrite PC/PRIMASK or call firmware functions: apply them only at a
                    # clean thread-mode point (no active exception, IRQs enabled, no NMI pending), or
                    # the ChibiOS ARMv6-M ISR exit path (__port_exit_from_isr -> NMI) discards them.
                    # Deferred at most 5 ms.
                    clean = not self.active and EXC_NMI not in self.pending and                         self.uc.reg_read(UC_ARM_REG_PRIMASK) == 0
                    if not clean and self.t_ms() < item[0] + 5.0:
                        continue
                    self.inject.remove(item)
                    item[1](self)
            if self.isp is not None:
                return 'ISP'
            if self.lockup:
                return 'LOCKUP'
            if stop and stop(self):
                return 'STOP'
            # SysTick timing
            st = self.systick['next']
            if st is not None and self.cyc >= st:
                self.reg[SYST_CSR] |= 1 << 16
                if self.reg[SYST_CSR] & 2:
                    self.pending.add(EXC_SYSTICK)
                rvr = self.reg[SYST_RVR] + 1
                self.systick['next'] = st + rvr * max(1, (self.cyc - st) // rvr + 1)
            # CT16B1 match and IRQ16 (level, while RIS.MR24IF is set)
            self.ct1_service()
            if (self.reg[CT16B1 + 0xA4] >> 24) & 1 and not self.irq16_suppressed and EXC_CT16B1 not in self.active:
                self.pending.add(EXC_CT16B1)
            elif self.irq16_suppressed:
                self.pending.discard(EXC_CT16B1)
            self.uart_tx_update()
            thre_irq = (self.reg[UART2 + 0x04] >> 1) & 1 and self.uart_thr is None and not (self.reg[UART2 + 0x0C] >> 7) & 1
            if (self.uart_rx and self.uart_rx[0][0] <= self.t_ms() and self.reg[UART2 + 0x04] & 1) or thre_irq:
                if 16 + IRQ_UART2 not in self.active:
                    self.pending.add(16 + IRQ_UART2)                # RDA or THRE, level
            else:
                self.pending.discard(16 + IRQ_UART2)
            for num in sorted(self.pending, key=lambda k: (self.exc_prio(k), k)):
                if self.can_take(num):
                    self.pending.discard(num)
                    self.take_exception(num)
                    break
            pc = self.uc.reg_read(UC_ARM_REG_PC)
            n = chunk
            if st is not None:
                n = max(1, min(n, self.systick['next'] - self.cyc if self.systick['next'] else n))
            nm = self.ct1_next_match()
            if nm is not None:
                n = max(1, min(n, nm - self.cyc))
            self.reset_req = None
            self.last_intr = None
            if self.flash_hot:
                n = min(n, self.FLASH_HOT_CHUNK)
                self.flash_hot -= 1
            self.efl_stopped = False
            err = None
            in_isr = self.in_isr16()
            ic0 = self.scan_icount
            self.chunk_ic0 = ic0
            self.in_emu = True
            try:
                self.uc.emu_start(pc | 1, UNTIL, count=n)
            except UcError as e:
                err = e
            self.in_emu = False
            if self.emu_flash_pending:
                for a, d in self.emu_flash_pending:
                    self.uc.mem_write(a, d)
                self.emu_flash_pending = []
            npc = self.uc.reg_read(UC_ARM_REG_PC)
            stopped_early = err is not None or self.last_intr is not None or self.reset_req or self.isp is not None \
                or EXC_NMI in self.pending or self.efl_stopped
            wfi = (not stopped_early) and self.is_wfi_stop(npc, n, stopped_early)
            done = n if not stopped_early else max(1, n // 2)
            if in_isr and stopped_early:
                done = max(1, self.scan_icount - ic0)   # exact: the ISR code is instruction-counted
            if wfi:
                done = 1
                self.wfi_count += 1
            self.advance(done)
            if self.pending_stall:
                stall, self.pending_stall = self.pending_stall, 0
                self.in_stall = True
                end = self.cyc + stall
                while True:   # timer keeps running during a flash stall, the CPU does not
                    nm = self.ct1_next_match()
                    if nm is None or nm > end:
                        break
                    self.advance(nm - self.cyc)
                    self.ct1_service()
                if not self.ct1['cen']:
                    # CPU still stalled, timer stopped at TC = 0: this is what the LEDs show now
                    routes = self.pwm_routes()
                    cl = self.col_levels(routes)
                    self.stall_matches.append((round(self.t_ms(), 4), self.lit_leds(routes)[:4],
                                               [c for c in range(17) if cl[c] == 0], 'stopped'))
                self.advance(max(0, end - self.cyc))
                self.in_stall = False
            if wfi:
                nxt = self.systick['next']
                if not (nxt is not None and nxt > self.cyc and (self.reg[SYST_CSR] & 2)):
                    nxt = None
                nm = self.ct1_next_match()
                if nm is not None and nm > self.cyc and (self.nvic_iser >> 16) & 1:
                    nxt = nm if nxt is None else min(nxt, nm)
                if nxt is not None:
                    self.advance(nxt - self.cyc)
            try:
                self.wdt_check()
            except Event:
                self.system_reset('WDT')
                continue
            if self.isp is not None:
                return 'ISP'
            if self.reset_req:
                self.system_reset(self.reset_req)
                continue
            if npc >= 0xFFFFFFF0:
                try:
                    self.exception_return(npc)
                except Event as e:
                    self.faults.append(str(e))
                    self.lockup = str(e)
                    return 'LOCKUP'
                continue
            if self.last_intr is not None and self.last_intr not in (8,):
                self.fault('cpu exception intno=%d at %s' % (self.last_intr, self.sym.at(npc)), npc)
                continue
            if err is not None:
                self.fault('%s at %s (pc=%#x)' % (err, self.sym.at(npc), npc), npc)
                continue
        return 'TIME'

    def is_wfi(self, pc):
        return False

    def is_wfi_stop(self, npc, n, stopped_early):
        # WFI makes uc.emu_start return before the instruction budget is used
        try:
            prev = struct.unpack_from('<H', self.flash, (npc - 2) & 0xFFFE)[0] if npc < FLASH_SIZE else 0
        except struct.error:
            return False
        return prev == 0xBF30 and self.sym.func(npc) == '__idle_thread'

    def advance(self, n):
        h = self.hclk()
        self.cyc += int(n * self.CPI)
        self.t_s += n * self.CPI / h

    def fault(self, why, pc):
        self.faults.append((round(self.t_ms(), 3), self.boot_index, why))
        self.note('FAULT: ' + why)
        if EXC_HARDFAULT in self.active:
            self.lockup = 'fault inside HardFault: ' + why
            return
        self.take_exception(EXC_HARDFAULT, ret_pc=pc)
