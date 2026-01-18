# app/ui/tabs/live_raw_tab.py
from __future__ import annotations

from PySide6 import QtWidgets

from app.ui.widgets.live_header_widget import LiveHeaderWidget
from app.ui.widgets.minisector_widget import MiniSectorWidget
from app.ui.widgets.strategy_cards_widget import StrategyCardsWidget


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

        # --- Live header widget (extracted) ---
        self.liveHeaderWidget = LiveHeaderWidget(self.tr, parent=self)
        live_outer.addWidget(self.liveHeaderWidget)

        # IMPORTANT: Keep legacy attribute names so MainWindow logic remains unchanged
        self.grpLive = self.liveHeaderWidget
        self.lblSC = self.liveHeaderWidget.lblSC
        self.lblWeather = self.liveHeaderWidget.lblWeather
        self.lblRain = self.liveHeaderWidget.lblRain
        self.lblRainAdvice = self.liveHeaderWidget.lblRainAdvice
        self.lblFieldShare = self.liveHeaderWidget.lblFieldShare
        self.lblFieldDelta = self.liveHeaderWidget.lblFieldDelta

        # --- Strategy cards widget (extracted) ---
        self.strategyCardsWidget = StrategyCardsWidget(self.tr, parent=self)
        live_outer.addWidget(self.strategyCardsWidget)

        # IMPORTANT: Keep legacy attribute names so MainWindow logic remains unchanged
        self.grpStrat = self.strategyCardsWidget
        self.cardWidgets = self.strategyCardsWidget.cardWidgets

        # --- Minisectors widget (extracted) ---
        self.miniSectorWidget = MiniSectorWidget(parent=self)
        live_outer.addWidget(self.miniSectorWidget, 1)

        # IMPORTANT: Keep legacy attribute names so MainWindow logic remains unchanged
        self.grpMini = self.miniSectorWidget  # groupbox itself
        self.tblMini = self.miniSectorWidget.tblMini
        self.lblTheoLast = self.miniSectorWidget.lblTheoLast
        self.lblTheoPB = self.miniSectorWidget.lblTheoPB
        self.lblTheoBest = self.miniSectorWidget.lblTheoBest
        self.lblTheoMiss = self.miniSectorWidget.lblTheoMiss

    def retranslate(self):
        """Called by MainWindow when language changes."""
        try:
            self.liveHeaderWidget.retranslate()
        except Exception:
            pass

        try:
            self.strategyCardsWidget.retranslate()
        except Exception:
            pass
