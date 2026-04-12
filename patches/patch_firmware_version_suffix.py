#!/usr/bin/env python3
"""
patch_firmware_version_suffix.py — Patch the embedded printable firmware version
string in a GivEnergy AC_GL1 firmware image.

This script looks for the human-readable version marker embedded in the binary,
for example:

    AC_GLx_1.14

It writes a patched copy of the firmware in the same directory, changing only
the final digit of that printable version string. By default it increments the
last digit by 1, so:

    AC_GLx_1.14 -> AC_GLx_1.15

Usage
-----
    python3 patch_firmware_version_suffix.py <firmware.bin>

Optional:
    python3 patch_firmware_version_suffix.py <firmware.bin> --digit 7

Produces:
    <firmware>_patched.bin
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


VERSION_PATTERN = re.compile(rb"AC_GLx_(\d)\.(\d)(\d)")


def _build_output_path(input_path: Path) -> Path:
    """Return the sibling output path for the patched firmware image."""

    return input_path.with_name(f"{input_path.stem}_patched{input_path.suffix}")


def _find_unique_version_marker(firmware: bytes) -> tuple[re.Match[bytes], int]:
    """Return the unique embedded printable firmware version marker."""

    matches = list(VERSION_PATTERN.finditer(firmware))
    if not matches:
        raise ValueError(
            "No printable firmware version marker matching AC_GLx_<major>.<minor><patch> was found."
        )
    if len(matches) > 1:
        offsets = ", ".join(f"0x{match.start():06X}" for match in matches[:8])
        raise ValueError(
            "Multiple printable firmware version markers were found; refusing to guess. "
            f"Candidate offsets: {offsets}"
        )

    match = matches[0]
    return match, match.start()


def patch_version_suffix(input_path: Path, target_digit: int | None = None) -> Path:
    """Patch the final digit of the embedded printable firmware version string."""

    firmware = bytearray(input_path.read_bytes())
    match, offset = _find_unique_version_marker(firmware)
    original_marker = match.group(0)

    current_last_digit = int(chr(match.group(3)[0]))
    new_last_digit = (
        target_digit if target_digit is not None else current_last_digit + 1
    )

    if not 0 <= new_last_digit <= 9:
        raise ValueError(
            f"Target digit must be in the range 0-9, got {new_last_digit}."
        )
    if new_last_digit == current_last_digit:
        raise ValueError(
            f"Target digit {new_last_digit} is the same as the existing final digit."
        )

    patched_marker = bytearray(original_marker)
    patched_marker[-1] = ord(str(new_last_digit))

    firmware[offset : offset + len(original_marker)] = patched_marker

    output_path = _build_output_path(input_path)
    output_path.write_bytes(firmware)

    print(f"Patched: {input_path}")
    print(f"  Offset : 0x{offset:06X}")
    print(f"  Before : {original_marker.decode('ascii')}")
    print(f"  After  : {patched_marker.decode('ascii')}")
    print(f"Output : {output_path}")
    return output_path


def main() -> int:
    """CLI entry point."""

    parser = argparse.ArgumentParser(
        description=(
            "Patch the final digit of the embedded printable firmware version string "
            "inside a firmware binary."
        )
    )
    parser.add_argument("firmware", type=Path, help="Path to the firmware .bin file")
    parser.add_argument(
        "--digit",
        type=int,
        help="Optional explicit replacement for the final version digit (0-9). "
        "If omitted, the script increments the existing final digit by 1.",
    )
    args = parser.parse_args()

    if not args.firmware.is_file():
        print(f"Error: file not found: {args.firmware}", file=sys.stderr)
        return 1

    try:
        patch_version_suffix(args.firmware, args.digit)
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
