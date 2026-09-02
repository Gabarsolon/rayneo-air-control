"""Command-ID catalogue for the taurus4p0 HID control protocol.

Every entry below was placed by disassembling the STM32 firmware image
(taurus4p0_20260110.dfu) at the handler-table dispatch (165 function
pointers starting at firmware address 0x08012414) and reading each
handler's body. The ``confidence`` field is not decoration — it is the
difference between "traced and sent to a live device with a matching
ack" and "the vtable slot exists and isn't null." Treat CONFIRMED as
safe, EXPERIMENTAL as reversible-but-unverified, and don't trust
anything not listed here at all (about 90 of the 165 table slots point
at the same "unimplemented" stub at 0x080123A7).
"""

from enum import Enum, IntEnum


class Confidence(Enum):
    CONFIRMED = "confirmed"        # sent live, response/behavior matched prediction
    TRACED = "traced"              # handler fully disassembled, not exercised live
    EXPERIMENTAL = "experimental"  # vtable slot located, callee not resolved


class DisplayMode(IntEnum):
    """Values for Command.DISPLAY_MODE (0x1A).

    Confirmed live: 0x00 was sent and produced ack 99c8400016ce2f02e308000d05010100.
    1 and 2 are inferred from the prior session's usage and from the
    px8618_sdr2hdr_hdr_set/_load code path, not independently verified here.
    """
    SDR = 0x00          # plain SDR, Pixelworks tone-mapper OUT of the pipeline
    AI_HDR = 0x01        # px8618 SDR->HDR inverse tone mapping (shadow-lifting)
    HDR10 = 0x02          # panel expects real PQ/HDR10 signal


# cmd_id -> (name, confidence, notes)
COMMANDS = {
    0x00: ("STATUS", Confidence.CONFIRMED,
           "Handler at 0x08010EF0. Only header/uptime/build-date/refresh-rate "
           "fields are real; see protocol.parse_status() for the honest field map."),
    0x1A: ("DISPLAY_MODE", Confidence.CONFIRMED,
           "Handler at 0x08011F70. val = DisplayMode. Persisted to NVM "
           "(0x20029A58+0x12 via save routine at 0x0800D3A8)."),
    0x29: ("PANEL_HDR10_CHANGE_A", Confidence.TRACED,
           "Handler at 0x08011EEC (hid_call_panel_hdr10_change). Same handler as 0x54."),
    0x54: ("PANEL_HDR10_CHANGE_B", Confidence.TRACED,
           "Alias of 0x29 -- both dispatch to hid_call_panel_hdr10_change."),
    0x48: ("AUDIO_TUBE_MODE", Confidence.TRACED,
           "Handler at 0x08011BD8 (hid_call_audio_tube_mode). Audio routing, not display."),
    0x6D: ("GAMMA_INDEX", Confidence.EXPERIMENTAL,
           "Handler at 0x080104EC indirects through *(0x2005A090)+0x30. Prior tool's "
           "0-5 'Cinema 2.4 / Dark Room / Bright Room' preset names are NOT in the "
           "firmware -- treat them as invented until the callee is resolved."),
    0x6E: ("GAMUT_MODE", Confidence.EXPERIMENTAL,
           "Handler at 0x08010504 indirects through *(0x2005A090)+0x34. Same caveat "
           "as GAMMA_INDEX -- prior tool's DCI-P3/Display-P3/BT.2020 names unverified."),
}


class Command(IntEnum):
    STATUS = 0x00
    DISPLAY_MODE = 0x1A
    PANEL_HDR10_CHANGE_A = 0x29
    AUDIO_TUBE_MODE = 0x48
    PANEL_HDR10_CHANGE_B = 0x54
    GAMMA_INDEX = 0x6D
    GAMUT_MODE = 0x6E


# Highest handler-table index seen in the firmware (165 entries, 0x00..0xA4).
# Anything not in COMMANDS above is unmapped: either it's one of the ~90
# slots pointing at the shared "unimplemented" stub (0x080123A7), or it's a
# real handler nobody has traced yet. `rayneo scan` walks this range live.
MAX_COMMAND_ID = 0xA4
