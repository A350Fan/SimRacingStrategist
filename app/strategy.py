# app/strategy.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple

# NOTE: We reuse the same normalization as the degradation model so DB tyre labels
# (C1..C6 / INTER / WET / etc.) behave consistently across the app.
from app.strategy_model import normalize_tyre


# =============================
# UI data models
# =============================

@dataclass
class StrategyCard:
    """A single strategy card (Plan A/B/C) shown in the Live UI."""
    name: str
    description: str
    next_pit_lap: Optional[int] = None
    tyre_plan: str = ""
    confidence: float = 0.5


@dataclass
class StrategyRecommendation:
    """High-level call shown above the cards."""
    action: str                    # "BOX" | "STAY OUT" | "WAIT"
    target_tyre: str               # normalized ("C3" / "INTER" / "WET" / ...)
    box_lap_estimate: Optional[int]
    confidence: float = 0.5
    reasoning: str = ""

@dataclass
class TeamContext:
    """
    Describes the strategic situation of the player relative to the field.
    This is PURE logic data – no UI, no decisions yet.
    """
    track: str

    # gaps in seconds
    gap_ahead_s: Optional[float]
    gap_behind_s: Optional[float]

    pit_loss_s: float
    safety_margin_s: float = 1.2  # conservative default

    def has_free_pit_stop(self) -> bool:
        """
        Free pit stop = we can pit and rejoin without losing position
        to the car behind.
        """
        if self.gap_behind_s is None:
            return False

        required_gap = self.pit_loss_s + self.safety_margin_s
        return self.gap_behind_s > required_gap

# =============================
# Small helpers (track-scoped)
# =============================

def _median(xs: List[float]) -> Optional[float]:
    xs = [float(x) for x in xs if isinstance(x, (int, float))]
    if not xs:
        return None
    xs = sorted(xs)
    return xs[len(xs) // 2]


def _tyre_pace_medians_for_track(db_rows: list) -> Dict[str, float]:
    """
    db_rows expected format (from db.laps_for_track):
      (created_at, session, track, tyre, weather, lap_time_s, fuel_load, wear_fl, wear_fr, wear_rl, wear_rr)

    Returns {tyre_norm: median_lap_time_s}.
    """
    by: Dict[str, List[float]] = {}
    for r in (db_rows or []):
        try:
            tyre = normalize_tyre(str(r[3] or ""))
            t = r[5]
        except Exception:
            continue

        if not tyre or t is None:
            continue

        try:
            tf = float(t)
        except Exception:
            continue

        # ignore clear garbage (spins, outlaps, etc.)
        if not (30.0 <= tf <= 300.0):
            continue

        by.setdefault(tyre, []).append(tf)

    out: Dict[str, float] = {}
    for tyre, ts in by.items():
        med = _median(ts)
        if med is not None:
            out[tyre] = float(med)
    return out


def _pit_loss_effective(base_pit_loss_s: float, sc_status: Optional[int]) -> float:
    """Very simple SC/VSC pit-loss reduction model."""
    try:
        sc = int(sc_status) if sc_status is not None else 0
    except Exception:
        sc = 0

    # 0=green, 1=SC, 2=VSC (see main.py mapping)
    if sc == 1:
        return float(base_pit_loss_s) * 0.60
    if sc == 2:
        return float(base_pit_loss_s) * 0.80
    return float(base_pit_loss_s)

# =============================
# Strategy core (MVP v1)
# =============================

def generate_strategy_cards(
    *,
    track: str,
    session: str,
    current_lap: Optional[int],
    current_tyre: str,
    current_wear_avg: Optional[float],
    laps_remaining: Optional[int],
    base_pit_loss_s: float,
    sc_status: Optional[int],
    db_rows: Optional[list],
) -> Tuple[StrategyRecommendation, List[StrategyCard]]:
    """
    Strategy core (MVP v1):
    - ALWAYS track-scoped: caller passes db_rows from laps_for_track(track)
    - Computes simple tyre pace medians + a pit-loss estimate
    - Outputs: (Recommendation header, Plan A/B/C cards)

    This version intentionally keeps logic "boring" and readable.
    We'll deepen it later (team context, opponents, real pit windows, etc.).
    """
    track = (track or "").strip()
    tyre_now = normalize_tyre(current_tyre or "")
    pace_med = _tyre_pace_medians_for_track(db_rows or [])
    pit_loss_s = _pit_loss_effective(base_pit_loss_s, sc_status)

    # ---- If we have no track or no data yet: return informative placeholders ----
    if not track:
        rec = StrategyRecommendation(
            action="WAIT",
            target_tyre=tyre_now or "—",
            box_lap_estimate=None,
            confidence=0.0,
            reasoning="No track selected yet.",
        )
        cards = [
            StrategyCard(
                name="Plan A",
                description="Select a track (Model tab) or start driving so DB has data.",
                tyre_plan="—",
                confidence=0.0,
            )
        ]
        return rec, cards

    if not pace_med:
        rec = StrategyRecommendation(
            action="WAIT",
            target_tyre=tyre_now or "—",
            box_lap_estimate=None,
            confidence=0.1,
            reasoning=f"No lap database yet for {track}.",
        )
        cards = [
            StrategyCard(
                name="Plan A",
                description=f"Drive a few clean laps on {track} so the app can learn tyre pace.",
                tyre_plan="—",
                confidence=0.1,
            )
        ]
        return rec, cards

    # ---- choose "best" (fastest median) tyre for current conditions ----
    if tyre_now in ("INTER", "WET"):
        best_tyre = tyre_now
    else:
        slicks = {k: v for k, v in pace_med.items() if k.startswith("C")}
        best_tyre = min(slicks, key=lambda k: slicks[k]) if slicks else min(pace_med, key=lambda k: pace_med[k])

    # ---- simple BOX / STAY OUT logic ----
    wear_rem = float(current_wear_avg) if isinstance(current_wear_avg, (int, float)) else None

    box = False
    reason_bits: List[str] = []

    # Wear trigger (remaining %) -> box when low
    if wear_rem is not None:
        if wear_rem <= 65.0:
            box = True
            reason_bits.append(f"wear low ({wear_rem:.0f}%)")
        elif wear_rem <= 72.0 and (laps_remaining is not None and laps_remaining >= 6):
            box = True
            reason_bits.append(f"wear trending low for remaining distance ({wear_rem:.0f}%)")

    # Tyre pace opportunity trigger (undercut awareness)
    if tyre_now and best_tyre and tyre_now != best_tyre:
        now_p = pace_med.get(tyre_now)
        best_p = pace_med.get(best_tyre)
        if now_p is not None and best_p is not None:
            gain = float(now_p - best_p)
            if gain >= 0.35:
                reason_bits.append(f"pace gain to {best_tyre} (~{gain:.2f}s/lap)")
                if pit_loss_s <= base_pit_loss_s * 0.85:
                    # SC/VSC makes the undercut more attractive
                    box = True

    # If race is almost done, default to stay out unless tyres are dying
    if laps_remaining is not None and laps_remaining <= 3 and not (wear_rem is not None and wear_rem <= 60.0):
        box = False
        reason_bits.append("late in stint")

    action = "BOX" if box else "STAY OUT"

    # lap estimate: "now" or "now+1" (simple)
    box_lap_est = None
    if current_lap is not None:
        box_lap_est = int(current_lap + (0 if box else 1))

    # Confidence: small heuristic
    conf = 0.55
    if wear_rem is not None:
        if wear_rem <= 65.0:
            conf += 0.20
        elif wear_rem <= 72.0:
            conf += 0.10
    if pit_loss_s <= base_pit_loss_s * 0.85:
        conf += 0.05
    conf = max(0.10, min(0.90, conf))

    rec = StrategyRecommendation(
        action=action,
        target_tyre=best_tyre,
        box_lap_estimate=box_lap_est,
        confidence=conf,
        reasoning=", ".join(reason_bits) if reason_bits else "insufficient signals",
    )

    # ---- Plans (A/B/C) ----
    plan_a = StrategyCard(
        name="Plan A (Safe)",
        description=f"Stable pace on {best_tyre}. Pit loss est: {pit_loss_s:.1f}s.",
        next_pit_lap=box_lap_est if box else None,
        tyre_plan=f"{tyre_now or '—'} → {best_tyre}",
        confidence=max(0.10, conf - 0.05),
    )

    undercut_lap = int(max(1, int(current_lap or 1) - 1)) if current_lap is not None else None
    plan_b = StrategyCard(
        name="Plan B (Aggro)",
        description="Earlier stop to attempt undercut if clean air is available.",
        next_pit_lap=undercut_lap,
        tyre_plan=f"{tyre_now or '—'} → {best_tyre}",
        confidence=max(0.10, conf - 0.12),
    )

    sc_hint = "SC/VSC window" if pit_loss_s < base_pit_loss_s else "normal green-flag window"
    plan_c = StrategyCard(
        name="Plan C (SC-ready)",
        description=f"Keep it flexible: {sc_hint}.",
        next_pit_lap=box_lap_est,
        tyre_plan=f"{tyre_now or '—'} → {best_tyre} (flex)",
        confidence=max(0.10, conf - 0.08),
    )

    return rec, [plan_a, plan_b, plan_c]


# Kept for UI smoke-tests / fallback
def generate_placeholder_cards() -> List[StrategyCard]:
    return [
        StrategyCard(name="Plan A (Safe)", description="Conservative / later stop", tyre_plan="M → H"),
        StrategyCard(name="Plan B (Aggro)", description="Earlier stop / undercut", tyre_plan="S → M → S"),
        StrategyCard(name="Plan C (SC-ready)", description="Flexible for SC/VSC", tyre_plan="M → H (flex window)"),
    ]