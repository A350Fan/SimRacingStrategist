#  Copyright (c) 2025-2026 A350Fan
#
#  All rights reserved.
#
#  This project is provided as source-available software.
#
#  You are permitted to:
#  - View and study the source code
#  - Modify the code for personal or educational use
#
#  Under the following conditions:
#  1. Any modified version must clearly attribute the original author
#     (A350Fan) and link to the original repository.
#  2. Modified versions may NOT be published, released, or distributed
#     as a standalone project or product.
#  3. This project, or any derivative work based on it, may NOT be sold,
#     sublicensed, or monetized, including but not limited to paid releases
#     or commercial redistribution.
#
#  Donations to the original author are explicitly permitted.
#
#  This software is provided "as is", without warranty of any kind.

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

LETTER_TO_BG = {
    "S": "#E10600",  # red
    "M": "#FFD200",  # yellow
    "H": "#F5F5F5",  # white
    "I": "#00A650",  # green
    "W": "#0072CE",  # blue
}
LETTER_TO_FG = {"S": "#FFFFFF", "M": "#111111", "H": "#111111", "I": "#FFFFFF", "W": "#FFFFFF"}


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

    def _apply_pill(self, letter: str) -> None:
        bg = LETTER_TO_BG.get(letter, "#444444")
        fg = LETTER_TO_FG.get(letter, "#FFFFFF")
        self.lblPill.setText(letter)
        self.lblPill.setStyleSheet(f"background:{bg}; color:{fg};")

    @staticmethod
    def _fmt(v) -> str:
        try:
            return f"{float(v):.0f}"
        except Exception:
            return "—"

    def update_from_state(self, state: object) -> None:
        # Reifentyp (visual compound -> S/M/H/I/W)
        v = getattr(state, "player_tyre_visual", None)
        letter = VISUAL_TO_LETTER.get(int(v), "?") if v is not None else "?"
        self._apply_pill(letter)

        # Wear (kommt bei dir aus CarDamage -> state.player_wear_..)
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
