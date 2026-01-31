# app/logic/rain_engine/tuning.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RainPitTuning:
    """
    Parameter-/Tuning-Container für RainEngine.
    Bewusst "frozen", damit du nicht aus Versehen zur Laufzeit Werte mutierst.
    """

    # =========================================================
    # 1) Wetness fusion / signal weights
    # =========================================================
    w_weather_enum: float = 0.15
    w_rain_now: float = 0.25
    w_temp_trend: float = 0.22
    w_delta_is: float = 0.35
    w_inter_share: float = 0.25
    w_forecast: float = 0.20
    w_baseline_loss: float = 0.20

    # ---------------------------------------------------------
    # rain_now -> wetness mapping
    # ---------------------------------------------------------
    rain_now_map_lo: float = 5.0
    rain_now_map_span: float = 55.0
    rain_now_floor_factor: float = 0.75

    # ---------------------------------------------------------
    # cold-track early switch boost
    # ---------------------------------------------------------
    cold_track_ref_c: float = 22.0
    cold_track_span_c: float = 18.0
    cold_track_boost_max: float = 0.08

    # =========================================================
    # 2) Confidence model
    # =========================================================
    conf_base: float = 0.15
    conf_per_signal: float = 0.20
    conf_samples_factor: float = 0.15

    # =========================================================
    # 3) Condition shift detection (Slick->Inter responsiveness)
    # =========================================================
    cond_rain_now_on: float = 18.0
    cond_track_drop_cpm: float = -0.45
    cond_delta_is_on: float = -0.25
    cond_fc_ramp_3to5: float = 18.0
    shift_boost: float = 0.08

    # =========================================================
    # 4) Slick -> Inter thresholds
    # =========================================================
    slick_hold_warming_cpm: float = +0.35
    slick_hold_max_wetness: float = 0.82
    slick_hard_weather_enum: int = 4
    slick_hard_wetness: float = 0.88
    slick_delta_is_box: float = -0.30

    # =========================================================
    # 5) Wet <-> Inter payback (W-I) thresholds
    # =========================================================
    wi_delta_min: float = 0.05
    wi_payback_min_gain: float = 0.10
    wi_fast_gain: float = 0.45

    # =========================================================
    # 6) Inter -> Slick (dry exit) thresholds
    # =========================================================
    dry_track_warming_cpm: float = +0.25
    dry_track_warming_fast_cpm: float = +0.40
    dry_track_temp_ok_c: float = 24.0
    dry_track_temp_very_ok_c: float = 27.0
    dry_rain_now_low: float = 18.0
    dry_rain_next_low: float = 12.0
    dry_hard_wetness_max: float = 0.20
    dry_hard_conf_min: float = 0.58
    dry_temp_conf_min: float = 0.55
    dry_temp_wetness_max: float = 0.60

    # forecast gates (absolute envelope)
    fc_dry_3: int = 20
    fc_dry_5: int = 25
    fc_dry_10: int = 30

    fc_very_dry_3: int = 8
    fc_very_dry_5: int = 10
    fc_very_dry_10: int = 15

    fc_ultra_3: int = 10
    fc_ultra_5: int = 10
    fc_ultra_10: int = 10
    fc_ultra_15: int = 5
    fc_ultra_20: int = 5

    # =========================================================
    # 7) Guards
    # =========================================================
    avoid_refresh_min_lr: int = 3
    short_rain_try_stay_wetness_max: float = 0.80
