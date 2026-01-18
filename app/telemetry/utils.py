from __future__ import annotations

# Codemasters Team IDs (default mapping, passt zu F1 25 in deinem aktuellen Stand)
TEAM_ID_TO_NAME = {
    0: "Mercedes",
    1: "Ferrari",
    2: "Red Bull",
    3: "Williams",
    4: "Aston Martin",
    5: "Alpine",
    6: "RB",
    7: "Haas",
    8: "McLaren",
    9: "Kick Sauber",
    255: "UNK",
}


def team_name_from_id(team_id: int) -> str:
    """
    Central helper for team-id -> name.
    Keeping it here makes later per-game mapping easy (game_profiles integration).
    """
    try:
        return TEAM_ID_TO_NAME.get(int(team_id), f"TEAM{int(team_id)}")
    except Exception:
        return "UNK"
