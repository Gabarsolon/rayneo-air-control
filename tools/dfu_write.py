#!/usr/bin/env python3
"""dfu_write.py -- DfuSe flash WRITE tool. Unlike dfu_read.py, this is NOT
read-only. It adds erase + program primitives on top of DfuDevice.

STM32 flash can only flip bits 1->0 when programming; going 0->1 requires
an explicit page erase first. So writing anywhere means: erase the whole
8KB sector that address lives in (destroying everything else in that
sector), then reprogram it. There is no "erase just these bytes."

This only ever targets the *application* region (0x0800C000+). The
bootloader's own DFU sector-permission descriptor declares the low 48KB
read-only (type 'a'), so a compliant device refuses erase/write commands
there regardless of what this tool sends -- but this tool does not special
case or guard that; it trusts the device's own enforcement, same as any
DFU host would.

Usage as a library:
    from dfu_write import DfuWriter
    d = DfuWriter(0x0483, 0xdf11)
    d.erase_page(0x0818A000)               # erase one 8KB sector
    d.download_range(0x0818A000, data)     # program it
    readback = d.upload_range(0x0818A000, len(data))  # verify (inherited)
"""
from __future__ import annotations

import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dfu_read import DfuDevice, REQTYPE_OUT, DFU_DNLOAD  # noqa: E402


class DfuWriter(DfuDevice):
    def erase_page(self, addr: int):
        """Erase the 8KB (or whatever the device's page size is) sector
        containing `addr`. Blocking -- waits for the device to report
        dfuDNLOAD-IDLE again, which for an erase can take tens of ms."""
        cmd = bytes([0x41]) + struct.pack("<I", addr)
        self._dnload_command(cmd)

    def download_block(self, block_num: int, data: bytes):
        """Send one DfuSe download block (block numbering >= 2, matching
        upload's convention) and wait for it to complete."""
        self.dev.ctrl_transfer(REQTYPE_OUT, DFU_DNLOAD, block_num, self.intf_num, data)
        self._wait_idle()

    def download_range(self, addr: int, data: bytes):
        """Program `data` starting at `addr`. Caller is responsible for
        erasing every sector `data` touches first -- this does not erase."""
        self.set_address_pointer(addr)
        block = 2
        off = 0
        while off < len(data):
            chunk = data[off: off + self.xfer_size]
            self.download_block(block, chunk)
            off += len(chunk)
            block += 1


if __name__ == "__main__":
    print(__doc__)
