# app/f1_udp.py
from __future__ import annotations

import datetime
import socket
import statistics
import struct
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from app.config import load_config
from app.game_profiles import GAME_PROFILES
from app.logging_util import AppLogger
from app.paths import cache_dir
from app.telemetry.header import read_header, try_parse_f1_header, hex_dump
from app.telemetry.packets.car_damage import handle_car_damage_packet
from app.telemetry.packets.car_status import handle_car_status_packet
from app.telemetry.packets.lap_data import handle_lap_data_packet
from app.telemetry.packets.participants import handle_participants_packet
from app.telemetry.packets.session import handle_session_packet
from app.telemetry.state import F1LiveState


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
            handle_session_packet(self, hdr, data)

        elif pid == 2:
            handle_lap_data_packet(self, hdr, data)

        elif pid == 4:
            handle_participants_packet(self, hdr, data)

        elif pid == 7:
            handle_car_status_packet(self, hdr, data)

        elif pid == 10:
            handle_car_damage_packet(self, hdr, data)

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

                # ========================================================
                # DEBUG: RAW UDP HEADER SNIFFER (before any parsing!)
                # ========================================================
                if self.debug:
                    print(
                        "[SNIFF]",
                        f"len={len(data)}",
                        "head=",
                        hex_dump(data, 32)
                    )

                    dbg_hdr = try_parse_f1_header(data)
                    if dbg_hdr:
                        print(
                            "[SNIFF HDR]",
                            f"fmt={dbg_hdr['packet_format']}",
                            f"ver={dbg_hdr['game_version']}",
                            f"pid={dbg_hdr['packet_id']}",
                            f"frame={dbg_hdr['frame_id']}",
                        )
                    else:
                        print("[SNIFF HDR] parse FAILED")

                # DEBUG: zeigen ob überhaupt UDP ankommt
                if self.debug:
                    print("RX", len(data))


            except socket.timeout:
                continue
            except OSError:
                break

            hdr = read_header(data)
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

        # AUTO: pick by UDP packetFormat (primary), fallback by gameYear (secondary)
        if user_key == "AUTO":
            # 1) primary: packetFormat exact match
            matches = [p for p in GAME_PROFILES.values() if p.packet_format == pkt_fmt]
            if len(matches) == 1:
                return matches[0]

            # 2) secondary: gameYear fallback (helps when packetFormat is reused across years)
            gy = hdr.get("gameYear", None)
            try:
                gy = int(gy) if gy is not None else None
            except Exception:
                gy = None

            if gy is not None:
                year_to_key = {
                    2025: "F1_25",
                    2024: "F1_24",
                    2023: "F1_23",
                    2022: "F1_22",
                    2021: "F1_21",
                    2020: "F1_2020",
                    2019: "F1_2019",
                    2018: "F1_2018",
                    2017: "F1_2017",
                }
                k = year_to_key.get(gy)
                if k and k in GAME_PROFILES:
                    return GAME_PROFILES[k]

            # 3) if ambiguous and we had packetFormat matches, prefer the "newest" one
            if matches:
                # higher packet_format is not helpful here (same), so prefer by name/year heuristic
                # just return the first deterministic match (dict order) OR choose a stable key:
                # (You can refine this later if needed)
                return matches[0]

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
        - For slicks: tries to map to "C1"-"C6" using known Codemasters codes.
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
                        hdr2 = read_header(payload)
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
        hdr = read_header(data)
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
        This is a tiny shim that contains your existing pid==1/2/4/7 etc. chain.
        We keep the chain in ONE place by moving it into this method.
        """
        # NOTE: Implemented by moving the existing pid-chain into this method in LIVE too.
        # If you haven't moved it yet, do that step below (2.6).
        return self._handle_packet(pid, hdr, data)
