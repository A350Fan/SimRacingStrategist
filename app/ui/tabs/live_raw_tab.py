from __future__ import annotations
from PySide6 import QtWidgets


class LiveRawTabWidget(QtWidgets.QWidget):
    """
    Live Raw Tab placeholder.
    We'll migrate the existing Live Raw UI here next, without changing functionality.
    """

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self.tr = tr

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)

        self.lblInfo = QtWidgets.QLabel("Live (placeholder) – migrate existing UI widgets here next.")
        self.lblInfo.setWordWrap(True)
        lay.addWidget(self.lblInfo, 0)

        lay.addStretch(1)

    def retranslate(self):
        pass
