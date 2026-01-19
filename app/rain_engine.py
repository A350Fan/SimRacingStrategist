# app/rain_engine.py
"""
Compat/Re-Export:
Historisch lag die komplette RainEngine hier.
Phase B: echte Module unter app/logic/rain_engine/*

Wichtig: Externe Imports bleiben stabil:
    from app.rain_engine import RainEngine, RainEngineOutput, RainPitTuning
"""

from __future__ import annotations

from app.logic.rain_engine.core import RainEngine, RainEngineOutput
from app.logic.rain_engine.tuning import RainPitTuning

__all__ = ["RainEngine", "RainEngineOutput", "RainPitTuning"]
