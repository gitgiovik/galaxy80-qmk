// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Shadows the generated version.h: everything the QMK tool generates is kept, except that
// QMK_BUILDDATE becomes the fixed G80_BUILD_DATE so that identical sources always produce
// identical images (the VIA EEPROM magic is derived from QMK_BUILDDATE).

#pragma once

#include_next "version.h"
#include "g80_version.h"

#undef QMK_BUILDDATE
#define QMK_BUILDDATE G80_BUILD_DATE
