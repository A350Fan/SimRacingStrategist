# app/track_map.py
from __future__ import annotations


# F1 25 trackId -> human name (partial; unknown ids fall back to TrackId:<n>)
# Keep this dict minimal and extend it as you confirm IDs.
TRACK_ID_TO_NAME: dict[int, str] = {
    0: "Melbourne",
    1: "Paul Ricard",
    2: "Shanghai",
    3: "Sakhir",
    4: "Catalunya",
    5: "Monaco",
    6: "Montreal",
    7: "Silverstone",
    8: "Hockenheim",
    9: "Hungaroring",
    10: "Spa-Francorchamps",
    11: "Monza",
    12: "Singapore",
    13: "Suzuka",
    14: "Abu Dhabi",
    15: "Austin, Texas",
    16: "Interlagos",
    17: "Red Bull Ring",
    18: "Sochi",
    19: "Mexico",
    20: "Baku",
    21: "Sakhir Short",
    22: "Silverstone Short",
    23: "Austin, Texas Short",
    24: "Suzuka Short",
    25: "Hanoi",
    26: "Zandvoort",
    27: "Imola",
    28: "Portimão",
    29: "Jeddah",
    30: "Miami",
    31: "Las Vegas",
    32: "Losail"
}

def track_label_from_id(track_id: int | None) -> str:
    """
    Returns a human-friendly track label.
    Falls back to 'TrackId:<n>' or 'Unknown Track' if not available.
    """
    if track_id is None:
        return "Unknown Track"
    try:
        tid = int(track_id)
    except Exception:
        return "Unknown Track"
    if tid < 0:
        return "Unknown Track"
    return TRACK_ID_TO_NAME.get(tid, f"TrackId:{tid}")