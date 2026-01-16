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
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # -----------------------
        # internal stopwatch state
        # -----------------------
        self._running = False
        self._lap_start_t = 0.0            # time.monotonic() at lap start
        self._last_lap_complete_ms = None  # last completed lap time (our measurement)
        self._pb_ms = None                 # best lap time (our measurement)

        # crossing detection helpers
        self._last_lap_num = None
        self._last_dist_m = None
        self._track_len_m = None

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
        self.lblLap.setText("Lap —")
        self.lblTime.setText("—")
        self.lblDelta.setText("Δ vs PB: —")

    def feed_state(self, state: object) -> None:
        """
        Called from UI thread (queued signal).
        Uses best-effort fields from F1LiveState.
        """
        lap_num = getattr(state, "player_current_lap_num", None)
        dist_m = getattr(state, "player_lap_distance_m", None)
        trk_len = getattr(state, "track_length_m", None)

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
            self._complete_lap_and_restart()

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

    def _start_new_lap(self) -> None:
        self._running = True
        self._lap_start_t = time.monotonic()

    def _elapsed_ms(self) -> int | None:
        if not self._running:
            return None
        return int(round((time.monotonic() - self._lap_start_t) * 1000.0))

    def _complete_lap_and_restart(self) -> None:
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
        if self._pb_ms is None or lap_ms < self._pb_ms:
            self._pb_ms = lap_ms

        # Restart stopwatch for next lap
        self._start_new_lap()

        # Update delta label immediately (nice “snap” on the line)
        self._update_delta_label(current_ms=0)

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
        """
        UI refresh loop:
        - Shows the running stopwatch time
        - Shows delta vs PB based on current elapsed (during the lap)
        """
        ms = self._elapsed_ms()
        self.lblTime.setText(_fmt_ms(ms))

        if ms is not None:
            self._update_delta_label(current_ms=ms)

    def _update_delta_label(self, current_ms: int | None) -> None:
        if self._pb_ms is None or current_ms is None:
            self.lblDelta.setText("Δ vs PB: —")
            return

        # Delta in seconds with sign (PB is best, so negative means faster than PB at that moment)
        delta_ms = current_ms - self._pb_ms
        sign = "+" if delta_ms >= 0 else "-"
        self.lblDelta.setText(f"Δ vs PB: {sign}{abs(delta_ms)/1000.0:.3f}")