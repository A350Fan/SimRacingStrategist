# app/ui/widgets/flag_widget.py
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class FlagWidget(QtWidgets.QWidget):
    """
    UI-only: draws a racing flag indicator without image assets.
    - Uses QPainter (rectangles) for simple, scalable visuals.
    - Supports a "double yellow" blinking pattern (top-left / bottom-right alternating),
      similar to real track displays.

    Inputs (best-effort, based on your existing mappings in main.py):
      flag code: -1/None unknown, 0 none, 1 green, 2 blue, 3 yellow
    """

    # Internal "render modes"
    MODE_NONE = "none"
    MODE_GREEN = "green"
    MODE_BLUE = "blue"
    MODE_YELLOW = "yellow"
    MODE_DOUBLE_YELLOW = "double_yellow"

    def __init__(self, parent=None):
        super().__init__(parent)

        # Visual sizing (all scalable)
        self._pad = 10
        self._corner = 10  # rounded corners
        self._border = 2

        # Current state
        self._mode = self.MODE_NONE
        self._blink_on = False  # toggles the double-yellow diagonals

        # debounce to avoid 1-frame glitches (e.g., spurious double yellow)
        self._pending_mode = None
        self._pending_count = 0
        self._debounce_frames = 2  # require N identical suggestions before switching

        # Timer for "double yellow" blink
        self._blink_timer = QtCore.QTimer(self)
        self._blink_timer.setInterval(300)  # ms; tweak to taste
        self._blink_timer.timeout.connect(self._on_blink)

        # Make it a compact, fixed-ish tile by default (still scalable)
        self.setMinimumSize(150, 90)

        # Rendering quality
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    # -----------------------
    # Public API
    # -----------------------
    def set_flags(self, track_flag: int | None, player_flag: int | None, sc_status: int | None = None) -> None:
        """
        Decide which flag to display.

        Improvements:
        - Default "GREEN" if session is green and no explicit flags are shown.
        - Debounce mode changes to avoid 1-frame glitches.

        Inputs:
          flag code: -1/None unknown, 0 none, 1 green, 2 blue, 3 yellow
          sc_status: safety car status (0=green, 1=SC, 2=VSC, 3=formation) if available
        """
        tf = self._norm_flag(track_flag)
        pf = self._norm_flag(player_flag)

        # Determine requested mode (pure logic, no state changes yet)
        requested = self.MODE_NONE

        # "Double yellow" heuristic (can be refined later if you find the exact UDP encoding)
        if tf == 3 and pf == 3:
            requested = self.MODE_DOUBLE_YELLOW
        else:
            chosen = self._choose_priority(pf, tf)
            if chosen == 3:
                requested = self.MODE_YELLOW
            elif chosen == 2:
                requested = self.MODE_BLUE
            elif chosen == 1:
                requested = self.MODE_GREEN
            else:
                # NEW: if session is GREEN (no SC/VSC) and no explicit flags -> show GREEN by default
                try:
                    if sc_status is not None and int(sc_status) == 0:
                        requested = self.MODE_GREEN
                    else:
                        requested = self.MODE_NONE
                except Exception:
                    requested = self.MODE_NONE

        # NEW: debounce switching (prevents 1-frame wrong flag)
        if requested != self._mode:
            if requested == self._pending_mode:
                self._pending_count += 1
            else:
                self._pending_mode = requested
                self._pending_count = 1

            if self._pending_count >= self._debounce_frames:
                self._pending_mode = None
                self._pending_count = 0
                self._set_mode(requested)
        else:
            # stable, reset pending
            self._pending_mode = None
            self._pending_count = 0

    # -----------------------
    # Internals
    # -----------------------
    def _norm_flag(self, v: int | None) -> int:
        try:
            if v is None:
                return -1
            return int(v)
        except Exception:
            return -1

    def _choose_priority(self, a: int, b: int) -> int:
        # Map unknown/-1 to 0 (none) for choosing
        aa = 0 if a in (-1,) else a
        bb = 0 if b in (-1,) else b
        # Higher number = higher priority in your mapping (green=1, blue=2, yellow=3)
        return max(aa, bb)

    def _set_mode(self, mode: str) -> None:
        if mode == self._mode:
            return

        self._mode = mode

        # Start/stop blink
        if self._mode == self.MODE_DOUBLE_YELLOW:
            if not self._blink_timer.isActive():
                self._blink_timer.start()
        else:
            if self._blink_timer.isActive():
                self._blink_timer.stop()
            self._blink_on = False

        self.update()

    def _on_blink(self) -> None:
        self._blink_on = not self._blink_on
        self.update()

    # -----------------------
    # Painting
    # -----------------------
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        r = self.rect()

        # --- Background tile (dark, like a steering wheel display housing) ---
        bg = QtGui.QColor(20, 20, 20)
        border = QtGui.QColor(70, 70, 70)

        p.setPen(QtGui.QPen(border, self._border))
        p.setBrush(bg)
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), self._corner, self._corner)

        inner = r.adjusted(self._pad, self._pad, -self._pad, -self._pad)

        # --- Choose main colors ---
        if self._mode == self.MODE_NONE:
            self._draw_text(p, inner, "—", QtGui.QColor(180, 180, 180))
            return

        if self._mode == self.MODE_GREEN:
            self._draw_solid(p, inner, QtGui.QColor(0, 190, 80), "GREEN")
            return

        if self._mode == self.MODE_BLUE:
            self._draw_solid(p, inner, QtGui.QColor(40, 110, 255), "BLUE")
            return

        if self._mode == self.MODE_YELLOW:
            self._draw_solid(p, inner, QtGui.QColor(245, 210, 40), "YELLOW")
            return

        if self._mode == self.MODE_DOUBLE_YELLOW:
            self._draw_double_yellow(p, inner)
            return

        # Fallback
        self._draw_text(p, inner, "?", QtGui.QColor(255, 255, 255))

    def _draw_text(self, p: QtGui.QPainter, rect: QtCore.QRect, text: str, col: QtGui.QColor) -> None:
        p.setPen(col)
        f = p.font()
        f.setBold(True)
        f.setPointSize(max(10, int(min(rect.width(), rect.height()) * 0.22)))
        p.setFont(f)
        p.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), text)

    def _draw_solid(self, p: QtGui.QPainter, rect: QtCore.QRect, col: QtGui.QColor, label: str) -> None:
        # Solid fill
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(rect, 8, 8)

        # Subtle diagonal sheen (still purely painted)
        sheen = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
        sheen.setColorAt(0.0, QtGui.QColor(255, 255, 255, 40))
        sheen.setColorAt(0.5, QtGui.QColor(255, 255, 255, 0))
        sheen.setColorAt(1.0, QtGui.QColor(0, 0, 0, 40))
        p.setBrush(sheen)
        p.drawRoundedRect(rect, 8, 8)

        # Label
        p.setPen(QtGui.QColor(10, 10, 10))
        f = p.font()
        f.setBold(True)
        f.setPointSize(max(9, int(min(rect.width(), rect.height()) * 0.20)))
        p.setFont(f)
        p.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), label)

    def _draw_double_yellow(self, p: QtGui.QPainter, rect: QtCore.QRect) -> None:
        """
        Alternate yellow blocks like real marshal LED panels:
        - Blink diagonal quadrants: top-left vs bottom-right.
        """
        # Base: dark panel
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(30, 30, 30))
        p.drawRoundedRect(rect, 8, 8)

        # Quadrants
        w = rect.width()
        h = rect.height()
        half_w = w // 2
        half_h = h // 2

        tl = QtCore.QRect(rect.left(), rect.top(), half_w, half_h)
        tr = QtCore.QRect(rect.left() + half_w, rect.top(), w - half_w, half_h)
        bl = QtCore.QRect(rect.left(), rect.top() + half_h, half_w, h - half_h)
        br = QtCore.QRect(rect.left() + half_w, rect.top() + half_h, w - half_w, h - half_h)

        yellow = QtGui.QColor(245, 210, 40)
        off = QtGui.QColor(70, 70, 70)

        # Blink pattern: TL+BR on, then TR+BL on (gives that alternating look)
        if self._blink_on:
            on_rects = (tl, br)
            off_rects = (tr, bl)
        else:
            on_rects = (tr, bl)
            off_rects = (tl, br)

        for rr in off_rects:
            p.setBrush(off)
            p.drawRect(rr)

        for rr in on_rects:
            p.setBrush(yellow)
            p.drawRect(rr)

        # Grid lines like LED segments
        p.setPen(QtGui.QPen(QtGui.QColor(15, 15, 15), 2))
        p.drawLine(rect.left() + half_w, rect.top(), rect.left() + half_w, rect.bottom())
        p.drawLine(rect.left(), rect.top() + half_h, rect.right(), rect.top() + half_h)

        # Label
        p.setPen(QtGui.QColor(10, 10, 10))
        f = p.font()
        f.setBold(True)
        f.setPointSize(max(9, int(min(rect.width(), rect.height()) * 0.18)))
        p.setFont(f)
        p.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), "DOUBLE\nYELLOW")
