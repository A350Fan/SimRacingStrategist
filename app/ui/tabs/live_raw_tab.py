from __future__ import annotations
from PySide6 import QtWidgets

from app.strategy import generate_placeholder_cards


class LiveRawTabWidget(QtWidgets.QWidget):
    """
    Live Raw Tab.
    This hosts the "old live" UI (full telemetry/raw display),
    and MainWindow's existing update logic will continue to target these widgets.
    """

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self.tr = tr

        # --- layout root ---
        live_outer = QtWidgets.QVBoxLayout(self)
        live_outer.setContentsMargins(10, 10, 10, 10)
        live_outer.setSpacing(10)

        # --- Live group header ---
        self.grpLive = QtWidgets.QGroupBox(self.tr.t("live.group_title", "Live (F1 UDP)"))
        live_outer.addWidget(self.grpLive)

        liveLayout = QtWidgets.QGridLayout(self.grpLive)
        liveLayout.setColumnStretch(0, 0)
        liveLayout.setColumnStretch(1, 1)
        liveLayout.setColumnStretch(2, 1)
        liveLayout.setColumnStretch(3, 2)

        self.lblSC = QtWidgets.QLabel(
            self.tr.t("live.sc_fmt", "SC/VSC: {status}").format(status=self.tr.t("common.na", "n/a"))
        )
        self.lblWeather = QtWidgets.QLabel(self.tr.t("live.weather_na", "Weather: n/a"))
        self.lblRain = QtWidgets.QLabel(self.tr.t("live.rain_na", "Rain(next): n/a"))

        for w in (self.lblSC, self.lblWeather, self.lblRain):
            w.setMinimumWidth(220)

        # Advice prominent
        self.lblRainAdvice = QtWidgets.QLabel(self.tr.t("live.rain_pit_na", "Rain pit: n/a"))
        self.lblRainAdvice.setStyleSheet("font-weight: 700;")
        self.lblRainAdvice.setMinimumWidth(360)

        self.lblFieldShare = QtWidgets.QLabel(self.tr.t("live.field_share_na", "Field: Inter/Wet share: n/a"))
        self.lblFieldDelta = QtWidgets.QLabel(self.tr.t("live.field_delta_na", "Field: Δpace (I-S): n/a"))
        self.lblFieldShare.setMinimumWidth(240)
        self.lblFieldDelta.setMinimumWidth(240)

        liveLayout.addWidget(self.lblSC, 0, 0)
        liveLayout.addWidget(self.lblWeather, 0, 1)
        liveLayout.addWidget(self.lblRain, 0, 2)
        liveLayout.addWidget(self.lblRainAdvice, 0, 3)

        liveLayout.addWidget(self.lblFieldShare, 1, 0, 1, 2)
        liveLayout.addWidget(self.lblFieldDelta, 1, 2, 1, 2)

        # --- Strategy cards group ---
        self.grpStrat = QtWidgets.QGroupBox(self.tr.t("cards.group_title", "Strategy Cards (Prototype)"))
        self.grpStrat.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum
        )
        live_outer.addWidget(self.grpStrat, 0)

        stratLayout = QtWidgets.QHBoxLayout(self.grpStrat)
        stratLayout.setSpacing(10)

        self.cardWidgets = []
        cards = generate_placeholder_cards()
        for c in cards:
            w = QtWidgets.QGroupBox(c.name)
            v = QtWidgets.QVBoxLayout(w)

            lbl_desc = QtWidgets.QLabel(c.description)
            lbl_desc.setWordWrap(True)

            lbl_plan = QtWidgets.QLabel(f"{self.tr.t('cards.tyres_prefix', 'Tyres:')} {c.tyre_plan}")
            lbl_plan.setStyleSheet("font-weight: 700;")

            v.addWidget(lbl_desc)
            v.addWidget(lbl_plan)

            if c.next_pit_lap is not None:
                v.addWidget(QtWidgets.QLabel(
                    self.tr.t("cards.next_pit_fmt", "Next pit: Lap {lap}").format(lap=c.next_pit_lap)
                ))

            v.addStretch(1)
            w.setMinimumWidth(260)

            stratLayout.addWidget(w)
            self.cardWidgets.append(w)

        # --- Minisectors group ---
        self.grpMini = QtWidgets.QGroupBox("Minisectors (10 per sector)")
        live_outer.addWidget(self.grpMini, 1)

        ml = QtWidgets.QVBoxLayout(self.grpMini)
        ml.setContentsMargins(6, 6, 6, 6)
        ml.setSpacing(6)

        self.tblMini = QtWidgets.QTableWidget()
        self.tblMini.verticalHeader().setVisible(False)
        self.tblMini.setColumnCount(5)
        self.tblMini.setHorizontalHeaderLabels(["Minisector", "Last", "PB", "Best", "ΔPB"])
        self.tblMini.setRowCount(33)
        self.tblMini.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)

        self.grpMini.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.tblMini.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)

        ml.addWidget(self.tblMini)

        # Theo lap time summary row
        theoRow = QtWidgets.QHBoxLayout()
        self.lblTheoLast = QtWidgets.QLabel("Theo Last: —")
        self.lblTheoPB = QtWidgets.QLabel("Theo PB: —")
        self.lblTheoBest = QtWidgets.QLabel("Theo Best: —")
        self.lblTheoMiss = QtWidgets.QLabel("")

        for w in (self.lblTheoLast, self.lblTheoPB, self.lblTheoBest):
            w.setStyleSheet("font-weight: 700;")

        theoRow.addWidget(self.lblTheoLast)
        theoRow.addSpacing(12)
        theoRow.addWidget(self.lblTheoPB)
        theoRow.addSpacing(12)
        theoRow.addWidget(self.lblTheoBest)
        theoRow.addStretch(1)
        theoRow.addWidget(self.lblTheoMiss)

        ml.addLayout(theoRow)

    def retranslate(self):
        """Called by MainWindow when language changes."""
        self.grpLive.setTitle(self.tr.t("live.group_title", "Live (F1 UDP)"))
        self.grpStrat.setTitle(self.tr.t("cards.group_title", "Strategy Cards (Prototype)"))

        self.lblSC.setText(self.tr.t("live.sc_na", "SC/VSC: n/a"))
        self.lblWeather.setText(self.tr.t("live.weather_na", "Weather: n/a"))
        self.lblRain.setText(self.tr.t("live.rain_na", "Rain(next): n/a"))
        self.lblRainAdvice.setText(self.tr.t("live.rain_pit_na", "Rain pit: n/a"))
        self.lblFieldShare.setText(self.tr.t("live.field_share_na", "Field: Inter/Wet share: n/a"))
        self.lblFieldDelta.setText(self.tr.t("live.field_delta_na", "Field: Δpace (I-S): n/a"))
