# app/config.py
from __future__ import annotations

import json
from dataclasses import dataclass, asdict

from app.paths import config_path


@dataclass
class AppConfig:
    telemetry_root: str = ""  # e.g. F:\OneDrive\...\SimRacingTelemetrie

    udp_port: int = 20777
    udp_enabled: bool = True

    udp_record_laps: bool = False  # write UDP lap summaries into DB (parallel)
    udp_write_csv_laps: bool = False
    udp_output_root: str = ""  # user-chosen folder for persistent data (NOT cache)

    # Debug spam control
    udp_debug: bool = True

    # --- Offline testing via UDP record/replay ---
    udp_source: str = "LIVE"  # "LIVE" or "REPLAY"
    udp_replay_file: str = ""  # path to *.bin dump
    udp_replay_speed: float = 1.0  # 1.0 realtime
    udp_dump_enabled: bool = False  # if True, write raw UDP packets to a dump file
    udp_dump_file: str = ""  # optional fixed path; if empty -> auto in output_root or cache
    # --------------------------------------------

    # UI language (lang/<code>.json). Example: "en", "de"
    language: str = "en"

    game_profile_key: str = "AUTO"


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        cfg = AppConfig()
        save_config(cfg)
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AppConfig(
            telemetry_root=str(data.get("telemetry_root", "")),

            udp_port=int(data.get("udp_port", 20777)),
            udp_enabled=bool(data.get("udp_enabled", True)),
            udp_record_laps=bool(data.get("udp_record_laps", False)),
            udp_write_csv_laps=bool(data.get("udp_write_csv_laps", False)),
            udp_output_root=str(data.get("udp_output_root", "")),
            udp_debug=bool(data.get("udp_debug", True)),

            # --- record/replay + dump ---
            udp_source=str(data.get("udp_source", "LIVE")),
            udp_replay_file=str(data.get("udp_replay_file", "")),
            udp_replay_speed=float(data.get("udp_replay_speed", 1.0)),
            udp_dump_enabled=bool(data.get("udp_dump_enabled", False)),
            udp_dump_file=str(data.get("udp_dump_file", "")),
            # ---------------------------

            language=str(data.get("language", "en")),
            game_profile_key=str(data.get("game_profile_key", "AUTO")),
        )

    except Exception:
        cfg = AppConfig()
        save_config(cfg)
        return cfg


def save_config(cfg: AppConfig) -> None:
    path = config_path()
    path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
