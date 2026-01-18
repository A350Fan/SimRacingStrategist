# app/ui/widgets/strategy_cards_widget.py
from __future__ import annotations

from PySide6 import QtWidgets

from app.strategy import generate_placeholder_cards


class StrategyCardsWidget(QtWidgets.QGroupBox):
    """
    UI-only widget: Strategy cards group.

    IMPORTANT:
    - No strategy logic here (still placeholder cards).
    - Exposes cardWidgets list (legacy compatibility).
    """

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self.tr = tr

        self.setTitle(self.tr.t("cards.group_title", "Strategy Cards (Prototype)"))
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum
        )

        stratLayout = QtWidgets.QHBoxLayout(self)
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

    def retranslate(self):
        self.setTitle(self.tr.t("cards.group_title", "Strategy Cards (Prototype)"))
        # NOTE: The placeholder cards contain text that was created at init-time.
        # If you want those to fully retranslate too, we can rebuild the cards on language switch later.