#!/usr/bin/env python3
"""dfu_write_debug.py -- manual, step-by-step DfuSe download debugger.

Sends the raw control transfers one at a time (set address pointer, then a
single data block) and prints GETSTATUS after each, instead of relying on
DfuWriter's higher-level helpers. Useful for confirming exactly where a
write attempt transitions to dfuERROR.

Usage:
    python tools/dfu_write_debug.py [--addr 0x0818A000]
"""
import argparse
import struct

import dfu_read as m
from dfu_write import DfuWriter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", type=lambda x: int(x, 0), default=0x0818A000)
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=0x0483)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=0xdf11)
    args = ap.parse_args()

    d = DfuWriter(args.vid, args.pid)
    print(f"xfer_size={d.xfer_size} intf={d.intf_num}")

    def gs(label=""):
        status, poll_ms, state = d._getstatus()
        name = m.DFU_STATE_NAMES.get(state, "?")
        print(f"  [{label}] GETSTATUS: status=0x{status:02x} poll={poll_ms}ms state={state} ({name})")
        return status, poll_ms, state

    print("\nclearing any error state from last run...")
    d.clear_status()
    gs("after clrstatus")

    print("\nsetting address pointer...")
    cmd = bytes([0x21]) + struct.pack("<I", args.addr)
    d.dev.ctrl_transfer(m.REQTYPE_OUT, m.DFU_DNLOAD, 0, d.intf_num, cmd)
    gs("after set-addr dnload")
    gs("after set-addr dnload (2nd poll)")

    print("\nsending block=2 data (16 bytes -- caller is responsible for the "
          "target sector already being erased)...")
    data = bytes(range(16))
    d.dev.ctrl_transfer(m.REQTYPE_OUT, m.DFU_DNLOAD, 2, d.intf_num, data)
    gs("after data dnload")
    gs("after data dnload (2nd poll)")


if __name__ == "__main__":
    main()
