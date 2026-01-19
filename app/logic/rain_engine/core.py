# app/logic/rain_engine/core.py
from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple, List

from app.f1_udp import F1LiveState
from app.strategy_model import RainPitAdvice

from app.logic.rain_engine.tuning import RainPitTuning
from app.logic.rain_engine.forecast import (
    estimate_next_lap_minute,
    fc_window_stats,
    fc_time_to_above,
    fc_time_to_below,
)


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _median(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    try:
        return statistics.median(xs)
    except Exception:
        return None


@dataclass
class RainEngineOutput:
    advice: RainPitAdvice
    wetness: float  # 0..1
    confidence: float  # 0..1
    debug: str


class RainEngine:
    """
    Zustandsbehaftete Entscheidungslogik:
    - fused wetness score aus:
      inter_share, delta(I-S), rain_next_pct, optional baseline_loss
    - Hysterese + "hold"-Timer gegen Flackern
    """

    def __init__(
        self,
        window_s: float = 20.0,  # rolling window length
        min_samples: int = 4,  # min samples before trusting much
        on_th: float = 0.65,  # switch-to-inter threshold
        off_th: float = 0.35,  # switch-back threshold
        hold_on_updates: int = 2,  # require N consecutive updates for ON
        hold_off_updates: int = 3,  # require N consecutive updates for OFF
        # full-wet mode thresholds (Inter -> Wet)
        wet_on_th: float = 0.78,
        wet_off_th: float = 0.55,
        wet_hold_on_updates: int = 2,
        wet_hold_off_updates: int = 3,
        lockout_laps_wet_to_inter: float = 2.0,  # prevent flip-flop after Wet -> Inter
        lockout_laps_inter_to_wet: float = 1.0,  # prevent flip-flop after Inter -> Wet
    ):
        self.window_s = float(window_s)
        self.min_samples = int(min_samples)
        self.on_th = float(on_th)
        self.off_th = float(off_th)
        self.hold_on_updates = int(hold_on_updates)
        self.hold_off_updates = int(hold_off_updates)
        self.wet_on_th = float(wet_on_th)
        self.wet_off_th = float(wet_off_th)
        self.wet_hold_on_updates = int(wet_hold_on_updates)
        self.wet_hold_off_updates = int(wet_hold_off_updates)
        self.lockout_laps_wet_to_inter = float(lockout_laps_wet_to_inter)
        self.lockout_laps_inter_to_wet = float(lockout_laps_inter_to_wet)

        # switch lockout (only for Wet <-> Inter to avoid flip-flop)
        self._wi_lockout_until: float = 0.0
        self._wi_last_target: Optional[str] = None

        # rolling samples: (t, value)
        self._inter_share: Deque[Tuple[float, float]] = deque()
        self._wet_share: Deque[Tuple[float, float]] = deque()

        self._delta_is: Deque[Tuple[float, float]] = deque()
        self._delta_wi: Deque[Tuple[float, float]] = deque()

        self._rain_now: Deque[Tuple[float, float]] = deque()
        self._rain_next: Deque[Tuple[float, float]] = deque()

        self._track_temp: Deque[Tuple[float, float]] = deque()
        self._air_temp: Deque[Tuple[float, float]] = deque()
        self._weather: Deque[Tuple[float, float]] = deque()

        # hysteresis state
        self._is_wet_mode = False
        self._on_counter = 0
        self._off_counter = 0
        self._is_fullwet_mode = False
        self._wet_on_counter = 0
        self._wet_off_counter = 0

        # cache baseline pace (track, tyre) -> (t, median_pace)
        self._baseline_cache: dict[Tuple[str, str], Tuple[float, float]] = {}

        # tuning
        self.p = RainPitTuning()

    def _push(self, dq: Deque[Tuple[float, float]], t: float, v: Optional[float]):
        if v is None:
            return
        dq.append((t, float(v)))
        # prune old
        cutoff = t - self.window_s
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def _slope_c_per_min(self, dq: Deque[Tuple[float, float]], window_s: float = 90.0) -> Optional[float]:
        """Return slope in °C/min over last window_s seconds (last - first)."""
        if not dq or len(dq) < 2:
            return None
        t_last, v_last = dq[-1]
        # find first sample within window
        t0, v0 = None, None
        for t, v in reversed(dq):
            if (t_last - t) >= window_s:
                t0, v0 = t, v
                break
        if t0 is None:
            t0, v0 = dq[0]
        dt = float(t_last - t0)
        if dt <= 1e-6:
            return None
        return (float(v_last) - float(v0)) / dt * 60.0

    def update(
        self,
        state: F1LiveState,
        *,
        track: str,
        current_tyre: str,
        laps_remaining: int,
        pit_loss_s: float,
        # DB rows from laps_for_track(track)
        db_rows: Optional[list] = None,
        your_last_lap_s: Optional[float] = None,
    ) -> RainEngineOutput:

        fc_series = getattr(state, "rain_fc_series", None) or []
        now = time.time()
        p = self.p

        # --- Rolling inputs (state -> windows) ---
        self._push(self._inter_share, now, getattr(state, "inter_share", None))
        self._push(self._delta_is, now, getattr(state, "pace_delta_inter_vs_slick_s", None))

        # rain: NOW + forecast
        self._push(self._rain_now, now, getattr(state, "rain_now_pct", None))
        self._push(self._rain_next, now, getattr(state, "rain_fc_pct", None))

        # temps
        self._push(self._track_temp, now, getattr(state, "track_temp_c", None))
        self._push(self._air_temp, now, getattr(state, "air_temp_c", None))

        # weather enum (0..5) as weak hint only
        self._push(self._weather, now, getattr(state, "weather", None))

        self._push(self._wet_share, now, getattr(state, "wet_share", None))
        self._push(self._delta_wi, now, getattr(state, "pace_delta_wet_vs_inter_s", None))

        # --- Medians (robust) ---
        inter_share_med = _median([v for _, v in self._inter_share])
        delta_is_med = _median([v for _, v in self._delta_is])  # I - S (sec); negative = inter faster
        rain_next_med = _median([v for _, v in self._rain_next])  # 0..100
        rain_now_med = _median([v for _, v in self._rain_now])  # 0..100
        air_temp_med = _median([v for _, v in self._air_temp])  # °C
        track_temp_med = _median([v for _, v in self._track_temp])  # °C
        weather_med = _median([v for _, v in self._weather])
        wet_share_med = _median([v for _, v in self._wet_share])
        delta_wi_field_med = _median([v for _, v in self._delta_wi])  # W - I (sec); negative = wet faster

        # Also learn W-I from YOUR reference deltas (more stable than field in some sessions)
        your_delta_wi = getattr(state, "your_delta_wet_vs_inter_s", None)

        # Combine: prefer field if present, otherwise your; if both, blend lightly
        delta_wi_med = None
        if delta_wi_field_med is not None and your_delta_wi is not None:
            try:
                delta_wi_med = 0.6 * float(delta_wi_field_med) + 0.4 * float(your_delta_wi)
            except Exception:
                delta_wi_med = float(delta_wi_field_med)
        elif delta_wi_field_med is not None:
            delta_wi_med = float(delta_wi_field_med)
        elif your_delta_wi is not None:
            delta_wi_med = float(your_delta_wi)

        track_slope_cpm = self._slope_c_per_min(self._track_temp, window_s=90.0)  # °C/min
        air_slope_cpm = self._slope_c_per_min(self._air_temp, window_s=120.0)  # °C/min

        # --- Forecast-derived features (ausgelagert) ---
        next_lap_min_int = estimate_next_lap_minute(your_last_lap_s=your_last_lap_s)

        # Classic horizons + NextLap
        mins = sorted(set([next_lap_min_int, 3, 5, 10, 15, 20]))
        fc_at = fc_window_stats(fc_series, mins)

        rain_nl = fc_at.get(next_lap_min_int)
        rain_3 = fc_at.get(3)
        rain_5 = fc_at.get(5)
        rain_10 = fc_at.get(10)
        rain_15 = fc_at.get(15)
        rain_20 = fc_at.get(20)

        # "Soon" = next-lap horizon if available, else fall back to 3min
        rain_soon = rain_nl if rain_nl is not None else rain_3

        # drying/heavy flags
        t_dry = fc_time_to_below(fc_series, threshold=25)
        drying_soon = (t_dry is not None and t_dry <= 15)

        t_heavy = fc_time_to_above(fc_series, threshold=60)
        heavy_incoming = (t_heavy is not None and t_heavy <= 10)

        # --- Baseline: expected slick pace (minimal) ---
        expected_pace = None
        if db_rows is not None and track and current_tyre:
            expected_pace = self._expected_pace_from_rows(track, current_tyre, db_rows)

        baseline_loss = None
        if expected_pace is not None and your_last_lap_s is not None:
            baseline_loss = float(your_last_lap_s) - float(expected_pace)

        # --- Scoring (wie vorher, nur hier zentral) ---
        s0 = None
        if weather_med is not None:
            w = int(weather_med)
            # 0 clear, 1 light cloud, 2 overcast, 3 light rain, 4 heavy rain, 5 storm
            if w <= 2:
                s0 = 0.0
            elif w == 3:
                s0 = 0.25
            elif w == 4:
                s0 = 0.55
            else:
                s0 = 0.75

        s_now = None
        if rain_now_med is not None:
            s_now = _clamp01((float(rain_now_med) - p.rain_now_map_lo) / p.rain_now_map_span)

        s_temp = None
        if track_slope_cpm is not None:
            wet_from_track = _clamp01(((-track_slope_cpm) - 0.20) / 0.80)
            dry_from_track = _clamp01((track_slope_cpm - 0.20) / 0.80)
            s_temp = _clamp01(wet_from_track - 0.60 * dry_from_track + 0.50)

        if s_temp is None and air_slope_cpm is not None:
            wet_from_air = _clamp01(((-air_slope_cpm) - 0.10) / 0.60)
            dry_from_air = _clamp01((air_slope_cpm - 0.10) / 0.60)
            s_temp = _clamp01(wet_from_air - 0.50 * dry_from_air + 0.50)

        s1 = None
        if inter_share_med is not None:
            s1 = _clamp01((inter_share_med - 0.15) / 0.35)

        s2 = None
        if delta_is_med is not None:
            s2 = _clamp01(((-delta_is_med) - 0.5) / 2.0)

        s3 = None
        if rain_next_med is not None:
            s3 = _clamp01((rain_next_med - 35.0) / 35.0)

        s4 = None
        if baseline_loss is not None:
            s4 = _clamp01((baseline_loss - 0.7) / 2.0)

        temp_boost = 0.0
        if track_temp_med is not None:
            temp_boost = _clamp01((p.cold_track_ref_c - track_temp_med) / p.cold_track_span_c) * p.cold_track_boost_max

        parts: list[float] = []
        weights: list[float] = []

        def add(sig: Optional[float], w: float):
            if sig is None:
                return
            parts.append(float(sig))
            weights.append(float(w))

        add(s0, p.w_weather_enum)
        add(s_now, p.w_rain_now)
        add(s_temp, p.w_temp_trend)
        add(s2, p.w_delta_is)
        add(s1, p.w_inter_share)
        add(s3, p.w_forecast)
        add(s4, p.w_baseline_loss)

        if parts and weights:
            wsum = sum(weights)
            wetness = sum(v * w for v, w in zip(parts, weights)) / max(1e-9, wsum)
        else:
            wetness = 0.0

        wetness = _clamp01(wetness + temp_boost)

        # hard floor by "actual rain now" (HUD/telemetry)
        if s_now is not None:
            wetness = max(wetness, float(s_now) * p.rain_now_floor_factor)

        # --- Separate "full wet" score (Inter -> Wet) ---
        fw0 = None
        if weather_med is not None:
            w = int(weather_med)
            if w <= 3:
                fw0 = 0.0
            elif w == 4:
                fw0 = 0.75
            else:
                fw0 = 0.95

        fw1 = None
        if wet_share_med is not None:
            fw1 = _clamp01((wet_share_med - 0.05) / 0.25)

        fw2 = None
        if delta_wi_med is not None:
            fw2 = _clamp01(((-delta_wi_med) - 0.20) / 1.30)

        fw3 = None
        if rain_next_med is not None:
            fw3 = _clamp01((rain_next_med - 60.0) / 30.0)

        fw_parts, fw_weights = [], []

        def fw_add(sig: Optional[float], w: float):
            if sig is None:
                return
            fw_parts.append(float(sig))
            fw_weights.append(float(w))

        fw_add(fw0, 0.35)
        fw_add(fw2, 0.35)
        fw_add(fw1, 0.25)
        fw_add(fw3, 0.20)

        if fw_parts and fw_weights:
            fw_wsum = sum(fw_weights)
            fullwet = sum(v * w for v, w in zip(fw_parts, fw_weights)) / max(1e-9, fw_wsum)
        else:
            fullwet = 0.0

        if fw0 is not None:
            fullwet = max(fullwet, float(fw0) * 0.85)
        fullwet = _clamp01(fullwet)

        if heavy_incoming:
            fullwet = min(1.0, fullwet + 0.10)

        wet_score = fullwet

        # Confidence: more signals + enough samples -> higher confidence
        n_signals = sum(x is not None for x in (s1, s2, s3, s4))
        n_samples = len(self._rain_next) + len(self._delta_is) + len(self._inter_share)
        conf = _clamp01(0.15 + 0.20 * n_signals + 0.15 * _clamp01(n_samples / (self.min_samples * 3)))

        # SC/VSC: allow earlier pit call
        sc = getattr(state, "safety_car_status", None)
        under_sc = sc in (1, 2)
        if under_sc:
            wetness = _clamp01(wetness + 0.06)
            conf = _clamp01(conf + 0.05)

        # --- Conditions Shift detector ---
        cond_shift = False
        cond_reason = []

        if rain_now_med is not None and rain_now_med >= p.cond_rain_now_on:
            cond_shift = True
            cond_reason.append(f"rain_now>={p.cond_rain_now_on:g}")

        if track_slope_cpm is not None and track_slope_cpm <= p.cond_track_drop_cpm:
            cond_shift = True
            cond_reason.append("trackTemp_drop")

        if delta_is_med is not None and delta_is_med <= p.cond_delta_is_on:
            cond_shift = True
            cond_reason.append(f"ΔIS<={p.cond_delta_is_on:g}")

        if rain_3 is not None and rain_5 is not None and (rain_5 - rain_3) >= p.cond_fc_ramp_3to5:
            cond_shift = True
            cond_reason.append("fc_ramp_3to5")

        cond_shift_txt = "COND_SHIFT" if cond_shift else "stable"
        cond_reason_txt = ",".join(cond_reason) if cond_reason else "-"

        # shift boost
        shift_boost = 0.0
        if cond_shift:
            shift_boost = p.shift_boost
            wetness = _clamp01(wetness + shift_boost)
            conf = _clamp01(conf + 0.05)

        # --- Hysteresis: inter mode ---
        on_needed = self.hold_on_updates
        if cond_shift:
            on_needed = max(1, self.hold_on_updates - 1)

        if wetness >= self.on_th:
            self._on_counter += 1
            self._off_counter = 0
        elif wetness <= self.off_th:
            self._off_counter += 1
            self._on_counter = 0
        else:
            self._on_counter = max(0, self._on_counter - 1)
            self._off_counter = max(0, self._off_counter - 1)

        if (not self._is_wet_mode) and self._on_counter >= on_needed:
            self._is_wet_mode = True
        if self._is_wet_mode and self._off_counter >= self.hold_off_updates:
            self._is_wet_mode = False

        # --- Hysteresis: full wet mode ---
        if self._is_wet_mode:
            if fullwet >= self.wet_on_th:
                self._wet_on_counter += 1
                self._wet_off_counter = 0
            elif fullwet <= self.wet_off_th:
                self._wet_off_counter += 1
                self._wet_on_counter = 0
            else:
                self._wet_on_counter = max(0, self._wet_on_counter - 1)
                self._wet_off_counter = max(0, self._wet_off_counter - 1)

            if (not self._is_fullwet_mode) and self._wet_on_counter >= self.wet_hold_on_updates:
                self._is_fullwet_mode = True
            if self._is_fullwet_mode and self._wet_off_counter >= self.wet_hold_off_updates:
                self._is_fullwet_mode = False
        else:
            self._is_fullwet_mode = False
            self._wet_on_counter = 0
            self._wet_off_counter = 0

        # --- Advice (unveraendert inhaltlich, nur lokal) ---
        tyre = (current_tyre or "").strip().upper()
        lr = max(0, int(laps_remaining))

        def stay(reason: str) -> RainPitAdvice:
            return RainPitAdvice("STAY OUT", None, None, reason)

        def box_in(n: int, target: str, reason: str) -> RainPitAdvice:
            n = max(1, int(n))
            return RainPitAdvice(f"BOX IN {n}", target, n, reason)

        if lr <= 1:
            advice = stay("≤1 lap remaining.")
        else:
            is_slick = tyre.startswith("C") or tyre in ("SLICK", "DRY")
            is_inter = ("INTER" in tyre) or (tyre == "INTERMEDIATE") or (tyre == "INTER")
            is_wet = ("WET" in tyre)

            if is_slick:
                track_slope_cpm2 = self._slope_c_per_min(self._track_temp, window_s=90.0)
                track_falling_fast = (track_slope_cpm2 is not None and track_slope_cpm2 <= p.cond_track_drop_cpm)
                track_rising_fast = (track_slope_cpm2 is not None and track_slope_cpm2 >= p.slick_hold_warming_cpm)

                w_enum = int(weather_med) if weather_med is not None else None

                cond_shift2 = False
                cond_reason2 = []

                if w_enum is not None and w_enum >= 3:
                    cond_shift2 = True
                    cond_reason2.append("w>=3")
                if track_falling_fast:
                    cond_shift2 = True
                    cond_reason2.append("track_drop")
                if delta_is_med is not None and delta_is_med <= -0.25:
                    cond_shift2 = True
                    cond_reason2.append("ΔIS<=-0.25")

                cond_reason2_txt = ",".join(cond_reason2) if cond_reason2 else "-"

                if not self._is_wet_mode:
                    if cond_shift2:
                        advice = stay(f"Conditions shifting ({cond_reason2_txt}) → Inter likely soon.")
                    else:
                        advice = stay("On Slick: wetness not high enough for Inter yet.")
                else:
                    if (
                        track_rising_fast
                        and wetness < p.slick_hold_max_wetness
                        and (not under_sc)
                        and laps_remaining > 3
                        and (delta_is_med is None or delta_is_med > -0.8)
                    ):
                        advice = stay("Track warming again → try to stay out on slick.")
                    else:
                        hard_weather = (w_enum is not None and w_enum >= p.slick_hard_weather_enum)
                        hard_wetness = (wetness >= p.slick_hard_wetness)

                        if hard_weather or hard_wetness or cond_shift2:
                            advice = box_in(1, "Intermediate", f"Slicks unsafe: COND_SHIFT({cond_reason2_txt})")
                        else:
                            if delta_is_med is not None and delta_is_med < p.slick_delta_is_box:
                                advice = box_in(1, "Intermediate", "Δpace(I-S): Inter faster.")
                            else:
                                n = 1 if wetness > 0.80 else 2
                                if under_sc:
                                    n = 1
                                advice = box_in(n, "Intermediate", "Wetness trend suggests Inter.")
            else:
                if is_inter and self._is_fullwet_mode:
                    if delta_wi_med is not None and float(delta_wi_med) < -p.wi_delta_min:
                        wet_gain_per_lap = max(0.0, -float(delta_wi_med))
                        buffer_laps = 0 if under_sc else 1
                        laps_to_payback = int((pit_loss_s / max(p.wi_payback_min_gain, wet_gain_per_lap)) + 0.999)
                        if laps_remaining >= (laps_to_payback + buffer_laps + 1):
                            n = 1 if under_sc or wet_gain_per_lap >= p.wi_fast_gain else 2
                            advice = box_in(n, "Wet",
                                            f"Wet faster by ~{wet_gain_per_lap:.2f}s/lap → payback ~{laps_to_payback} lap(s).")
                        else:
                            advice = stay("Wet faster, but not enough laps left to pay back a stop.")
                    else:
                        n = 1 if wet_score > 0.88 else 2
                        if under_sc:
                            n = 1
                        advice = box_in(n, "Wet", "Rain intensity suggests switching to Full Wet.")
                elif is_wet and (not self._is_fullwet_mode) and self._is_wet_mode:
                    if delta_wi_med is not None and float(delta_wi_med) > p.wi_delta_min:
                        inter_gain_per_lap = max(0.0, float(delta_wi_med))
                        buffer_laps = 0 if under_sc else 1
                        laps_to_payback = int((pit_loss_s / max(p.wi_payback_min_gain, inter_gain_per_lap)) + 0.999)
                        if laps_remaining >= (laps_to_payback + buffer_laps + 1):
                            n = 1 if under_sc or inter_gain_per_lap >= p.wi_fast_gain else 2
                            advice = box_in(n, "Intermediate",
                                            f"Inter faster by ~{inter_gain_per_lap:.2f}s/lap → payback ~{laps_to_payback} lap(s).")
                        else:
                            advice = stay("Inter faster, but not enough laps left to pay back a stop.")
                    else:
                        drying_now = (wet_score <= 0.72 and conf >= p.dry_temp_conf_min)
                        forecast_dry = False
                        if rain_3 is not None and rain_5 is not None:
                            forecast_dry = (rain_3 < p.fc_dry_3 and rain_5 < p.fc_dry_5)
                        elif rain_next_med is not None:
                            forecast_dry = (rain_next_med < float(p.fc_dry_5))
                        if drying_now and (forecast_dry or under_sc):
                            n = 1 if under_sc else 2
                            advice = box_in(n, "Intermediate", "Drying trend + forecast: switch Wet → Inter.")
                        else:
                            advice = stay("On Wet: signals not strong enough to go back to Inter yet.")
                else:
                    hard_dry_exit = (
                        wetness <= 0.20
                        and conf >= 0.58
                        and rain_next_med is not None and rain_next_med <= 5.0
                        and weather_med is not None and int(weather_med) <= 2
                    )
                    if (not hard_dry_exit) and (rain_3 is not None and rain_5 is not None and rain_10 is not None):
                        if wetness <= 0.21 and conf >= 0.60 and rain_3 <= 5 and rain_5 <= 5 and rain_10 <= 10 and int(weather_med or 9) <= 2:
                            hard_dry_exit = True

                    if hard_dry_exit:
                        advice = box_in(1, "C4", "Track dry: Inter no longer justified.")
                    else:
                        w_enum = int(weather_med) if weather_med is not None else None
                        track_warming = (track_slope_cpm is not None and track_slope_cpm >= p.dry_track_warming_cpm)
                        track_warming_fast = (track_slope_cpm is not None and track_slope_cpm >= p.dry_track_warming_fast_cpm)

                        fc_dry = False
                        if rain_3 is not None and rain_5 is not None:
                            fc_dry = (rain_3 < 20 and rain_5 < 25)
                        elif rain_next_med is not None:
                            fc_dry = (rain_next_med < 25.0)

                        hard_dry = (
                            (wetness <= 0.25)
                            and (rain_now_med is not None and rain_now_med <= 15.0)
                            and (fc_dry or drying_soon)
                        )

                        low_inter_share = (inter_share_med is not None and inter_share_med < 0.20)

                        if (hard_dry or (not self._is_wet_mode)) and not (w_enum is not None and w_enum >= 3):
                            if fc_dry and (track_warming or drying_soon) and wetness < 0.60 and low_inter_share:
                                n = 1
                                advice = box_in(n, "C4", "Drying confirmed: forecast low + track warming + low I/W share.")
                            elif fc_dry and wetness < 0.72 and (track_warming_fast or low_inter_share):
                                n = 1 if under_sc else 2
                                advice = box_in(n, "C4", "Drying trend: slick soon (moderate confidence).")
                            else:
                                advice = stay("Drying not confirmed enough for slick yet.")
                        else:
                            if is_wet:
                                advice = stay("Stay on Wet: wet-mode still active.")
                            else:
                                advice = stay("Stay on Inter: wet-mode still active.")

                if drying_soon and (not under_sc) and lr > 3 and (advice.target_tyre in (None, "Intermediate", "Wet")):
                    advice = stay("Forecast: drying soon → avoid unnecessary tyre refresh.")

                if is_slick and self._is_wet_mode and drying_soon and (wetness < 0.80) and (not under_sc):
                    advice = stay("Forecast: rain phase short → try to stay out on slick.")

        # --- Wet <-> Inter lockout (anti flip-flop) ---
        if advice.action.startswith("BOX") and advice.target_tyre:
            tgt = advice.target_tyre.strip().upper()

            cur = tyre
            if "INTER" in cur:
                cur = "INTERMEDIATE"
            elif "WET" in cur:
                cur = "WET"

            if cur in ("WET", "INTERMEDIATE") and tgt in ("WET", "INTERMEDIATE") and tgt != cur:
                emergency = False
                try:
                    emergency = (
                        (delta_wi_med is not None and abs(float(delta_wi_med)) >= 0.90)
                        or wet_score >= 0.97
                        or wet_score <= 0.25
                    )
                except Exception:
                    emergency = False

                if (now < self._wi_lockout_until) and (not emergency):
                    remaining = int(self._wi_lockout_until - now)
                    advice = stay(f"Lockout active ({remaining}s) to avoid Wet↔Inter flip-flop.")
                else:
                    lap_s = float(expected_pace) if expected_pace is not None else 85.0
                    if cur == "WET" and tgt == "INTERMEDIATE":
                        lock_laps = self.lockout_laps_wet_to_inter
                    elif cur == "INTERMEDIATE" and tgt == "WET":
                        lock_laps = self.lockout_laps_inter_to_wet
                    else:
                        lock_laps = 1.0

                    dur = max(45.0, float(lock_laps) * lap_s)
                    self._wi_lockout_until = now + dur
                    self._wi_last_target = tgt

        dbg = (
            f"wetness={wetness:.2f} conf={conf:.2f} mode={'INTER' if self._is_wet_mode else 'DRY'} "
            f"fullwet={'ON' if self._is_fullwet_mode else 'OFF'} wetScore={wet_score:.2f} | "
            f"share(I+W)={None if inter_share_med is None else round(inter_share_med, 3)} "
            f"share(W)={None if wet_share_med is None else round(wet_share_med, 3)} "
            f"ΔI-S={None if delta_is_med is None else round(delta_is_med, 2)} "
            f"ΔW-I={None if delta_wi_med is None else round(delta_wi_med, 2)} "
            f"rainNext={None if rain_next_med is None else round(rain_next_med, 1)} "
            f"trackT={None if track_temp_med is None else round(track_temp_med, 1)} "
            f"baseLoss={None if baseline_loss is None else round(baseline_loss, 2)}"
            f" | {cond_shift_txt}({cond_reason_txt})"
            f" | trackSlope={None if track_slope_cpm is None else round(track_slope_cpm, 2)}"
            f" COND_SHIFT={cond_shift}({cond_reason_txt})"
            f" lockoutUntil={int(self._wi_lockout_until - now) if now < self._wi_lockout_until else 0}s"
        )

        return RainEngineOutput(advice=advice, wetness=wetness, confidence=conf, debug=dbg)

    def _expected_pace_from_rows(self, track: str, tyre: str, rows: list) -> Optional[float]:
        """
        rows = laps_for_track(track) tuples:
        (created_at, session, track, tyre, weather, lap_time_s, fuel_load, wear_fl, wear_fr, wear_rl, wear_rr)
        """
        key = (track.strip(), tyre.strip().upper())
        now = time.time()
        cached = self._baseline_cache.get(key)
        if cached and (now - cached[0]) < 10.0:
            return cached[1]

        t = tyre.strip().upper()
        times: List[float] = []
        for r in rows:
            try:
                r_tyre = str(r[3]).strip().upper()
                lap_time = float(r[5])
            except Exception:
                continue

            if r_tyre != t:
                continue

            if lap_time <= 10.0 or lap_time >= 400.0:
                continue
            times.append(lap_time)

        med = _median(times)
        if med is not None:
            self._baseline_cache[key] = (now, med)
        return med
