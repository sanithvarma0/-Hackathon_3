"""Plant topology (BUILD_PLAN.md 5.2).

FEED -> M1 -> M2 -> M3 -> M4 -> SHIP        GW-A: M1, M2, M5
               ^           ^                 GW-B: M3, M4
               +--- M5 ----+   (M5 loads/unloads M2 and M4)
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MachineProfile:
    id: str
    name: str
    profile: str
    gateway: str
    axes: tuple[str, ...]
    controller: str  # log source name
    base_config_version: tuple[int, int, int]


MACHINES: dict[str, MachineProfile] = {
    m.id: m
    for m in (
        MachineProfile(
            "M1",
            "CNC vertical mill",
            "Haas VF-4, commissioned 2021",
            "GW-A",
            ("X", "Y", "Z"),
            "haas-ngc",
            (4, 8, 2),
        ),
        MachineProfile(
            "M2",
            "CNC lathe",
            "Mazak QT-250, commissioned 2019",
            "GW-A",
            ("X", "Z", "C"),
            "mazatrol-smooth",
            (2, 14, 3),
        ),
        MachineProfile(
            "M3",
            "Robotic welding cell",
            "Fanuc ARC Mate 100iD, commissioned 2022",
            "GW-B",
            ("J1", "J2", "J3", "J4", "J5", "J6"),
            "fanuc-r30ib",
            (9, 3, 1),
        ),
        MachineProfile(
            "M4",
            "5-axis machining center",
            "DMG Mori NVX 5080, commissioned 2020",
            "GW-B",
            ("X", "Y", "Z", "B", "C"),
            "celos-840d",
            (3, 2, 0),
        ),
        MachineProfile(
            "M5",
            "Robotic material-handling cell",
            "ABB IRB 6700 with vision-guided gripper, commissioned 2022",
            "GW-A",
            ("J1", "J2", "J3", "J4", "J5", "J6"),
            "abb-omnicore",
            (7, 1, 4),
        ),
    )
}

GATEWAYS: dict[str, tuple[str, ...]] = {"GW-A": ("M1", "M2", "M5"), "GW-B": ("M3", "M4")}

LINE: tuple[str, ...] = ("M1", "M2", "M3", "M4")
FEEDS: dict[str, tuple[str, ...]] = {"M5": ("M2", "M4")}


def machines_on_gateway(gateway: str) -> tuple[str, ...]:
    return GATEWAYS[gateway]


def downstream(machine_id: str) -> tuple[str, ...]:
    """Machines whose flow is starved when `machine_id` degrades."""
    if machine_id in FEEDS:
        fed = FEEDS[machine_id]
        start = min(LINE.index(m) for m in fed)
        return LINE[start:]
    return LINE[LINE.index(machine_id) + 1 :]


def format_version(v: tuple[int, int, int]) -> str:
    return f"v{v[0]}.{v[1]}.{v[2]}"
