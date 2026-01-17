# app/main.py
from __future__ import annotations
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from PySide6 import QtCore, QtWidgets, QtGui

# --- UI split: tab widgets ---
from app.ui.tabs.live_raw_tab import LiveRawTabWidget
from app.ui.tabs.live_tab import LiveTabWidget
from app.ui.tabs.db_tab import DbTabWidget
from app.ui.tabs.model_tab import ModelTabWidget
from app.ui.tabs.settings_tab import SettingsTabWidget

from app.config import load_config, save_config, AppConfig
from app.watcher import FolderWatcher
from app.overtake_csv import parse_overtake_csv, lap_summary
from app.db import upsert_lap, latest_laps, distinct_tracks, laps_for_track
from app.f1_udp import F1UDPListener, F1UDPReplayListener, F1LiveState
from app.logging_util import AppLogger
from app.strategy_model import LapRow, estimate_degradation_for_track_tyre, pit_window_one_stop, pit_windows_two_stop
from app.rain_engine import RainEngine
from app.translator import Translator
from app.track_map import track_label_from_id
from app.minisectors import MiniSectorTracker

import sys
import re
import csv


_RE_RACE = re.compile(r"(^|_)r($|_)")
_RE_QUALI = re.compile(r"(^|_)q($|_)|(^|_)q[123]($|_)")
_RE_PRACTICE = re.compile(r"(^|_)p($|_)|(^|_)p[123]($|_)")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SimRacingStrategist – Prototype")
        self.resize(1100, 700)

        self.cfg: AppConfig = load_config()

        # i18n
        self.tr = Translator(self.cfg.language)

        self.watcher: Optional[FolderWatcher] = None
        self.out_watcher: Optional[FolderWatcher] = None  # NEW: watch udp_output_root too

        self.udp: Optional[F1UDPListener] = None
        self._dedupe_mtime = {}  # src_path -> last_mtime_ns

        self._build_ui()
        self._retranslate_ui()

        # --- Data Health (MVP) ---
        # Store last known states for CSV / UDP / DB.
        self._health = {
            "csv": {"status": "—", "last_file": "—", "ts": "—", "err": ""},
            "udp": {
                "enabled": bool(self.cfg.udp_enabled),
                "port": int(self.cfg.udp_port),
                "src": str(getattr(self.cfg, "udp_source", "LIVE") or "LIVE").strip().upper(),
                "live_age_s": "—",
                "replay_age_s": "—",
            },
            "db": {"status": "—", "ts": "—", "err": ""},
        }
        self._refresh_health_panel()

        # --- Data Health ticking (UDP packet age) ---
        # UI timer: refresh UDP "age" a few times per second (cheap + smooth)
        self._health_timer = QtCore.QTimer(self)
        self._health_timer.setInterval(250)  # 4 Hz
        self._health_timer.timeout.connect(self._on_health_tick)
        self._health_timer.start()
        # -------------------------------------------

        # Log bleibt im File aktiv, aber kein UI-Debugfenster mehr.
        # (ui_sink=None => nur app.log, kein QPlainTextEdit-Spam)
        self.logger = AppLogger(ui_sink=None)
        self.logger.info("App started.")

        self._apply_cfg_to_ui()
        self._refresh_db_views()
        self._refresh_track_combo()
        self._start_services_if_possible()
        self._live_state: F1LiveState = F1LiveState()
        self.rain_engine = RainEngine()
        self.ms = MiniSectorTracker()


        self._your_last_lap_s = None
        self._your_last_tyre = None
        self._your_last_track = None

        self._udp_last_recorded_lastlap_ms = None

        # Debug throttle for live telemetry prints
        self._live_dbg_last_ts = 0.0


    # NOTE: Legacy implementation of _detect_session() (inline regex calls) was replaced
    # by precompiled regex constants (_RE_RACE/_RE_QUALI/_RE_PRACTICE) for readability and speed.
    # The logic is unchanged; see _detect_session() below.

    def _detect_session(self, src: Path) -> str:
        n = src.stem.lower()

        if _RE_RACE.search(n):
            return "R"

        if _RE_QUALI.search(n):
            return "Q"

        if _RE_PRACTICE.search(n):
            return "P"

        return ""

    def _session_label_from_udp(self, st: object) -> str:
        """
        Map UDP sessionType IDs (F1 20xx/25) to our coarse categories: P/Q/R/TT.
        We only need the category, not the exact session number.
        """
        try:
            v = int(st)
        except Exception:
            return ""

        # Common Codemasters sessionType mapping (coarse):
        # Practice: 1..4   (P1/P2/P3 + short/one-shot practice depending on year)
        # Quali:    5..9   (Q1/Q2/Q3 + short + one-shot)
        # Race:    10..11  (Race + Race2)
        # TT:      12      (Time Trial)
        if 1 <= v <= 4:
            return "P"
        if 5 <= v <= 9:
            return "Q"
        if 10 <= v <= 11:
            return "R"
        if v == 12:
            return "TT"

        return ""

    def _on_estimate_deg(self):
        track = self.cmbTrack.currentText().strip()
        tyre = self.cmbTyre.currentText().strip()
        if not track or not tyre:
            return

        thr = float(self.spinWearThr.value())
        race_laps = int(self.spinRaceLaps.value())

        rows_raw = laps_for_track(track, limit=5000)

        rows = []
        for r in rows_raw:
            rows.append(LapRow(
                created_at=r[0], session=r[1] or "", track=r[2] or "", tyre=r[3] or "",
                weather=r[4] or "", lap_time_s=r[5], fuel_load=r[6],
                wear_fl=r[7], wear_fr=r[8], wear_rl=r[9], wear_rr=r[10]
            ))

        est = estimate_degradation_for_track_tyre(rows, track=track, tyre=tyre, wear_threshold=thr)

        # If not enough data, show note and stop
        if est.predicted_laps_to_threshold is None:
            self.lblDeg.setText(f"{tyre} @ {track}\n{est.notes}")
            return

        # Pit window (1-stop)
        max_from_fresh = getattr(est, "max_stint_from_fresh_laps", None)
        pw = None
        if isinstance(max_from_fresh, (int, float)) and max_from_fresh > 0:
            pw = pit_window_one_stop(race_laps, max_from_fresh, min_stint_laps=5)

        pw2 = None
        if isinstance(max_from_fresh, (int, float)) and max_from_fresh > 0:
            pw2 = pit_windows_two_stop(race_laps, max_from_fresh, min_stint_laps=5)

        pit_txt = "pit window (1-stop): —"
        if pw is not None:
            pit_txt = f"pit window (1-stop): lap {pw[0]} – {pw[1]}"

        pit2_txt = "pit windows (2-stop): —"
        if pw2 is not None:
            pit2_txt = f"pit windows (2-stop): stop1 lap {pw2[0]} – {pw2[1]}, stop2 lap {pw2[2]} – {pw2[3]}"

        max_txt = ""
        if isinstance(max_from_fresh, (int, float)):
            max_txt = f"max stint to {thr:.0f}% ≈ {max_from_fresh:.1f} laps\n"

        self.lblDeg.setText(
            f"{tyre} @ {track}\n"
            f"n={est.n_laps_used} | wear/lap ≈ {est.wear_per_lap_pct:.2f}%\n"
            f"pace loss ≈ {est.pace_loss_per_pct_s:.3f}s per 1% wear\n"
            f"{max_txt}"
            f"{pit_txt}\n"
            f"{pit2_txt}\n"
            f"{est.notes}"
        )

    def _build_ui(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)

        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        # --- Tabs (reduces clutter massively) ---
        self.tabs = QtWidgets.QTabWidget()
        outer.addWidget(self.tabs, 1)

        # --- Data Health (MVP) ---
        # Read-only status strip that shows: CSV / UDP / DB health at a glance.
        self.healthFrame = QtWidgets.QFrame(root)
        self.healthFrame.setObjectName("healthFrame")
        self.healthFrame.setStyleSheet("""
            QFrame#healthFrame {
                background: rgba(18, 18, 18, 220);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 12px;
            }
            QLabel {
                color: rgba(255,255,255,210);
            }
        """)

        hf_lay = QtWidgets.QHBoxLayout(self.healthFrame)
        hf_lay.setContentsMargins(10, 6, 10, 6)
        hf_lay.setSpacing(14)

        # CSV status (left)
        self.lblHealthCsv = QtWidgets.QLabel("CSV: —", self.healthFrame)
        self.lblHealthCsv.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        hf_lay.addWidget(self.lblHealthCsv, 2)

        # UDP status (center)
        self.lblHealthUdp = QtWidgets.QLabel("UDP: —", self.healthFrame)
        self.lblHealthUdp.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        hf_lay.addWidget(self.lblHealthUdp, 1)

        # DB status (right)
        self.lblHealthDb = QtWidgets.QLabel("DB: —", self.healthFrame)
        self.lblHealthDb.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        hf_lay.addWidget(self.lblHealthDb, 1)

        outer.addWidget(self.healthFrame, 0)

        # =========================
        # TAB 1a: LIVE
        # =========================
        self.live_tab = LiveTabWidget(self.tr, parent=self)
        self.tabs.addTab(self.live_tab, self.tr.t("tab.live", "Live"))

        # =========================
        # TAB 1b: LIVE RAW
        # =========================
        self.live_raw_tab = LiveRawTabWidget(self.tr, parent=self)
        self.tabs.addTab(self.live_raw_tab, self.tr.t("tab.live_raw", "Live (Raw)"))

        # Expose widgets under the SAME names MainWindow logic already uses
        # IMPORTANT: Bind existing logic to LIVE RAW, because LIVE (GUI) is WIP/placeholder.
        self.grpLive = self.live_raw_tab.grpLive
        self.lblSC = self.live_raw_tab.lblSC
        self.lblWeather = self.live_raw_tab.lblWeather
        self.lblRain = self.live_raw_tab.lblRain
        self.lblRainAdvice = self.live_raw_tab.lblRainAdvice
        self.lblFieldShare = self.live_raw_tab.lblFieldShare
        self.lblFieldDelta = self.live_raw_tab.lblFieldDelta

        self.grpStrat = self.live_raw_tab.grpStrat
        self.cardWidgets = self.live_raw_tab.cardWidgets

        self.grpMini = self.live_raw_tab.grpMini
        self.tblMini = self.live_raw_tab.tblMini
        self.lblTheoLast = self.live_raw_tab.lblTheoLast
        self.lblTheoPB = self.live_raw_tab.lblTheoPB
        self.lblTheoBest = self.live_raw_tab.lblTheoBest
        self.lblTheoMiss = self.live_raw_tab.lblTheoMiss

        # =========================
        # TAB 2: LAPS / DB
        # =========================
        self.db_tab = DbTabWidget(self.tr, on_refresh_clicked=self._refresh_db_views, parent=self)
        self.tabs.addTab(self.db_tab, "Laps / DB")

        self.btnRefreshDb = self.db_tab.btnRefreshDb
        self.tbl = self.db_tab.tbl

        # =========================
        # TAB 3: MODEL
        # =========================
        self.model_tab = ModelTabWidget(self.tr, on_estimate_clicked=self._on_estimate_deg, parent=self)
        self.tabs.addTab(self.model_tab, "Model")

        self.grpDeg = self.model_tab.grpDeg
        self.cmbTrack = self.model_tab.cmbTrack
        self.cmbTyre = self.model_tab.cmbTyre
        self.spinRaceLaps = self.model_tab.spinRaceLaps
        self.spinWearThr = self.model_tab.spinWearThr
        self.btnDeg = self.model_tab.btnDeg
        self.lblDeg = self.model_tab.lblDeg

        # =========================
        # TAB 4: SETTINGS
        # =========================
        self.settings_tab = SettingsTabWidget(
            self.tr,
            self.cfg,
            on_pick_folder=self.pick_folder,
            on_pick_output_folder=self.pick_output_folder,
            on_apply_settings=self.apply_settings,
            on_apply_language=self.apply_language,
            parent=self
        )
        self.tabs.addTab(self.settings_tab, self.tr.t("tab.settings", "Settings"))

        # expose settings controls used by existing logic
        self.grpLang = self.settings_tab.grpLang
        self.lblLang = self.settings_tab.lblLang
        self.cmbLang = self.settings_tab.cmbLang
        self.btnApplyLang = self.settings_tab.btnApplyLang

        self.lblFolder = self.settings_tab.lblFolder
        self.btnPick = self.settings_tab.btnPick
        self.lblOutFolder = self.settings_tab.lblOutFolder
        self.btnPickOut = self.settings_tab.btnPickOut

        self.chkUdpWriteLaps = self.settings_tab.chkUdpWriteLaps
        self.chkUdp = self.settings_tab.chkUdp
        self.spinPort = self.settings_tab.spinPort
        self.btnApply = self.settings_tab.btnApply

        # Status bar (kept)
        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)

    def _health_now_str(self) -> str:
        """Human-readable timestamp for the health panel (local time)."""
        try:
            return datetime.now().strftime("%H:%M:%S")
        except Exception:
            return "—"

    def _refresh_health_panel(self) -> None:
        """
        Render the current health dict into the 3 labels.
        Keep it robust: never crash the UI because of health formatting.
        """
        try:
            h = getattr(self, "_health", {}) or {}

            csv_h = h.get("csv", {})
            udp_h = h.get("udp", {})
            db_h = h.get("db", {})

            # CSV
            csv_status = csv_h.get("status", "—")
            csv_file = csv_h.get("last_file", "—")
            csv_ts = csv_h.get("ts", "—")
            csv_err = csv_h.get("err", "")
            csv_tail = f" | {csv_err}" if csv_err else ""
            self.lblHealthCsv.setText(f"CSV: {csv_status} | {csv_ts} | {csv_file}{csv_tail}")

            # UDP (packet age comes in next step)
            udp_enabled = udp_h.get("enabled", False)
            udp_port = udp_h.get("port", "—")
            udp_src = udp_h.get("src", "LIVE")

            live_age = udp_h.get("live_age_s", "—")
            replay_age = udp_h.get("replay_age_s", "—")

            # getrennt labeln: LIVE vs REPLAY
            self.lblHealthUdp.setText(
                f"UDP: {'ON' if udp_enabled else 'OFF'} | src {udp_src} | port {udp_port} | "
                f"LIVE age {live_age} | REPLAY age {replay_age}"
            )

            # DB
            db_status = db_h.get("status", "—")
            db_ts = db_h.get("ts", "—")
            db_err = db_h.get("err", "")
            db_tail = f" | {db_err}" if db_err else ""
            self.lblHealthDb.setText(f"DB: {db_status} | {db_ts}{db_tail}")

        except Exception:
            # Worst case: show something minimal
            try:
                self.lblHealthCsv.setText("CSV: —")
                self.lblHealthUdp.setText("UDP: —")
                self.lblHealthDb.setText("DB: —")
            except Exception:
                pass

    def _on_health_tick(self) -> None:
        """
        Periodic UI tick to update 'UDP packet age' in the health panel.
        Must never crash the UI.
        """
        try:
            h = getattr(self, "_health", None)
            if not isinstance(h, dict):
                return

            udp_h = h.get("udp", {})
            if not isinstance(udp_h, dict):
                udp_h = {}
                h["udp"] = udp_h

            # Keep enabled/port in sync with current config
            udp_h["enabled"] = bool(getattr(self.cfg, "udp_enabled", False))
            udp_h["port"] = int(getattr(self.cfg, "udp_port", 0) or 0)
            udp_h["src"] = str(getattr(self.cfg, "udp_source", "LIVE") or "LIVE").strip().upper()

            # If UDP service isn't running yet, show "—" (IMPORTANT: set the fields the panel actually renders)
            if (not udp_h["enabled"]) or (self.udp is None):
                udp_h["live_age_s"] = "—"
                udp_h["replay_age_s"] = "—"
                self._refresh_health_panel()
                return

            # Get ages separately (LIVE vs REPLAY). Robust fallback if older listener version is running.
            live_age = None
            replay_age = None

            try:
                if hasattr(self.udp, "get_last_live_packet_age_s"):
                    live_age = self.udp.get_last_live_packet_age_s()
            except Exception:
                live_age = None

            try:
                if hasattr(self.udp, "get_last_replay_packet_age_s"):
                    replay_age = self.udp.get_last_replay_packet_age_s()
            except Exception:
                replay_age = None

            # Optional fallback: very old API (single age)
            if live_age is None and replay_age is None:
                try:
                    if hasattr(self.udp, "get_last_packet_age_s"):
                        live_age = self.udp.get_last_packet_age_s()
                except Exception:
                    live_age = None

            udp_h["live_age_s"] = "—" if live_age is None else f"{float(live_age):.2f}s"
            udp_h["replay_age_s"] = "—" if replay_age is None else f"{float(replay_age):.2f}s"

            self._refresh_health_panel()

        except Exception:
            pass

    def _apply_cfg_to_ui(self):
        # delegated to SettingsTabWidget (keeps behavior identical, just moved)
        self.settings_tab.apply_cfg_to_ui()

    def pick_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select telemetry root folder")
        if d:
            self.cfg.telemetry_root = d
            self._apply_cfg_to_ui()

    def pick_output_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select data output folder")
        if d:
            self.cfg.udp_output_root = d
            self._apply_cfg_to_ui()

    def apply_settings(self):
        self.cfg.udp_enabled = self.chkUdp.isChecked()
        self.cfg.udp_port = int(self.spinPort.value())
        self.cfg.udp_write_csv_laps = self.chkUdpWriteLaps.isChecked()
        save_config(self.cfg)

        # Keep Health panel in sync immediately (port/enabled)
        try:
            self._health["udp"]["enabled"] = bool(self.cfg.udp_enabled)
            self._health["udp"]["port"] = int(self.cfg.udp_port)
            self._health["udp"]["src"] = str(getattr(self.cfg, "udp_source", "LIVE") or "LIVE").strip().upper()
        except Exception:
            pass

        self._refresh_health_panel()

        self.status.showMessage("Settings saved. Restarting services…", 3000)
        self._restart_services()

    def apply_language(self):
        new_lang = self.cmbLang.currentData() or "en"
        if not new_lang:
            return

        # persist
        self.cfg.language = new_lang
        save_config(self.cfg)

        # reload translator + update UI texts
        try:
            self.tr.load_language(new_lang)
        except Exception:
            # fallback to English if file missing/broken
            self.tr.load_language("en")
            self.cfg.language = "en"
            save_config(self.cfg)

        self._retranslate_ui()
        self.status.showMessage(self.tr.t("msg.language_applied", "Language applied."), 2500)

    def _retranslate_ui(self):
        # Window title
        self.setWindowTitle(self.tr.t("app.title", "SimRaceStrategist – Prototype"))

        # Tab titles
        self.tabs.setTabText(0, self.tr.t("tab.live", "Live"))
        self.tabs.setTabText(1, self.tr.t("tab.live_raw", "Live (Raw)"))
        self.tabs.setTabText(2, self.tr.t("tab.db", "Laps / DB"))
        self.tabs.setTabText(3, self.tr.t("tab.model", "Model"))
        self.tabs.setTabText(4, self.tr.t("tab.settings", "Settings"))

        # Group titles
        self.grpLive.setTitle(self.tr.t("live.group_title", "Live (F1 UDP)"))
        self.grpStrat.setTitle(self.tr.t("cards.group_title", "Strategy Cards (Prototype)"))
        self.grpDeg.setTitle(self.tr.t("model.deg_group", "Degradation model"))

        # DB tab
        self.btnRefreshDb.setText(self.tr.t("db.refresh", "Refresh"))

        # Model tab labels/button
        # NOTE: these are the static labels inside the layout (created inline).
        # For minimal change: only update the button + placeholder label.
        self.btnDeg.setText(self.tr.t("model.estimate_btn", "Estimate degradation + pit windows"))
        if self.lblDeg.text().strip() == "—":
            self.lblDeg.setText(self.tr.t("common.placeholder", "—"))

        # Settings tab
        self.btnPick.setText(self.tr.t("settings.pick_folder", "Pick Folder…"))
        self.chkUdp.setText(self.tr.t("settings.udp_enabled", "UDP enabled (F1 SC/Wetter)"))
        self.btnApply.setText(self.tr.t("settings.apply", "Apply Settings"))

        # Language group (if exists)
        if hasattr(self, "grpLang"):
            self.grpLang.setTitle(self.tr.t("settings.language_group", "Language"))
        if hasattr(self, "lblLang"):
            self.lblLang.setText(self.tr.t("settings.language_label", "Language"))
        if hasattr(self, "btnApplyLang"):
            self.btnApplyLang.setText(self.tr.t("settings.language_apply", "Apply language"))

        # Live labels: set defaults (actual content updated by _update_live_labels)
        self.lblSC.setText(self.tr.t("live.sc_na", "SC/VSC: n/a"))
        self.lblWeather.setText(self.tr.t("live.weather_na", "Weather: n/a"))
        self.lblRain.setText(self.tr.t("live.rain_na", "Rain(next): n/a"))
        self.lblRainAdvice.setText(self.tr.t("live.rain_pit_na", "Rain pit: n/a"))
        self.lblFieldShare.setText(self.tr.t("live.field_share_na", "Field: Inter/Wet share: n/a"))
        self.lblFieldDelta.setText(self.tr.t("live.field_delta_na", "Field: Δpace (I-S): n/a"))

        # Let the split tab widgets update their internal static labels too
        try:
            self.live_tab.retranslate()
        except Exception:
            pass
        try:
            self.live_raw_tab.retranslate()
        except Exception:
            pass
        try:
            self.db_tab.retranslate()
        except Exception:
            pass
        try:
            self.model_tab.retranslate()
        except Exception:
            pass
        try:
            self.settings_tab.retranslate()
        except Exception:
            pass

    def _tr_keep(self, key: str, fallback: str) -> str:
        """
        Translate via JSON. If key does not exist, fallback is used.
        Use this for motorsport terms too (fallback may be English).
        """
        return self.tr.t(key, fallback)

    def _tr_action(self, action: str) -> str:
        """
        Maps engine action strings to i18n keys.
        Example: "STAY OUT" -> action.STAY_OUT
        If you want EN words in DE as well, set DE translation identical.
        """
        if not action:
            return self._tr_keep("common.na", "n/a")

        norm = action.strip().upper()
        # normalize spaces to underscore for keys
        key = "action." + "_".join(norm.split())
        return self._tr_keep(key, action)

    def _tr_tyre(self, tyre: str) -> str:
        """
        Maps tyre labels to i18n keys.
        Example: "INTER" -> tyre.INTER
        """
        if not tyre:
            return self._tr_keep("common.na", "n/a")

        norm = tyre.strip().upper()
        key = f"tyre.{norm}"
        return self._tr_keep(key, tyre)


    def _restart_services(self):
        self._stop_services()
        self._start_services_if_possible()

    def _start_services_if_possible(self):
        # watcher 1: telemetry_root (Iko/Overtake CSVs)
        if self.cfg.telemetry_root:
            root = Path(self.cfg.telemetry_root)
            if root.exists():
                self.watcher = FolderWatcher(root, self._on_new_csv)
                self.watcher.start()
                self.status.showMessage("Telemetry watcher started.", 2500)
            else:
                self.status.showMessage("Telemetry root folder does not exist.", 4000)

        # watcher 2: udp_output_root (your own UDP lap CSVs)
        out_root = (getattr(self.cfg, "udp_output_root", "") or "").strip()
        if out_root:
            out = Path(out_root)
            if out.exists():
                self.out_watcher = FolderWatcher(out, self._on_new_csv)
                self.out_watcher.start()
                self.status.showMessage("Output watcher started.", 2500)
            else:
                self.status.showMessage("Output folder does not exist.", 4000)

        # udp
        if self.cfg.udp_enabled:
            src = str(getattr(self.cfg, "udp_source", "LIVE") or "LIVE").strip().upper()
            replay_file = str(getattr(self.cfg, "udp_replay_file", "") or "").strip()

            if src == "REPLAY" and replay_file:
                speed = float(getattr(self.cfg, "udp_replay_speed", 1.0) or 1.0)
                self.udp = F1UDPReplayListener(replay_file, self._on_live_state, speed=speed, debug=self.cfg.udp_debug)
            else:
                self.udp = F1UDPListener(self.cfg.udp_port, self._on_live_state, debug=self.cfg.udp_debug)

            self.udp.start()

    def _stop_services(self):
        if self.watcher:
            try:
                self.watcher.stop()
            except Exception:
                pass
            self.watcher = None

        if self.out_watcher:
            try:
                self.out_watcher.stop()
            except Exception:
                pass
            self.out_watcher = None
        if self.udp:
            try: self.udp.stop()
            except Exception: pass
            self.udp = None

    @QtCore.Slot(object)
    def _on_live_state(self, state: F1LiveState):
        self._live_state = state

        # NEW: optional UDP lap recorder (writes lap summaries into DB, does NOT touch CSV import path)
        try:
            if getattr(self.cfg, "udp_record_laps", False):
                self._maybe_record_udp_lap(state)
        except Exception:
            pass

        # --- DEV DEBUG (throttled) ---
        # Prints track length + player lap distance every ~0.5s if udp_debug is enabled.
        try:
            if getattr(self.cfg, "udp_debug", False):
                now = QtCore.QDateTime.currentMSecsSinceEpoch() / 1000.0
                if (now - getattr(self, "_live_dbg_last_ts", 0.0)) >= 0.5:
                    self._live_dbg_last_ts = now

                    tl = getattr(state, "track_length_m", None)
                    ld = getattr(state, "player_lap_distance_m", None)
                    clt = getattr(state, "player_current_lap_time_ms", None)
                    llt = getattr(state, "player_last_lap_time_ms", None)

                    print(
                        "[LIVE DBG]",
                        f"track_len_m={tl}",
                        f"lap_dist_m={None if ld is None else round(float(ld), 1)}",
                        f"curLap_ms={clt}",
                        f"lastLap_ms={llt}",
                    )
        except Exception:
            pass

        # Minisector update (purely additive; uses existing state fields)
        try:
            changed = self.ms.update(
                lap_dist_m=getattr(state, "player_lap_distance_m", None),
                cur_lap_time_ms=getattr(state, "player_current_lap_time_ms", None),
                cur_lap_num=getattr(state, "player_current_lap_num", None),
                track_len_m=getattr(state, "track_length_m", None),
                sector2_start_m=getattr(state, "sector2_start_m", None),
                sector3_start_m=getattr(state, "sector3_start_m", None),
                allow_sector_fallback=(getattr(state, "game_year", None) == 20),
            )

            if changed:
                QtCore.QMetaObject.invokeMethod(self, "_update_minisector_table", QtCore.Qt.QueuedConnection)

            # NEW (additive): write completed minisector laps to CSV
            try:
                completed = self.ms.pop_completed_laps()
                for lap in completed:
                    self._append_udp_minisector_lap_csv(state, lap)
            except Exception:
                pass

        except Exception:
            pass

        # Update LIVE (Raw) labels (existing behavior)
        QtCore.QMetaObject.invokeMethod(
            self,
            "_update_live_labels",
            QtCore.Qt.QueuedConnection,
        )

        # NEW (additive): also feed the new Live (GUI) tab in parallel
        # IMPORTANT: do NOT call QWidget methods directly from UDP thread!
        # Use a Qt Signal -> queued to UI thread.
        try:
            if hasattr(self, "live_tab") and self.live_tab is not None:
                self.live_tab.stateReceived.emit(state)
        except Exception:
            pass

    def _maybe_record_udp_lap(self, state: F1LiveState) -> None:
        """
        Record a lap summary into DB when UDP reports a new lastLapTime.
        This is additive and runs in parallel to Overtake CSV import.
        """
        last_ms = getattr(state, "player_last_lap_time_ms", None)
        cur_lap_num = getattr(state, "player_current_lap_num", None)

        # We only record when last lap time is known and plausible
        if last_ms is None:
            return
        try:
            last_ms = int(last_ms)
        except Exception:
            return
        if last_ms <= 0 or last_ms > 10_000_000:  # > ~2h 46m => ignore
            return

        # Deduplicate: same last lap time should only be recorded once
        if getattr(self, "_udp_last_recorded_lastlap_ms", None) == last_ms:
            return

        # Lap number: when crossing the line, currentLapNum increments,
        # so last lap is usually currentLapNum - 1
        lap_n = None
        try:
            if cur_lap_num is not None:
                cn = int(cur_lap_num)
                if cn > 0:
                    lap_n = cn - 1
        except Exception:
            lap_n = None

        sess_uid = getattr(state, "session_uid", None) or "nosess"
        # track_id = getattr(state, "track_id", None)

        # Track label: prefer pretty names, fallback to TrackId:<n>
        track_label = track_label_from_id(getattr(state, "track_id", None))

        # Weather label (UDP enum)
        weather_enum = getattr(state, "weather", None)
        weather_map = {
            0: "Clear",
            1: "Light cloud",
            2: "Overcast",
            3: "Light rain",
            4: "Heavy rain",
            5: "Storm",
        }
        weather_label = weather_map.get(int(weather_enum), "Unknown") if weather_enum is not None else "Unknown"

        # Coarse class for rain engine: SLICK/INTER/WET
        tyre_class = (getattr(state, "player_tyre_cat", None) or "").upper().strip()
        if tyre_class not in ("SLICK", "INTER", "WET"):
            tyre_class = ""

        # Exact label for DB/strategy: C1..C6 for slicks, else INTER/WET.
        tyre_label = (getattr(state, "player_tyre_compound", None) or "").upper().strip()
        if not tyre_label:
            tyre_label = tyre_class

        # Session type from UDP (coarse P/Q/R/TT). Keep old fallback to "R".
        session_label = self._session_label_from_udp(getattr(state, "session_type_id", None)) or "R"

        lap_time_s = float(last_ms) / 1000.0

        # IMPORTANT: source_file must be unique
        source = f"udp://{sess_uid}/lap{lap_n if lap_n is not None else 'x'}/t{last_ms}"

        # Game label best-effort (keeps old behavior if unknown)
        gy = getattr(state, "game_year", None)
        game_label = "F1 25"
        try:
            if int(gy) == 20:
                game_label = "F1 2020"
        except Exception:
            pass

        summ = {
            "game": game_label,
            "track": track_label,
            "session": session_label,
            "session_uid": str(sess_uid),
            "weather": weather_label,
            "tyre": tyre_label,
            "lap_time_s": lap_time_s,

            # NEW (additive): pulled from UDP state if available
            "fuel_load": getattr(state, "player_fuel_in_tank", None),

            "wear_fl": getattr(state, "player_wear_fl", None),
            "wear_fr": getattr(state, "player_wear_fr", None),
            "wear_rl": getattr(state, "player_wear_rl", None),
            "wear_rr": getattr(state, "player_wear_rr", None),
        }

        upsert_lap(source, summ)
        self._udp_last_recorded_lastlap_ms = last_ms

        # Optional: persistent CSV output (standalone)
        try:
            if bool(getattr(self.cfg, "udp_write_csv_laps", False)):
                self._append_udp_lap_csv({
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "game": "F1 25",
                    "track": track_label,
                    "session": session_label,
                    "session_uid": str(sess_uid),
                    "lap_n": lap_n if lap_n is not None else "",
                    "lap_time_s": lap_time_s,
                    "tyre": tyre_label,
                    "weather": weather_label,
                    "source": source,
                })
        except Exception:
            pass


        # Update UI/DB views like the CSV path does
        QtCore.QMetaObject.invokeMethod(self, "_after_db_update", QtCore.Qt.QueuedConnection)

    def _safe_token(self, s: object, fallback: str = "NA") -> str:
        """
        Make a filename-safe token: letters/numbers/_/-. only.
        """
        try:
            t = str(s) if s is not None else ""
        except Exception:
            t = ""
        t = t.strip()
        if not t:
            t = fallback
        # Replace illegal filename chars with "_"
        t = re.sub(r"[^A-Za-z0-9_\-\.]+", "_", t)
        # Avoid absurd length
        return t[:80]

    def _fmt_laptime_token(self, lap_time_ms: int | None) -> str:
        """
        Token for filename: use milliseconds as integer (stable, no ':' issues).
        """
        if lap_time_ms is None:
            return "NA"
        try:
            return str(int(lap_time_ms))
        except Exception:
            return "NA"

    def _append_udp_minisector_lap_csv(self, state: F1LiveState, lap: dict) -> None:
        """
        One file per completed lap (your custom format):

        Folder: <output_root>/<Track>/
        Filename: <session>_<team>_lap<lap>_t<laptime_ms>_<timestamp>.csv

        CSV contains ONE row (wide format) with metadata + MS01..MS30.

        Additive + safe:
        - Does not affect Iko/Overtake CSV import.
        - Missing values are written as empty cells.
        """
        try:
            out_root = (getattr(self.cfg, "udp_output_root", "") or "").strip()
            if not out_root:
                return

            base = Path(out_root)
            base.mkdir(parents=True, exist_ok=True)

            # ---- track folder ----
            track_id = getattr(state, "track_id", None)
            track_label = track_label_from_id(track_id)
            track_dir = base / self._safe_token(track_label, fallback="UnknownTrack")
            track_dir.mkdir(parents=True, exist_ok=True)

            # ---- session label (P/Q/R/TT) from UDP session_type_id ----
            session_label = self._session_label_from_udp(getattr(state, "session_type_id", None))

            # ---- team ----
            team = getattr(state, "player_team_name", None)
            if not team:
                tid = getattr(state, "player_team_id", None)
                team = f"TEAM{int(tid)}" if tid is not None else "UNK"

            lap_num = lap.get("lap_num")
            lap_time_ms = lap.get("lap_time_ms")

            ts_utc = datetime.now(timezone.utc)
            ts_token = ts_utc.strftime("%Y%m%dT%H%M%SZ")

            fn = (
                f"{self._safe_token(session_label or 'NA')}_"
                f"{self._safe_token(team)}_"
                f"lap{self._safe_token(lap_num)}_"
                f"t{self._fmt_laptime_token(lap_time_ms)}_"
                f"{ts_token}.csv"
            )
            out_path = track_dir / fn

            # ---- sector times (best-effort) ----
            s1 = getattr(state, "player_sector1_time_ms", None)
            s2 = getattr(state, "player_sector2_time_ms", None)
            s3 = None
            try:
                if lap_time_ms is not None and s1 is not None and s2 is not None:
                    s3 = int(lap_time_ms) - int(s1) - int(s2)
            except Exception:
                s3 = None

            # ---- other metadata ----
            sess_uid = getattr(state, "session_uid", None) or "nosess"
            # Write exact compound label into CSV for later strategy work.
            # Keep class separately to not break rain logic / debugging.
            tyre_class = getattr(state, "player_tyre_cat", None)
            tyre = getattr(state, "player_tyre_compound", None) or tyre_class
            weather = getattr(state, "weather", None)

            rain_now = getattr(state, "rain_now_pct", None)
            rain_fc = getattr(state, "rain_fc_pct", None)

            sc = getattr(state, "safety_car_status", None)
            track_flag = getattr(state, "track_flag", None)
            player_flag = getattr(state, "player_fia_flag", None)

            # ---- fuel + wear (NEW) ----
            fuel_in_tank = getattr(state, "player_fuel_in_tank", None)
            fuel_remaining_laps = getattr(state, "player_fuel_remaining_laps", None)

            wear_fl = getattr(state, "player_wear_fl", None)
            wear_fr = getattr(state, "player_wear_fr", None)
            wear_rl = getattr(state, "player_wear_rl", None)
            wear_rr = getattr(state, "player_wear_rr", None)

            # ---- minisector completion ----
            complete = int(bool(lap.get("complete")))
            missing = lap.get("missing") or []
            missing_str = ",".join(str(x) for x in missing)

            minis = (lap.get("minis") or [])
            total_minis = int(lap.get("total_minis") or 30)

            ms_map: dict[int, int | str] = {}
            minis_sum = 0
            ms01_estimated = 0

            for m in minis:
                no = m.get("ms_no")
                v = m.get("split_ms")
                est = bool(m.get("estimated"))

                if no is None:
                    continue

                if v is None:
                    ms_map[int(no)] = ""
                else:
                    iv = int(v)

                    # Mark only MS01 with '*' (visual hint in CSV).
                    if est and int(no) == 1:
                        ms01_estimated = 1
                        ms_map[int(no)] = f"{iv}*"
                    else:
                        ms_map[int(no)] = iv

                    minis_sum += iv

            ms_cols = [f"MS{n:02d}_ms" for n in range(1, total_minis + 1)]

            # ---- game label (best-effort) ----
            game_label = "F1 25"
            try:
                if int(getattr(state, "game_year", 25)) == 20:
                    game_label = "F1 2020"
            except Exception:
                pass

            cols = [
                "created_at_utc",
                "game",
                "session",
                "session_uid",
                "team",
                "track_id",
                "track",
                "lap_num",
                "lap_time_ms",
                "sector1_ms",
                "sector2_ms",
                "sector3_ms",
                "tyre_cat",
                "tyre_class",
                "weather_enum",
                "rain_now_pct",
                "rain_fc_pct",
                "safety_car_status",
                "track_flag",
                "player_fia_flag",

                "fuel_in_tank",
                "fuel_remaining_laps",
                "wear_fl_pct",
                "wear_fr_pct",
                "wear_rl_pct",
                "wear_rr_pct",
                "ms01_estimated",
                "complete",
                "missing_minisectors",
                "minis_sum_ms",
                *ms_cols,
            ]

            row = [
                ts_utc.isoformat(timespec="seconds"),
                game_label,
                session_label,
                str(sess_uid),
                str(team),
                "" if track_id is None else int(track_id),
                track_label,
                "" if lap_num is None else int(lap_num),
                "" if lap_time_ms is None else int(lap_time_ms),
                "" if s1 is None else int(s1),
                "" if s2 is None else int(s2),
                "" if s3 is None else int(s3),
                "" if tyre is None else str(tyre),
                "" if weather is None else int(weather),
                "" if weather is None else int(weather),
                "" if rain_now is None else int(rain_now),
                "" if rain_fc is None else int(rain_fc),
                "" if sc is None else int(sc),
                "" if track_flag is None else int(track_flag),
                "" if player_flag is None else int(player_flag),

                # NEW:
                "" if fuel_in_tank is None else float(fuel_in_tank),
                "" if fuel_remaining_laps is None else float(fuel_remaining_laps),
                "" if wear_fl is None else float(wear_fl),
                "" if wear_fr is None else float(wear_fr),
                "" if wear_rl is None else float(wear_rl),
                "" if wear_rr is None else float(wear_rr),

                complete,
                missing_str,
                minis_sum,
            ]

            for n in range(1, total_minis + 1):
                row.append(ms_map.get(n, ""))

            with out_path.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(cols)
                w.writerow(row)

        except Exception:
            # Never break live loop
            pass

    @QtCore.Slot()
    def _update_live_labels(self):
        try:
            # WIP/DEBUG UI:
            # This method formats near-raw telemetry + heuristic outputs into human-readable text.
            # The displayed values/format are not stable APIs and may change as the strategy logic matures.
            state = getattr(self, "_live_state", F1LiveState())

            sc = -1 if state.safety_car_status is None else int(state.safety_car_status)
            weather = -1 if state.weather is None else int(state.weather)

            rain_fc = getattr(state, "rain_fc_pct", None)
            rain_now = getattr(state, "rain_now_pct", None)

            rain_fc_i = -1 if rain_fc is None else int(rain_fc)
            rain_now_i = -1 if rain_now is None else int(rain_now)

            # --- SC/VSC ---
            sc_text = {
                0: self.tr.t("live.sc_green", "Green"),
                1: self.tr.t("live.sc_sc", "Safety Car"),
                2: self.tr.t("live.sc_vsc", "VSC"),
                3: self.tr.t("live.sc_formation", "Formation"),
            }.get(sc, "–")

            def _flag_text(v: int | None) -> str:
                if v is None:
                    return self.tr.t("common.na", "n/a")
                return {
                    -1: self.tr.t("common.na", "n/a"),
                    0: self.tr.t("live.flag_none", "None"),
                    1: self.tr.t("live.flag_green", "Green"),
                    2: self.tr.t("live.flag_blue", "Blue"),
                    3: self.tr.t("live.flag_yellow", "Yellow"),
                }.get(int(v), f"{self.tr.t('live.flag_unknown', 'Unknown')}({int(v)})")

            track_flag = getattr(state, "track_flag", None)
            player_flag = getattr(state, "player_fia_flag", None)

            flags_suffix = self.tr.t(
                "live.flags_short_fmt",
                " | Flags: Track={track} You={you}"
            ).format(track=_flag_text(track_flag), you=_flag_text(player_flag))

            sc_line = self.tr.t("live.sc_fmt", "SC/VSC: {status}").format(status=sc_text) + flags_suffix
            if self.lblSC.text() != sc_line:
                self.lblSC.setText(sc_line)

            # --- Weather as human-readable text (0..5 -> label) ---
            def _weather_text(w: int | None) -> str:
                if w is None or int(w) < 0:
                    return self.tr.t("common.na", "n/a")
                try:
                    wi = int(w)
                except Exception:
                    return self.tr.t("common.na", "n/a")

                return {
                    0: self.tr.t("live.weather_clear", "Clear Skies"),
                    1: self.tr.t("live.weather_light_cloud", "Light Cloud"),
                    2: self.tr.t("live.weather_overcast", "Overcast"),
                    3: self.tr.t("live.weather_light_rain", "Light Rain"),
                    4: self.tr.t("live.weather_heavy_rain", "Heavy Rain"),
                    5: self.tr.t("live.weather_storm", "Storm"),
                }.get(wi, f"{self.tr.t('live.weather_unknown', 'Unknown')}({wi})")

            weather_txt = _weather_text(weather if isinstance(weather, int) else None)

            # optional: keep enum for debugging (can remove if you want it clean)
            if isinstance(weather, int) and weather >= 0:
                weather_txt = f"{weather_txt} (enum:{weather})"

            weather_line = self.tr.t("live.weather_fmt", "Weather: {w}").format(w=weather_txt)
            if self.lblWeather.text() != weather_line:
                self.lblWeather.setText(weather_line)

            # --- Forecast helper: stepwise lookup for forecast values at given horizons (minutes).
            # Only used for display text, not for final decision logic.
            def _fc_at(series, tmin):
                """
                Stepwise forecast lookup:
                returns the rain% of the nearest sample with timeOffset >= tmin.
                If all samples are < tmin, return the last available sample.
                """
                if not series:
                    return None

                # series: list[(min_from_now, rain_pct, weather_enum)] sorted by time
                best = None
                for tup in series:
                    try:
                        tm, pct, _w = tup
                    except Exception:
                        continue

                    try:
                        tm_i = int(tm)
                        pct_i = int(pct)
                    except Exception:
                        continue

                    # first sample at/after horizon
                    if tm_i >= int(tmin):
                        best = pct_i
                        break

                if best is not None:
                    return best

                # horizon beyond last sample -> last known
                try:
                    return int(series[-1][1])
                except Exception:
                    return None

            fc = getattr(state, "rain_fc_series", None) or []
            fc_list = []
            for t in (3, 5, 10, 15, 20):
                v = _fc_at(fc, t)
                if v is None:
                    fc_list.append(self.tr.t("common.na", "n/a"))
                else:
                    try:
                        fc_list.append(str(int(v)))
                    except Exception:
                        fc_list.append(self.tr.t("common.na", "n/a"))
            fc_txt = "/".join(fc_list)

            rain_now_txt = (rain_now_i if rain_now_i >= 0 else self.tr.t("common.na", "n/a"))
            rain_line = self.tr.t(
                "live.rain_line_fmt",
                "Rain: {now} | FC(3/5/10/15/20): {fc}"
            ).format(now=rain_now_txt, fc=fc_txt)
            if self.lblRain.text() != rain_line:
                self.lblRain.setText(rain_line)

            # --- Rain pit advice (WIP) ---
            try:
                rn = float(rain_fc_i) if rain_fc_i >= 0 else 0.0
            except Exception:
                rn = 0.0

            # Use LIVE tyre from UDP if available (prevents "Slicks unsafe" when you're actually on WET/INTER).
            current_tyre = None
            try:
                cat = (getattr(state, "player_tyre_cat", None) or "").upper().strip()  # "SLICK"/"INTER"/"WET"
                if cat in ("SLICK", "INTER", "WET"):
                    # Rain logic expects labels like "SLICK"/"INTER"/"WET"
                    current_tyre = cat
            except Exception:
                current_tyre = None

            # Fallback: UI selection (Model tab) if live value missing
            if not current_tyre:
                try:
                    current_tyre = self.cmbTyre.currentText()
                except Exception:
                    current_tyre = ""

            # --- fallbacks (weil die spinBoxes aktuell nicht existieren) ---
            laps_remaining = 25
            pit_loss_s = 20.0

            # best-effort: wenn du spinRaceLaps hast, nimm den als groben Placeholder
            if hasattr(self, "spinRaceLaps"):
                try:
                    laps_remaining = int(self.spinRaceLaps.value())
                except Exception:
                    pass

            # track name for DB lookup
            track = ""
            if hasattr(self, "cmbTrack"):
                try:
                    track = (self.cmbTrack.currentText() or "").strip()
                except Exception:
                    track = ""

            # DB rows: nicht jedes UI-Tick neu laden -> simple cache
            db_rows_list = None
            try:
                if track:
                    if getattr(self, "_db_cache_track", None) != track:
                        self._db_cache_track = track
                        self._db_cache_rows = laps_for_track(track, limit=5000)
                    db_rows_list = getattr(self, "_db_cache_rows", None)
            except Exception:
                db_rows_list = None

            # NEW RainEngine API
            out = self.rain_engine.update(
                state,
                track=track or "UNKNOWN",
                current_tyre=current_tyre,
                laps_remaining=laps_remaining,
                pit_loss_s=pit_loss_s,
                db_rows=db_rows_list,
                your_last_lap_s=self._your_last_lap_s,
            )

            # WIP/ADVISORY UI: heuristic suggestion only (no forced decisions).
            ad = out.advice

            action_ui = self._tr_action(ad.action)
            target_ui = self._tr_tyre(ad.target_tyre) if ad.target_tyre else self._tr_keep("common.na", "n/a")

            # NOTE: ad.reason is currently free text from the engine.
            # It is shown as-is (debug/heuristic transparency). If you want this fully localized,
            # we can move to reason_key + params later without breaking existing behavior.
            advice_line = self.tr.t(
                "live.rain_pit_advice_fmt",
                "Rain pit: {action} → {target} | wet={wet:.2f} conf={conf:.2f} | {reason}"
            ).format(
                action=action_ui,
                target=target_ui,
                wet=out.wetness,
                conf=out.confidence,
                reason=ad.reason
            )

            if self.lblRainAdvice.text() != advice_line:
                self.lblRainAdvice.setText(advice_line)

            # WIP/DEBUG UI: verbose internal diagnostics (can be noisy; shown for dev visibility).
            self.status.showMessage(out.debug)

            # --- Field signals (share + pace deltas) ---
            if state.inter_share is None or state.inter_count is None or state.slick_count is None:
                share_line = self.tr.t("live.field_share_na", "Field: Inter/Wet share: n/a")

            else:
                total = int(getattr(state, "field_total_cars", (state.inter_count + state.slick_count)))
                unk = getattr(state, "unknown_tyre_count", 0)
                share_line = self.tr.t(
                    "live.field_share_fmt",
                    "Field: Inter/Wet share: {pct:.0f}% ({inter}/{total})  unk:{unk}"
                ).format(pct=state.inter_share * 100.0, inter=state.inter_count, total=total, unk=unk)

            if self.lblFieldShare.text() != share_line:
                self.lblFieldShare.setText(share_line)

            # WIP/TELEMETRY SIGNAL (field-level pace):
            if state.pace_delta_inter_vs_slick_s is None:
                field_line = self.tr.t("live.field_delta_na", "Field: Δpace (I-S): n/a")
            else:
                field_line = self.tr.t(
                    "live.field_delta_fmt",
                    "Field: Δpace (I-S): {delta:+.2f}s"
                ).format(delta=state.pace_delta_inter_vs_slick_s)

            # WIP/LEARNING SIGNAL (player-specific):
            rc = getattr(state, "your_ref_counts", None) or "S:0 I:0 W:0"
            yd = getattr(state, "your_delta_inter_vs_slick_s", None)
            yw = getattr(state, "your_delta_wet_vs_slick_s", None)

            if yd is None and yw is None:
                your_line = self.tr.t("live.your_delta_na_fmt", "Your: Δ(I-S): n/a ({rc})").format(rc=rc)
            else:
                parts = []
                if yd is not None:
                    parts.append(self.tr.t("live.your_part_is_fmt", "Δ(I-S) {d:+.2f}s").format(d=yd))
                if yw is not None:
                    parts.append(self.tr.t("live.your_part_ws_fmt", "Δ(W-S) {d:+.2f}s").format(d=yw))
                your_line = self.tr.t("live.your_prefix", "Your: ") + ", ".join(parts) + f" ({rc})"

            txt = field_line + "\n" + your_line
            if self.lblFieldDelta.text() != txt:
                self.lblFieldDelta.setText(txt)

        except Exception as e:
            try:
                self.status.showMessage(f"UI update error: {type(e).__name__}: {e}", 12000)
            except Exception:
                pass
            print("[UI UPDATE ERROR]", type(e).__name__, e)

    @QtCore.Slot()
    def _update_minisector_table(self):
        try:
            rows = self.ms.rows()
            if not hasattr(self, "tblMini"):
                return

            cur_mi = self.ms.current_index()

            # Layout rows (NO gaps):
            # 0      : SECTOR 1 header
            # 1-10   : minis 0..9
            # 11     : SECTOR 2 header
            # 12-21  : minis 10..19
            # 22     : SECTOR 3 header
            # 23-32  : minis 20..29
            def mini_to_table_row(mi: int) -> int:
                if mi < 10:
                    return 1 + mi
                if mi < 20:
                    return 12 + (mi - 10)
                return 23 + (mi - 20)

            header_rows = {0: "SECTOR 1", 11: "SECTOR 2", 22: "SECTOR 3"}

            # Ensure table has correct row count
            if self.tblMini.rowCount() != 33:
                self.tblMini.setRowCount(33)

            # Paint header rows
            for hr, title in header_rows.items():
                self.tblMini.setRowHeight(hr, 22)
                for c in range(5):
                    txt = title if c == 0 else ""
                    it = QtWidgets.QTableWidgetItem(txt)
                    it.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
                    it.setBackground(QtGui.QColor(35, 35, 35))
                    it.setForeground(QtGui.QColor(220, 220, 220))
                    it.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    self.tblMini.setItem(hr, c, it)

            cur_row = mini_to_table_row(cur_mi) if cur_mi is not None else None

            def fmt(ms, est: bool = False):
                """Format milliseconds as seconds text, with optional '*' marker for estimated values."""
                if ms is None:
                    return "—"
                txt = f"{ms / 1000.0:.3f}"
                return f"{txt}*" if est else txt

            def set_readable_text_color(item: QtWidgets.QTableWidgetItem, bg: QtGui.QColor):
                # perceived luminance (0..255)
                lum = 0.2126 * bg.red() + 0.7152 * bg.green() + 0.0722 * bg.blue()
                item.setForeground(QtGui.QColor(0, 0, 0) if lum > 140 else QtGui.QColor(255, 255, 255))


            for mi, r in enumerate(rows):
                i = mini_to_table_row(mi)

                sector = (mi // 10) + 1

                # base cells
                self.tblMini.setItem(i, 0, QtWidgets.QTableWidgetItem(f"{mi + 1:02d}"))
                self.tblMini.setItem(
                    i, 1,
                    QtWidgets.QTableWidgetItem(fmt(r.last_ms, getattr(r, "last_estimated", False)))
                )
                self.tblMini.setItem(i, 2, QtWidgets.QTableWidgetItem(fmt(r.pb_ms)))
                self.tblMini.setItem(i, 3, QtWidgets.QTableWidgetItem(fmt(r.best_ms)))

                d = None
                if r.last_ms is not None and r.pb_ms is not None:
                    d = r.last_ms - r.pb_ms

                delta_txt = "—" if d is None else f"{d/1000.0:+.3f}"
                self.tblMini.setItem(i, 4, QtWidgets.QTableWidgetItem(delta_txt))

                # color logic:
                # lila = best overall (best_ms)
                # grün = personal best (pb_ms)
                # gelb = slower than PB
                # (for now best==pb, later best can be "field best")
                bg = None
                if r.last_ms is None:
                    bg = QtGui.QColor(45, 45, 45)  # dark neutral
                else:
                    if r.best_ms is not None and r.last_ms == r.best_ms:
                        bg = QtGui.QColor(160, 32, 240)  # purple
                    elif r.pb_ms is not None and r.last_ms == r.pb_ms:
                        bg = QtGui.QColor(0, 255, 0)  # green (WIP, not tested yet)
                    else:
                        bg = QtGui.QColor(255, 239, 0)  # yellow

                # --- COLORS (column based) ---
                PURPLE = QtGui.QColor(160, 32, 240)
                GREEN = QtGui.QColor(0, 255, 0)
                YELLOW = QtGui.QColor(255, 239, 0)
                NEUTRAL = QtGui.QColor(45, 45, 45)

                # 1) base color for Minisector + Last (keep your old logic)
                # keep EXACT behavior: purple if last==best, green if last==pb else yellow, neutral if none
                base_bg = NEUTRAL
                if r.last_ms is not None:
                    if r.best_ms is not None and r.last_ms == r.best_ms:
                        base_bg = PURPLE
                    elif r.pb_ms is not None and r.last_ms == r.pb_ms:
                        base_bg = GREEN
                    else:
                        base_bg = YELLOW

                # 2) PB color: always green, unless PB equals absolute best -> purple
                pb_bg = NEUTRAL
                if r.pb_ms is not None:
                    pb_bg = PURPLE if (r.best_ms is not None and r.pb_ms == r.best_ms) else GREEN

                # 3) Best color: always purple if present
                best_bg = NEUTRAL
                if r.best_ms is not None:
                    best_bg = PURPLE

                # 4) Delta color: yellow if slower, else PB color (green/purple)
                delta_bg = NEUTRAL
                if r.last_ms is not None and r.pb_ms is not None:
                    dms = r.last_ms - r.pb_ms
                    delta_bg = YELLOW if dms > 0 else pb_bg

                # apply per column
                col_bg = {
                    0: base_bg,  # Minisector
                    1: base_bg,  # Last
                    2: pb_bg,  # PB
                    3: best_bg,  # Best
                    4: delta_bg  # ΔPB
                }

                for c in range(5):
                    it = self.tblMini.item(i, c)
                    if not it:
                        continue
                    bgc = col_bg.get(c, NEUTRAL)
                    it.setBackground(bgc)
                    set_readable_text_color(it, bgc)

                # highlight current minisector row (blue + bold)
                is_cur = (cur_row is not None and i == cur_row)
                if is_cur:
                    cur_bg = QtGui.QColor(60, 90, 140)  # blue-ish
                    for c in (0, 1):
                        it = self.tblMini.item(i, c)
                        if not it:
                            continue
                        it.setBackground(cur_bg)
                        set_readable_text_color(it, cur_bg)
                        f = it.font()
                        f.setBold(True)
                        it.setFont(f)
                else:
                    # ensure non-current rows are not bold (so it doesn't "stick")
                    for c in (0, 1):
                        it = self.tblMini.item(i, c)
                        if not it:
                            continue
                        f = it.font()
                        f.setBold(False)
                        it.setFont(f)

            # --- Theo lap times from minisectors ---
            def fmt_lap(ms_total: int | None) -> str:
                if ms_total is None:
                    return "—"
                s = ms_total / 1000.0
                m = int(s // 60)
                ss = s - 60 * m
                return f"{m}:{ss:06.3f}"

            t_last = self.ms.sum_last_ms_current()
            t_pb = self.ms.sum_pb_ms()
            t_best = self.ms.sum_best_ms()

            self.lblTheoLast.setText(f"Theo Last: {fmt_lap(t_last)}")
            self.lblTheoPB.setText(f"Theo PB: {fmt_lap(t_pb)}")
            self.lblTheoBest.setText(f"Theo Best: {fmt_lap(t_best)}")

            missing = self.ms.missing_current_indices()
            if missing:
                # show only first few to avoid UI spam
                preview = ", ".join(f"{x:02d}" for x in missing[:6])
                more = "" if len(missing) <= 6 else f" +{len(missing)-6}"
                self.lblTheoMiss.setText(f"Missing Last: {preview}{more}")
            else:
                self.lblTheoMiss.setText("")

            # --- NEW: update Live (GUI) ASCII HUD minisector bars with the SAME source data ---
            try:
                if hasattr(self, "live_tab") and self.live_tab is not None:
                    hud = getattr(self.live_tab, "hud", None)
                    if hud is not None and hasattr(hud, "update_minisectors_from_tracker"):
                        hud.update_minisectors_from_tracker(rows, cur_mi)
            except Exception:
                pass



        except Exception as e:
            print("[MINISECTOR UI ERROR]", type(e).__name__, e)


    def _on_new_csv(self, src: Path, cached: Path):
        # ---- DEDUPE: gleiche Datei (create/modify) nur 1x verarbeiten ----
        try:
            stat = cached.stat()
            sig = (stat.st_size, stat.st_mtime_ns)
        except Exception:
            return

        key = str(src)
        last_sig = self._dedupe_mtime.get(key)

        if last_sig == sig:
            return

        self._dedupe_mtime[key] = sig

        # Cooldown entfernt: watcher.copy_to_cache() wartet bereits auf stabile Dateigröße.
        # Doppel-Events werden durch (size, mtime_ns) dedupe abgefangen.
        #
        # Legacy optional cooldown (kept for reference):
        # - Was used to suppress duplicate FS events via a 1s time-based gate.
        # - Currently disabled because copy_to_cache() already waits for a stable file size
        #   and we additionally dedupe by (size, mtime_ns) above.
        #
        # Example (disabled):
        #   now = time.time()
        #   last_t = getattr(self, '_dedupe_time_sec', {}).get(key, 0)
        #   if now - last_t < 1.0: return
        #   self._dedupe_time_sec = getattr(self, '_dedupe_time_sec', {})
        #   self._dedupe_time_sec[key] = now

        # ------------------------------------------------------------------

        # NEVER process files from our own cache folder (prevents infinite loops)

        # try:
        #     cache_dir = Path(app_data_dir()) / "cache"
        #     if cache_dir in src.resolve().parents:
        #         return
        # except Exception:
        #     pass

        #-------------------------------------------------------------------
        
        name = src.stem.lower()
        if "_tt_" in name or name.endswith("_tt") or name.startswith("tt_"):
            self.logger.info(f"Skipped (time trial): {src.name}")
            return

        session = self._detect_session(src)
        if session == "Q":
            self.logger.info(f"Skipped (qualifying not used for strategy): {src.name}")
            return

        # Health: CSV seen (we set status first, then refine to OK/FAIL)
        try:
            self._health["csv"]["status"] = "SEEN"
            self._health["csv"]["last_file"] = src.name
            self._health["csv"]["ts"] = self._health_now_str()
            self._health["csv"]["err"] = ""
            self._refresh_health_panel()
        except Exception:
            pass

        try:
            # 1) NEW: try your own UDP lap CSV format first
            if self._import_own_udp_lap_csv(src, cached):
                # Health: CSV import OK
                try:
                    self._health["csv"]["status"] = "OK"
                    self._health["csv"]["ts"] = self._health_now_str()
                    self._health["csv"]["err"] = ""
                    self._refresh_health_panel()
                except Exception:
                    pass

                QtCore.QMetaObject.invokeMethod(self, "_after_db_update", QtCore.Qt.QueuedConnection)
                return

            # 2) legacy: Iko/Overtake CSV import (unchanged)
            parsed = parse_overtake_csv(cached)
            summ = lap_summary(parsed)
            try:
                self._your_last_lap_s = float(summ.get("lap_time_s")) if summ.get("lap_time_s") is not None else None
            except Exception:
                self._your_last_lap_s = None

            self._your_last_tyre = (summ.get("tyre") or None)
            self._your_last_track = (summ.get("track") or None)
            self.logger.info(
                f"[PLAYER] last_lap={self._your_last_lap_s} tyre={self._your_last_tyre} track={self._your_last_track}")

            if not isinstance(summ, dict):
                raise ValueError("lap_summary did not return a dict")
            summ["session"] = session

            # attach current UDP session uid (run id), fallback if UDP not ready yet
            sess_uid = None
            try:
                if self.udp and getattr(self.udp, "state", None):
                    sess_uid = self.udp.state.session_uid
            except Exception:
                sess_uid = None

            if sess_uid is None:
                # Fallback: use file timestamp (seconds) as run id so new race doesn't merge into NULL bucket
                # This is stable enough to separate sessions and avoids "lap continues from previous race".
                sess_uid = int(stat.st_mtime_ns // 1_000_000_000)

            summ["session_uid"] = str(sess_uid)

            upsert_lap(str(src), summ)

            self.logger.info(
                f"Imported: track={summ.get('track')} tyre={summ.get('tyre')} "
                f"weather={summ.get('weather')} lap_time_s={summ.get('lap_time_s')}"
            )

            # Health: CSV import OK
            try:
                self._health["csv"]["status"] = "OK"
                self._health["csv"]["ts"] = self._health_now_str()
                self._health["csv"]["err"] = ""
                self._refresh_health_panel()
            except Exception:
                pass

            QtCore.QMetaObject.invokeMethod(self, "_after_db_update", QtCore.Qt.QueuedConnection)

        except Exception as e:

            self.logger.error(f"IMPORT FAILED for {src.name}: {type(e).__name__}: {e}")

            # Health: CSV import FAIL (short reason)

            try:
                reason = f"{type(e).__name__}: {e}"

                # keep it short to avoid UI spam
                if len(reason) > 120:
                    reason = reason[:117] + "..."

                self._health["csv"]["status"] = "FAIL"
                self._health["csv"]["last_file"] = src.name
                self._health["csv"]["ts"] = self._health_now_str()
                self._health["csv"]["err"] = reason
                self._refresh_health_panel()

            except Exception:
                pass

            QtCore.QMetaObject.invokeMethod(
                self.status, "showMessage", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, f"CSV import failed: {src.name} ({type(e).__name__}: {e})"),
                QtCore.Q_ARG(int, 12000),
            )

    @QtCore.Slot()
    def _after_db_update(self):
        # Health: DB write OK (we mark whenever a CSV import triggers a DB refresh)
        try:
            self._health["db"]["status"] = "OK"
            self._health["db"]["ts"] = self._health_now_str()
            self._health["db"]["err"] = ""
            self._refresh_health_panel()
        except Exception:
            pass

        self._refresh_track_combo()
        self._refresh_db_views()
        self.status.showMessage("DB updated from new CSV.", 2500)

    def _refresh_db_views(self):
        rows = latest_laps(800)

        def wear_avg(row):
            vals = [row[9], row[10], row[11], row[12]]
            vals = [v for v in vals if v is not None]
            return (sum(vals) / len(vals)) if vals else None

        # Gruppieren nach (game, track, session) → damit Boxenstopp über Tyre-Wechsel erkannt wird
        by_group = {}
        for i, row in enumerate(rows):
            key = (row[1], row[2], row[3], row[4])  # (game, track, session, session_uid)

            by_group.setdefault(key, []).append(i)

        lapno = {}  # row_index -> lap number
       
        tags = ["OK"] * len(rows)

        WEAR_DROP_THR = 2.0
        OUTLIER_SEC = 2.0

        # ---- Compute lap numbers + tags per (game, track, session, session_uid) ----
        for idxs in by_group.values():
            # rows sind newest-first → für Lapnummern umdrehen
            idxs = sorted(idxs, key=lambda j: rows[j][0])

            # Lapnummern
            for n, j in enumerate(idxs, start=1):
                lapno[j] = n

            # Wear-Drop → IN / OUT
            def wear_avg_idx(j):
                vals = [rows[j][9], rows[j][10], rows[j][11], rows[j][12]]
                vals = [v for v in vals if v is not None]
                return (sum(vals) / len(vals)) if vals else None

            w = [wear_avg_idx(j) for j in idxs]

            for k in range(1, len(idxs)):
                if w[k - 1] is None or w[k] is None:
                    continue
                if (w[k - 1] - w[k]) > WEAR_DROP_THR:
                    tags[idxs[k - 1]] = "IN"
                    tags[idxs[k]] = "OUT"

            # --- Time outliers → SHIFT / SLOW (per-tyre ONLY; no cross-tyre fallback!) ---
            SHIFT_SEC = 1.2  # moderate outlier vs normal pace on same tyre
            SLOW_SEC = 6.0  # big outlier (ERS recharge etc.) on same tyre

            # Collect lap times per tyre (ignore IN/OUT)
            times_by_tyre: dict[str, list[float]] = {}

            for j in idxs:
                if tags[j] in ("IN", "OUT"):
                    continue

                t = rows[j][7]  # lap_time_s
                tyre = rows[j][5]  # tyre
                if t is None or tyre is None:
                    continue

                try:
                    tf = float(t)
                except Exception:
                    continue

                if not (10.0 < tf < 400.0):
                    continue

                tyre_key = str(tyre).strip().upper()
                times_by_tyre.setdefault(tyre_key, []).append(tf)

            def median(ts: list[float]) -> float:
                s = sorted(ts)
                return s[len(s) // 2]

            # Baseline per tyre: if not enough samples, DON'T tag that tyre at all
            baseline: dict[str, float] = {}
            for tyre_key, ts in times_by_tyre.items():
                if len(ts) >= 3:
                    baseline[tyre_key] = median(ts)

            # Apply tagging per tyre
            for j in idxs:
                if tags[j] != "OK":
                    continue

                t = rows[j][7]
                tyre = rows[j][5]
                if t is None or tyre is None:
                    continue

                try:
                    tf = float(t)
                except Exception:
                    continue

                tyre_key = str(tyre).strip().upper()
                base = baseline.get(tyre_key)
                if base is None:
                    continue  # <3 laps on this tyre → do NOT tag (prevents "Inter slower than Slick" false SLOW)

                if tf > base + SLOW_SEC:
                    tags[j] = "SLOW"
                elif tf > base + SHIFT_SEC:
                    tags[j] = "SHIFT"

        # Tabelle rendern
        self.tbl.setRowCount(len(rows))
        for r, row in enumerate(rows):
            # col 0: lap number
            lap_item = QtWidgets.QTableWidgetItem(str(lapno.get(r, "")))
            self.tbl.setItem(r, 0, lap_item)

            # cols 1...: original db columns (BUT hide session_uid from display)
            # row indices now: 0 created_at,1 game,2 track,3 session,4 session_uid,5 tyre,6 weather,7 lap_time_s,8 fuel,9..12 wear
            # display_row = row[:4] + row[5:]  # remove session_uid
            display_row = row[:5] + row[5:]

            for c, val in enumerate(display_row):
                item = QtWidgets.QTableWidgetItem("" if val is None else str(val))
                self.tbl.setItem(r, c + 1, item)

            # last col: lap_tag
            tag_item = QtWidgets.QTableWidgetItem(tags[r])
            self.tbl.setItem(r, len(display_row) + 1, tag_item)

    def _refresh_track_combo(self):
        try:
            tracks = distinct_tracks()
        except Exception:
            tracks = []
        self.cmbTrack.clear()
        self.cmbTrack.addItems(tracks)


    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._stop_services()
        super().closeEvent(event)

    @QtCore.Slot(str)
    def _append_log(self, line: str):
        # UI log removed: show only the latest line in the status bar (optional)
        try:
            self.status.showMessage(line, 5000)  # 5s
        except Exception:
            pass

    def _append_log_threadsafe(self, line: str):
        QtCore.QMetaObject.invokeMethod(
            self,
            "_append_log",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, line),
        )

    def _append_udp_lap_csv(self, row: dict) -> None:
        """
        Append one lap row to a persistent CSV in the user-chosen output folder.

        NEW behavior:
        - Creates one folder per track name (sanitized for Windows).
        - Writes one CSV per session_uid inside that track folder.
        - File name: "<TrackName>_<session_uid>.csv" (sanitized).
        Safe + additive: never throws.
        """
        try:
            import re
            from app.track_map import track_label_from_id

            out_root = (getattr(self.cfg, "udp_output_root", "") or "").strip()
            if not out_root:
                return

            def _safe_fs_name(s: str) -> str:
                # Windows-illegal: <>:"/\|?*  (plus control chars)
                s = (s or "").strip()
                if not s:
                    return "Unknown"
                s = re.sub(r'[<>:"/\\\\|?*\\x00-\\x1F]', "_", s)
                s = s.rstrip(" .")  # Windows: no trailing dot/space
                return s or "Unknown"

            # Prefer already-resolved track string in row, otherwise derive from track_id if present
            track_name = (row.get("track") or "").strip()
            if not track_name:
                track_name = track_label_from_id(row.get("track_id"))

            track_dir_name = _safe_fs_name(track_name)
            track_dir = Path(out_root) / track_dir_name
            track_dir.mkdir(parents=True, exist_ok=True)

            session_uid = (row.get("session_uid") or "").strip()
            session_uid_safe = _safe_fs_name(session_uid) if session_uid else "unknown_session"

            file_stem = _safe_fs_name(f"{track_dir_name}_{session_uid_safe}")
            csv_path = track_dir / f"{file_stem}.csv"

            # stable column order
            cols = [
                "created_at",
                "game",
                "track",
                "session",
                "session_uid",
                "lap_n",
                "lap_time_s",
                "tyre",
                "weather",
                "source",
            ]

            write_header = (not csv_path.exists())

            with csv_path.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                if write_header:
                    w.writeheader()
                w.writerow({k: row.get(k, "") for k in cols})
        except Exception:
            pass

    def _is_own_udp_lap_csv(self, path: Path) -> bool:
        """
        Detect your custom one-lap wide CSV format (minisectors).
        Safe/cheap check: only reads the header line.
        """
        try:
            with path.open("r", encoding="utf-8", newline="") as f:
                header = f.readline().strip().lower()
            # strong signals for your format
            return ("created_at_utc" in header and "minis_sum_ms" in header) or ("ms01_ms" in header)
        except Exception:
            return False

    def _import_own_udp_lap_csv(self, src: Path, cached: Path) -> bool:
        """
        Import your custom UDP lap CSV into DB.
        Returns True if it was your format and import succeeded (or at least attempted).
        """
        if not self._is_own_udp_lap_csv(cached):
            return False

        try:
            with cached.open("r", encoding="utf-8", newline="") as f:
                r = csv.DictReader(f)
                row = next(r, None)

            if not row:
                return True  # recognized format, but empty

            # mandatory-ish fields
            track = (row.get("track") or "").strip() or "Unknown"
            session = (row.get("session") or "").strip()  # "P"/"Q"/"R" or ""
            session_uid = str(row.get("session_uid") or "nosess")

            # lap time
            lap_time_s = None
            try:
                if row.get("lap_time_ms"):
                    lap_time_s = float(row["lap_time_ms"]) / 1000.0
            except Exception:
                lap_time_s = None

            tyre = (row.get("tyre_cat") or "").strip()
            weather = row.get("weather_enum")

            # fuel + wear (optional)
            def _f(key):
                try:
                    v = row.get(key, "")
                    return None if v in ("", None) else float(v)
                except Exception:
                    return None

            summ = {
                "game": (row.get("game") or "F1").strip(),
                "track": track,
                "session": session,
                "session_uid": session_uid,
                "weather": ("" if weather in ("", None) else str(weather)),
                "tyre": tyre,
                "lap_time_s": lap_time_s,

                "fuel_load": _f("fuel_in_tank"),
                "wear_fl": _f("wear_fl_pct"),
                "wear_fr": _f("wear_fr_pct"),
                "wear_rl": _f("wear_rl_pct"),
                "wear_rr": _f("wear_rr_pct"),
            }

            upsert_lap(str(src), summ)
            return True
        except Exception as e:
            # still "recognized", so return True to avoid trying to parse as Iko CSV
            try:
                self.logger.error(f"OWN UDP CSV import failed for {src.name}: {type(e).__name__}: {e}")
            except Exception:
                pass
            return True


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()