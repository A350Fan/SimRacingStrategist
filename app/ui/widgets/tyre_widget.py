# app/ui/widgets/tyre_widget.py
from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets

VISUAL_TO_LETTER = {
    16: "S",  # Soft
    17: "M",  # Medium
    18: "H",  # Hard
    7: "I",  # Inter
    8: "W",  # Wet
}

# Colors are based on the *role* (Soft/Medium/Hard/Inter/Wet), not on the C-number itself.
ROLE_TO_BG = {
    "S": "#E10600",  # red
    "M": "#FFD200",  # yellow
    "H": "#F5F5F5",  # white
    "I": "#00A650",  # green
    "W": "#0072CE",  # blue
    "?": "#444444",  # fallback
}
ROLE_TO_FG = {"S": "#FFFFFF", "M": "#111111", "H": "#111111", "I": "#FFFFFF", "W": "#FFFFFF", "?": "#FFFFFF"}


class TyreWidget(QtWidgets.QFrame):
    """
    HUD element:
    - pill: S/M/H/I/W in correct color
    - wear: avg progress + 4 corners
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("tyreHud")

        self.setStyleSheet("""
            QFrame#tyreHud {
                border: 1px solid rgba(255,255,255,40);
                border-radius: 14px;
                background: rgba(18,18,18,220);
            }
            QLabel#pill {
                border-radius: 8px;
                padding: 2px 8px;
                font-weight: 900;
            }
            QLabel#corners {
                color: rgba(255,255,255,170);
                font-size: 10px;
                font-weight: 700;
            }
        """)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        self.lblPill = QtWidgets.QLabel("?", self)
        self.lblPill.setObjectName("pill")
        self.lblPill.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lblPill.setFixedWidth(32)

        self.barWear = QtWidgets.QProgressBar(self)
        self.barWear.setRange(0, 100)
        self.barWear.setValue(0)
        self.barWear.setTextVisible(True)
        self.barWear.setFixedWidth(160)
        self.barWear.setFormat("Wear: —")

        self.lblCorners = QtWidgets.QLabel("FL — | FR —\nRL — | RR —", self)
        self.lblCorners.setObjectName("corners")

        lay.addWidget(self.lblPill)
        lay.addWidget(self.barWear)
        lay.addWidget(self.lblCorners)
        lay.addStretch(1)

        self._apply_pill("?")

    def _apply_pill(self, text: str, role: str = "?") -> None:
        """Set pill text + background based on the tyre *role* (S/M/H/I/W)."""
        bg = ROLE_TO_BG.get(role, ROLE_TO_BG["?"])
        fg = ROLE_TO_FG.get(role, ROLE_TO_FG["?"])
        self.lblPill.setText(text)
        self.lblPill.setStyleSheet(f"background:{bg}; color:{fg};")

    @staticmethod
    def _fmt(v) -> str:
        try:
            return f"{float(v):.0f}"
        except Exception:
            return "—"

    def update_from_state(self, state: object) -> None:
        # -------------------------
        # Tyre label + color logic
        # -------------------------
        # Goal:
        # - Slicks: show C0..C6 (e.g. "C4"), but color it by the weekend role (Soft/Medium/Hard).
        # - Inter/Wet: show I/W like before.
        tyre_cat = (getattr(state, "player_tyre_cat", None) or "").upper().strip()
        compound_label = (getattr(state, "player_tyre_compound", None) or "").upper().strip()

        role = "?"
        pill_text = "?"

        if tyre_cat in ("INTER", "WET"):
            pill_text = "I" if tyre_cat == "INTER" else "W"
            role = pill_text

        elif tyre_cat == "SLICK":
            if compound_label.startswith("C"):
                pill_text = compound_label

                # Preferred: weekend mapping inferred in the UDP listener
                slick_role_map = getattr(state, "slick_role_map", None) or {}
                role = slick_role_map.get(compound_label, "?")

                # Fallback (until role map is known): use visual S/M/H for color.
                if role not in ("S", "M", "H"):
                    v = getattr(state, "player_tyre_visual", None)
                    try:
                        role = VISUAL_TO_LETTER.get(int(v), "?")
                    except Exception:
                        role = "?"
            else:
                # No C# info yet -> fallback to visual S/M/H
                v = getattr(state, "player_tyre_visual", None)
                try:
                    role = VISUAL_TO_LETTER.get(int(v), "?")
                except Exception:
                    role = "?"
                pill_text = role if role in ("S", "M", "H") else "?"

        else:
            # Unknown -> best-effort
            v = getattr(state, "player_tyre_visual", None)
            try:
                role = VISUAL_TO_LETTER.get(int(v), "?")
                pill_text = role
            except Exception:
                role = "?"
                pill_text = "?"

        self._apply_pill(pill_text, role)

        # -------------------------
        # Wear (CarDamage -> state.player_wear_..)
        # -------------------------
        fl = getattr(state, "player_wear_fl", None)
        fr = getattr(state, "player_wear_fr", None)
        rl = getattr(state, "player_wear_rl", None)
        rr = getattr(state, "player_wear_rr", None)

        # avg nur wenn alle 4 da sind
        avg = None
        try:
            vals = [float(fl), float(fr), float(rl), float(rr)]
            avg = sum(vals) / 4.0
        except Exception:
            avg = None

        if avg is None:
            self.barWear.setValue(0)
            self.barWear.setFormat("Wear: —")
        else:
            avg_i = int(max(0, min(100, round(avg))))
            self.barWear.setValue(avg_i)
            self.barWear.setFormat("Wear: %p%")

        self.lblCorners.setText(
            f"FL {self._fmt(fl)} | FR {self._fmt(fr)}\n"
            f"RL {self._fmt(rl)} | RR {self._fmt(rr)}"
        )
