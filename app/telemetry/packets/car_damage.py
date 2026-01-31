from __future__ import annotations

import struct
from typing import Optional


def handle_car_damage_packet(self, hdr, data: bytes) -> None:
    """
    PID 10: CarDamage parsing (we decode tyre wear floats for player car)
    """
    # --- NEW (additive): tyre wear from CarDamage packet ---
    # We only decode the first 4 floats (tyresWear) per car.
    base = int(hdr.get("headerSize", 29))
    remaining = len(data) - base
    if remaining <= 0:
        return

    # In most games the per-car struct is stable; we only need first 16 bytes anyway.
    car_size = remaining // 22
    if car_size < 16:
        return

    pidx = int(hdr.get("playerCarIndex", 0))
    if not (0 <= pidx < 22):
        return

    off = base + pidx * car_size
    if off + 16 > len(data):
        return

    changed = False
    try:
        # order in spec comments often differs; we keep consistent mapping as FL, FR, RL, RR
        w1, w2, w3, w4 = struct.unpack_from("<ffff", data, off)

        def _to_wear_pct(x: float) -> Optional[float]:
            """
            Return tyre wear in percent (0..100):
            0 = new, 100 = fully worn.

            F1 25 CarDamage.m_tyresWear is already a percentage float (0..100).
            IMPORTANT:
            - Do NOT auto-scale 0..1 -> 0..100, because fresh tyres can legitimately be < 1.0 (%),
              e.g. 0.3 means 0.3% wear, not 30%.
            """
            try:
                xv = float(x)
            except Exception:
                return None

            # Defensive sanity: ignore clearly invalid values
            if xv < 0.0 or xv > 100.0:
                return None

            # Clamp just in case of tiny float noise (e.g. -0.0001 / 100.0001)
            if xv < 0.0:
                xv = 0.0
            if xv > 100.0:
                xv = 100.0

            return xv

        p1 = _to_wear_pct(w1)
        p2 = _to_wear_pct(w2)
        p3 = _to_wear_pct(w3)
        p4 = _to_wear_pct(w4)

        if p1 is not None and self.state.player_wear_fl != p1:
            self.state.player_wear_fl = p1
            changed = True
        if p2 is not None and self.state.player_wear_fr != p2:
            self.state.player_wear_fr = p2
            changed = True
        if p3 is not None and self.state.player_wear_rl != p3:
            self.state.player_wear_rl = p3
            changed = True
        if p4 is not None and self.state.player_wear_rr != p4:
            self.state.player_wear_rr = p4
            changed = True

    except Exception:
        pass

    if changed:
        self._dirty = True
    pass
