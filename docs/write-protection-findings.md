# Why raw DFU writes fail, and how the official updater gets around it

This documents the full trace behind the write-path research in
`tools/dfu_write.py`. Everything here comes from disassembling
`bootloader.bin` (the 48KB region `0x08000000`–`0x0800C000`, read back
live with `tools/dfu_read.py`) and from RayNeo's own public web
updater at `ota.rayneo.com`, whose JS bundles are plain, unminified-enough
client-side code anyone's browser downloads to run the page — nothing
here came from bypassing any protection to get at RayNeo's source.

## The bootloader is a custom build, not stock ST code

The device enumerates in DFU mode as VID `0x0483` PID `0xdf11` — ST's
generic "STM32 BOOTLOADER" identifier — which initially suggested this
might be unmodified ST ROM code. String-dumping `bootloader.bin` rules
that out: it contains custom strings (`Rayneo`, `Rayneo AR Glasses
DFU`, `WINUSB`) not present in stock ST bootloaders, alongside the
expected DfuSe sector-descriptor string
(`@Internal Flash/0x08000000/6*08Ka,192*8Kg`). It's ST middleware-based
but RayNeo-customized.

The 48KB region also isn't entered via a normal CPU reset vector — the
bytes at file offset 0 (`SP=0x00003A01`, `reset=0x00000008`) aren't a
plausible vector table. It's most likely called directly as a function
library from the running application, not booted independently.

## The unlock sequence is standard STM32 FPEC code

At `0x0800339C`:

```
0800339C  ldr   r3, [pc, #0x18]   ; =0x40022000    (FLASH_R_BASE)
0800339E  ldr   r0, [r3, #0x28]                     (read CR)
080033A0  ands  r0, r0, #1                           (LOCK bit)
080033A4  beq   #0x80033b6                           (already unlocked -> skip)
080033A6  ldr   r2, [pc, #0x14]   ; =0x45670123      (KEY1)
080033A8  str   r2, [r3, #4]                          (write to KEYR)
080033AA  add.w r2, r2, #-0x77777778                  (compute KEY2 = 0xCDEF89AB)
080033AE  str   r2, [r3, #4]                          (write KEY2)
```

KEY2 (`0xCDEF89AB`) never appears as a literal anywhere in the image —
it's computed as `KEY1 + (-0x77777778)`, which is why a raw literal
scan for the second magic key comes up empty. There's a matching
`Lock()` function next to it (`0x080033B8`) that sets the LOCK bit back.
This is completely ordinary ST flash-driver code, not something RayNeo
added protection logic on top of — meaning the bootloader unlocks
flash itself, automatically, before every operation. It's not a
missing handshake on the host side.

## FLASH_R_BASE register map (as used by this bootloader)

Confirmed by direct disassembly, offsets from `0x40022000`:

| Offset | Register (inferred) | Evidence |
|---|---|---|
| `+0x04` | KEYR | KEY1/KEY2 unlock sequence above |
| `+0x20` | SR (status) | polled for busy/error bits after program (`0x080033D8`) |
| `+0x28` | CR (control) | LOCK bit 0; PER-style bit 1 set before erase/program |
| `+0x40` | OPTR (option register) | read live, see below |
| `+0x100`/`+0x104` | privilege/secure-access bits | set (bit30/bit31) before erase, pattern consistent with a TrustZone-style dual alias (L5/U5-class) rather than plain F1/F4 |

## Live register read: RDP Level 1 confirmed

Reading `0x40022000`–`0x40022140` live (safe — it's the exact range
the bootloader's own code already dereferences) turned up:

```
+0x028 (CR)   = 0x00001DC1   -- bit0 (LOCK) set, i.e. locked at rest
+0x040 (OPTR) = 0x01FF0001
```

The low byte of the option register is the Read-Out Protection level
byte on every STM32 family: `0xAA` = disabled, `0xCC` = level 2, and
**any other value defaults to level 1**. `0x01` means RDP Level 1 is
active — a deliberate factory security setting, not a default/blank
state.

## The actual write failure: a hardware error flag, not a policy check

The program routine (`0x08003338`, invoked from `0x08003430`) does a
plain unlock → set program-enable bit → direct word writes → status
check. The status check, at `0x080033D8`, is where every write attempt
has actually failed:

```
080033E2  ldr   r4, [pc, #0x44]   ; =0x40022000
080033E4  ldr   r3, [r4, #0x20]                       (read SR)
080033E6  tst.w r3, #0xb                                (busy bits)
080033EA  bne   #0x8003402                              (still busy -> poll/timeout)
080033EC  ldr   r3, [r4, #0x20]                        (read SR again)
080033EE  ands  r3, r3, #0xfe0000                       (mask error-flag bits)
080033F2  beq   #0x8003418                              (no error -> success)
080033F4  movs  r0, #1                                  ; -- error path --
080033F8  ldr   r2, [r1, #4]
080033FA  orrs  r2, r3
080033FC  str   r2, [r1, #4]                            (stash flags for diagnostics)
080033FE  str   r3, [r4, #0x30]                         (write-1-to-clear)
08003400  pop   {r4, r5, r6, pc}                        (return 1 = failure)
```

That failure return propagates straight up to the DFU status the host
sees: `errWRITE` (`0x03`). The flash controller itself is raising a
genuine hardware error flag on the program step specifically — never
on erase, never on read — which is the textbook signature of
per-region **Write Protection (WRP)**. The bootloader code around it
is unremarkable; it's just faithfully reporting what the silicon told it.

Separately, a boundary check in the calling function (`0x080021D2`,
comparing the target range against `0x0800C000`, the app-region start)
confirmed the *opposite* of an early worry: addresses in the app
region correctly reach the write loop. The bootloader's own low 48KB
is the one structurally routed away from ever attempting a write —
consistent with the DfuSe sector descriptor's read-only claim for that
region. The WRP/RDP protection is a separate, independent layer on top
of that, covering the app region itself.

## How does RayNeo's own site write it, then?

`ota.rayneo.com`'s JS (`chunk-40fab0de.*.js`) implements the same
standard DfuSe download sequence this repo does — same commands, same
structure. The one meaningful difference is the very first step of
`dfuseDownLoad()`:

```js
console.log("Erasing sector 0x41414141");
return this.erase(0x41414141, 5);
```

`0x41414141` isn't a real flash address — it's a sentinel meaning
**mass erase the entire chip**, not a single sector. This is the whole
answer:

- Downgrading RDP from Level 1 back to Level 0 is only ever done, by
  design, through a full mass erase — that's ST's built-in anti-tamper
  behavior (no partial read-back of the old protected contents on the
  way down).
- The same mass erase is documented to reset WRP configuration back to
  unprotected as a side effect.

So the sequence that actually works is: mass-erase the whole chip
first (wiping bootloader *and* application, stripping both RDP and WRP
in the process) → then rewrite everything from scratch with the exact
same page-by-page DfuSe writes already implemented here. There is no
partial-write bypass — reaching a writable state means accepting a
full-chip wipe first, immediately followed by a full reflash. This
repo has deliberately never gone further than page-erase tests on
already-blank space, specifically to avoid that step without a much
more explicit reason to take it.

## Two more findings from the same JS, unrelated to the write path

- **VID/PID list**: the site recognizes `0483:df11` (ST), `3941:af51`
  and `1bbb:af51` as DFU-mode identifiers for different RayNeo models,
  confirming `0483:df11` isn't a secret — it's the same generic ST
  bootloader PID this whole repo already assumed.
- **The "damaged firmware" message is cosmetic.** The UI's device
  status label does `if (this.isDFU) return this.$t("damagedFirmware")`
  — unconditionally, no actual corruption check. Any device that
  enumerates with a DFU-mode PID gets this label. A device stuck in
  DFU mode (e.g. from an interrupted session, or simply not exiting
  DFU cleanly) will show "firmware is damaged" even when nothing is
  actually corrupted — worth knowing before assuming the worst.
