# app/ui/tabs/db_tab.py
from __future__ import annotations
from PySide6 import QtWidgets


class DbTabWidget(QtWidgets.QWidget):
    def __init__(self, tr, on_refresh_clicked, parent=None):
        super().__init__(parent)
        self.tr = tr

        db_outer = QtWidgets.QVBoxLayout(self)
        db_outer.setContentsMargins(10, 10, 10, 10)
        db_outer.setSpacing(10)

        db_bar = QtWidgets.QHBoxLayout()
        db_outer.addLayout(db_bar)

        self.btnRefreshDb = QtWidgets.QPushButton(self.tr.t("db.refresh", "Refresh"))
        self.btnRefreshDb.clicked.connect(on_refresh_clicked)
        db_bar.addWidget(self.btnRefreshDb)

        # retention/debug tools (wired by MainWindow, so DbTab stays "dumb UI")
        self.btnExportDb = QtWidgets.QPushButton(self.tr.t("db.export_csv", "Export DB (CSV)…"))
        db_bar.addWidget(self.btnExportDb)

        self.btnClearCache = QtWidgets.QPushButton(self.tr.t("db.clear_cache", "Clear cache…"))
        db_bar.addWidget(self.btnClearCache)

        db_bar.addStretch(1)

        self.tbl = QtWidgets.QTableWidget()
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setColumnCount(15)
        self.tbl.setHorizontalHeaderLabels([
            "lap",
            "created_at",
            "game",
            "track",
            "session",
            "session_uid",
            "tyre",
            "weather",
            "lap_time_s",
            "fuel",
            "wear_FL",
            "wear_FR",
            "wear_RL",
            "wear_RR",
            "lap_tag",
        ])
        self.tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        db_outer.addWidget(self.tbl, 1)

    def retranslate(self):
        self.btnRefreshDb.setText(self.tr.t("db.refresh", "Refresh"))
        if hasattr(self, "btnExportDb"):
            self.btnExportDb.setText(self.tr.t("db.export_csv", "Export DB (CSV)…"))
        if hasattr(self, "btnClearCache"):
            self.btnClearCache.setText(self.tr.t("db.clear_cache", "Clear cache…"))
