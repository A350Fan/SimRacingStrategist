# app/f1_udp.py
from __future__ import annotations
import socket
import statistics
import struct
import threading
import time
import datetime
from dataclasses import dataclass
from typing import Callable, Optional
from collections import deque
from pathlib import Path
from app.config import load_config
from app.game_profiles import GAME_PROFILES
from app.paths import cache_dir
from app.logging_util import AppLogger

# Codemasters Team IDs (F1 25)
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

    # NEW: exact compound label for slicks ("C1".."C6"), otherwise same as category ("INTER"/"WET").
    # Intended for lap DB + later strategy logic. Keep player_tyre_cat as the coarse class.
    player_tyre_compound: Optional[str] = None

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

    # wear values stored as "remaining %" (100 = new, 0 = dead)
    player_wear_fl: Optional[float] = None
    player_wear_fr: Optional[float] = None
    player_wear_rl: Optional[float] = None
    player_wear_rr: Optional[float] = None

    # --- Field meta ---
    field_total_cars: Optional[int] = None
    unknown_tyre_count: Optional[int] = None


def _read_header(data: bytes):
    """
    Supports:
      - F1 25 header (29 bytes): <HBBBBBQfIIBB
      - F1 2020 header (24 bytes): <HBBBBQfIBB
    Returns a dict with at least:
      packetFormat, gameYear (synthetic for 2020), packetId, sessionUID, playerCarIndex, headerSize
    """
    if len(data) < 24:
        return None

    # Peek packetFormat (uint16 LE)
    try:
        (pkt_fmt,) = struct.unpack_from("<H", data, 0)
    except Exception:
        return None

    # --- F1 25 / modern (2025) ---
    # Header 29 bytes (includes gameYear + overallFrameIdentifier)
    if len(data) >= 29 and pkt_fmt >= 2025:
        try:
            u = struct.unpack_from("<HBBBBBQfIIBB", data, 0)
            return {
                "packetFormat": int(u[0]),  # 2025
                "gameYear": int(u[1]),  # 25
                "packetId": int(u[5]),
                "sessionUID": u[6],
                "playerCarIndex": int(u[10]),
                "headerSize": 29,
            }
        except Exception:
            return None

    # --- F1 2020 legacy (2020) ---
    # Header 24 bytes (no gameYear, no overallFrameIdentifier)
    if pkt_fmt == 2020:
        try:
            u = struct.unpack_from("<HBBBBQfIBB", data, 0)
            return {
                "packetFormat": int(u[0]),  # 2020
                "gameYear": 20,  # synthetic (for your debug print / UI)
                "packetId": int(u[4]),
                "sessionUID": u[5],
                "playerCarIndex": int(u[8]),
                "headerSize": 24,
            }
        except Exception:
            return None

    # Unknown packet format -> ignore
    return None


class _Debounce:
    """Only accept a value if it stays the same for N updates or T seconds."""

    def __init__(self, n: int = 5, max_age_s: float = 1.0):
        self.n = n
        self.max_age_s = max_age_s
        self._candidate = None
        self._count = 0
        self._t0 = 0.0

    def update(self, value):
        now = time.time()
        if value != self._candidate:
            self._candidate = value
            self._count = 1
            self._t0 = now
            return None

        self._count += 1
        if self._count >= self.n or (now - self._t0) >= self.max_age_s:
            return self._candidate
        return None


class F1UDPListener:
    def __init__(self, port: int, on_state: Callable[[F1LiveState], None], *, debug: bool = True):
        self.port = port
        self.on_state = on_state
        self.debug = bool(debug)
        self.state = F1LiveState()
        self.config = load_config()
        self._game_profile = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # --- Data Health: last packet age (LIVE vs REPLAY getrennt) ---
        # LIVE UDP thread + Replay thread update these; UI reads them -> lock.
        self._pkt_lock = threading.Lock()
        self._last_live_packet_mono: Optional[float] = None
        self._last_replay_packet_mono: Optional[float] = None
        # -------------------------------------------------------------

        self._deb_sc = _Debounce(n=6, max_age_s=0.7)
        self._deb_weather = _Debounce(n=6, max_age_s=0.7)
        self._deb_rain_now = _Debounce(n=6, max_age_s=0.7)
        self._deb_rain_fc = _Debounce(n=6, max_age_s=0.7)

        self._last_lap_ms = [None] * 22
        self._tyre_cat = [None] * 22  # "SLICK" / "INTER" / "WET"

        # exact label used for DB ("C1"..."C6" for slicks, else "INTER"/"WET"/"SLICK").
        self._tyre_compound = [None] * 22

        # --- Active cars detection (LapData: resultStatus) ---
        # 0 invalid, 1 inactive, 2 active, 3 finished, ...
        self._result_status = [0] * 22

        self._emit_interval_s = 0.5  # 2 Hz

        # Outlap-Erkennung: nur wenn Lap deutlich langsamer als vorherige ist
        self._outlap_slow_ms = 8000  # 8s langsamer als vorherige Lap => sehr wahrscheinlich Outlap

        # --- Outlier filter (your reference laps) ---
        self._your_outlier_sec = 2.5  # akzeptiere nur ±2.5s um Median
        self._your_outlier_min_n = 3  # erst ab 3 vorhandenen Laps filtern
        self._your_lap_min_s = 20.0  # harte Plausi-Grenzen
        self._your_lap_max_s = 400.0

        self._last_emit_t = 0.0
        self._dirty = False  # merken: es gab neue Daten seit letztem Emit
        self._dirty_session = False
        self._tyre_last_seen = [0.0] * 22
        self._tyre_timeout_s = 2.5
        self._pit_status = [0] * 22  # 0 none, 1 pitting, 2 in pit area
        self._pit_cycle = [0] * 22  # 0 none, 1 saw pit start, 2 expect outlap
        self._pending_tyre = [None] * 22  # Reifenwahl während Pit (wird erst beim Exit übernommen)

        self._tyre_actual = [None] * 22
        self._tyre_visual = [None] * 22

        # --- Lap quality ---
        self._ignore_next_lap = [False] * 22  # True => nächste LapTime wird verworfen (Outlap nach Reifenwechsel)
        self._last_tyre_cat = [None] * 22  # Merken, ob Reifenklasse gewechselt hat
        self._lap_valid = [True] * 22  # Valid-Flag für "letzte Runde" pro Auto

        # --- Player tracking ---
        self._player_idx: Optional[int] = None
        self._session_uid: Optional[int] = None
        self._last_session_uid: Optional[int] = None

        # last N laps for YOU per tyre category (seconds)
        self._your_laps = {
            "SLICK": deque(maxlen=5),
            "INTER": deque(maxlen=5),
            "WET": deque(maxlen=5),
        }

        # rolling lap history per car and tyre cat (seconds)
        self._car_laps = [
            {
                "SLICK": deque(maxlen=5),
                "INTER": deque(maxlen=5),
                "WET": deque(maxlen=5),
            }
            for _ in range(22)
        ]

        self._lap_flag = ["OK"] * 22

        # --- NEW: UDP raw dump (for offline replay) ---
        self._dump_fp = None
        self._dump_path = None
        self._dump_err_logged = False  # avoid log spam on repeated write errors
        _log = AppLogger()

        try:
            cfg = self.config
            if bool(getattr(cfg, "udp_dump_enabled", False)):
                dump_file = str(getattr(cfg, "udp_dump_file", "") or "").strip()

                if dump_file:
                    p = Path(dump_file)
                else:
                    # auto path: prefer udp_output_root, else cache
                    root = str(getattr(cfg, "udp_output_root", "") or "").strip()
                    out_dir = Path(root) if root else cache_dir()
                    out_dir.mkdir(parents=True, exist_ok=True)

                    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    p = out_dir / f"udp_dump_{ts}.bin"

                # ensure parent exists if user provided a custom file path
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass

                self._dump_path = p
                self._dump_fp = p.open("ab")  # append

                # GUI apps often don't show print() -> log to app.log too
                _log.info(f"UDP dump enabled -> {str(p)}")
                if self.debug:
                    print(f"[DUMP] Writing UDP dump to: {str(p)}")

        except Exception as e:
            self._dump_fp = None
            self._dump_path = None
            _log.error(f"UDP dump init failed: {type(e).__name__}: {e}")
        # ---------------------------------------------

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

        # --- NEW: close dump file ---
        try:
            if self._dump_fp:
                self._dump_fp.flush()
                self._dump_fp.close()
        except Exception:
            pass
        self._dump_fp = None
        # ----------------------------

    # -----------------------------
    # Data Health public API
    # -----------------------------
    def get_last_live_packet_age_s(self) -> Optional[float]:
        """Seconds since last LIVE UDP packet. None = never received."""
        try:
            with self._pkt_lock:
                t0 = self._last_live_packet_mono
            if t0 is None:
                return None
            return max(0.0, time.monotonic() - float(t0))
        except Exception:
            return None

    def get_last_replay_packet_age_s(self) -> Optional[float]:
        """Seconds since last REPLAY payload processed. None = never processed."""
        try:
            with self._pkt_lock:
                t0 = self._last_replay_packet_mono
            if t0 is None:
                return None
            return max(0.0, time.monotonic() - float(t0))
        except Exception:
            return None

    def get_last_packet_age_s(self) -> Optional[float]:
        """
        Legacy single-age API (compat):
        Prefer LIVE age if available, else REPLAY age.
        """
        a = self.get_last_live_packet_age_s()
        if a is not None:
            return a
        return self.get_last_replay_packet_age_s()

    # ------------------------------------------------------------
    # (QoL): Alias-Namen für leichteres Suchen
    # ------------------------------------------------------------
    def udp_age_s(self) -> Optional[float]:
        """
        Alias für die "eine" UDP-Age, die viele intuitiv als `udp_age` suchen.
        Entspricht get_last_packet_age_s().
        """
        return self.get_last_packet_age_s()

    def udp_live_age_s(self) -> Optional[float]:
        """Alias für LIVE-Age (wie get_last_live_packet_age_s)."""
        return self.get_last_live_packet_age_s()

    def udp_replay_age_s(self) -> Optional[float]:
        """Alias für REPLAY-Age (wie get_last_replay_packet_age_s)."""
        return self.get_last_replay_packet_age_s()

    def _handle_packet(self, pid, hdr, data: bytes) -> None:

        if pid == 1:
            if self.debug:
                print("[PID1] Session packet received len=", len(data))

            # basic size sanity check
            if len(data) < 150:
                return

            base = int(hdr.get("headerSize", 29))  # after PacketHeader

            changed = False

            # SessionType + TrackId (see F1 25 spec)
            # offsets: base+6 = sessionType (uint8), base+7 = trackId (int8)
            try:
                sess_type = struct.unpack_from("<B", data, base + 6)[0]
                if sess_type != self.state.session_type_id:
                    self.state.session_type_id = int(sess_type)
                    changed = True
            except Exception:
                pass

            try:
                trk_id = struct.unpack_from("<b", data, base + 7)[0]  # int8
                if trk_id != self.state.track_id:
                    self.state.track_id = int(trk_id)
                    changed = True
            except Exception:
                pass

            # Track length is at base+4 (see F1 25 spec: weather/temps/totalLaps then uint16 trackLength)
            try:
                track_len = struct.unpack_from("<H", data, base + 4)[0]
                if track_len > 0 and track_len != self.state.track_length_m:
                    self.state.track_length_m = int(track_len)
                    changed = True
            except Exception:
                pass

            # NACHDEM du self.state.track_length_m gesetzt hast (F1 25 / pkt_fmt >= 2025):
            # Im F1 25 Spec liegen diese beiden floats am Ende vom PacketSessionData:
            # float m_sector2LapDistanceStart; float m_sector3LapDistanceStart;
            # -> best-effort: von hinten lesen, wenn genug Bytes da sind.

            if hdr.get("packetFormat", 0) >= 2025 and len(data) >= 8:
                try:
                    s2, s3 = struct.unpack_from("<ff", data, len(data) - 8)
                    # Plausi: innerhalb Tracklänge
                    tl = self.state.track_length_m
                    if tl and 0.0 < s2 < tl and 0.0 < s3 < tl and s2 < s3:
                        self.state.sector2_start_m = float(s2)
                        self.state.sector3_start_m = float(s3)
                except Exception:
                    pass

            # --- Sector start distances (needed for minisectors) ---
            # F1 25+: sector2/sector3 start are appended as 2 floats at end of session packet.
            # Older games (e.g. 2020): not available -> optional fallback (approx thirds).

            sec2 = sec3 = None
            pf = int(hdr.get("packetFormat", 0) or 0)

            # F1 25+ (your normal path)
            if pf >= 2025 and len(data) >= 8:
                try:
                    sec2, sec3 = struct.unpack_from("<ff", data, len(data) - 8)
                except Exception:
                    sec2 = sec3 = None

            tl = float(self.state.track_length_m or 0.0)

            def _apply_sector_starts(a: float, b: float) -> None:
                nonlocal changed
                if a != self.state.sector2_start_m:
                    self.state.sector2_start_m = float(a)
                    changed = True
                if b != self.state.sector3_start_m:
                    self.state.sector3_start_m = float(b)
                    changed = True

            # sanity + apply real values if present
            if tl > 0 and sec2 is not None and sec3 is not None and 0.0 < sec2 < sec3 < tl:
                _apply_sector_starts(float(sec2), float(sec3))

            # fallback ONLY for older games / when enabled in profile
            elif tl > 0 and self._game_profile and getattr(self._game_profile, "minisector_sector_fallback", False):
                # crude but consistent: 1/3 and 2/3 of track length
                f2 = float(getattr(self._game_profile, "sector2_frac", 1.0 / 3.0))
                f3 = float(getattr(self._game_profile, "sector3_frac", 2.0 / 3.0))
                # keep sane
                f2 = max(0.10, min(0.60, f2))
                f3 = max(0.40, min(0.90, f3))
                if f2 < f3:
                    _apply_sector_starts(tl * f2, tl * f3)

            # --- Marshal zones / track flags (F1 25 spec) ---
            # Your code already uses the "base + 19 + (21*5)" scheme for safetyCarStatus,
            # so we align marshal-zone offsets to that:
            # numMarshalZones @ base+18, marshalZones[] start @ base+19, each 5 bytes (float + int8).
            track_flag = None
            try:
                num_mz = data[base + 18]
                mz_start = base + 19
                max_flag = None
                for j in range(min(int(num_mz), 21)):
                    zone_flag = struct.unpack_from("<b", data, mz_start + j * 5 + 4)[0]  # int8
                    if zone_flag >= 0:  # ignore -1 invalid
                        max_flag = zone_flag if max_flag is None else max(max_flag, zone_flag)
                track_flag = max_flag  # 0..3 or None
            except Exception:
                track_flag = None

            if self.state.track_flag != track_flag:
                self.state.track_flag = track_flag
                changed = True

            # --- Session packet fields (F1 25 spec) ---
            weather_raw = data[base + 0]  # 0..5

            if self.debug:
                print("[SESSION] weather_raw", weather_raw, "trackTemp",
                      int.from_bytes(data[base + 1:base + 2], "little", signed=True))

            safety_car_off = base + 19 + (21 * 5)
            if safety_car_off + 3 >= len(data):
                return

            sc_raw = data[safety_car_off]  # 0..3
            num_fc = data[safety_car_off + 2]
            fc_off = safety_car_off + 3

            # --- Rain: current + forecast (from forecast samples) ---
            rain_now_raw = None
            rain_fc_raw = None
            fc_series = []
            self.state.rain_fc_series = None  # reset each session packet unless we fill it

            # print("[RAIN RAW]", "now", rain_now_raw, "fc", rain_fc_raw, "n_fc", int(num_fc))

            # fc_dbg = "fc:none"

            stride = 8
            if isinstance(num_fc, int) and num_fc > 0:
                need = fc_off + (num_fc * stride)
                if need <= len(data):
                    for j in range(num_fc):
                        o = fc_off + j * stride
                        time_off_min = int(data[o + 1])  # usually minutes into future
                        weather_fc = int(data[o + 2])  # 0..5
                        rain_fc = int(data[o + 7])  # 0..100
                        # guard
                        if 0 <= time_off_min <= 240 and 0 <= weather_fc <= 5 and 0 <= rain_fc <= 100:
                            fc_series.append((time_off_min, rain_fc, weather_fc))

                    # sort + dedupe by time offset
                    fc_series.sort(key=lambda x: x[0])
                    dedup = []
                    seen = set()
                    for t, r, w in fc_series:
                        if t in seen:
                            continue
                        seen.add(t)
                        dedup.append((t, r, w))
                    fc_series = dedup

                    # "rain_fc_pct" = first sample (nearest future)
                    if fc_series:
                        # rain_now = sample with timeOffset==0 if present, else use the earliest sample as best-effort
                        now_samples = [r for (t, r, w) in fc_series if t == 0]
                        if now_samples:
                            rain_now_raw = now_samples[0]

                        # rain_fc = nearest FUTURE sample (>0). If none, fall back to first.
                        future = [(t, r) for (t, r, w) in fc_series if t > 0]
                        if future:
                            future.sort(key=lambda x: x[0])
                            rain_fc_raw = future[0][1]
                        else:
                            rain_fc_raw = fc_series[0][1]

                # publish series (None if empty)
                self.state.rain_fc_series = fc_series if fc_series else None

            # Rain NOW
            if rain_now_raw is not None:
                try:
                    rain_now_i = int(rain_now_raw)
                except Exception:
                    rain_now_i = None

                if rain_now_i is not None and 0 <= rain_now_i <= 100:
                    r_now = self._deb_rain_now.update(rain_now_i)
                    if r_now is not None and r_now != self.state.rain_now_pct:
                        self.state.rain_now_pct = r_now
                        changed = True

            # Rain FORECAST
            if rain_fc_raw is not None and 0 <= rain_fc_raw <= 100:
                r_fc = self._deb_rain_fc.update(int(rain_fc_raw))
                if r_fc is not None and r_fc != self.state.rain_fc_pct:
                    self.state.rain_fc_pct = r_fc
                    changed = True

            # Weather
            if 0 <= weather_raw <= 5:
                w = self._deb_weather.update(int(weather_raw))
                if w is not None and w != self.state.weather:
                    self.state.weather = w
                    changed = True

            # Safety Car
            if sc_raw in (0, 1, 2, 3):
                sc = self._deb_sc.update(int(sc_raw))
                if sc is not None and sc != self.state.safety_car_status:
                    self.state.safety_car_status = sc
                    changed = True

            if changed:
                self._dirty = True

        elif pid == 2:

            base = int(hdr.get("headerSize", 29))

            changed = False

            # PacketLapData total size is 1285 bytes in F1 25 spec.

            # LapData struct is exactly 57 bytes, repeated 22 times.

            base = int(hdr.get("headerSize", 29))
            pkt_fmt = int(hdr.get("packetFormat", 0))

            changed = False

            # ----------------------------
            # F1 2020 LapData (1190 bytes):
            # header 24 + 22 * 53 = 1190
            # ----------------------------
            if pkt_fmt == 2020:
                car_size = 53
                if len(data) < base + car_size * 22:
                    return

                for i in range(22):
                    off = base + i * car_size

                    try:
                        # F1 2020: last/current lap times are float seconds
                        last_s = struct.unpack_from("<f", data, off + 0)[0]
                        cur_s = struct.unpack_from("<f", data, off + 4)[0]

                        # sector times are uint16 ms (best-effort; ok if 0)
                        s1_ms = struct.unpack_from("<H", data, off + 8)[0]
                        s2_ms = struct.unpack_from("<H", data, off + 10)[0]

                        # lapDistance / totalDistance are floats (metres)
                        lap_dist_m = struct.unpack_from("<f", data, off + 32)[0]
                        total_dist_m = struct.unpack_from("<f", data, off + 36)[0]

                        # status bytes near the end
                        lap_num = struct.unpack_from("<B", data, off + 46)[0]
                        pit_status = struct.unpack_from("<B", data, off + 47)[0]
                        result_status = struct.unpack_from("<B", data, off + 52)[0]

                    except struct.error:
                        continue

                    # convert seconds -> ms
                    last_ms = int(round(last_s * 1000.0)) if last_s and last_s > 0 else None
                    cur_ms = int(round(cur_s * 1000.0)) if cur_s and cur_s > 0 else 0

                    self._pit_status[i] = int(pit_status)
                    self._result_status[i] = int(result_status)

                    # update player fields
                    if i == self._player_idx:
                        if self.state.player_lap_distance_m != float(lap_dist_m):
                            self.state.player_lap_distance_m = float(lap_dist_m)
                            changed = True

                        if self.state.player_current_lap_time_ms != int(cur_ms):
                            self.state.player_current_lap_time_ms = int(cur_ms)
                            changed = True

                        if self.state.player_sector1_time_ms != int(s1_ms):
                            self.state.player_sector1_time_ms = int(s1_ms)
                            changed = True

                        if self.state.player_sector2_time_ms != int(s2_ms):
                            self.state.player_sector2_time_ms = int(s2_ms)
                            changed = True

                        if self.state.player_pit_status != int(pit_status):
                            self.state.player_pit_status = int(pit_status)
                            changed = True

                        if self.state.player_current_lap_num != int(lap_num):
                            self.state.player_current_lap_num = int(lap_num)
                            changed = True

                    # last-lap handling (for deltas/history)
                    if last_ms is not None and last_ms < 10_000_000:
                        prev_ms = self._last_lap_ms[i]
                        if prev_ms != last_ms:
                            self._last_lap_ms[i] = last_ms
                            changed = True

                            # keep your existing validity/outlap logic as-is (minimal safe)
                            valid = True
                            lap_flag = "OK"

                            if self._pit_status[i] != 0 and last_ms >= 200_000:
                                valid = False
                                lap_flag = "IN"

                            if hasattr(self, "_ignore_next_lap") and self._ignore_next_lap[i]:
                                looks_like_outlap = False
                                if isinstance(prev_ms, int) and prev_ms > 0:
                                    if (last_ms - prev_ms) >= getattr(self, "_outlap_slow_ms", 45_000):
                                        looks_like_outlap = True
                                if last_ms >= 200_000:
                                    looks_like_outlap = True

                                if looks_like_outlap:
                                    valid = False
                                    lap_flag = "OUT"
                                self._ignore_next_lap[i] = False

                            self._lap_valid[i] = valid
                            self._lap_flag[i] = lap_flag

                            if i == self._player_idx:
                                if self.state.player_last_lap_time_ms != last_ms:
                                    self.state.player_last_lap_time_ms = last_ms
                                    changed = True

                            # keep per-car history buffers if present
                            if hasattr(self, "_car_laps") and hasattr(self, "_tyre_cat"):
                                cat = self._tyre_cat[i]
                                if valid and cat in ("SLICK", "INTER", "WET"):
                                    lap_s = last_ms / 1000.0
                                    buf = self._car_laps[i][cat]
                                    if self._robust_accept_lap(buf, lap_s):
                                        buf.append(lap_s)

                            if (
                                    hasattr(self, "_your_laps")
                                    and self._player_idx is not None
                                    and i == self._player_idx
                                    and hasattr(self, "_tyre_cat")
                            ):
                                cat = self._tyre_cat[i]
                                if valid and cat in ("SLICK", "INTER", "WET"):
                                    lap_s = last_ms / 1000.0
                                    ybuf = self._your_laps[cat]
                                    if self._robust_accept_lap(ybuf, lap_s):
                                        ybuf.append(lap_s)

                if changed:
                    self._update_field_metrics_and_emit()
                return  # IMPORTANT: stop here for 2020, don’t fall through to F1 25 parser

            # ----------------------------
            # F1 25 LapData (your existing code)
            # ----------------------------
            car_size = 57
            if len(data) < base + car_size * 22:
                return

            fmt_lap = (
                "<II"  # last/current lap ms
                "H B"  # s1 ms part, s1 min part
                "H B"  # s2 ms part, s2 min part
                "H B"  # delta front ms part, delta front min part
                "H B"  # delta leader ms part, delta leader min part
                "f f f"  # lapDistance, totalDistance, safetyCarDelta
                "15B"  # 15x uint8
                "H H"  # pitLaneTimeInLaneMS, pitStopTimerInMS
                "B"  # pitStopShouldServePen
                "f"  # speedTrapFastestSpeed (km/h)
                "B"  # speedTrapFastestLap
            )

            # ... ab hier bleibt dein existierender F1-25 Loop unverändert ...

            for i in range(22):
                off = base + i * car_size

                (
                    last_ms,
                    cur_ms,

                    s1_ms_part, s1_min_part,
                    s2_ms_part, s2_min_part,

                    d_front_ms_part, d_front_min_part,
                    d_lead_ms_part, d_lead_min_part,

                    lap_dist_m,
                    total_dist_m,
                    sc_delta_s,

                    car_pos,
                    lap_num,
                    pit_status,
                    num_pit,
                    sector,
                    lap_invalid,
                    penalties,
                    total_warn,
                    cc_warn,
                    unserved_dt,
                    unserved_sg,
                    grid_pos,
                    driver_status,
                    result_status,
                    pit_lane_timer_active,

                    pit_lane_time_ms,
                    pit_stop_timer_ms,
                    pit_should_serve_pen,

                    speed_trap_fast_kmph,
                    speed_trap_fast_lap,
                ) = struct.unpack_from(fmt_lap, data, off)

                # sector times are split into minutes + ms-part

                s1_ms = int(s1_ms_part) + int(s1_min_part) * 60_000

                s2_ms = int(s2_ms_part) + int(s2_min_part) * 60_000

                self._pit_status[i] = int(pit_status)
                self._result_status[i] = int(result_status)

                # update player live fields

                if i == self._player_idx:

                    # lapDistance may be negative before crossing the line; keep it but it's fine

                    if self.state.player_lap_distance_m != float(lap_dist_m):
                        self.state.player_lap_distance_m = float(lap_dist_m)

                        changed = True

                    if self.state.player_current_lap_time_ms != int(cur_ms):
                        self.state.player_current_lap_time_ms = int(cur_ms)

                        changed = True

                    if self.state.player_sector1_time_ms != s1_ms:
                        self.state.player_sector1_time_ms = s1_ms

                        changed = True

                    if self.state.player_sector2_time_ms != s2_ms:
                        self.state.player_sector2_time_ms = s2_ms

                        changed = True

                    if self.state.player_pit_status != int(pit_status):
                        self.state.player_pit_status = int(pit_status)

                        changed = True

                    if self.state.player_current_lap_num != int(lap_num):
                        self.state.player_current_lap_num = int(lap_num)

                        changed = True

                # Last lap time handling (this is what you used for deltas/history)

                # ignore obvious garbage

                if last_ms and last_ms < 10_000_000:

                    last_ms = int(last_ms)

                else:

                    last_ms = None

                if last_ms is not None:

                    prev_ms = self._last_lap_ms[i]

                    if prev_ms != last_ms:

                        self._last_lap_ms[i] = last_ms

                        changed = True

                        # your existing validity/outlap logic can remain,

                        # but now it uses the real pit_status and real last lap time:

                        valid = True

                        lap_flag = "OK"

                        # conservative "IN" detection: only if pit status says pitting AND lap is very slow

                        if self._pit_status[i] != 0 and last_ms >= 200_000:
                            valid = False

                            lap_flag = "IN"

                        # keep your outlap ignore mechanism if you want (only if you have _ignore_next_lap)

                        if hasattr(self, "_ignore_next_lap") and self._ignore_next_lap[i]:

                            looks_like_outlap = False

                            if isinstance(prev_ms, int) and prev_ms > 0:

                                if (last_ms - prev_ms) >= getattr(self, "_outlap_slow_ms", 45_000):
                                    looks_like_outlap = True

                            if last_ms >= 200_000:
                                looks_like_outlap = True

                            if looks_like_outlap:
                                valid = False

                                lap_flag = "OUT"

                            self._ignore_next_lap[i] = False

                        self._lap_valid[i] = valid

                        self._lap_flag[i] = lap_flag

                        # update player's last lap in state

                        if i == self._player_idx:

                            if self.state.player_last_lap_time_ms != last_ms:
                                self.state.player_last_lap_time_ms = last_ms

                                changed = True

                        # keep your per-car history updates if they exist

                        if hasattr(self, "_car_laps") and hasattr(self, "_tyre_cat"):

                            cat = self._tyre_cat[i]

                            if valid and cat in ("SLICK", "INTER", "WET"):

                                lap_s = last_ms / 1000.0

                                buf = self._car_laps[i][cat]

                                if self._robust_accept_lap(buf, lap_s):
                                    buf.append(lap_s)

                        # keep your "your laps" buffers if they exist

                        if (

                                hasattr(self, "_your_laps")

                                and self._player_idx is not None

                                and i == self._player_idx

                                and hasattr(self, "_tyre_cat")

                        ):

                            cat = self._tyre_cat[i]

                            if valid and cat in ("SLICK", "INTER", "WET"):

                                lap_s = last_ms / 1000.0

                                ybuf = self._your_laps[cat]

                                if self._robust_accept_lap(ybuf, lap_s):
                                    ybuf.append(lap_s)

            if changed:
                self._update_field_metrics_and_emit()


        elif pid == 4:

            # Participants packet (F1 25):

            # header(29) + numActiveCars(1) + 22 * ParticipantData(57)

            # ParticipantData (F1 25) layout:

            # 0 aiControlled

            # 1 driverId

            # 2 networkId

            # 3 teamId

            # 4 myTeam

            # 5 raceNumber

            # 6 nationality

            # 7..38 name[32]

            # 39 yourTelemetry

            # 40 showOnlineNames

            # 41..42 techLevel (uint16)

            # 43 platform

            # 44 numColours

            # 45..56 liveryColours[4] (4 * RGB)

            try:

                base = int(hdr.get("headerSize", 29))

                # num_active = struct.unpack_from("<B", data, base)[0]  # optional

                psize = 57

                p0 = base + 1

                pidx = int(self._player_idx) if self._player_idx is not None else int(hdr.get("playerCarIndex", 0))

                if 0 <= pidx < 22 and (p0 + (pidx + 1) * psize) <= len(data):

                    off = p0 + pidx * psize

                    team_id = struct.unpack_from("<B", data, off + 3)[0]

                    team_name = TEAM_ID_TO_NAME.get(int(team_id), f"TEAM{int(team_id)}")

                    changed = False

                    if int(team_id) != (self.state.player_team_id if self.state.player_team_id is not None else -1):
                        self.state.player_team_id = int(team_id)

                        changed = True

                    if team_name != (self.state.player_team_name or ""):
                        self.state.player_team_name = team_name

                        changed = True

                    # optional debug

                    # if changed:

                    #     print(f"[P4] player teamId={team_id} teamName={team_name}")


            except Exception:

                pass



        elif pid == 7:

            base = int(hdr.get("headerSize", 29))

            remaining = len(data) - base

            pkt_fmt = int(hdr.get("packetFormat", 0))
            car_size = 60 if pkt_fmt == 2020 else 55

            if remaining < 22 * car_size:
                return

            # car_size = remaining // 22  # bei dir i.d.R. 55

            changed = False

            for i in range(22):

                off = base + i * car_size

                if off + car_size > len(data):
                    break

                try:
                    (
                        _tc,
                        _abs,
                        _fuel_mix,
                        _bbias,
                        _pitlim,
                        _fuel_in_tank,
                        _fuel_cap,
                        _fuel_rem_laps,
                        _max_rpm,
                        _idle_rpm,
                        _max_gears,
                        _drs_allowed,
                        _drs_dist,
                        actual,
                        visual,
                        _tyre_age,
                        fia_flag,
                    ) = struct.unpack_from("<BBBBBfffHHBBHBBBb", data, off)

                    self._tyre_actual[i] = int(actual)
                    self._tyre_visual[i] = int(visual)

                    # Save player-specific FIA flag (blue/yellow/green/none)
                    player_idx = int(hdr.get("playerCarIndex", 0))
                    if i == player_idx:
                        # FIA flag (existing)
                        if self.state.player_fia_flag != int(fia_flag):
                            self.state.player_fia_flag = int(fia_flag)
                            changed = True

                        # --- NEW: fuel (additive, best-effort) ---
                        try:
                            fin = float(_fuel_in_tank)
                            if self.state.player_fuel_in_tank != fin:
                                self.state.player_fuel_in_tank = fin
                                changed = True
                        except Exception:
                            pass

                        try:
                            fcap = float(_fuel_cap)
                            if self.state.player_fuel_capacity != fcap:
                                self.state.player_fuel_capacity = fcap
                                changed = True
                        except Exception:
                            pass

                        try:
                            frem = float(_fuel_rem_laps)
                            if self.state.player_fuel_remaining_laps != frem:
                                self.state.player_fuel_remaining_laps = frem
                                changed = True
                        except Exception:
                            pass

                except struct.error:
                    continue

                if visual == 8:
                    tyre_cat = "WET"
                elif visual == 7:
                    tyre_cat = "INTER"
                else:
                    tyre_cat = "SLICK"

                # NEW: exact compound label for DB/strategy (C1..C6 for slicks)
                try:
                    self._tyre_compound[i] = self._compound_label(
                        actual=int(actual), visual=int(visual), tyre_cat=tyre_cat
                    )
                except Exception:
                    self._tyre_compound[i] = tyre_cat

                now = time.monotonic()
                self._tyre_last_seen[i] = now

                pit = self._pit_status[i]

                # Während Pit nur "merken" (damit du es nicht VOR dem Stopp siehst)
                if pit in (1, 2):
                    self._pending_tyre[i] = tyre_cat
                else:
                    # auf Strecke: normal aktualisieren (z.B. Start, SC, etc.)
                    prev_cat = self._tyre_cat[i]
                    if prev_cat != tyre_cat:
                        self._tyre_cat[i] = tyre_cat
                        changed = True

                        # WICHTIG:
                        # Reifenklasse wechselt oft VOR dem nächsten LapTime-Event.
                        # Dann würde die letzte Slick-Zeit fälschlich als Inter/Wet gezählt werden.
                        self._last_lap_ms[i] = None
                        self._lap_valid[i] = False
                        self._lap_flag[i] = "TYRE_SWAP"

                        # Arm outlap-ignore ONLY if this looks like a real pit tyre change:
                        if prev_cat is not None:
                            self._pit_cycle[i] = 2
                            self._ignore_next_lap[i] = True

                        self._last_tyre_cat[i] = tyre_cat

            # DEBUG: nach dem Verarbeiten aller 22 Autos einmal ausgeben (sonst spam)
            interwet = []
            for j in range(22):
                if self._tyre_cat[j] in ("INTER", "WET"):
                    interwet.append(
                        (j, self._tyre_cat[j], self._last_lap_ms[j], self._tyre_actual[j], self._tyre_visual[j]))
            if self.debug:
                print("[TYRE DEBUG] inter/wet cars:", interwet)

            if changed:
                self._dirty = True

        elif pid == 10:
            # --- NEW (additive): tyre wear from CarDamage packet ---
            # We only decode the first 4 floats (tyresWear) per car.
            base = int(hdr.get("headerSize", 29))
            remaining = len(data) - base
            if remaining <= 0:
                return

            # In most games the per-car struct is stable; we only need first 16 bytes anyway.
            car_size = remaining // 22
            if car_size < 16:
                return

            pidx = int(hdr.get("playerCarIndex", 0))
            if not (0 <= pidx < 22):
                return

            off = base + pidx * car_size
            if off + 16 > len(data):
                return

            changed = False
            try:
                # order in spec comments often differs; we keep consistent mapping as FL, FR, RL, RR
                w1, w2, w3, w4 = struct.unpack_from("<ffff", data, off)

                def _to_wear_pct(x: float) -> Optional[float]:
                    """
                    Return tyre wear in percent:
                    0 = new, 100 = fully worn.

                    Some games/packets may deliver 0..1; we normalize to 0..100 in that case.
                    """
                    try:
                        xv = float(x)
                    except Exception:
                        return None

                    # normalize 0..1 -> 0..100 (defensive)
                    if 0.0 <= xv <= 1.0:
                        xv *= 100.0

                    if xv < 0.0 or xv > 100.0:
                        return None

                    # clamp (defensive)
                    if xv < 0.0:
                        xv = 0.0
                    if xv > 100.0:
                        xv = 100.0
                    return xv

                p1 = _to_wear_pct(w1)
                p2 = _to_wear_pct(w2)
                p3 = _to_wear_pct(w3)
                p4 = _to_wear_pct(w4)

                if p1 is not None and self.state.player_wear_fl != p1:
                    self.state.player_wear_fl = p1
                    changed = True
                if p2 is not None and self.state.player_wear_fr != p2:
                    self.state.player_wear_fr = p2
                    changed = True
                if p3 is not None and self.state.player_wear_rl != p3:
                    self.state.player_wear_rl = p3
                    changed = True
                if p4 is not None and self.state.player_wear_rr != p4:
                    self.state.player_wear_rr = p4
                    changed = True

            except Exception:
                pass

            if changed:
                self._dirty = True

            self._maybe_emit()

        self._maybe_emit()

        # At the very end keep whatever your loop does after parsing
        # (e.g. self._maybe_emit()).
        #
        # (See step B below: you'll cut it out of _run and paste it here.)
        pass

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self.port))
        sock.settimeout(0.5)

        while not self._stop.is_set():
            try:
                data, _addr = sock.recvfrom(2048)

                # --- Data Health: LIVE packet received ---
                try:
                    with self._pkt_lock:
                        self._last_live_packet_mono = time.monotonic()
                except Exception:
                    pass
                # ---------------------------------------

                # --- write raw packet to dump file (t_rel_ms + len + payload) ---
                try:
                    if self._dump_fp:
                        t_ms = int(time.monotonic() * 1000)
                        self._dump_fp.write(struct.pack("<QI", t_ms, len(data)))
                        self._dump_fp.write(data)
                except Exception as e:
                    if not getattr(self, "_dump_err_logged", False):
                        self._dump_err_logged = True
                        AppLogger().error(f"UDP dump write failed: {type(e).__name__}: {e}")

                # ---------------------------------------------------------------

                # DEBUG: zeigen ob überhaupt UDP ankommt
                if self.debug:
                    print("RX", len(data))


            except socket.timeout:
                continue
            except OSError:
                break

            hdr = _read_header(data)
            if not hdr:
                continue

            # remember game identity (from header)
            try:
                self.state.packet_format = int(hdr.get("packetFormat")) if hdr.get("packetFormat") is not None else None
            except Exception:
                self.state.packet_format = None
            try:
                self.state.game_year = int(hdr.get("gameYear")) if hdr.get("gameYear") is not None else None
            except Exception:
                self.state.game_year = None

            # remember player index + session
            self._player_idx = int(hdr.get("playerCarIndex", 0))
            self._session_uid = hdr.get("sessionUID")
            self.state.session_uid = str(self._session_uid) if self._session_uid is not None else None

            # --- Game profile resolve (AUTO or manual from settings) ---
            if not hasattr(self, "_game_profile") or self._game_profile is None:
                self._game_profile = self._resolve_game_profile(hdr)

                if self.debug and self._game_profile:
                    print(
                        f"[GAME] Using profile: {self._game_profile.name} "
                        f"(packetFormat={hdr.get('packetFormat')})"
                    )

            # reset player ref buffers on session change (prevents mixing sessions)
            if self._session_uid != self._last_session_uid:
                self._last_session_uid = self._session_uid
                for k in self._your_laps:
                    self._your_laps[k].clear()

            # DEBUG: Packet IDs zählen/anzeigen
            if self.debug:
                print(
                    f"RX len={len(data)} fmt={hdr.get('packetFormat')} year={hdr.get('gameYear')} pid={hdr.get('packetId')}"
                )

            pid = hdr.get("packetId")
            try:
                self._handle_packet(pid, hdr, data)
            except Exception:
                # never crash telemetry thread
                pass
        sock.close()

    def _update_field_metrics_and_emit(self):

        # "Field" = only ACTIVE cars (resultStatus == 2).
        # This removes the 2 unused slots in 20-car sessions that otherwise look like SLICK.
        active_idx = [i for i in range(22) if self._result_status[i] == 2]

        # Fallback: before we have LapData/resultStatus, assume full grid.
        if not active_idx:
            active_idx = list(range(22))

        # Known tyre category cars among active cars
        known_idx = [i for i in active_idx if self._tyre_cat[i] in ("SLICK", "INTER", "WET")]

        # Denominator for shares is only known tyres (S/I/W). Unknowns should not dilute share.
        denom = len(known_idx)

        inter = wet = slick = 0
        for i in known_idx:
            cat = self._tyre_cat[i]
            if cat == "INTER":
                inter += 1
            elif cat == "WET":
                wet += 1
            elif cat == "SLICK":
                slick += 1

        unknown = len(active_idx) - denom
        interwet = inter + wet

        # Meta
        self.state.field_total_cars = len(active_idx)
        self.state.unknown_tyre_count = unknown

        if self.debug:
            print(
                "[TYRE DEBUG] field_total:", len(active_idx),
                "inter:", inter, "wet:", wet, "slick:", slick, "unknown:", unknown
            )

        # compat: inter_share == (INTER+WET)/(SLICK+INTER+WET)  [unknown excluded]
        self.state.inter_share = (interwet / denom) if denom > 0 else 0.0
        self.state.inter_only_share = (inter / denom) if denom > 0 else 0.0
        self.state.wet_share = (wet / denom) if denom > 0 else 0.0

        # compat: inter_count == (INTER+WET)
        self.state.inter_count = interwet
        self.state.inter_only_count = inter
        self.state.wet_count = wet
        self.state.slick_count = slick

        # --- Player tyre category (ALWAYS use real playerCarIndex) ---
        try:
            pidx = int(self._player_idx) if self._player_idx is not None else 0
        except Exception:
            pidx = 0

        if not (0 <= pidx < 22):
            pidx = 0

        self.state.player_car_index = pidx
        self.state.player_tyre_cat = self._tyre_cat[pidx] if (0 <= pidx < len(self._tyre_cat)) else None

        # NEW: exact tyre label (C1..C6 for slicks) for DB / future strategy.
        self.state.player_tyre_compound = (
            self._tyre_compound[pidx] if (0 <= pidx < len(self._tyre_compound)) else None
        )

        # --- Field Δ(I-S) computed per-driver (prevents Norris vs Gasly bias) ---
        deltas = []
        for i in range(22):
            slick_laps = list(self._car_laps[i]["SLICK"])
            interwet_laps = list(self._car_laps[i]["INTER"]) + list(self._car_laps[i]["WET"])

            # IMPORTANT: require 2+ samples each side to avoid outlap / stale values dominating
            if len(slick_laps) >= 2 and len(interwet_laps) >= 2:
                try:
                    d = statistics.median(interwet_laps) - statistics.median(slick_laps)

                    # reject insane deltas (spins/outlaps)
                    if -10.0 < d < 10.0:
                        deltas.append(d)
                except Exception:
                    pass

        # WIP/TELEMETRY SIGNAL:
        # Field-level delta is derived from live lap samples (median across cars).
        # It can fluctuate (sample size, outlaps, traffic), so it should be treated as an input
        # signal for advice/visualization, not as a hard pit trigger.
        if len(deltas) >= 3:
            self.state.pace_delta_inter_vs_slick_s = statistics.median(deltas)
        else:
            self.state.pace_delta_inter_vs_slick_s = None

        # --- Field Δ(W-I) and Δ(W-S) (separately) ---
        deltas_wi = []
        deltas_ws = []
        for i in range(22):
            slick_i = list(self._car_laps[i]["SLICK"])
            inter_i = list(self._car_laps[i]["INTER"])
            wet_i = list(self._car_laps[i]["WET"])

            # require 2+ samples each side
            if len(wet_i) >= 2 and len(inter_i) >= 2:
                try:
                    d = statistics.median(wet_i) - statistics.median(inter_i)
                    if -10.0 < d < 10.0:
                        deltas_wi.append(d)
                except Exception:
                    pass

            if len(wet_i) >= 2 and len(slick_i) >= 2:
                try:
                    d = statistics.median(wet_i) - statistics.median(slick_i)
                    if -10.0 < d < 10.0:
                        deltas_ws.append(d)
                except Exception:
                    pass

        self.state.pace_delta_wet_vs_inter_s = statistics.median(deltas_wi) if len(deltas_wi) >= 3 else None
        self.state.pace_delta_wet_vs_slick_s = statistics.median(deltas_ws) if len(deltas_ws) >= 3 else None

        # --- Your delta (learned from your own laps) ---
        s = list(self._your_laps["SLICK"])
        i_ = list(self._your_laps["INTER"])
        w = list(self._your_laps["WET"])

        self.state.your_ref_counts = f"S:{len(s)} I:{len(i_)} W:{len(w)}"

        if len(s) >= 2 and len(i_) >= 2:
            self.state.your_delta_inter_vs_slick_s = statistics.median(i_) - statistics.median(s)
        else:
            self.state.your_delta_inter_vs_slick_s = None

        if len(s) >= 2 and len(w) >= 2:
            self.state.your_delta_wet_vs_slick_s = statistics.median(w) - statistics.median(s)
        else:
            self.state.your_delta_wet_vs_slick_s = None

        if len(i_) >= 2 and len(w) >= 2:
            self.state.your_delta_wet_vs_inter_s = statistics.median(w) - statistics.median(i_)
        else:
            self.state.your_delta_wet_vs_inter_s = None

        # emit
        try:
            self.on_state(self.state)
        except Exception:
            pass

        if self.debug:
            print("[DELTA DEBUG] percar_deltas", len(deltas), "field_delta", self.state.pace_delta_inter_vs_slick_s)

    def _maybe_emit(self):
        if not getattr(self, "_dirty", False):
            return

        now = time.monotonic()
        if (now - getattr(self, "_last_emit_t", 0.0)) < getattr(self, "_emit_interval_s", 0.5):
            return

        self._last_emit_t = now
        self._dirty = False
        self._update_field_metrics_and_emit()

    def _robust_accept_lap(self, buf: list[float], lap_s: float) -> bool:
        """
        Robust outlier gate for reference laps:
        - needs some history
        - uses MAD (median absolute deviation) if possible
        - falls back to absolute threshold (self._your_outlier_sec)
        """
        # minimal history gate
        if len(buf) < self._your_outlier_min_n:
            return True

        try:
            med = statistics.median(buf)
            devs = [abs(x - med) for x in buf]
            mad = statistics.median(devs)

            # MAD->sigma approx (normal dist): sigma ~= 1.4826 * MAD
            sigma = 1.4826 * mad

            # dynamic threshold:
            # - at least your fixed threshold
            # - or 3.5 sigma (robust)
            dyn_thr = max(self._your_outlier_sec, 3.5 * sigma)

            return abs(lap_s - med) <= dyn_thr
        except Exception:
            # safest fallback
            try:
                med = statistics.median(buf)
                return abs(lap_s - med) <= self._your_outlier_sec
            except Exception:
                return True

    def _resolve_game_profile(self, hdr: dict):
        """
        Resolve selected game profile from Settings (config.game_profile_key).
        - "AUTO": match by packetFormat
        - manual: return requested profile, but warn if packetFormat differs
        """
        user_key = getattr(self.config, "game_profile_key", "AUTO") or "AUTO"
        pkt_fmt = int(hdr.get("packetFormat", -1))

        # AUTO: pick by UDP packetFormat
        if user_key == "AUTO":
            for p in GAME_PROFILES.values():
                if p.packet_format == pkt_fmt:
                    return p
            return None

        # MANUAL
        profile = GAME_PROFILES.get(user_key)
        if not profile:
            return None

        # Validation (helps debugging)
        if profile.packet_format != pkt_fmt:
            try:
                # prefer logger if you have it wired, else print (only when debug)
                if self.debug:
                    print(f"[WARN] Game mismatch: Settings={profile.name} UDP={pkt_fmt}")
            except Exception:
                pass

        return profile

    def _compound_label(self, *, actual: int | None, visual: int | None, tyre_cat: str) -> str:
        """Return exact tyre label for DB.

        - For INTER/WET: returns "INTER"/"WET".
        - For slicks: tries to map to "C1".."C6" using known Codemasters codes.
          Falls back to "SLICK" if unknown.

        NOTE: We keep player_tyre_cat as coarse class (SLICK/INTER/WET) for rain logic.
        """
        cat = (tyre_cat or "").upper().strip()
        if cat in ("INTER", "WET"):
            return cat

        # prefer visual code (what the UI shows) when it looks like a slick compound
        code = None
        try:
            v = int(visual) if visual is not None else None
            if v is not None:
                code = v
        except Exception:
            code = None

        if code is None:
            try:
                code = int(actual) if actual is not None else None
            except Exception:
                code = None

        # Common patterns across recent F1 UDP specs:
        # - 16..21 => slick compounds (we map 16->C1, ..., 21->C6)
        # - 0..5   => sometimes used in older profiles (map 0->C1, ..., 5->C6)
        try:
            pf = int(getattr(self.state, "packet_format", 0) or 0)

            c = int(code) if code is not None else None
            if c is not None and 16 <= c <= 21:
                # F1 25: observed mapping appears inverted (e.g. code 16 == C6)
                if pf >= 2025:
                    return f"C{22 - c}"  # 16->C6 ... 21->C1
                else:
                    return f"C{c - 15}"  # legacy: 16->C1 ... 21->C6

            if c is not None and 0 <= c <= 5:
                # Older range; keep legacy unless you confirm it's inverted there too
                if pf >= 2025:
                    return f"C{6 - c}"  # 0->C6 ... 5->C1
                else:
                    return f"C{c + 1}"  # 0->C1 ... 5->C6
        except Exception:
            pass

        return "SLICK"


# FUTURE/WIP: Robust parser for "rain next" extraction from Session packets without fixed offsets.
# Currently unused (not wired into the live pipeline), but kept as a fallback strategy if offsets change
# across game versions / patches.
def _find_rain_next_from_session_packet(data: bytes, base: int = 24):
    """
    Robust: sucht im Session-Packet nach dem Forecast-Array, ohne feste Offsets.
    Erwartet WeatherForecastSample-Strides von 8 bytes (F1 üblich).
    Gibt (rain_next_pct, debug_str) zurück.
    """
    best = None  # (score, offset_num, n, rain_next, layout)

    # wir scannen nach einer Stelle, wo ein plausibles 'numForecastSamples' steht
    for off_num in range(base, len(data) - 1):
        n = data[off_num]
        if not (1 <= n <= 56):
            continue

        start = off_num + 1
        stride = 8
        end = start + n * stride
        if end > len(data):
            continue

        # Layout A: weather at +2, trackTemp +3 (int8), airTemp +4 (int8), rainPct +7
        score = 0
        for j in range(n):
            o = start + j * stride
            weather = data[o + 2]
            rain = data[o + 7]
            track_temp = int.from_bytes(bytes([data[o + 3]]), "little", signed=True)
            air_temp = int.from_bytes(bytes([data[o + 4]]), "little", signed=True)
            time_offset = data[o + 1]

            if 0 <= weather <= 5:
                score += 1
            if 0 <= rain <= 100:
                score += 1
            if -30 <= track_temp <= 80:
                score += 1
            if -30 <= air_temp <= 80:
                score += 1
            if 0 <= time_offset <= 240:
                score += 1

        rain_next = data[start + 7]  # first sample rainPercentage
        cand = (score, off_num, n, rain_next, "A")
        if best is None or cand[0] > best[0]:
            best = cand

    if best is None:
        return None, "forecast_not_found"

    _, off_num, n, rain_next, layout = best
    return float(rain_next), f"forecast_found off_num={off_num} n={n} layout={layout}"


class F1UDPReplayListener(F1UDPListener):
    """
    Offline replay for previously recorded UDP dumps.

    Dump format:
      <uint64 t_ms><uint32 n_bytes><payload...> repeated
    where t_ms is monotonic milliseconds recorded during capture.
    """

    def __init__(self, replay_file: str, on_state: Callable[[F1LiveState], None], *, speed: float = 1.0,
                 debug: bool = True):
        # port unused for replay, but keep base init intact
        super().__init__(port=0, on_state=on_state, debug=debug)
        self.replay_file = str(replay_file or "").strip()
        self.speed = float(speed) if speed and float(speed) > 0 else 1.0

        # Never write a dump while replaying
        try:
            if self._dump_fp:
                self._dump_fp.close()
        except Exception:
            pass
        self._dump_fp = None

    def _run(self):
        p = Path(self.replay_file)
        if not p.exists() or not p.is_file():
            if self.debug:
                print(f"[REPLAY] File not found: {self.replay_file}")
            return

        if self.debug:
            print(f"[REPLAY] Playing: {self.replay_file} @ speed={self.speed}x")

        try:
            with p.open("rb") as f:
                first_t = None
                wall_t0 = time.monotonic()

                while not self._stop.is_set():
                    hdr = f.read(12)
                    if len(hdr) < 12:
                        break

                    t_ms, n = struct.unpack("<QI", hdr)
                    payload = f.read(int(n))
                    if len(payload) < int(n):
                        break

                    if first_t is None:
                        first_t = int(t_ms)
                        wall_t0 = time.monotonic()

                    # timing: replay relative gaps, scaled by speed
                    try:
                        rel_s = (int(t_ms) - int(first_t)) / 1000.0
                        target = wall_t0 + (rel_s / self.speed)
                        while not self._stop.is_set():
                            now = time.monotonic()
                            if now >= target:
                                break
                            time.sleep(min(0.01, target - now))
                    except Exception:
                        pass

                    # feed packet into the normal parser path
                    try:
                        # This is literally the same code path as LIVE, because we reuse F1UDPListener logic.
                        # We just bypass the socket.
                        hdr2 = _read_header(payload)
                        if not hdr2:
                            continue
                    except Exception:
                        continue

                    # We don't want to duplicate the entire live loop body here.
                    # Trick: temporarily emulate the minimal part the live loop would do:
                    # call the same parsing logic by copying the live logic entrypoint.
                    self._process_one_payload(payload)

        except Exception as e:
            if self.debug:
                print("[REPLAY] error:", repr(e))

    def _process_one_payload(self, data: bytes) -> None:
        """
        Extracted minimal entrypoint: this reuses the exact logic already in the LIVE loop.
        We keep it as a small wrapper so replay doesn't have to duplicate the whole _run().
        """
        # Data Health: REPLAY payload processed (getrennt von LIVE!)
        try:
            with self._pkt_lock:
                self._last_replay_packet_mono = time.monotonic()
        except Exception:
            pass

        # This is the same as the LIVE _run() body AFTER recvfrom().
        hdr = _read_header(data)
        if not hdr:
            return

        # --- BEGIN: copied from LIVE loop (kept minimal) ---
        try:
            self.state.packet_format = int(hdr.get("packetFormat")) if hdr.get("packetFormat") is not None else None
        except Exception:
            self.state.packet_format = None
        try:
            self.state.game_year = int(hdr.get("gameYear")) if hdr.get("gameYear") is not None else None
        except Exception:
            self.state.game_year = None

        self._player_idx = int(hdr.get("playerCarIndex", 0))
        self._session_uid = hdr.get("sessionUID")
        self.state.session_uid = str(self._session_uid) if self._session_uid is not None else None

        if not hasattr(self, "_game_profile") or self._game_profile is None:
            self._game_profile = self._resolve_game_profile(hdr)
            if self.debug and self._game_profile:
                print(
                    f"[GAME] Using profile: {self._game_profile.name} "
                    f"(packetFormat={hdr.get('packetFormat')})"
                )

        if self._session_uid != self._last_session_uid:
            self._last_session_uid = self._session_uid
            for k in self._your_laps:
                self._your_laps[k].clear()

        if self.debug:
            print(
                f"RX len={len(data)} fmt={hdr.get('packetFormat')} year={hdr.get('gameYear')} pid={hdr.get('packetId')}"
            )

        # Now fall through to the same packetId handlers you already have:
        pid = hdr.get("packetId")

        # IMPORTANT:
        # We reuse your existing code by calling the same internal handlers:
        # easiest + safest: inline-call the existing if/elif chain by delegating to a helper
        self._dispatch_packet(pid, hdr, data)
        # --- END ---

    def _dispatch_packet(self, pid, hdr, data) -> None:
        """
        This is a tiny shim that contains your existing pid==1/2/4/7 etc chain.
        We keep the chain in ONE place by moving it into this method.
        """
        # NOTE: Implemented by moving the existing pid-chain into this method in LIVE too.
        # If you haven't moved it yet, do that step below (2.6).
        return self._handle_packet(pid, hdr, data)