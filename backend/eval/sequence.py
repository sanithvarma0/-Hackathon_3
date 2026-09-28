"""The incident sequence one eval run faces (BUILD_PLAN.md 11.2).

The sequence depends only on the seed, so the memory-ON and memory-OFF runs of a seed face the
identical incidents in the identical order (paired design):

- Classes alternate: rounds of one incident per class, never the same class twice in a row.
- Discrimination probe: in every round a sensor drift comes right after a config regression —
  the case where replaying the last fix (rollback) would be wrong.
- Transfer: a class's first two exposures land on M1–M3; later exposures on the held-out
  machines M4–M5, so "seen it before" means "seen it on another machine".
- Quiet time between incidents (2–12 sim-hours) keeps the plant's recent history realistic:
  sometimes the previous incident's events are still within the agent's look-back windows.
- Every incident carries its own spec seed (identical signature, magnitude, timing, wording).
"""

import random
from dataclasses import dataclass

from backend.schemas import IncidentType

CLASSES: tuple[IncidentType, ...] = (
    "config_regression",
    "sensor_drift",
    "network_failure",
    "resource_exhaustion",
)
TRAIN_MACHINES = ("M1", "M2", "M3")
HELD_OUT_MACHINES = ("M4", "M5")
TRAIN_EXPOSURES = 2  # exposures 1-2 on M1-M3, 3+ on M4-M5
GAP_SIM_S = (2 * 3600, 12 * 3600)


@dataclass(frozen=True)
class Planned:
    position: int  # 1-based order in the run
    type: IncidentType
    machine: str
    exposure: int  # k-th incident of this class in the run
    spec_seed: int
    gap_sim_s: int  # quiet time before this incident
    held_out: bool  # on M4-M5 (transfer test)
    after_config_regression: bool  # sensor drift right after a config regression (probe)


def build_sequence(seed: int, n: int = 24) -> list[Planned]:
    if n % len(CLASSES):
        raise ValueError(f"n must be a multiple of {len(CLASSES)}")
    rng = random.Random(f"memoryops-eval-sequence-{seed}")
    order: list[IncidentType] = []
    for _ in range(n // len(CLASSES)):
        while True:
            blocks: list[list[IncidentType]] = [
                ["config_regression", "sensor_drift"],
                ["network_failure"],
                ["resource_exhaustion"],
            ]
            rng.shuffle(blocks)
            flat = [c for block in blocks for c in block]
            if not order or order[-1] != flat[0]:
                break
        order.extend(flat)

    planned: list[Planned] = []
    seen: dict[IncidentType, int] = {}
    previous_machine: str | None = None
    for i, incident_type in enumerate(order):
        exposure = seen.get(incident_type, 0) + 1
        seen[incident_type] = exposure
        held_out = exposure > TRAIN_EXPOSURES
        pool = [m for m in (HELD_OUT_MACHINES if held_out else TRAIN_MACHINES)]
        if previous_machine in pool and len(pool) > 1:
            pool.remove(previous_machine)  # vary the machine between consecutive incidents
        machine = rng.choice(pool)
        previous_machine = machine
        planned.append(
            Planned(
                position=i + 1,
                type=incident_type,
                machine=machine,
                exposure=exposure,
                spec_seed=rng.randrange(2**31),
                gap_sim_s=rng.randint(*GAP_SIM_S),
                held_out=held_out,
                after_config_regression=(
                    incident_type == "sensor_drift"
                    and i > 0
                    and order[i - 1] == "config_regression"
                ),
            )
        )
    return planned
