from __future__ import annotations

import struct

from app.telemetry.utils import TEAM_ID_TO_NAME


def handle_participants_packet(self, hdr, data: bytes) -> None:
    """
    PID 4: Participants parsing (team id/name etc.)
    """
    # Participants packet (F1 25):

    # header(29) + numActiveCars(1) + 22 * ParticipantData(57)

    # ParticipantData (F1 25) layout:

    # 0 aiControlled

    # 1 driverId

    # 2 networkId

    # 3 teamId

    # 4 myTeam

    # 5 raceNumber

    # 6 nationality

    # 7..38 name[32]

    # 39 yourTelemetry

    # 40 showOnlineNames

    # 41..42 techLevel (uint16)

    # 43 platform

    # 44 numColours

    # 45..56 liveryColours[4] (4 * RGB)

    try:

        base = int(hdr.get("headerSize", 29))

        # num_active = struct.unpack_from("<B", data, base)[0]  # optional

        psize = 57

        p0 = base + 1

        pidx = int(self._player_idx) if self._player_idx is not None else int(hdr.get("playerCarIndex", 0))

        if 0 <= pidx < 22 and (p0 + (pidx + 1) * psize) <= len(data):

            off = p0 + pidx * psize

            team_id = struct.unpack_from("<B", data, off + 3)[0]

            team_name = TEAM_ID_TO_NAME.get(int(team_id), f"TEAM{int(team_id)}")

            changed = False

            if int(team_id) != (self.state.player_team_id if self.state.player_team_id is not None else -1):
                self.state.player_team_id = int(team_id)

                changed = True

            if team_name != (self.state.player_team_name or ""):
                self.state.player_team_name = team_name

                changed = True

            # optional debug

            # if changed:

            #     print(f"[P4] player teamId={team_id} teamName={team_name}")


    except Exception:

        pass
    pass
