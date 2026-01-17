# app/ui/tabs/live_tab.py
from __future__ import annotations

from PySide6 import QtCore, QtWidgets, QtGui

from app.ui.widgets.flag_widget import FlagWidget
from app.ui.widgets.ascii_hud_widget import AsciiHudWidget


class LiveTabWidget(QtWidgets.QWidget):
    """
    Live (GUI) Tab - new compact UI (WIP).
    For now we start by adding a proper flag indicator (painted, no image assets).
    """

    # thread-safe state handover (UDP thread -> UI thread)
    stateReceived = QtCore.Signal(object)

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self.tr = tr

        # ensure updates always run on the UI thread
        self.stateReceived.connect(self.set_live_state)


        # Keep last state (optional, for future widgets)
        self._state = None

        # Root layout
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)

        # "Stage" area where future widgets will live
        # We use a grid so we can pin items top-left etc.
        self.stage = QtWidgets.QWidget(self)
        self.stage.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding
        )
        lay.addWidget(self.stage, 1)

        stage_grid = QtWidgets.QGridLayout(self.stage)
        stage_grid.setContentsMargins(0, 0, 0, 0)
        stage_grid.setSpacing(18)
        stage_grid.setRowStretch(0, 0)
        stage_grid.setRowStretch(1, 1)
        stage_grid.setColumnStretch(0, 0)
        stage_grid.setColumnStretch(1, 1)

        # --- Flag overlay container (top-left, elevated) ---
        self.flagContainer = QtWidgets.QFrame(self.stage)
        self.flagContainer.setObjectName("flagContainer")
        self.flagContainer.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        # A slightly lighter background than the stage, so it feels like a "layer"
        self.flagContainer.setStyleSheet("""
            QFrame#flagContainer {
                background: rgba(18, 18, 18, 220);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 14px;
            }
        """)

        # Drop shadow -> "second layer / floating"
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.flagContainer)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 6)
        shadow.setColor(QtGui.QColor(0, 0, 0, 160))
        self.flagContainer.setGraphicsEffect(shadow)

        fc_lay = QtWidgets.QVBoxLayout(self.flagContainer)
        fc_lay.setContentsMargins(10, 10, 10, 10)
        fc_lay.setSpacing(6)

        self.flagWidget = FlagWidget(self.flagContainer)
        self.flagWidget.setFixedSize(190, 110)  # tweak as you like; scalable anyway
        fc_lay.addWidget(self.flagWidget)

        # Optional caption (can remove later)
        self.lblFlagHint = QtWidgets.QLabel(self.tr.t("live_gui.flags", "Flags"), self.flagContainer)
        self.lblFlagHint.setStyleSheet("color: rgba(255,255,255,160); font-weight: 600;")
        fc_lay.addWidget(self.lblFlagHint)

        # Pin top-left with alignment (row 0, col 0)
        stage_grid.addWidget(self.flagContainer, 0, 0, alignment=QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)

        # --- Main content (top-right) ---
        # This holds: LapTimer (top) + Minisectors (below).
        self.content = QtWidgets.QFrame(self.stage)
        self.content.setObjectName("liveContent")
        self.content.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.content.setStyleSheet("""
            QFrame#liveContent {
                background: rgba(18, 18, 18, 0);   /* transparent, stage shows through */
            }
        """)

        content_lay = QtWidgets.QVBoxLayout(self.content)
        content_lay.setContentsMargins(6, 0, 0, 0)
        content_lay.setSpacing(10)

        # ASCII-like HUD (compact, boxed, monospace)
        self.hud = AsciiHudWidget(self.content)
        content_lay.addWidget(self.hud, 1)

        # Place content to the right of the flag container (row 0, col 1)
        # Align top so it matches your ASCII mockup style.
        stage_grid.addWidget(
            self.content,
            0, 1,
            alignment=QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft
        )

        # --- Layout behavior: left (flags) stays compact, right (content) gets the space ---
        stage_grid.setColumnStretch(0, 0)  # flags column
        stage_grid.setColumnStretch(1, 1)  # content column expands

        stage_grid.setRowStretch(0, 0)
        stage_grid.setRowStretch(1, 1)

        # Spacer to push everything else down/right later
        stage_grid.addItem(
            QtWidgets.QSpacerItem(
                0, 0,
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding
            ),
            1, 1
        )

        # Bottom stretch outside stage (kept)
        lay.addStretch(0)

    # -----------------------
    # Public API (called by MainWindow)
    # -----------------------
    def set_live_state(self, state) -> None:
        """
        Called from MainWindow._on_live_state (QueuedConnection path).
        We only read values and update UI. No logic/telemetry parsing here.
        """
        self._state = state

        track_flag = getattr(state, "track_flag", None)
        player_flag = getattr(state, "player_fia_flag", None)
        sc_status = getattr(state, "safety_car_status", None)

        # Update flag widget (unchanged)
        try:
            self.flagWidget.set_flags(track_flag=track_flag, player_flag=player_flag, sc_status=sc_status)
        except Exception:
            pass

        # Feed ASCII HUD (includes lap timer, minisectors, delta bar, footer)
        try:
            self.hud.feed_state(state)
        except Exception:
            pass

    def retranslate(self):
        try:
            self.lblFlagHint.setText(self.tr.t("live_gui.flags", "Flags"))
        except Exception:
            pass
