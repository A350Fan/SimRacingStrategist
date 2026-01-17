# app/ui/widgets/strategy_cards_widget.py
from __future__ import annotations
from typing import Optional, List
from PySide6 import QtWidgets

from app.strategy import generate_placeholder_cards, StrategyCard, StrategyRecommendation


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

        # Root layout inside the group box (vertical):
        #  - Recommendation header
        #  - Cards row
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.lblRecommendation = QtWidgets.QLabel(
            self.tr.t("cards.reco_default", "Recommendation: —")
        )
        self.lblRecommendation.setStyleSheet("font-weight: 800;")
        self.lblRecommendation.setWordWrap(True)
        root.addWidget(self.lblRecommendation)

        self._cardsRow = QtWidgets.QHBoxLayout()
        self._cardsRow.setSpacing(10)
        root.addLayout(self._cardsRow)

        self.cardWidgets = []
        self.update_cards(recommendation=None, cards=generate_placeholder_cards())

    def _clear_cards(self) -> None:
        """Delete all card widgets from the row layout."""
        while self._cardsRow.count():
            item = self._cardsRow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.cardWidgets = []

    def update_cards(self, recommendation: Optional[StrategyRecommendation], cards: List[StrategyCard]) -> None:
        """Rebuilds the card widgets from the provided strategy output."""
        # --- Recommendation header ---
        if recommendation is None:
            self.lblRecommendation.setText(self.tr.t("cards.reco_default", "Recommendation: —"))
        else:
            lap_txt = "—" if recommendation.box_lap_estimate is None else str(int(recommendation.box_lap_estimate))
            self.lblRecommendation.setText(
                self.tr.t(
                    "cards.reco_fmt",
                    "Recommendation: {action} | target={tyre} | box lap≈{lap} | conf={conf:.2f} | {reason}",
                ).format(
                    action=recommendation.action,
                    tyre=recommendation.target_tyre,
                    lap=lap_txt,
                    conf=float(recommendation.confidence),
                    reason=recommendation.reasoning or "",
                )
            )

        # --- Cards ---
        self._clear_cards()

        for c in (cards or []):
            w = QtWidgets.QGroupBox(c.name)
            v = QtWidgets.QVBoxLayout(w)

            lbl_desc = QtWidgets.QLabel(c.description)
            lbl_desc.setWordWrap(True)

            lbl_plan = QtWidgets.QLabel(f"{self.tr.t('cards.tyres_prefix', 'Tyres:')} {c.tyre_plan}")
            lbl_plan.setStyleSheet("font-weight: 700;")

            v.addWidget(lbl_desc)
            v.addWidget(lbl_plan)

            if c.next_pit_lap is not None:
                v.addWidget(
                    QtWidgets.QLabel(
                        self.tr.t("cards.next_pit_fmt", "Next pit: Lap {lap}").format(lap=c.next_pit_lap)
                    )
                )

            # Small confidence hint (kept subtle)
            try:
                v.addWidget(QtWidgets.QLabel(f"conf: {float(c.confidence):.2f}"))
            except Exception:
                pass

            v.addStretch(1)
            w.setMinimumWidth(260)

            self._cardsRow.addWidget(w)
            self.cardWidgets.append(w)

    def retranslate(self):
        self.setTitle(self.tr.t("cards.group_title", "Strategy Cards (Prototype)"))
        # NOTE: The placeholder cards contain text that was created at init-time.
        # If you want those to fully retranslate too, we can rebuild the cards on language switch later.