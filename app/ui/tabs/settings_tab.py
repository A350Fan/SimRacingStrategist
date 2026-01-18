# app/ui/tabs/settings_tab.py
from __future__ import annotations

from PySide6 import QtWidgets

from app.translator import Translator


class SettingsTabWidget(QtWidgets.QWidget):
    def __init__(self, tr, cfg, on_pick_folder, on_pick_output_folder, on_apply_settings, on_apply_language, parent=None):
        super().__init__(parent)
        self.tr = tr
        self.cfg = cfg

        s_outer = QtWidgets.QVBoxLayout(self)
        s_outer.setContentsMargins(10, 10, 10, 10)
        s_outer.setSpacing(10)

        grp_cfg = QtWidgets.QGroupBox("Telemetry + UDP")
        s_outer.addWidget(grp_cfg)
        s = QtWidgets.QGridLayout(grp_cfg)

        # --- Language group ---
        self.grpLang = QtWidgets.QGroupBox(self.tr.t("settings.language_group", "Language"))
        s_outer.addWidget(self.grpLang)
        gl = QtWidgets.QGridLayout(self.grpLang)

        self.lblLang = QtWidgets.QLabel(self.tr.t("settings.language_label", "Language"))
        self.cmbLang = QtWidgets.QComboBox()

        langs = self.tr.available_languages() or ["en", "de"]
        names = Translator.language_display_names()

        items = []
        for code in langs:
            label = names.get(code, code)
            items.append((label, code))
        items.sort(key=lambda x: x[0].casefold())

        self.cmbLang.clear()
        for label, code in items:
            self.cmbLang.addItem(label, code)

        cur_lang = (self.cfg.language or "en").strip()
        idx = self.cmbLang.findData(cur_lang)
        if idx >= 0:
            self.cmbLang.setCurrentIndex(idx)

        self.btnApplyLang = QtWidgets.QPushButton(self.tr.t("settings.language_apply", "Apply language"))
        self.btnApplyLang.clicked.connect(on_apply_language)

        gl.addWidget(self.lblLang, 0, 0)
        gl.addWidget(self.cmbLang, 0, 1)
        gl.addWidget(self.btnApplyLang, 1, 0, 1, 2)

        # --- folders + udp ---
        self.lblFolder = QtWidgets.QLabel("Telemetry Folder: (not set)")
        self.btnPick = QtWidgets.QPushButton(self.tr.t("settings.pick_folder", "Pick Folder…"))
        self.btnPick.clicked.connect(on_pick_folder)

        self.lblOutFolder = QtWidgets.QLabel("Data Output Folder: (not set)")
        self.btnPickOut = QtWidgets.QPushButton("Pick Output Folder…")
        self.btnPickOut.clicked.connect(on_pick_output_folder)

        self.chkUdpWriteLaps = QtWidgets.QCheckBox("UDP: write laps.csv (standalone)")

        self.chkUdp = QtWidgets.QCheckBox(self.tr.t("settings.udp_enabled", "UDP enabled (F1 SC/Wetter)"))
        self.spinPort = QtWidgets.QSpinBox()
        self.spinPort.setRange(1024, 65535)
        self.spinPort.setSingleStep(1)

        self.btnApply = QtWidgets.QPushButton(self.tr.t("settings.apply", "Apply Settings"))
        self.btnApply.clicked.connect(on_apply_settings)

        s.addWidget(self.lblFolder, 0, 0, 1, 3)
        s.addWidget(self.btnPick, 0, 3)

        s.addWidget(self.lblOutFolder, 1, 0, 1, 3)
        s.addWidget(self.btnPickOut, 1, 3)

        s.addWidget(self.chkUdpWriteLaps, 2, 0, 1, 4)

        s.addWidget(self.chkUdp, 3, 0, 1, 2)
        s.addWidget(QtWidgets.QLabel("Port:"), 3, 2)
        s.addWidget(self.spinPort, 3, 3)

        s.addWidget(self.btnApply, 4, 0, 1, 4)

        s_outer.addStretch(1)

    def apply_cfg_to_ui(self):
        """MainWindow calls this (same timing as before)"""
        if self.cfg.telemetry_root:
            self.lblFolder.setText(f"Telemetry Folder: {self.cfg.telemetry_root}")
        else:
            self.lblFolder.setText("Telemetry Folder: (not set)")

        self.chkUdp.setChecked(self.cfg.udp_enabled)
        self.spinPort.setValue(self.cfg.udp_port)

        out_root = (getattr(self.cfg, "udp_output_root", "") or "").strip()
        if out_root:
            self.lblOutFolder.setText(f"Data Output Folder: {out_root}")
        else:
            self.lblOutFolder.setText("Data Output Folder: (not set)")

        self.chkUdpWriteLaps.setChecked(bool(getattr(self.cfg, "udp_write_csv_laps", False)))

    def retranslate(self):
        self.grpLang.setTitle(self.tr.t("settings.language_group", "Language"))
        self.lblLang.setText(self.tr.t("settings.language_label", "Language"))
        self.btnApplyLang.setText(self.tr.t("settings.language_apply", "Apply language"))

        self.btnPick.setText(self.tr.t("settings.pick_folder", "Pick Folder…"))
        self.chkUdp.setText(self.tr.t("settings.udp_enabled", "UDP enabled (F1 SC/Wetter)"))
        self.btnApply.setText(self.tr.t("settings.apply", "Apply Settings"))