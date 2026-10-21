// Copyright 2026 gitgiovik
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Firmware identity constants that must not depend on the build.

#pragma once

/* Firmware version string. */
#define G80_FW_VERSION "1.0.0"

/* Fixed stand-in for QMK_BUILDDATE (format "YYYY-MM-DD-HH:MM:SS"). VIA derives its EEPROM magic
 * from digits 2..3, 5..6 and 8..9 of this string, so a build date would make every build of the
 * same sources differ and would also reset the VIA EEPROM at each flash. Change the value only
 * when the VIA EEPROM contents must be invalidated; a new custom-block layout is covered by
 * G80_EE_LAYOUT in g80_perkey.h. */
#define G80_BUILD_DATE "2026-10-20-00:00:00"
