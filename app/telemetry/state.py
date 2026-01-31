from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class F1LiveState:
    safety_car_status: Optional[int] = None  # 0 none, 1 SC, 2 VSC, 3 formation lap
    weather: Optional[int] = None  # enum (best-effort)

    # NEW: flags
    track_flag: Optional[int] = None  # from marshal zones: -1/0/1/2/3
    player_fia_flag: Optional[int] = None  # from car status:   -1/0/1/2/3

    rain_now_pct: Optional[int] = None  # 0..100 (current rain)
    rain_fc_pct: Optional[int] = None  # 0..100 (forecast next sample)

    # Forecast samples: list of (time_offset_min, rain_pct, weather_enum)
    rain_fc_series: Optional[list[tuple[int, int, int]]] = None

    track_temp_c: Optional[float] = None
    air_temp_c: Optional[float] = None

    # optional: einfache Trends (aus letzten n Samples)
    track_temp_trend_c_per_min: Optional[float] = None
    rain_trend_pct_per_min: Optional[float] = None

    # NOTE: you assign this as str in the listener; keep it consistent with DB (TEXT).
    session_uid: Optional[str] = None

    # Player meta (you already set these in _update_field_metrics_and_emit)
    player_car_index: Optional[int] = None
    player_tyre_cat: Optional[str] = None  # "SLICK" / "INTER" / "WET"

    # exact compound label for slicks ("C1"-"C6"), otherwise same as category ("INTER"/"WET").
    # Intended for lap DB + later strategy logic. Keep player_tyre_cat as the coarse class.
    player_tyre_compound: Optional[str] = None

    # for HUD: raw tyre IDs from CarStatus (so we can show S/M/H/I/W correctly)
    # visual: 16=Soft, 17=Medium, 18=Hard, 7=Inter, 8=Wet (Codemasters mapping)
    player_tyre_visual: Optional[int] = None
    player_tyre_actual: Optional[int] = None

    player_team_id: Optional[int] = None  # from Participants packet (pid=4)
    player_team_name: Optional[str] = None

    # NOTE: inter_share bleibt aus Kompatibilitätsgründen = Anteil (INTER+WET) von (SLICK+INTER+WET)
    inter_share: Optional[float] = None
    # Neue, getrennte Werte (Inter vs Wet)
    inter_only_share: Optional[float] = None
    wet_share: Optional[float] = None

    pace_delta_inter_vs_slick_s: Optional[float] = None
    # Neue, getrennte Pace-Deltas (Field)
    pace_delta_wet_vs_inter_s: Optional[float] = None
    pace_delta_wet_vs_slick_s: Optional[float] = None

    # NOTE: inter_count bleibt aus Kompatibilitätsgründen = Anzahl (INTER+WET)
    inter_count: Optional[int] = None
    inter_only_count: Optional[int] = None
    wet_count: Optional[int] = None
    slick_count: Optional[int] = None

    # --- Your (player) learned reference deltas ---
    your_delta_inter_vs_slick_s: Optional[float] = None
    your_delta_wet_vs_slick_s: Optional[float] = None
    your_delta_wet_vs_inter_s: Optional[float] = None
    your_ref_counts: Optional[str] = None  # z.B. "S:3 I:2 W:0"

    # --- Game/profile meta (from UDP header) ---
    packet_format: Optional[int] = None
    game_year: Optional[int] = None

    # --- Session meta (from Session packet) ---
    track_id: Optional[int] = None
    session_type_id: Optional[int] = None

    # --- Track geometry (from Session packet) ---
    track_length_m: Optional[int] = None
    sector2_start_m: Optional[float] = None
    sector3_start_m: Optional[float] = None

    # --- Player lap telemetry (from LapData packet) ---
    player_current_lap_time_ms: Optional[int] = None
    player_last_lap_time_ms: Optional[int] = None
    player_lap_distance_m: Optional[float] = None
    player_sector1_time_ms: Optional[int] = None
    player_sector2_time_ms: Optional[int] = None
    player_pit_status: Optional[int] = None
    player_current_lap_num: Optional[int] = None

    # --- NEW (additive): fuel + tyre wear for lap database ---
    # fuel: typically kg in Codemasters UDP
    player_fuel_in_tank: Optional[float] = None
    player_fuel_capacity: Optional[float] = None
    player_fuel_remaining_laps: Optional[float] = None

    # tyre wear stored as "worn %" (0 = new, 100 = fully worn/dead)
    player_wear_fl: Optional[float] = None
    player_wear_fr: Optional[float] = None
    player_wear_rl: Optional[float] = None
    player_wear_rr: Optional[float] = None

    # --- Field meta ---
    field_total_cars: Optional[int] = None
    unknown_tyre_count: Optional[int] = None
