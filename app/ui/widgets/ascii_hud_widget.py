# app/ui/widgets/ascii_hud_widget.py
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from app.ui.widgets.lap_timer_widget import LapTimerWidget


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
        self.setMinimumHeight(14)
        self.setMaximumHeight(16)

    def set_progress(self, filled: int):
        self._filled = max(0, min(int(filled), self._segments))
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

        # Use palette so it adapts to your theme
        base = self.palette().color(QtGui.QPalette.ColorRole.Dark)
        fill = self.palette().color(QtGui.QPalette.ColorRole.Highlight)

        p.setPen(QtCore.Qt.PenStyle.NoPen)
        for i in range(seg):
            x = r.x() + int(i * (w + gap))
            rect = QtCore.QRectF(x, r.y(), w, h)
            p.setBrush(fill if i < self._filled else base)
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

    def set_row(self, idx: int, filled: int, delta_s: float | None):
        if idx < 0 or idx > 2:
            return
        self._bars[idx].set_progress(filled)
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

        self.lapTimer = LapTimerWidget(self.boxTop)  # already running stopwatch logic :contentReference[oaicite:1]{index=1}
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
        # Right now we don't have your real minisector live values wired in this file.
        # So we show placeholders that are easy to spot while you hook real numbers.
        # You can replace these three lines with real minisector progress + delta values.
        try:
            self.miniBars.set_row(0, 7, +0.032)
            self.miniBars.set_row(1, 10, -0.141)
            self.miniBars.set_row(2, 10, -0.129)
        except Exception:
            pass

        # --- Live delta (prefer state value, fallback to LapTimerWidget delta) ---
        delta_s = (
                getattr(state, "live_delta_to_pb_s", None)
                or getattr(state, "delta_to_pb_s", None)
                or getattr(state, "delta_s", None)
        )

        # Fallback: LapTimerWidget already knows PB + elapsed, so we can always draw the bar
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