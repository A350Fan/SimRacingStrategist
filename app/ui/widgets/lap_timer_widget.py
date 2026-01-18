# app/ui/widgets/lap_timer_widget.py
from __future__ import annotations

import time

from PySide6 import QtCore, QtWidgets


def _fmt_ms(ms: int | None) -> str:
    """Format milliseconds -> M:SS.mmm (like 1:28.432)."""
    if ms is None or ms < 0:
        return "—"
    m = ms // 60000
    s = (ms % 60000) // 1000
    mm = ms % 1000
    return f"{m}:{s:02d}.{mm:03d}"


class LapTimerWidget(QtWidgets.QWidget):
    """
    UI-only: Live lap stopwatch measured by *our own* Start/Finish crossing detection.

    Inputs (best-effort):
      - state.player_current_lap_num
      - state.player_lap_distance_m
      - (optional) state.track_length_m

    Behavior:
      - When we detect crossing S/F -> we "complete" the current lap, compute lap time from monotonic clock,
        update PB, reset stopwatch for the next lap.
      - While on lap -> a QTimer refreshes the display (smooth stopwatch).

    Cooldown / Standbild:
      - Nach S/F halten wir die *fertige* Rundenzeit kurz als Standbild,
        damit man sie lesen kann. Intern starten wir die nächste Runde trotzdem sofort,
        damit die Messung korrekt bleibt.
    """

    # Emitted when we detect a Start/Finish crossing and accept a lap time.
    # Argument: completed lap time in ms.
    lapCompleted = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        # -----------------------
        # internal stopwatch state
        # -----------------------
        self._running = False
        self._lap_start_t = 0.0  # time.monotonic() at lap start
        self._last_lap_complete_ms = None  # last completed lap time (our measurement)

        self._pb_ms = None  # best lap time (our measurement)

        # -----------------------
        # NEW: Pause handling (game pause)
        # -----------------------
        # We detect pause via telemetry: player_current_lap_time_ms stops advancing.
        # While paused, we freeze elapsed time and when unpausing we shift _lap_start_t
        # forward by the paused duration (so elapsed excludes pause time).
        self._paused = False
        self._pause_started_t = 0.0

        self._last_tel_lap_ms: int | None = None
        self._tel_stale_since_t: float | None = None

        # How long telemetry lap time must be "stuck" before we treat it as paused.
        # (Small debounce to avoid jitter / packet hiccups.)
        self._pause_detect_after_s = 0.25

        # NEW: If the game pause menu stops UDP packets, feed_state() won't be called.
        # Then we need a second pause detector: "no state updates for X seconds".
        self._last_state_update_t: float | None = None
        self._stale_pause_after_s = 0.35  # tweak if needed (0.25-0.6 typical)

        # crossing detection helpers
        self._last_lap_num = None
        self._last_dist_m = None
        self._track_len_m = None

        # We store the last *normalized* lap distance we got from telemetry,
        # so the top-right Δ vs PB label can compute "delta at same point".
        # (UI tick runs independently, so it needs a cached distance.)
        self._last_live_norm_dist_m: float | None = None

        # -------------------------------------------------
        # distance->time traces for "delta at same point"
        # -------------------------------------------------
        # current lap trace: list of (dist_m, elapsed_ms)
        self._cur_trace: list[tuple[float, int]] = []

        # PB trace: list of (dist_m, elapsed_ms) from the lap that set _pb_ms
        self._pb_trace: list[tuple[float, int]] = []

        # sampling guards (avoid millions of points if UI updates faster)
        self._last_trace_dist: float | None = None
        self._last_trace_ms: int | None = None

        # tune: record a point if we moved at least X meters OR time advanced by Y ms
        self._trace_min_dist_m = 2.0
        self._trace_min_dt_ms = 50

        # -----------------------
        # "cooldown" display hold
        # -----------------------
        # After crossing S/F we want to briefly hold the finished lap time on screen,
        # so you can read/check it. Internally, we still start the next lap immediately
        # (so timing stays accurate), but the UI stays frozen for a short duration.
        self._freeze_seconds = 5.0
        self._freeze_until_t = 0.0
        self._frozen_ms: int | None = None
        self._frozen_delta_text: str | None = None
        self._frozen_lap_label: str | None = None

        # Heuristics:
        # - "wrap" detection (distance suddenly drops a lot)
        self._wrap_drop_m = 150.0
        # - "near start/finish" zone (distance small)
        self._sf_zone_m = 80.0
        # - debounce: ignore multiple triggers within this window
        self._last_cross_t = 0.0
        self._cross_debounce_s = 0.7

        # -------------
        # UI components
        # -------------
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # Top line (Lap + time + delta)
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)

        self.lblLap = QtWidgets.QLabel("Lap —")
        self.lblLap.setStyleSheet("font-weight: 700;")

        self.lblTime = QtWidgets.QLabel("—")
        self.lblTime.setStyleSheet("font-weight: 800; font-size: 20px;")

        self.lblDelta = QtWidgets.QLabel("Δ vs PB: —")
        self.lblDelta.setStyleSheet("font-weight: 700;")

        top.addWidget(self.lblLap)
        top.addStretch(1)
        top.addWidget(self.lblTime)
        top.addStretch(1)
        top.addWidget(self.lblDelta)

        # Second line (optional status line; you can later feed tyre/weather/session here)
        self.lblInfo = QtWidgets.QLabel("—")
        self.lblInfo.setStyleSheet("color: #b0b0b0;")
        self.lblInfo.setWordWrap(True)

        root.addLayout(top)
        root.addWidget(self.lblInfo)

        # Timer for smooth stopwatch updates
        self._ui_timer = QtCore.QTimer(self)
        self._ui_timer.setInterval(50)  # 20 Hz feels smooth enough
        self._ui_timer.timeout.connect(self._on_tick)
        self._ui_timer.start()

    # -----------------------
    # Public API
    # -----------------------
    def set_info_line(self, text: str) -> None:
        """Optional: set the second line text."""
        self.lblInfo.setText(text or "—")

    def reset_all(self) -> None:
        """Hard reset: clears PB/last, stops stopwatch."""
        self._running = False
        self._lap_start_t = 0.0
        self._last_lap_complete_ms = None
        self._pb_ms = None
        self._last_lap_num = None
        self._last_dist_m = None
        self._track_len_m = None
        self._last_live_norm_dist_m = None
        self._freeze_until_t = 0.0
        self._frozen_ms = None
        self._frozen_delta_text = None
        self._frozen_lap_label = None
        self.lblLap.setText("Lap —")
        self.lblTime.setText("—")
        self.lblDelta.setText("Δ vs PB: —")

    def set_freeze_seconds(self, seconds: float) -> None:
        """Configure how long the widget should hold the finished lap time after S/F."""
        try:
            self._freeze_seconds = max(0.0, float(seconds))
        except Exception:
            self._freeze_seconds = 0.0

    # -----------------------
    # Public getters (used by HUD)
    # -----------------------
    def get_elapsed_ms(self) -> int | None:
        """Current running lap elapsed time in ms (stopwatch)."""
        return self._elapsed_ms()

    def get_pb_ms(self) -> int | None:
        """Current best lap (PB) in ms, based on our own measured laps."""
        return self._pb_ms

    def feed_state(self, state: object) -> None:
        """
        Called from UI thread (queued signal).
        Uses best-effort fields from F1LiveState.
        """
        # Mark: we received fresh telemetry right now (important for pause detection)
        self._last_state_update_t = time.monotonic()

        lap_num = getattr(state, "player_current_lap_num", None)
        dist_m = getattr(state, "player_lap_distance_m", None)
        trk_len = getattr(state, "track_length_m", None)

        # NEW: telemetry-based pause detection
        tel_lap_ms = getattr(state, "player_current_lap_time_ms", None)
        try:
            self._update_pause_from_telemetry(tel_lap_ms)
        except Exception:
            pass

        try:
            self._track_len_m = int(trk_len) if trk_len is not None else self._track_len_m
        except Exception:
            pass

        # Update Lap label (even if we don't run yet)
        if lap_num is not None:
            try:
                self.lblLap.setText(f"Lap {int(lap_num)}")
            except Exception:
                pass

        # Decide if we should start running (first time we get reasonable data)
        if not self._running:
            # Start once we have a lap number (and optionally distance)
            if lap_num is not None:
                self._start_new_lap()
                self._last_lap_num = int(lap_num) if self._safe_int(lap_num) is not None else None

        # Detect crossing Start/Finish:
        crossed = self._detect_sf_crossing(lap_num=lap_num, dist_m=dist_m)
        if crossed:
            # If lap number increments, _last_lap_num is the lap we just finished.
            # Otherwise, best-effort use current lap_num.
            completed_lap_num = self._last_lap_num if self._last_lap_num is not None else self._safe_int(lap_num)
            self._complete_lap_and_restart(completed_lap_num=completed_lap_num)

        # -------------------------
        # sample trace (60 Hz)
        # -------------------------
        # We sample *after* potential lap start is running.
        try:
            cur_ms = self._elapsed_ms()
            d = self._safe_float(dist_m)
            if cur_ms is not None and d is not None:
                nd = self._norm_dist(d)
                if nd is not None:
                    # cache distance for the UI tick -> top delta label
                    self._last_live_norm_dist_m = float(nd)

                    # and still record into the trace
                    self._trace_add_point(nd, int(cur_ms))
        except Exception:
            pass

        # Remember last values for next detection step
        self._last_lap_num = self._safe_int(lap_num) if lap_num is not None else self._last_lap_num
        self._last_dist_m = self._safe_float(dist_m) if dist_m is not None else self._last_dist_m

    # -----------------------
    # Internals
    # -----------------------
    def _safe_int(self, v) -> int | None:
        try:
            return int(v)
        except Exception:
            return None

    def _safe_float(self, v) -> float | None:
        try:
            return float(v)
        except Exception:
            return None

    # -------------------------------------------------
    # NEW: Trace helpers (distance -> time)
    # -------------------------------------------------
    def _norm_dist(self, dist_m: float) -> float | None:
        """
        Normalize lap distance into a stable "within-lap" distance.

        Preferred (wenn TrackLength bekannt):
          - d in [0, track_len)

        Fallback (wenn TrackLength fehlt):
          - benutze dist_m direkt (Codemasters liefert i.d.R. schon lapDistance ab S/F)
          - clamp auf >= 0 und plausibilisieren
        """
        if dist_m is None:
            return None

        try:
            d = float(dist_m)
        except Exception:
            return None

        # Plausi: negative Werte weg (manche Replays/Glitches)
        if d < 0:
            d = 0.0

        tl = self._track_len_m
        if tl is not None:
            try:
                tl_f = float(tl)
            except Exception:
                tl_f = None

            if tl_f is not None and tl_f > 100.0:
                # normaler Weg: sauber auf TrackLength normalisieren
                d = d % tl_f
                if d < 0:
                    d = 0.0
                return d

        # --------
        # Fallback ohne TrackLength:
        # - Distanz muss innerhalb einer Runde monoton steigen.
        # - Extrem große Werte sind fast sicher Müll → verwerfen.
        # (25 km ist schon sehr großzügig)
        # --------
        if d > 25_000.0:
            return None

        return d

    def _trace_reset_for_new_lap(self) -> None:
        """Start a fresh trace for the new lap."""
        self._cur_trace = []
        self._last_trace_dist = None
        self._last_trace_ms = None

    def _trace_add_point(self, dist_m: float, elapsed_ms: int) -> None:
        """
        Add a (dist, time) sample if it is "new enough" vs last sample.
        Assumes dist is already normalized and within [0, tl].
        """
        if dist_m is None or elapsed_ms is None:
            return

        # If distance goes backwards, it is usually just telemetry jitter.
        # Only treat it as a NEW LAP / WRAP if the drop is really large.
        if self._last_trace_dist is not None and dist_m < self._last_trace_dist:
            drop = self._last_trace_dist - dist_m

            # Large drop => real wrap / rewind
            if drop > self._wrap_drop_m:
                self._trace_reset_for_new_lap()
                return  # start fresh, don't mix traces
            else:
                # Small drop => ignore this sample (keep monotonic trace)
                return

        if self._last_trace_dist is not None and self._last_trace_ms is not None:
            dd = dist_m - self._last_trace_dist
            dt = elapsed_ms - self._last_trace_ms

            # only store if we progressed enough in distance OR time
            if dd < self._trace_min_dist_m and dt < self._trace_min_dt_ms:
                return

        self._cur_trace.append((float(dist_m), int(elapsed_ms)))
        self._last_trace_dist = float(dist_m)
        self._last_trace_ms = int(elapsed_ms)

    def _interp_pb_ms_at_dist(self, dist_m: float) -> int | None:
        """
        Linear interpolate PB elapsed time at given distance.
        Returns PB time in ms at that distance.
        """
        if not self._pb_trace:
            return None

        d = float(dist_m)

        # Fast paths
        if d <= self._pb_trace[0][0]:
            return int(self._pb_trace[0][1])
        if d >= self._pb_trace[-1][0]:
            return int(self._pb_trace[-1][1])

        # Find segment [i, i+1] where dist lies
        # (pb_trace is monotonic in dist)
        lo = 0
        hi = len(self._pb_trace) - 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if self._pb_trace[mid][0] <= d:
                lo = mid
            else:
                hi = mid

        d0, t0 = self._pb_trace[lo]
        d1, t1 = self._pb_trace[lo + 1]

        if d1 <= d0:
            return int(t0)

        # linear interpolation
        a = (d - d0) / (d1 - d0)
        return int(round(t0 + a * (t1 - t0)))

    def get_live_delta_to_pb_s(self, dist_m: float | None) -> float | None:
        """
        Delta at the SAME point on track:
          current_elapsed(dist) - pb_elapsed(dist)
        Positive = slower, Negative = faster.
        """
        if dist_m is None:
            return None
        cur_ms = self._elapsed_ms()
        if cur_ms is None:
            return None

        d = self._norm_dist(dist_m)
        if d is None:
            return None

        pb_ms_at_d = self._interp_pb_ms_at_dist(d)
        if pb_ms_at_d is None:
            return None

        return (cur_ms - pb_ms_at_d) / 1000.0

    def _update_pause_from_telemetry(self, tel_lap_ms: int | None) -> None:
        """
        Pause detection/resume based on telemetry lap time.

        Idea:
        - When the game is paused, F1 telemetry's "current lap time" typically stops increasing.
        - Our UI stopwatch uses time.monotonic(), so we must freeze it while telemetry is frozen.

        tel_lap_ms: state.player_current_lap_time_ms (ms), best-effort.
        """
        if not self._running:
            return

        now = time.monotonic()

        # If we don't have this field, we can't reliably detect pause -> do nothing.
        if tel_lap_ms is None:
            return

        try:
            tel = int(tel_lap_ms)
        except Exception:
            return

        # First sample -> just store
        if self._last_tel_lap_ms is None:
            self._last_tel_lap_ms = tel
            self._tel_stale_since_t = None
            return

        if tel != self._last_tel_lap_ms:
            # Telemetry is advancing -> resume if we were paused
            self._last_tel_lap_ms = tel
            self._tel_stale_since_t = None

            if self._paused:
                # Shift lap start forward by the paused duration so elapsed excludes pause
                paused_dt = max(0.0, now - self._pause_started_t)
                self._lap_start_t += paused_dt
                self._paused = False
            return

        # Telemetry is stuck (same ms as last time) -> maybe paused
        if self._tel_stale_since_t is None:
            self._tel_stale_since_t = now
            return

        if (now - self._tel_stale_since_t) >= self._pause_detect_after_s:
            if not self._paused:
                self._paused = True
                self._pause_started_t = now

    def _start_new_lap(self) -> None:
        self._running = True
        self._lap_start_t = time.monotonic()

        # reset pause state for the new lap
        self._paused = False
        self._pause_started_t = 0.0
        self._last_tel_lap_ms = None
        self._tel_stale_since_t = None

    def _update_pause_from_stale_feed(self) -> None:
        """
        If no feed_state() updates arrive for a while, assume the game is paused
        (or telemetry is interrupted) and freeze the stopwatch.
        """
        if not self._running:
            return

        if self._last_state_update_t is None:
            return

        now = time.monotonic()
        if (now - self._last_state_update_t) >= self._stale_pause_after_s:
            if not self._paused:
                self._paused = True
                self._pause_started_t = now

    def _elapsed_ms(self) -> int | None:
        if not self._running:
            return None

        # If paused, freeze elapsed time at the pause start moment
        t = self._pause_started_t if self._paused else time.monotonic()
        return int(round((t - self._lap_start_t) * 1000.0))

    def _complete_lap_and_restart(self, completed_lap_num: int | None = None) -> None:
        now = time.monotonic()
        # debounce so we don't trigger twice if distance jitters around 0
        if (now - self._last_cross_t) < self._cross_debounce_s:
            return
        self._last_cross_t = now

        lap_ms = self._elapsed_ms()
        if lap_ms is None:
            return

        # Sanity (ignore absurd values)
        if lap_ms < 5_000 or lap_ms > 10_000_000:
            # keep running but don't record
            self._start_new_lap()
            return

        self._last_lap_complete_ms = lap_ms

        old_pb_ms = self._pb_ms  # merken für Anzeige am Ziel

        if self._pb_ms is None or lap_ms < self._pb_ms:
            self._pb_ms = lap_ms

            # Ensure PB trace has a "finish" point, otherwise interpolation clamps
            pb = list(self._cur_trace)
            try:
                # best: use track length if known, else use last recorded dist
                if self._track_len_m is not None and float(self._track_len_m) > 100.0:
                    end_d = float(self._track_len_m) - 0.001
                else:
                    end_d = float(pb[-1][0]) if pb else 0.0

                # append finish anchor (monotonic)
                if not pb or end_d > pb[-1][0]:
                    pb.append((end_d, int(lap_ms)))
                else:
                    # overwrite last time at last dist to match lap finish
                    pb[-1] = (pb[-1][0], int(lap_ms))
            except Exception:
                pass

            self._pb_trace = pb

        # -----------------------
        # Freeze UI for a moment
        # -----------------------
        if self._freeze_seconds > 0:
            self._freeze_until_t = now + self._freeze_seconds
            self._frozen_ms = lap_ms

            # Lap label: show the lap we just completed (nice for review)
            if completed_lap_num is not None:
                self._frozen_lap_label = f"Lap {int(completed_lap_num)}"
            else:
                self._frozen_lap_label = self.lblLap.text()

            # Delta label at the finish line: finished lap vs (OLD) PB
            if old_pb_ms is None:
                self._frozen_delta_text = "Δ vs PB: —"
            else:
                d_ms = lap_ms - old_pb_ms
                sign = "+" if d_ms >= 0 else "-"
                self._frozen_delta_text = f"Δ vs PB: {sign}{abs(d_ms) / 1000.0:.3f}"

            # Apply frozen texts immediately (so the snap is visible)
            if self._frozen_lap_label:
                self.lblLap.setText(self._frozen_lap_label)
            self.lblTime.setText(_fmt_ms(self._frozen_ms))
            if self._frozen_delta_text:
                self.lblDelta.setText(self._frozen_delta_text)

        # Restart stopwatch for next lap
        self._start_new_lap()

        # new lap -> new trace
        self._trace_reset_for_new_lap()

        # Update delta label immediately (nice “snap” on the line)
        self._update_delta_label(current_ms=0)

        # Notify others (e.g. minisectors HUD) that a lap was completed.
        try:
            self.lapCompleted.emit(int(lap_ms))
        except Exception:
            pass

    def _detect_sf_crossing(self, lap_num, dist_m) -> bool:
        """
        Cross detection (best-effort, robust-ish):

        Primary:
          - lap number increments (current lap num changes) -> crossed S/F
        Secondary:
          - lap distance wraps (drops a lot) AND we are near start/finish (dist small)
        """
        now = time.monotonic()

        # If lap number increments: strong signal
        ln = self._safe_int(lap_num)
        if ln is not None and self._last_lap_num is not None:
            if ln != self._last_lap_num:
                # lap num changed -> S/F crossed
                if (now - self._last_cross_t) >= self._cross_debounce_s:
                    return True

        # Fallback: distance wrap detection
        d = self._safe_float(dist_m)
        if d is None or self._last_dist_m is None:
            return False

        # If the distance suddenly drops a lot, it's likely a wrap
        if (self._last_dist_m - d) > self._wrap_drop_m:
            # extra guard: we want to be near start/finish after wrap
            if d <= self._sf_zone_m:
                if (now - self._last_cross_t) >= self._cross_debounce_s:
                    return True

        return False

    def _on_tick(self) -> None:
        # NEW: if telemetry stopped (pause menu often stops UDP), freeze stopwatch.
        try:
            self._update_pause_from_stale_feed()
        except Exception:
            pass

        # If we are in "cooldown", keep showing the finished lap as a standstill.
        if self._freeze_until_t and time.monotonic() < self._freeze_until_t:
            if self._frozen_lap_label:
                self.lblLap.setText(self._frozen_lap_label)
            self.lblTime.setText(_fmt_ms(self._frozen_ms))
            if self._frozen_delta_text:
                self.lblDelta.setText(self._frozen_delta_text)
            return

        ms = self._elapsed_ms()
        self.lblTime.setText(_fmt_ms(ms))

        if ms is not None:
            self._update_delta_label(current_ms=ms)

    def _update_delta_label(self, current_ms: int | None) -> None:
        """
        Top-right Δ vs PB label should be the SAME logic as the live delta bar:
        compare against PB at the exact same point on track (distance->time trace).

        Fallbacks:
          - If we don't have a PB trace yet -> use full-lap PB (old behavior).
          - If we don't know current distance -> also fallback.
        """
        if current_ms is None:
            self.lblDelta.setText("Δ vs PB: —")
            return

        # Need at least some PB reference
        if self._pb_ms is None:
            self.lblDelta.setText("Δ vs PB: —")
            return

        delta_ms: int | None = None

        # Preferred: "same point" delta using PB trace interpolation
        try:
            if self._last_live_norm_dist_m is not None:
                pb_at_d = self._interp_pb_ms_at_dist(self._last_live_norm_dist_m)
                if pb_at_d is not None:
                    delta_ms = int(current_ms) - int(pb_at_d)
        except Exception:
            delta_ms = None

        mode = "SP"  # same-point (trace)
        # Fallback: old "gap to full PB lap time"
        if delta_ms is None:
            mode = "LAP"  # fallback
            delta_ms = int(current_ms) - int(self._pb_ms)

        sign = "+" if delta_ms >= 0 else "-"
        self.lblDelta.setText(f"Δ vs PB [{mode}]: {sign}{abs(delta_ms) / 1000.0:.3f}")
