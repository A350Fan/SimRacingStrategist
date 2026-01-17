# app/overtake_csv.py
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Dict
import pandas as pd


def _parse_two_line_block(header_line: str, data_line: str) -> Dict[str, str]:
    header = next(csv.reader([header_line]))
    data = next(csv.reader([data_line]))
    if len(data) < len(header):
        data += [""] * (len(header) - len(data))
    return {h.strip(): d.strip() for h, d in zip(header, data)}

class OvertakeCSVError(ValueError):
    """
    Raised when an Overtake/Iko telemetry CSV cannot be parsed.
    Includes file + block + reason for much better UI/log messages.
    """
    def __init__(self, path: Path, block: str, reason: str):
        self.path = Path(path)
        self.block = str(block)
        self.reason = str(reason)
        super().__init__(f"{self.path.name} | block={self.block} | {self.reason}")


def _safe_csv_row(line: str) -> list[str]:
    try:
        return next(csv.reader([line]))
    except Exception:
        return []


def _safe_parse_two_line_block(lines: list[str], header_i: int, data_i: int, *, path: Path, block: str) -> Dict[str, str]:
    """
    Try parsing a (header,data) block.
    If indices are missing -> return {} (block missing is tolerated).
    If present but malformed -> raise OvertakeCSVError with details.
    """
    if header_i >= len(lines) or data_i >= len(lines):
        return {}

    header_line = lines[header_i].strip()
    data_line = lines[data_i].strip()

    # Empty lines -> treat as missing block
    if not header_line and not data_line:
        return {}

    # If one of those looks like telemetry header, don't treat as block
    if header_line.startswith("LapDistance") or data_line.startswith("LapDistance"):
        return {}

    try:
        return _parse_two_line_block(header_line, data_line)
    except Exception as e:
        raise OvertakeCSVError(path, block, f"Malformed 2-line block at lines {header_i+1}/{data_i+1}: {type(e).__name__}: {e}")


def _find_telemetry_start(lines: list[str]) -> int | None:
    """
    Find the telemetry header start line.
    Robust against leading spaces/BOM and minor formatting.
    """
    for i, raw in enumerate(lines):
        l = (raw or "").lstrip("\ufeff").strip()
        # strict: standard header begins with LapDistance
        if l.startswith("LapDistance"):
            return i
        # fallback: sometimes there's whitespace before it
        if "LapDistance" in l and l.split(",")[0].strip() == "LapDistance":
            return i
    return None


def parse_overtake_csv(path: Path) -> Dict[str, Any]:
    """
    Parse the Overtake Telemetry Tool CSV format (multi-block).
    Returns dict with: player, game, track, setup, telemetry (DataFrame)

    Robustness goals:
    - tolerate missing/empty meta blocks
    - give better errors: file + block + reason
    - handle minor header formatting differences
    """
    try:
        text = path.read_text(errors="replace")
    except Exception as e:
        raise OvertakeCSVError(path, "file", f"Cannot read file: {type(e).__name__}: {e}")

    lines = text.splitlines()
    if len(lines) < 3:
        raise OvertakeCSVError(path, "file", f"CSV too short (lines={len(lines)})")

    # --- Telemetry start index (required) ---
    start_idx = _find_telemetry_start(lines)
    if start_idx is None:
        raise OvertakeCSVError(path, "telemetry", "Telemetry header 'LapDistance' not found")

    # --- Player line (best-effort, optional) ---
    player_row = _safe_csv_row(lines[0]) if lines else []
    player = {
        "raw": player_row,
        "tag": player_row[0] if len(player_row) > 0 else "",
        "tool_version": player_row[1] if len(player_row) > 1 else "",
        "driver": player_row[2] if len(player_row) > 2 else "",
        "timestamp": player_row[4] if len(player_row) > 4 else "",
    }

    # --- Meta blocks: best-effort (do NOT hard fail if missing) ---
    # Typical layout: [0]=player, (1,2)=game, (3,4)=track, (5,6)=setup
    # If those lines overlap with telemetry start or are missing, block becomes {}.
    game = _safe_parse_two_line_block(lines, 1, 2, path=path, block="game")
    track = _safe_parse_two_line_block(lines, 3, 4, path=path, block="track")
    setup = _safe_parse_two_line_block(lines, 5, 6, path=path, block="setup")

    # --- Telemetry dataframe (required, but empty is allowed) ---
    telemetry_text = "\n".join(lines[start_idx:])
    try:
        df = pd.read_csv(io.StringIO(telemetry_text))
    except Exception as e:
        raise OvertakeCSVError(path, "telemetry", f"pandas.read_csv failed: {type(e).__name__}: {e}")

    # Sanity: must contain LapDistance column (or a known alias)
    if isinstance(df, pd.DataFrame):
        if "LapDistance" not in df.columns:
            # common aliases (just in case)
            for alias in ("Lap Distance", "LapDistance [m]", "LapDistance[m]"):
                if alias in df.columns:
                    df = df.rename(columns={alias: "LapDistance"})
                    break

    return {"player": player, "game": game, "track": track, "setup": setup, "telemetry": df}

def lap_summary(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Create a compact summary row for the database."""
    game = parsed.get("game") or {}
    track = parsed.get("track") or {}
    setup = parsed.get("setup") or {}
    df: pd.DataFrame = parsed.get("telemetry")

    # Lap time (best-effort)
    lap_time_s = None
    for k in ("LapTime [s]", "LapTime[s]", "laptime [s]", "laptime[s]", "LapTime"):
        if k in game:
            try:
                lap_time_s = float(game[k])
            except Exception:
                lap_time_s = None
            break

    tyre = track.get("Tyre [txt]", track.get("Tyre", ""))
    weather = track.get("Weather [txt]", track.get("Weather", ""))
    track_name = game.get("Track", game.get("track", "")) or track.get("Track", "")
    game_name = game.get("Game", game.get("game", ""))

    # fuel = None
    # for k in ("FuelLoad", "FuelLoad [kg]", "Fuel Load", "FuelLoad[kg]"):
    #     if k in setup:
    #         try:
    #             fuel = float(setup[k])
    #         except Exception:
    #             fuel = None
    #         break

    # Fuel: prefer "remaining fuel" from telemetry (changes each lap), fallback to setup fuel load
    fuel = None

    # 1) Try telemetry columns (end-of-lap value)
    if isinstance(df, pd.DataFrame) and not df.empty:
        for col in (
            "FuelInTank [kg]",
            "Fuel in tank [kg]",
            "FuelInTank",
            "FuelRemaining [kg]",
            "Fuel Remaining [kg]",
            "FuelRemaining",
            "Fuel [kg]",
            "Fuel",
        ):
            if col in df.columns:
                try:
                    fuel = float(df[col].iloc[-1])
                except Exception:
                    fuel = None
                break

    # 2) Fallback: setup fuel (static)
    if fuel is None:
        for k in ("FuelLoad", "FuelLoad [kg]", "Fuel Load", "FuelLoad[kg]"):
            if k in setup:
                try:
                    fuel = float(setup[k])
                except Exception:
                    fuel = None
                break

    # Wear: try a couple of common column spellings/variants
    wear_map = [
        (("TyreWearFrontLeft [%]", "Tyre Wear Front Left [%]", "TyreWearFL [%]", "TyreWearFL"), "wear_fl"),
        (("TyreWearFrontRight [%]", "Tyre Wear Front Right [%]", "TyreWearFR [%]", "TyreWearFR"), "wear_fr"),
        (("TyreWearRearLeft [%]", "Tyre Wear Rear Left [%]", "TyreWearRL [%]", "TyreWearRL"), "wear_rl"),
        (("TyreWearRearRight [%]", "Tyre Wear Rear Right [%]", "TyreWearRR [%]", "TyreWearRR"), "wear_rr"),
    ]
    wear: Dict[str, Any] = {k: None for _, k in wear_map}

    if isinstance(df, pd.DataFrame) and not df.empty:
        for cols, out in wear_map:
            chosen = None
            for c in cols:
                if c in df.columns:
                    chosen = c
                    break
            if chosen is None:
                continue
            try:
                wear[out] = float(df[chosen].iloc[-1])
            except Exception:
                wear[out] = None

    # ✅ IMPORTANT: Always return a dict (never None)
    return {
        "game": game_name,
        "track": track_name,
        "weather": weather,
        "tyre": tyre,
        "lap_time_s": lap_time_s,
        "fuel_load": fuel,
        **wear,
    }