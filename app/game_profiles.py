# app/game_profiles.py
from dataclasses import dataclass


@dataclass(frozen=True)
class GameProfile:
    key: str
    name: str
    packet_format: int

    header_size: int
    lap_car_size: int
    car_status_size: int

    lap_time_is_float: bool
    has_sector_start_distances: bool
    has_minisectors: bool


GAME_PROFILES = {
    "AUTO": GameProfile(
        key="AUTO",
        name="Auto Detect",
        packet_format=-1,
        header_size=0,
        lap_car_size=0,
        car_status_size=0,
        lap_time_is_float=False,
        has_sector_start_distances=False,
        has_minisectors=False,
    ),

    "F1_25": GameProfile(
        key="F1_25",
        name="F1 25",
        packet_format=2025,
        header_size=29,
        lap_car_size=57,
        car_status_size=55,
        lap_time_is_float=False,
        has_sector_start_distances=True,
        has_minisectors=True,
    ),

    "F1_2020": GameProfile(
        key="F1_2020",
        name="F1 2020",
        packet_format=2020,
        header_size=24,
        lap_car_size=53,
        car_status_size=60,
        lap_time_is_float=True,
        has_sector_start_distances=False,
        has_minisectors=False,
    ),
}