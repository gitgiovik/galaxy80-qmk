# Is this my board?

This firmware was written for, and tested on, **one** board. A different revision can have a different
pin wiring, and a wrong firmware on a different board can leave it unusable until it is re-flashed from
the bootloader (which still works, see [recovery.md](recovery.md)). Check everything below *before*
flashing. If one line does not match, stop.

## What is supported

| Item | Value |
|---|---|
| Keyboard | Epomaker x Feker Galaxy80 (wired, 2.4G dongle, Bluetooth; slider and Win/Mac switch) |
| Stock USB vendor:product ID | **05AC:024F** |
| Stock firmware | **v1.10**. It is the only version tested; older or newer ones may use other settings |
| Microcontroller | SONiX SN32F248BF (Cortex-M0, 64 KiB flash) |
| PCB marking | "galaxy80-RGB-HFD", date code 20231124 |

About the USB ID: 05AC is the vendor ID that belongs to Apple. The stock firmware uses it anyway, so tools
that look the vendor up by ID may call the keyboard an "Apple" device. The 2.4G dongle also shows
up as 05AC:024F, so read the ID with the dongle unplugged and the keyboard on its cable.

## How to read the USB ID

Plug the keyboard in by cable, slider OFF, with the stock firmware still on it.

* **Windows**: Device Manager, open "Human Interface Devices" or "Keyboards", Properties of the keyboard, tab
  Details, property "Hardware Ids" (look for `VID_05AC&PID_024F`). Or in PowerShell:

      Get-PnpDevice -PresentOnly | Where-Object InstanceId -match 'VID_05AC' | Format-Table Status,FriendlyName,InstanceId

* **Linux**: `lsusb` (look for `05ac:024f`).
* **macOS**: Apple menu > About This Mac > System Report > USB, or `system_profiler SPUSBDataType`.

## Firmware version and chip check

* The stock firmware version is shown by the vendor driver software. Only v1.10 is supported.
* With the flasher, in the ROM bootloader (`0C45:7040`), the read-only command
  `sonixflasher -v 0c45/7040 -i` prints the chip family and flash size. Expected: an SN32F240B-family
  chip with 64 KB. Anything else: do not flash.

## The PCB marking

The marking and date code are printed on the board and cannot be read without opening the keyboard.
This project does **not** ask you to open it, and recovery never needs it. If you have opened the board
anyway and the marking is different, treat it as a different revision. A matching USB ID and firmware
version is the practical check from the outside.

## If it does not match

Do not flash. Open a *Hardware revision* issue (the template asks for the USB ID, the stock firmware
version, the PCB marking and date code if you know them, and the chip marking) so that the revision can be
reviewed. Attach photos of the marking, never vendor firmware.

## After flashing

The keyboard enumerates as **4758:0080** with the product string "Galaxy80 QMK (unofficial)". The vendor
driver software no longer recognises it (it is looking for the stock ID); that is expected.
