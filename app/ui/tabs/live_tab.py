from __future__ import annotations
from PySide6 import QtWidgets


class LiveTabWidget(QtWidgets.QWidget):
    """
    Live (GUI) Tab - placeholder for the future nicer UI.
    Intentionally empty for now.
    """

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self.tr = tr

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)

        title = QtWidgets.QLabel(self.tr.t("live_gui.placeholder_title", "Live (GUI) – coming soon"))
        title.setStyleSheet("font-weight: 700; font-size: 16px;")
        lay.addWidget(title)

        hint = QtWidgets.QLabel(
            self.tr.t(
                "live_gui.placeholder_hint",
                "This tab will become the new compact UI.\n"
                "For now, all live data stays in Live (Raw)."
            )
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)

        lay.addStretch(1)

    def retranslate(self):
        # Optional: update placeholder texts if you add i18n keys later
        pass
