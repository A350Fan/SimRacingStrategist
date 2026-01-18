from __future__ import annotations

import struct
from typing import Optional, Dict, Any

# ============================================================
# DEBUG: Minimal UDP header sniffer (F1 telemetry)
# ============================================================

HDR_FMT = "<HBBBBQfIBB"  # Codemasters UDP header (24 bytes)
HDR_SIZE = struct.calcsize(HDR_FMT)


def hex_dump(b: bytes, n: int = 32) -> str:
    """Small helper for debug prints: first n bytes as hex."""
    return " ".join(f"{x:02X}" for x in b[:n])


def try_parse_f1_header(data: bytes) -> Optional[Dict[str, Any]]:
    """
    Try to parse a Codemasters F1 UDP header at offset 0.
    Returns dict if plausible, otherwise None.
    """
    if len(data) < HDR_SIZE:
        return None

    try:
        (
            packet_format,
            game_major,
            game_minor,
            packet_version,
            packet_id,
            session_uid,
            session_time,
            frame_id,
            player_idx,
            secondary_idx,
        ) = struct.unpack_from(HDR_FMT, data, 0)
    except Exception:
        return None

    # Plausibility checks (very important!)
    if not (2017 <= packet_format <= 2030):
        return None
    if not (0 <= packet_id <= 20):
        return None

    return {
        "packet_format": packet_format,
        "game_version": f"{game_major}.{game_minor}",
        "packet_version": packet_version,
        "packet_id": packet_id,
        "session_uid": session_uid,
        "session_time": session_time,
        "frame_id": frame_id,
        "player_idx": player_idx,
        "secondary_idx": secondary_idx,
    }


def read_header(data: bytes):
    """
    Supports:
      - F1 25 header (29 bytes): <HBBBBBQfIIBB
      - F1 2017..2024 header (24 bytes): <HBBBBQfIBB

    Returns a dict with at least:
      packetFormat, gameYear (synthetic for legacy), packetId, sessionUID, playerCarIndex, headerSize
    """
    if len(data) < 24:
        return None

    # Peek packetFormat (uint16 LE)
    try:
        (pkt_fmt,) = struct.unpack_from("<H", data, 0)
    except Exception:
        return None

    # --- F1 25 / modern (2025) ---
    # Header 29 bytes (includes gameYear + overallFrameIdentifier)
    if len(data) >= 29 and pkt_fmt >= 2025:
        try:
            u = struct.unpack_from("<HBBBBBQfIIBB", data, 0)
            return {
                "packetFormat": int(u[0]),  # 2025
                "gameYear": int(u[1]),      # 25
                "packetId": int(u[5]),
                "sessionUID": u[6],
                "playerCarIndex": int(u[10]),
                "headerSize": 29,
            }
        except Exception:
            return None

    # --- F1 2017..2024 legacy style (24 bytes header) ---
    # Header 24 bytes (no gameYear, no overallFrameIdentifier)
    if 2017 <= pkt_fmt <= 2024:
        try:
            u = struct.unpack_from("<HBBBBQfIBB", data, 0)
            return {
                "packetFormat": int(u[0]),          # 2017-2024
                "gameYear": int(u[0]) - 2000,       # synthetic: 2022 -> 22
                "packetId": int(u[4]),
                "sessionUID": u[5],
                "playerCarIndex": int(u[8]),
                "headerSize": 24,
            }
        except Exception:
            return None

    return None
