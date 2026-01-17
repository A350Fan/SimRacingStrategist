# app/ui/widgets/ascii_hud_widget.py
from __future__ import annotations
from PySide6 import QtCore, QtGui, QtWidgets
from app.ui.widgets.lap_timer_widget import LapTimerWidget

import time


def _pct(v) -> str:
    try:
        x = float(v)
        # handle 0..1 or 0..100
        if 0.0 <= x <= 1.0:
            x *= 100.0
        x = max(0.0, min(100.0, x))
        return f"{x:.0f}%"
    except Exception:
        return "—"


def _int(v) -> str:
    try:
        return str(int(v))
    except Exception:
        return "—"


def _speed_kmh(v) -> str:
    try:
        # F1 UDP is usually m/s or km/h depending on state; we best-effort:
        x = float(v)
        if x < 120:  # likely m/s
            x *= 3.6
        return f"{int(round(x))}"
    except Exception:
        return "—"


class SegmentBar(QtWidgets.QWidget):
    """ASCII-like horizontal bar using small blocks (no images)."""
    def __init__(self, segments: int = 10, parent=None):
        super().__init__(parent)
        self._segments = int(segments)
        self._filled = 0

        # Optional per-segment colors (len == segments). If None -> palette highlight.
        self._segment_colors: list[QtGui.QColor] | None = None

        self.setMinimumHeight(14)
        self.setMaximumHeight(16)

    def set_progress(self, filled: int, segment_colors: list[QtGui.QColor] | None = None):
        """
        filled: how many segments are "active" (0..segments)
        segment_colors: per segment fill colors for active segments (len==segments preferred)
        """
        self._filled = max(0, min(int(filled), self._segments))
        self._segment_colors = segment_colors
        self.update()

    def paintEvent(self, e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        r = self.rect().adjusted(0, 0, -1, -1)
        seg = self._segments
        if seg <= 0:
            return

        gap = 3
        w = (r.width() - gap * (seg - 1)) / seg
        h = r.height()

        base = self.palette().color(QtGui.QPalette.ColorRole.Dark)
        fallback = self.palette().color(QtGui.QPalette.ColorRole.Highlight)

        p.setPen(QtCore.Qt.PenStyle.NoPen)
        for i in range(seg):
            x = r.x() + int(i * (w + gap))
            rect = QtCore.QRectF(x, r.y(), w, h)

            if i >= self._filled:
                color = base
            else:
                if self._segment_colors is not None and i < len(self._segment_colors):
                    color = self._segment_colors[i]
                else:
                    color = fallback

            p.setBrush(color)
            p.drawRoundedRect(rect, 2.5, 2.5)


class MiniSectorBars(QtWidgets.QWidget):
    """S1/S2/S3 each with a 10-segment bar + delta label."""
    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        self._bars = []
        self._deltas = []

        for row, name in enumerate(("S1", "S2", "S3")):
            lbl = QtWidgets.QLabel(name)
            lbl.setObjectName("asciiSmall")
            bar = SegmentBar(segments=10)
            delta = QtWidgets.QLabel("—")
            delta.setObjectName("asciiSmall")
            delta.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            delta.setMinimumWidth(70)

            grid.addWidget(lbl, row, 0)
            grid.addWidget(bar, row, 1)
            grid.addWidget(delta, row, 2)

            self._bars.append(bar)
            self._deltas.append(delta)

        grid.setColumnStretch(1, 1)

    def set_row(self, idx: int, filled: int, delta_s: float | None, segment_colors: list[QtGui.QColor] | None = None):
        if idx < 0 or idx > 2:
            return
        self._bars[idx].set_progress(filled, segment_colors=segment_colors)

        if delta_s is None:
            self._deltas[idx].setText("—")
        else:
            sign = "+" if delta_s >= 0 else "-"
            self._deltas[idx].setText(f"{sign}{abs(delta_s):.3f}")


class LiveDeltaBar(QtWidgets.QWidget):
    """
    Live delta bar like ASCII:
      negative -> filled to left
      positive -> filled to right
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._delta_s = None
        self._max_abs = 1.0  # +/- 1s scale for now
        self.setMinimumHeight(18)
        self.setMaximumHeight(22)

    def set_delta(self, delta_s: float | None, max_abs: float | None = None):
        self._delta_s = delta_s
        if max_abs is not None and max_abs > 0:
            self._max_abs = float(max_abs)
        self.update()

    def paintEvent(self, e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        r = self.rect().adjusted(0, 0, -1, -1)

        # center marker
        mid_x = r.center().x()

        # palette colors
        base = self.palette().color(QtGui.QPalette.ColorRole.Dark)
        fill = self.palette().color(QtGui.QPalette.ColorRole.Highlight)
        line = self.palette().color(QtGui.QPalette.ColorRole.Mid)

        # background bar
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(base)
        p.drawRoundedRect(r, 3, 3)

        # center tick
        p.setPen(QtGui.QPen(line, 2))
        p.drawLine(mid_x, r.y() + 2, mid_x, r.bottom() - 2)

        # filled portion
        if self._delta_s is None:
            return

        d = float(self._delta_s)

        # Clamp to scale
        d = max(-self._max_abs, min(self._max_abs, d))
        frac = abs(d) / self._max_abs

        half_w = r.width() / 2.0
        fill_w = half_w * frac

        # Colors as requested:
        # left = slower (delta > 0) -> red
        # right = faster (delta < 0) -> green
        red = QtGui.QColor(220, 70, 70)
        green = QtGui.QColor(70, 200, 120)

        p.setPen(QtCore.Qt.PenStyle.NoPen)

        if d > 0:
            # slower -> LEFT (red)
            p.setBrush(red)
            fr = QtCore.QRectF(mid_x - fill_w, r.y(), fill_w, r.height())
        elif d < 0:
            # faster -> RIGHT (green)
            p.setBrush(green)
            fr = QtCore.QRectF(mid_x, r.y(), fill_w, r.height())
        else:
            return  # exactly zero -> no fill

        p.drawRoundedRect(fr, 3, 3)


class AsciiHudWidget(QtWidgets.QFrame):
    """
    One compact ASCII-style HUD panel:
      - LapTimerWidget top line (Lap / time / Δ vs PB)
      - status line
      - Minisectors (S1..S3 bars)
      - Live delta (value + bar)
      - Footer (speed/gear/throttle/brake)
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("asciiHud")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        # Styles: monospace + subtle box border like ASCII
        self.setStyleSheet("""
            QFrame#asciiHud {
                background: rgba(18, 18, 18, 0);
            }
            QLabel#asciiTitle {
                font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
                font-weight: 700;
                font-size: 13px;
            }
            QLabel#asciiSmall {
                font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
                font-weight: 600;
                font-size: 12px;
            }
            QFrame#asciiBox {
                border: 1px solid rgba(255,255,255,60);
                border-radius: 10px;
                padding: 10px;
                background: rgba(18,18,18,130);
            }
        """)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        # --- TOP BOX: Lap/Time/Delta + status line ---
        self.boxTop = QtWidgets.QFrame(self)
        self.boxTop.setObjectName("asciiBox")
        topLay = QtWidgets.QVBoxLayout(self.boxTop)
        topLay.setContentsMargins(10, 10, 10, 10)
        topLay.setSpacing(6)

        # After a lap is completed we want to keep the *end-of-lap* minisector bars on screen
        # for a short duration (standstill), so you can verify the minisector outcome.
        # LapTimerWidget already freezes its own stopwatch UI.
        self._mini_freeze_seconds = 5.0
        self._mini_freeze_until_t = 0.0

        self.lapTimer = LapTimerWidget(self.boxTop)

        # When LapTimerWidget detects S/F, freeze minisectors for a short moment as well.
        try:
            self.lapTimer.lapCompleted.connect(self._on_lap_completed)
        except Exception:
            pass

        topLay.addWidget(self.lapTimer)

        outer.addWidget(self.boxTop)

        # --- Minisectors BOX ---
        self.boxMini = QtWidgets.QFrame(self)
        self.boxMini.setObjectName("asciiBox")
        miniLay = QtWidgets.QVBoxLayout(self.boxMini)
        miniLay.setContentsMargins(10, 10, 10, 10)
        miniLay.setSpacing(8)

        titleMini = QtWidgets.QLabel("Minisectors")
        titleMini.setObjectName("asciiTitle")
        miniLay.addWidget(titleMini)

        self.miniBars = MiniSectorBars(self.boxMini)
        miniLay.addWidget(self.miniBars)

        outer.addWidget(self.boxMini)

        # --- Live delta BOX ---
        self.boxDelta = QtWidgets.QFrame(self)
        self.boxDelta.setObjectName("asciiBox")
        deltaLay = QtWidgets.QVBoxLayout(self.boxDelta)
        deltaLay.setContentsMargins(10, 10, 10, 10)
        deltaLay.setSpacing(8)

        titleDelta = QtWidgets.QLabel("LIVE DELTA")
        titleDelta.setObjectName("asciiTitle")
        deltaLay.addWidget(titleDelta)

        self.lblLiveDelta = QtWidgets.QLabel("— s")
        self.lblLiveDelta.setObjectName("asciiSmall")
        deltaLay.addWidget(self.lblLiveDelta)

        self.deltaBar = LiveDeltaBar(self.boxDelta)
        deltaLay.addWidget(self.deltaBar)

        outer.addWidget(self.boxDelta)

        # --- Footer line (single row, like ASCII bottom line) ---
        self.boxFooter = QtWidgets.QFrame(self)
        self.boxFooter.setObjectName("asciiBox")
        footLay = QtWidgets.QHBoxLayout(self.boxFooter)
        footLay.setContentsMargins(10, 8, 10, 8)
        footLay.setSpacing(10)

        self.lblSpeed = QtWidgets.QLabel("Speed — km/h")
        self.lblSpeed.setObjectName("asciiSmall")

        self.lblGear = QtWidgets.QLabel("Gear —")
        self.lblGear.setObjectName("asciiSmall")

        self.lblThrottle = QtWidgets.QLabel("T —")
        self.lblThrottle.setObjectName("asciiSmall")

        self.lblBrake = QtWidgets.QLabel("B —")
        self.lblBrake.setObjectName("asciiSmall")

        footLay.addWidget(self.lblSpeed)
        footLay.addWidget(QtWidgets.QLabel("|"))
        footLay.addWidget(self.lblGear)
        footLay.addWidget(QtWidgets.QLabel("|"))
        footLay.addWidget(self.lblThrottle)
        footLay.addWidget(QtWidgets.QLabel("|"))
        footLay.addWidget(self.lblBrake)
        footLay.addStretch(1)

        outer.addWidget(self.boxFooter)

    def feed_state(self, state: object) -> None:
        # Lap timer
        self.lapTimer.feed_state(state)

        # --- Status line (inside LapTimerWidget) ---
        # Best-effort: you can later replace with your own translator strings
        sess = getattr(state, "session_type_id", None)
        weather = getattr(state, "weather", None)
        tyre = getattr(state, "player_tyre_compound", None) or getattr(state, "player_tyre_cat", None)
        self.lapTimer.set_info_line(f"Q | {weather if weather is not None else '—'} | {tyre if tyre is not None else '—'} | C{sess if sess is not None else '—'}")

        # --- Minisectors ---
        # Updated via MainWindow._update_minisector_table() -> hud.update_minisectors_from_tracker(...)
        # (We intentionally don't guess here, so Live Raw stays source of truth.)
        pass

        # --- Live delta (prefer state value, fallback to LapTimerWidget delta) ---
        # Prefer "delta at same point" from LapTimerWidget (distance->time PB trace).
        # This avoids showing "gap to full PB lap time".
        dist_m = getattr(state, "player_lap_distance_m", None)

        delta_s = None
        try:
            delta_s = self.lapTimer.get_live_delta_to_pb_s(dist_m)
        except Exception:
            delta_s = None

        # If we don't have a PB trace yet, fall back to simple elapsed - PB(lap)
        if delta_s is None:
            try:
                delta_s = self.lapTimer.get_delta_to_pb_s()
            except Exception:
                delta_s = None

        try:
            if delta_s is None:
                self.lblLiveDelta.setText("— s")
                self.deltaBar.set_delta(None)
            else:
                ds = float(delta_s)

                # text
                sign = "+" if ds >= 0 else "-"
                self.lblLiveDelta.setText(f"{sign}{abs(ds):.3f} s")

                # bar (your rule is handled in LiveDeltaBar.paintEvent: left=slower/red, right=faster/green)
                self.deltaBar.set_delta(ds, max_abs=1.0)
        except Exception:
            self.lblLiveDelta.setText("— s")
            self.deltaBar.set_delta(None)

        # --- Footer telemetry ---
        speed = getattr(state, "speed", None) or getattr(state, "player_speed", None) or getattr(state, "car_speed", None)
        gear = getattr(state, "gear", None) or getattr(state, "player_gear", None)
        thr = getattr(state, "throttle", None) or getattr(state, "accel", None) or getattr(state, "throttle_pct", None)
        brk = getattr(state, "brake", None) or getattr(state, "brake_pct", None)

        self.lblSpeed.setText(f"Speed {_speed_kmh(speed)} km/h")
        self.lblGear.setText(f"Gear {_int(gear)}")
        self.lblThrottle.setText(f"T {_pct(thr)}")
        self.lblBrake.setText(f"B {_pct(brk)}")

    def set_mini_freeze_seconds(self, seconds: float) -> None:
        """Configure how long minisectors should stay frozen after a lap is completed."""
        try:
            self._mini_freeze_seconds = max(0.0, float(seconds))
        except Exception:
            self._mini_freeze_seconds = 0.0

    def _on_lap_completed(self, lap_ms: int) -> None:
        """Slot: triggered by LapTimerWidget when it detects a lap completion."""
        if self._mini_freeze_seconds <= 0:
            return
        self._mini_freeze_until_t = time.monotonic() + self._mini_freeze_seconds

    def update_minisectors_from_tracker(self, rows: list[object], cur_mi: int | None) -> None:
        """
        Called by MainWindow after it updated Live Raw minisector table.
        Uses the SAME color logic as MainWindow._update_minisector_table().

        rows: self.ms.rows()  (len 30)
        cur_mi: self.ms.current_index() (0..29) or None
        """

        if self._mini_freeze_until_t and time.monotonic() < self._mini_freeze_until_t:
            return

        # Colors copied from your Live Raw logic
        PURPLE = QtGui.QColor(160, 32, 240)
        GREEN = QtGui.QColor(0, 255, 0)
        YELLOW = QtGui.QColor(255, 239, 0)
        NEUTRAL = QtGui.QColor(45, 45, 45)

        def base_color(r) -> QtGui.QColor:
            # EXACT behavior: purple if last==best, green if last==pb else yellow, neutral if none
            if getattr(r, "last_ms", None) is None:
                return NEUTRAL
            last_ms = r.last_ms
            best_ms = getattr(r, "best_ms", None)
            pb_ms = getattr(r, "pb_ms", None)

            if best_ms is not None and last_ms == best_ms:
                return PURPLE
            if pb_ms is not None and last_ms == pb_ms:
                return GREEN
            return YELLOW

        def delta_s_of(r) -> float | None:
            last_ms = getattr(r, "last_ms", None)
            pb_ms = getattr(r, "pb_ms", None)
            if last_ms is None or pb_ms is None:
                return None
            return (last_ms - pb_ms) / 1000.0

        if not rows or len(rows) < 30:
            # nothing to do
            self.miniBars.set_row(0, 0, None, NEUTRAL)
            self.miniBars.set_row(1, 0, None, NEUTRAL)
            self.miniBars.set_row(2, 0, None, NEUTRAL)
            return

        for sector_idx in range(3):
            start = sector_idx * 10
            end = start + 9

            # filled segments = progress of current minisector index
            filled = 0
            if cur_mi is not None:
                if cur_mi < start:
                    filled = 0
                elif cur_mi > end:
                    filled = 10
                else:
                    filled = (cur_mi - start) + 1  # 1..10

            # color/delta based on LAST completed minisector in that sector (up to cur_mi)
            last_done = None
            for mi in range(start, end + 1):
                r = rows[mi]
                if getattr(r, "last_ms", None) is None:
                    continue
                if cur_mi is not None and mi > cur_mi:
                    continue
                last_done = mi

            # Per-minisector colors (10 segments)
            seg_colors: list[QtGui.QColor] = []

            for mi in range(start, end + 1):
                r = rows[mi]

                # If minisector not completed yet -> keep neutral (it'll still be "inactive" if mi>=filled)
                if getattr(r, "last_ms", None) is None:
                    seg_colors.append(NEUTRAL)
                    continue

                # --- Your requested rule per minisector ---
                # yellow if LAST slower than PB
                # else green/purple depending on whether PB is also absolute best
                last_ms = r.last_ms
                pb_ms = getattr(r, "pb_ms", None)
                best_ms = getattr(r, "best_ms", None)

                if pb_ms is None:
                    seg_colors.append(NEUTRAL)
                else:
                    if last_ms > pb_ms:
                        seg_colors.append(YELLOW)
                    else:
                        # last_ms <= pb_ms  (equal or faster)
                        if best_ms is not None and pb_ms == best_ms:
                            seg_colors.append(PURPLE)
                        else:
                            seg_colors.append(GREEN)

            # Delta label stays: use the last completed minisector in the sector
            if last_done is None:
                self.miniBars.set_row(sector_idx, filled, None, segment_colors=seg_colors)
            else:
                r = rows[last_done]
                self.miniBars.set_row(sector_idx, filled, delta_s_of(r), segment_colors=seg_colors)

