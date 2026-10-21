# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Low-power extension of the Galaxy80 emulator (g80emu.G80 is not modified; this is a subclass).

Adds the parts of the SN32F240B the deep sleep of g80_lpm.c touches:
  - PMU_CTRL (0x40032040): MODE 2 = deep sleep, 4 = sleep, 0 = normal.
  - Clock tree: IHRC on/off (SYS0_ANBCTRL bit 0) with a start-up time before CSST.IHRCRDY reads 1
    (ihrc_start_us, or never with ihrc_never=True); system clock select (CLKCFG.SYSCLKSEL) and
    status (CLKCFG.SYSCLKST, bits 4..6) that follows the select only after clk_switch_us and only if
    the target oscillator runs (sysclk_never=0/1 keeps that source from ever being reached). HCLK
    follows the STATUS, not the select (base model: the select), divided by AHBCP.
    Violations: IHRC switched off while it clocks the CPU; HCLK above 24 MHz with FLASH_LPCTRL
    LPMODE != 5 (the flash would need more wait states), checked on every clock / LPCTRL write.
  - WFI outside the ChibiOS idle thread: if an enabled interrupt (NVIC) or SysTick is already
    pending the WFI returns at once; otherwise the CPU sleeps. While it sleeps no cycle is counted
    (HCLK stopped in deep sleep: CT16B0, SysTick and CT16B1 freeze), the simulated time jumps to the
    next event (scheduled key edge, injection, end of run()), the rows P3.6-11 and the slider P0.7
    are watched for edges: an edge on a pin with GPIO IE set raises RIS and pends IRQ28 (P3) / IRQ31
    (P0); an enabled pending IRQ ends the sleep (PRIMASK does not matter for the wake, as on
    Cortex-M0). UART bytes that arrive while the CPU sleeps are lost (no HCLK). A running watchdog
    keeps counting (it runs on ILRC) and resets the board through the base model.
  - Knobs for fault injection: p3_ie_broken (writes to P3 IE ignored), uart_temt_stuck (UART LS
    TEMT never set), on_sleep_enter (callback at the first deep-sleep register write).
  - tx_listener: callback after every UART TX byte (a simulated radio module can ACK in time).
  - lpm_trace: (t_ms, name, value, pc function) of every write to the registers of the sleep path
    (clock, flash LP, PMU, USB_CFG, WDT CFG, SysTick CSR, P0/P3 interrupt registers, NVIC
    ISER/ICER/ICPR, SYS1 AHBCLKEN, SYS0 LVDCTRL), kept even with keep_reg_writes off.
  - sleeps: list of dicts, one per WFI (entry state snapshot; wake_t / cause None while still asleep,
    cause 'pending ...' when the WFI returned at once). Entry detection: the watchdog stop called from
    g80_wdt_suspend / g80_lpm_sleep (on_sleep_enter fires there).
"""
import struct
from g80emu import G80, SYS0, SYS1, FMC, WDT, USB, UART2, SYST_CSR, NVIC_ISER, NVIC_ICER, NVIC_ICPR, \
    EXC_SYSTICK, EXC_NMI, ROW_MASK, FLASH_SIZE
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_PRIMASK, UC_ARM_REG_LR

PMU_CTRL = 0x40032040
P0, P3 = 0x40044000, 0x4004A000
IRQ_P3, IRQ_P0 = 28, 31
SLIDER_BIT = 1 << 7

REG_NAMES = {
    SYS0 + 0x00: 'ANBCTRL', SYS0 + 0x0C: 'CLKCFG', SYS0 + 0x10: 'AHBCP', SYS0 + 0x18: 'LVDCTRL',
    FMC + 0x00: 'FLASH_LPCTRL', PMU_CTRL: 'PMU_CTRL', USB + 0x10: 'USB_CFG', WDT + 0x00: 'WDT_CFG',
    SYST_CSR: 'SYST_CSR', SYS1 + 0x00: 'AHBCLKEN',
    P3 + 0x0C: 'P3_IS', P3 + 0x10: 'P3_IBS', P3 + 0x14: 'P3_IEV', P3 + 0x18: 'P3_IE', P3 + 0x20: 'P3_IC',
    P0 + 0x0C: 'P0_IS', P0 + 0x10: 'P0_IBS', P0 + 0x14: 'P0_IEV', P0 + 0x18: 'P0_IE', P0 + 0x20: 'P0_IC',
    NVIC_ISER: 'ISER', NVIC_ICER: 'ICER', NVIC_ICPR: 'ICPR',
}
IHRC, ILRC = 0, 1


class G80Lpm(G80):
    def __init__(self, *a, ihrc_start_us=20.0, clk_switch_us=1.0, ihrc_never=False, sysclk_never=None,
                 p3_ie_broken=False, uart_temt_stuck=False, **kw):
        self.ihrc_start_us = ihrc_start_us
        self.clk_switch_us = clk_switch_us
        self.ihrc_never = ihrc_never
        self.sysclk_never = sysclk_never
        self.p3_ie_broken = p3_ie_broken
        self.uart_temt_stuck = uart_temt_stuck
        self.on_sleep_enter = None
        self.lpm_trace = []
        self.lpm_viol = []
        self.sleeps = []
        self.lpm_until = None
        self._asleep = None
        self.tx_listener = None       # callable(m), called whenever a UART TX byte has gone out
        self.heal_on_reset = False    # True: a system reset clears ihrc_never / sysclk_never (fault healed)
        super().__init__(*a, **kw)

    # ------------------------------------------------------------ reset state
    def periph_reset(self):
        super().periph_reset()
        self.clk = dict(ihrc_en=True, ihrc_rdy_t=-1.0, sel=IHRC, st=IHRC, sw_t=0.0)
        self._asleep = None
        self._entered = False

    def system_reset(self, cause):
        if self.heal_on_reset:
            self.ihrc_never, self.sysclk_never = False, None
        super().system_reset(cause)

    # ------------------------------------------------------------ clock model
    def _ihrc_ready(self):
        c = self.clk
        return c['ihrc_en'] and not self.ihrc_never and self.t_ms() >= c['ihrc_rdy_t']

    def _clk_update(self):
        c = self.clk
        if c['st'] != c['sel'] and self.t_ms() >= c['sw_t'] and self.sysclk_never != c['sel']:
            if c['sel'] == ILRC or self._ihrc_ready():
                c['st'] = c['sel']

    def hclk(self):
        self._clk_update()
        sysclk = 48e6 if self.clk['st'] == IHRC else 32e3
        return sysclk / (1 << (self.reg[SYS0 + 0x10] & 7))

    def _clk_check(self, pc, what):
        if self.clk['st'] == IHRC and not self.clk['ihrc_en']:
            self.lpm_viol.append('IHRC off while it clocks the CPU (%s at %s)' % (what, self.sym.at(pc)))
        if self.hclk() > 24e6 and (self.reg[FMC] & 0xF) != 5 and self._entered:
            self.lpm_viol.append('HCLK %.0f MHz with FLASH_LPCTRL LPMODE=%d (%s at %s)' % (
                self.hclk() / 1e6, self.reg[FMC] & 0xF, what, self.sym.at(pc)))

    # ------------------------------------------------------------ register hooks
    def on_read(self, uc, access, addr, size, value, user):
        super().on_read(uc, access, addr, size, value, user)
        a = addr & ~3
        v = None
        if a == SYS0 + 0x08:
            v = 1 if self._ihrc_ready() else 0
        elif a == SYS0 + 0x0C:
            self._clk_update()
            v = (self.reg[a] & 1) | (self.clk['st'] << 4)
        elif a == UART2 + 0x14 and self.uart_temt_stuck:
            v = struct.unpack('<I', bytes(uc.mem_read(a, 4)))[0] & ~(1 << 6)
        elif a == PMU_CTRL:
            v = self.reg[a] & 7
        if v is not None:
            uc.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))

    def _trace(self, a, value, pc):
        name = REG_NAMES.get(a)
        if name is None:
            return
        f = self.sym.func(pc)
        if name == 'WDT_CFG' and not (value & 1) and f in ('g80_wdt_stop', 'g80_wdt_suspend') and                 self.sym.func(self.uc.reg_read(UC_ARM_REG_LR)) in ('g80_wdt_suspend', 'g80_lpm_sleep'):
            self._entered = True          # first register write of the deep-sleep sequence
            if self.on_sleep_enter:
                self.on_sleep_enter(self)
        self.lpm_trace.append((round(self.t_ms(), 4), self.boot_index, name, value, f))

    def on_write(self, uc, access, addr, size, value, user):
        a = addr & ~3
        pc = self.cur_pc()
        if a == P3 + 0x18 and self.p3_ie_broken:
            value = 0
        super().on_write(uc, access, addr, size, value, user)
        v = value & 0xFFFFFFFF if size == 4 else self.reg[a]
        c = self.clk
        if a == SYS0 + 0x00:
            on = bool(v & 1)
            if on and not c['ihrc_en']:
                c['ihrc_rdy_t'] = self.t_ms() + self.ihrc_start_us / 1e3
            c['ihrc_en'] = on
            self._clk_check(pc, 'ANBCTRL=%#x' % v)
        elif a == SYS0 + 0x0C:
            self._clk_update()
            c['sel'] = v & 1
            c['sw_t'] = self.t_ms() + self.clk_switch_us / 1e3
            self._clk_check(pc, 'CLKCFG=%#x' % v)
        elif a == SYS0 + 0x10 or a == FMC:
            self._clk_check(pc, '%s=%#x' % (REG_NAMES[a], v))
        self._trace(a, v, pc)

    def on_scs_write(self, uc, access, addr, size, value, user):
        super().on_scs_write(uc, access, addr, size, value, user)
        a = addr & ~3
        if a in (SYST_CSR, NVIC_ISER, NVIC_ICER, NVIC_ICPR):
            self._trace(a, value & 0xFFFFFFFF, self.cur_pc())

    def uart_tx_update(self):
        n = len(self.uart_tx)
        super().uart_tx_update()
        if self.tx_listener and len(self.uart_tx) != n:
            self.tx_listener(self)

    def uart_tx_write(self, b, pc):
        n = len(self.uart_tx)
        super().uart_tx_write(b, pc)
        if self.tx_listener and len(self.uart_tx) != n:
            self.tx_listener(self)

    # ------------------------------------------------------------ WFI / deep sleep
    SLOW_CHUNK = 32   # instructions per emulation chunk while HCLK runs from ILRC (32 = 4 ms at 8 kHz)

    def run(self, until_ms, stop=None, chunk=4000):
        """base run(); while HCLK runs from ILRC the chunks are SLOW_CHUNK instructions (time only moves
        between chunks: 4000 instructions at 8 kHz would be a 0.5 s step)"""
        self.lpm_until = until_ms
        while True:
            slow = self.hclk() < 1e6
            changed = lambda mm, slow=slow: (mm.hclk() < 1e6) != slow
            r = super().run(until_ms, stop=(lambda mm: changed(mm) or (stop is not None and stop(mm))),
                            chunk=self.SLOW_CHUNK if slow else chunk)
            if r != 'STOP' or (stop is not None and stop(self)):
                return r

    def _wake_pending(self):
        for n in self.pending:
            if n >= 16 and (self.nvic_iser >> (n - 16)) & 1:
                return 'IRQ%d' % (n - 16)
            if n in (EXC_SYSTICK, EXC_NMI):
                return 'exc%d' % n
        return None

    def _levels(self):
        return self.gpio_in(3) & ROW_MASK, self.gpio_in(0) & SLIDER_BIT

    def _edges(self):
        rows, sl = self._levels()
        s = self._asleep
        ch3, ch0 = rows ^ s['rows'], sl ^ s['slider']
        s['rows'], s['slider'] = rows, sl
        for base, ch, irq in ((P3, ch3, IRQ_P3), (P0, ch0, IRQ_P0)):
            if ch:
                self.reg[base + 0x1C] |= ch            # RIS (both-edge sense assumed, as configured)
                if self.reg[base + 0x18] & ch:          # IE
                    self.pending.add(16 + irq)

    def _next_event(self):
        now = self.t_ms()
        cand = [self.lpm_until if self.lpm_until is not None else now + 1.0]
        cand += [t for t, _ in self.inject if t > now]
        for a, b, _ in self.schedule:
            cand += [x for x in (a, b) if x > now]
        return max(now + 0.001, min(cand))

    def is_wfi_stop(self, npc, n, stopped_early):
        if super().is_wfi_stop(npc, n, stopped_early):
            return True
        try:
            prev = struct.unpack_from('<H', self.flash, (npc - 2) & 0xFFFE)[0] if npc < FLASH_SIZE else 0
        except struct.error:
            return False
        if prev != 0xBF30:
            return False
        wfi_pc = npc - 2
        if self._asleep is None:
            pend = self._wake_pending()
            snap = dict(t=round(self.t_ms(), 4), boot=self.boot_index, pmu=self.reg[PMU_CTRL] & 7,
                        clk_st=self.clk['st'], ahbcp=self.reg[SYS0 + 0x10] & 7, lpctrl=self.reg[FMC] & 0xF,
                        anbctrl=self.reg[SYS0] & 1, primask=self.uc.reg_read(UC_ARM_REG_PRIMASK),
                        wdt_en=self.wdt['en'], syst_csr=self.reg[SYST_CSR] & 7, iser=self.nvic_iser,
                        p3_ie=self.reg[P3 + 0x18], p3_ibs=self.reg[P3 + 0x10], p0_ie=self.reg[P0 + 0x18],
                        ct1clk=self.ct1_clk(), selects=self.selects_high(), cols=self.col_levels(),
                        usb_cfg=self.reg[USB + 0x10], flash_ops=len(self.flash_ops), func=self.sym.func(wfi_pc),
                        pend=pend, wake_t=None, cause=None)
            if pend:
                snap.update(wake_t=snap['t'], cause='pending ' + pend)
                self.sleeps.append(snap)
                self.note('WFI in %s returns at once: %s pending' % (snap['func'], pend))
                return True
            rows, sl = self._levels()
            self._asleep = dict(snap=snap, rows=rows, slider=sl)
            self.sleeps.append(snap)          # wake_t / cause filled in at the wake
            self.note('DEEP SLEEP (PMU=%d) in %s' % (snap['pmu'], snap['func']))
        # asleep: no cycles, time jumps to the next event
        self.t_s = self._next_event() / 1e3
        self._edges()
        cause = self._wake_pending()
        if cause:
            s = self._asleep['snap']
            s.update(wake_t=round(self.t_ms(), 4), cause=cause)
            self._asleep = None
            t0 = s['t']
            self.uart_rx = [x for x in self.uart_rx if not (t0 <= x[0] < self.t_ms())]  # no HCLK: lost
            self.note('WAKE from deep sleep: %s' % cause)
        else:
            self.uc.reg_write(UC_ARM_REG_PC, wfi_pc | 1)                              # still asleep
        return True

    # ------------------------------------------------------------ helpers
    def sleep_trace(self, boot=None):
        """lpm_trace entries of the sleep sequence (from the watchdog stop of the first deep sleep onwards)"""
        out, on = [], False
        for e in self.lpm_trace:
            if boot is not None and e[1] != boot:
                continue
            if e[2] == 'WDT_CFG' and e[4] == 'g80_wdt_suspend' and not (e[3] & 1):
                on = True
            if on:
                out.append(e)
        return out
