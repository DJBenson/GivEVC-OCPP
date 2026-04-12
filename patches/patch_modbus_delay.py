#!/usr/bin/env python3
"""
patch_modbus_delay.py — Patch GivEnergy AC_GL1 firmware to remove ~10-minute
Modbus TCP startup delay.

Background
----------
The firmware runs a WiFi STA connection wait loop before starting the Modbus TCP
task. The outer loop iterates 65,327 times with a 10 ms sleep per iteration,
producing a worst-case delay of ~10.9 minutes before Modbus becomes available.

The loop exits early if WiFi connects (polls SRAM+0x18), so on a connected
device the delay is rarely hit — but if WiFi is unavailable or slow to associate,
the Modbus server is blocked for the full duration.

Patch
-----
File offset : 0x018612  (flash address 0x08018612)
Before      : 4F F4 2F 76  — MOVW R6, #0xFF2F  (= 65,327 iterations)
After       : 40 F2 01 06  — MOVW R6, #0x0001  (= 1 iteration, ~10 ms total)

Both are 32-bit Thumb-2 MOVW T3 encodings — same size, no alignment shift,
no effect on surrounding branch targets or the subsequent state-machine dispatch.

Usage
-----
    python3 patch_modbus_delay.py <firmware.bin>

Produces <firmware_patched.bin> in the same directory as the input file.
"""

import sys
import os

PATCH_OFFSET = 0x018612
ORIGINAL_BYTES = bytes([0x4F, 0xF4, 0x2F, 0x76])  # MOVW R6, #0xFF2F (65327)
PATCHED_BYTES  = bytes([0x40, 0xF2, 0x01, 0x06])   # MOVW R6, #0x0001 (1)


def patch(input_path: str) -> str:
    base, ext = os.path.splitext(input_path)
    output_path = f"{base}_patched{ext}"

    with open(input_path, "rb") as f:
        fw = bytearray(f.read())

    actual = bytes(fw[PATCH_OFFSET:PATCH_OFFSET + 4])
    if actual == PATCHED_BYTES:
        print(f"Firmware is already patched — no changes written.")
        return input_path

    if actual != ORIGINAL_BYTES:
        raise ValueError(
            f"Unexpected bytes at offset 0x{PATCH_OFFSET:06X}: "
            f"got {actual.hex(' ').upper()}, "
            f"expected {ORIGINAL_BYTES.hex(' ').upper()}. "
            f"This patch targets AC_GL1 1.14 — wrong firmware version?"
        )

    fw[PATCH_OFFSET:PATCH_OFFSET + 4] = PATCHED_BYTES

    with open(output_path, "wb") as f:
        f.write(fw)

    print(f"Patched: {input_path}")
    print(f"  Offset  : 0x{PATCH_OFFSET:06X}")
    print(f"  Before  : {ORIGINAL_BYTES.hex(' ').upper()}  (MOVW R6, #65327 — ~10.9 min delay)")
    print(f"  After   : {PATCHED_BYTES.hex(' ').upper()}  (MOVW R6, #1     — ~10 ms delay)")
    print(f"Output  : {output_path}")
    return output_path


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <firmware.bin>")
        sys.exit(1)

    input_path = sys.argv[1]
    if not os.path.isfile(input_path):
        print(f"Error: file not found: {input_path}")
        sys.exit(1)

    try:
        patch(input_path)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
