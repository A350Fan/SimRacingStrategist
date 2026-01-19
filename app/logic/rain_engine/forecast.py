# app/logic/rain_engine/forecast.py
from __future__ import annotations

import math
from typing import Optional


def estimate_next_lap_minute(
    *,
    your_last_lap_s: Optional[float],
    default_lap_s: float = 90.0,
    margin_s: float = 10.0,
    min_seconds: float = 30.0,
    min_minute: int = 1,
    max_minute: int = 240,
) -> int:
    """
    Mappe "next lap" von Sekunden -> Forecast-Minuten-Samples (UDP ist minutenbasiert).

    Idee (konservativ):
    - Zielzeitpunkt ist frueh in der naechsten Runde: (lap_time - margin)
    - Sekunden -> Minuten via CEIL => nimmt den ersten Sample >= Zielminute
      (vermeidet, Regen zu unterschaetzen).
    """
    lap_est_s = None
    try:
        lap_est_s = float(your_last_lap_s) if your_last_lap_s is not None else None
        if lap_est_s is not None and lap_est_s <= 0.0:
            lap_est_s = None
    except Exception:
        lap_est_s = None

    if lap_est_s is None:
        lap_est_s = float(default_lap_s)

    next_lap_s = max(float(min_seconds), float(lap_est_s) - float(margin_s))
    next_lap_min_int = int(math.ceil(next_lap_s / 60.0))
    return max(int(min_minute), min(int(max_minute), next_lap_min_int))


def fc_value_at(fc_series, t_min: int) -> Optional[int]:
    """
    Rain% at/after t_min using nearest sample >= t_min (stepwise).
    fc_series Format (wie bei dir): [(minute, rain_pct, weather_enum), ...]
    """
    if not fc_series:
        return None
    for t, r, _w in fc_series:
        if t >= t_min:
            return int(r)
    return int(fc_series[-1][1])


def fc_window_stats(fc_series, mins: list[int]) -> dict[int, Optional[int]]:
    """Convenience: {minute: rain%} fuer mehrere Horizonte."""
    return {m: fc_value_at(fc_series, m) for m in mins}


def fc_time_to_below(fc_series, threshold: int) -> Optional[int]:
    """Erste Minute, in der rain% <= threshold."""
    if not fc_series:
        return None
    for t, r, _w in fc_series:
        if int(r) <= threshold:
            return int(t)
    return None


def fc_time_to_above(fc_series, threshold: int) -> Optional[int]:
    """Erste Minute, in der rain% >= threshold."""
    if not fc_series:
        return None
    for t, r, _w in fc_series:
        if int(r) >= threshold:
            return int(t)
    return None
