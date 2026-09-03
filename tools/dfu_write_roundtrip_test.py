#!/usr/bin/env python3
"""dfu_write_roundtrip_test.py -- stages a full erase/write/verify/restore
round trip on ONE sector, deliberately picked to be past real firmware
content so a failure can't touch anything that matters.

Usage:
    python tools/dfu_write_roundtrip_test.py [--addr 0x08082000]

On this device this currently fails at step [4] with a clean, protocol-level
errWRITE (DFU status 0x03) -- see docs/write-protection-findings.md for why.
Steps [1]-[3] (read, erase, verify-erase) succeed; the script never proceeds
past a failed erase-verification, and always attempts to restore the sector
to blank before exiting.
"""
import argparse
import sys
import time

from dfu_write import DfuWriter

DEFAULT_ADDR = 0x08082000  # just past real fw content on taurus4p0; still blank
LEN = 256


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", type=lambda x: int(x, 0), default=DEFAULT_ADDR)
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=0x0483)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=0xdf11)
    args = ap.parse_args()

    d = DfuWriter(args.vid, args.pid)
    d.clear_status()
    print(f"xfer_size={d.xfer_size}  intf={d.intf_num}")

    print("\n[1] reading current content before touching anything...")
    before = d.upload_range(args.addr, LEN)
    print("  first 32 bytes:", before[:32].hex())
    print("  all 0xFF?", before == b"\xff" * LEN)

    print("\n[2] erasing sector...")
    t0 = time.time()
    d.erase_page(args.addr)
    print(f"  erase done in {time.time() - t0:.3f}s")

    print("\n[3] verifying erase (should be all 0xFF)...")
    after_erase = d.upload_range(args.addr, LEN)
    print("  first 32 bytes:", after_erase[:32].hex())
    print("  all 0xFF?", after_erase == b"\xff" * LEN)
    if after_erase != b"\xff" * LEN:
        print("  !! erase did not produce blank flash -- STOPPING before any write.")
        sys.exit(1)

    print("\n[4] writing test pattern...")
    pattern = (b"RAYNEO_DFU_WRITE_TEST_ROUNDTRIP_" + bytes(range(256)))[:LEN]
    try:
        d.download_range(args.addr, pattern)
    except RuntimeError as e:
        print("  !! write failed:", e)
        d.clear_status()
        print("  cleared error state, state now:", d._getstatus())
        sys.exit(1)

    print("\n[5] reading back and verifying...")
    readback = d.upload_range(args.addr, LEN)
    match = readback == pattern
    print("  match:", match)
    if not match:
        print("  expected:", pattern.hex())
        print("  got:     ", readback.hex())

    print("\n[6] restoring to blank (erasing again)...")
    d.erase_page(args.addr)
    final = d.upload_range(args.addr, LEN)
    print("  restored to all-0xFF?", final == b"\xff" * LEN)

    print("\n=== RESULT:", "PASS - write path verified" if match else "FAIL - do not proceed", "===")


if __name__ == "__main__":
    main()
