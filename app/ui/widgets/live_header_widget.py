# app/ui/widgets/live_header_widget.py
from __future__ import annotations

from PySide6 import QtWidgets


class LiveHeaderWidget(QtWidgets.QGroupBox):
    """
    UI-only widget: SC/Weather/Rain + Advice + Field signals.

    IMPORTANT:
    - No logic here. MainWindow still sets texts in lblSC/lblWeather/... as before.
    - Exposes the same label attributes for compatibility.
    """

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self.tr = tr

        self.setTitle(self.tr.t("live.group_title", "Live (F1 UDP)"))

        liveLayout = QtWidgets.QGridLayout(self)
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

    def retranslate(self):
        self.setTitle(self.tr.t("live.group_title", "Live (F1 UDP)"))

        self.lblSC.setText(self.tr.t("live.sc_na", "SC/VSC: n/a"))
        self.lblWeather.setText(self.tr.t("live.weather_na", "Weather: n/a"))
        self.lblRain.setText(self.tr.t("live.rain_na", "Rain(next): n/a"))
        self.lblRainAdvice.setText(self.tr.t("live.rain_pit_na", "Rain pit: n/a"))
        self.lblFieldShare.setText(self.tr.t("live.field_share_na", "Field: Inter/Wet share: n/a"))
        self.lblFieldDelta.setText(self.tr.t("live.field_delta_na", "Field: Δpace (I-S): n/a"))