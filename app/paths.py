# app/paths.py
from __future__ import annotations

import os
from pathlib import Path


def app_dir() -> Path:
    # Windows: %LOCALAPPDATA%\SimRaceStrategist
    local = os.environ.get("LOCALAPPDATA")
    if local:
        p = Path(local) / "SimRacingStrategist"
    else:
        # fallback (e.g. sandbox)
        p = Path.home() / ".simracingstrategist"
    p.mkdir(parents=True, exist_ok=True)
    return p

def config_path() -> Path:
    return app_dir() / "config.json"

def cache_dir() -> Path:
    p = app_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p

def data_dir() -> Path:
    p = app_dir() / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p

def db_path() -> Path:
    return app_dir() / "data.db"