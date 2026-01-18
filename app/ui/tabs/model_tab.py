# app/ui/tabs/model_tab.py
from __future__ import annotations

from PySide6 import QtWidgets


class ModelTabWidget(QtWidgets.QWidget):
    def __init__(self, tr, on_estimate_clicked, parent=None):
        super().__init__(parent)
        self.tr = tr

        model_outer = QtWidgets.QVBoxLayout(self)
        model_outer.setContentsMargins(10, 10, 10, 10)
        model_outer.setSpacing(10)

        self.grpDeg = QtWidgets.QGroupBox(self.tr.t("model.deg_group", "Degradation model"))
        model_outer.addWidget(self.grpDeg)

        degLayout = QtWidgets.QGridLayout(self.grpDeg)

        self.cmbTrack = QtWidgets.QComboBox()
        self.cmbTyre = QtWidgets.QComboBox()
        self.cmbTyre.addItems(["C1", "C2", "C3", "C4", "C5", "C6", "INTER", "WET"])

        self.spinRaceLaps = QtWidgets.QSpinBox()
        self.spinRaceLaps.setRange(1, 200)
        self.spinRaceLaps.setValue(50)

        self.spinWearThr = QtWidgets.QSpinBox()
        self.spinWearThr.setRange(40, 99)
        self.spinWearThr.setValue(70)

        self.btnDeg = QtWidgets.QPushButton(self.tr.t("model.estimate_btn", "Estimate degradation + pit windows"))
        self.btnDeg.clicked.connect(on_estimate_clicked)

        self.lblDeg = QtWidgets.QLabel(self.tr.t("common.placeholder", "—"))
        self.lblDeg.setWordWrap(True)

        degLayout.addWidget(QtWidgets.QLabel("Track"), 0, 0)
        degLayout.addWidget(self.cmbTrack, 0, 1)

        degLayout.addWidget(QtWidgets.QLabel("Tyre"), 1, 0)
        degLayout.addWidget(self.cmbTyre, 1, 1)

        degLayout.addWidget(QtWidgets.QLabel("Race laps"), 2, 0)
        degLayout.addWidget(self.spinRaceLaps, 2, 1)

        degLayout.addWidget(QtWidgets.QLabel("Wear threshold (%)"), 3, 0)
        degLayout.addWidget(self.spinWearThr, 3, 1)

        degLayout.addWidget(self.btnDeg, 4, 0, 1, 2)
        degLayout.addWidget(self.lblDeg, 5, 0, 1, 2)

        model_outer.addStretch(1)

    def retranslate(self):
        self.grpDeg.setTitle(self.tr.t("model.deg_group", "Degradation model"))
        self.btnDeg.setText(self.tr.t("model.estimate_btn", "Estimate degradation + pit windows"))