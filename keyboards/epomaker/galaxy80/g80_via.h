// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// VIA custom channel ("Galaxy80" menu), the VIA packet pre-hook and the deferred execution of the
// VIA commands that write EEPROM.
#pragma once

#include "galaxy80.h"

#if defined(G80_FEATURES) && defined(VIA_ENABLE)

/* Called first for every VIA packet (via_command_kb, galaxy80.c). Returns true when the packet
 * has been taken here (answered now, or answered later by g80_via_task()). */
bool g80_via_command(uint8_t *data, uint8_t length);

/* Housekeeping: performs one bounded step of a deferred VIA EEPROM write and sends its answer when
 * the write is complete (g80_features_task). */
void g80_via_task(void);

/* A deferred VIA write is waiting or running (its answer has not been sent yet). */
bool g80_via_write_pending(void);

/* Bytes left in the wear-leveling write log before the next consolidation (g80_wear_leveling.c). */
uint32_t g80_wl_log_free(void);

#endif
