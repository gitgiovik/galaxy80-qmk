/* Copyright 2026 gitgiovik
   SPDX-License-Identifier: GPL-2.0-or-later */
/* Galaxy80 link-time guard (implicit linker script, appended to SN32F240B.ld via
 * EXTRALDFLAGS in rules.mk). Everything stored in flash must end below the EEPROM
 * emulation area that starts at 0xF7C0, and the boot flag must be at 0xFFFC. */
G80_EEPROM_START = 0xF7C0;
ASSERT(__textdata_base__ + SIZEOF(.data) <= G80_EEPROM_START, "Galaxy80: firmware overlaps the EEPROM area at 0xF7C0")
ASSERT(__ram0_init_text__ + SIZEOF(.ram0_init) <= G80_EEPROM_START, "Galaxy80: ram0_init overlaps the EEPROM area at 0xF7C0")
ASSERT(_flag_start == 0xFFFC, "Galaxy80: boot flag not at 0xFFFC")
/* RAM left between .bss and the end of RAM (ChibiOS core heap, unused by QMK)
 * must stay >= 512 B. The 0xDEADBEEF bootloader magic lives in the last word. */
ASSERT(__heap_end__ - __heap_base__ >= 512, "Galaxy80: less than 512 B of free RAM")
