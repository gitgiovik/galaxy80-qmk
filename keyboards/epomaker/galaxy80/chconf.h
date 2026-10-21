// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include_next <chconf.h>

/* Any ChibiOS system halt (panic) goes to the mask-ROM ISP instead of hanging. */
#if !defined(_FROM_ASM_)
void galaxy80_fault_to_isp(void);
#endif
#undef CH_CFG_SYSTEM_HALT_HOOK
#define CH_CFG_SYSTEM_HALT_HOOK(reason) \
    {                                   \
        (void)(reason);                 \
        galaxy80_fault_to_isp();        \
    }
