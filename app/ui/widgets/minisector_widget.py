from __future__ import annotations
from PySide6 import QtWidgets


class MiniSectorWidget(QtWidgets.QGroupBox):
    """
    UI-only widget: Minisectors table + Theo summary row.

    IMPORTANT:
    - No logic here (no tracker, no update functions).
    - MainWindow continues to write into tblMini / lblTheo... via LiveRawTabWidget exposing.
    """

    def __init__(self, parent=None):
        super().__init__("Minisectors (10 per sector)", parent)

        # Make it expand nicely
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding
        )

        ml = QtWidgets.QVBoxLayout(self)
        ml.setContentsMargins(6, 6, 6, 6)
        ml.setSpacing(6)

        # --- Table ---
        self.tblMini = QtWidgets.QTableWidget()
        self.tblMini.verticalHeader().setVisible(False)
        self.tblMini.setColumnCount(5)
        self.tblMini.setHorizontalHeaderLabels(["Minisector", "Last", "PB", "Best", "ΔPB"])
        self.tblMini.setRowCount(33)
        self.tblMini.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)

        self.tblMini.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding
        )

        ml.addWidget(self.tblMini)

        # --- Theo summary row ---
        theo_row = QtWidgets.QHBoxLayout()

        self.lblTheoLast = QtWidgets.QLabel("Theo Last: —")
        self.lblTheoPB = QtWidgets.QLabel("Theo PB: —")
        self.lblTheoBest = QtWidgets.QLabel("Theo Best: —")
        self.lblTheoMiss = QtWidgets.QLabel("")

        for w in (self.lblTheoLast, self.lblTheoPB, self.lblTheoBest):
            w.setStyleSheet("font-weight: 700;")

        theo_row.addWidget(self.lblTheoLast)
        theo_row.addSpacing(12)
        theo_row.addWidget(self.lblTheoPB)
        theo_row.addSpacing(12)
        theo_row.addWidget(self.lblTheoBest)
        theo_row.addStretch(1)
        theo_row.addWidget(self.lblTheoMiss)

        ml.addLayout(theo_row)