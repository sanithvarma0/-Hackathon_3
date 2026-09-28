"""Small, dependency-free statistics for the eval report (BUILD_PLAN.md 11.3).

- Means get a percentile bootstrap 95% CI (resampling incidents, or pairs for ON − OFF).
- Proportions from counts (false replays, recall@1) get a Wilson 95% interval, which stays
  sensible at small n and at 0 or n successes.
- Confidence honesty: Brier score and a reliability table.

Limitation, stated in the report: incidents within one seed share a world and a memory bank,
so they are not fully independent; with 3 seeds a seed-level (cluster) bootstrap would be too
coarse, so per-seed means are reported next to the pooled CIs.
"""

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

N_BOOT = 4000
Z95 = 1.959963984540054


@dataclass(frozen=True)
class Estimate:
    mean: float
    lo: float
    hi: float
    n: int

    def excludes_zero(self) -> bool:
        return self.lo > 0 or self.hi < 0

    def as_dict(self) -> dict[str, float | int]:
        return {"mean": self.mean, "lo": self.lo, "hi": self.hi, "n": self.n}


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def bootstrap(values: Sequence[float], *, seed: int = 0, n_boot: int = N_BOOT) -> Estimate | None:
    """Mean with a percentile bootstrap 95% CI; None when there is no data."""
    if not values:
        return None
    m = mean(values)
    if len(values) == 1:
        return Estimate(m, m, m, 1)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(mean([values[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot))
    return Estimate(m, means[int(0.025 * n_boot)], means[int(0.975 * n_boot) - 1], n)


def paired(
    on: dict[tuple[int, int], float], off: dict[tuple[int, int], float], *, seed: int = 0
) -> Estimate | None:
    """Mean ON − OFF over pairs present in both (same seed, same position)."""
    keys = sorted(set(on) & set(off))
    return bootstrap([on[k] - off[k] for k in keys], seed=seed)


def difference(
    a: Sequence[float], b: Sequence[float], *, seed: int = 0, n_boot: int = N_BOOT
) -> Estimate | None:
    """mean(a) − mean(b) for two independent samples, percentile bootstrap 95% CI."""
    if not a or not b:
        return None
    rng = random.Random(seed)
    d = mean(a) - mean(b)
    diffs = sorted(
        mean([a[rng.randrange(len(a))] for _ in a]) - mean([b[rng.randrange(len(b))] for _ in b])
        for _ in range(n_boot)
    )
    return Estimate(d, diffs[int(0.025 * n_boot)], diffs[int(0.975 * n_boot) - 1], len(a) + len(b))


def wilson(k: int, n: int) -> Estimate | None:
    if n == 0:
        return None
    p = k / n
    denom = 1 + Z95**2 / n
    centre = (p + Z95**2 / (2 * n)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95**2 / (4 * n * n)) / denom
    return Estimate(p, max(0.0, centre - half), min(1.0, centre + half), n)


def brier(probs: Sequence[float], outcomes: Sequence[int]) -> float | None:
    if not probs:
        return None
    return mean([(p - o) ** 2 for p, o in zip(probs, outcomes, strict=True)])


@dataclass(frozen=True)
class Bin:
    lo: float
    hi: float
    n: int
    mean_confidence: float | None
    accuracy: float | None


def reliability(
    probs: Sequence[float],
    outcomes: Sequence[int],
    edges: Sequence[float] = (0, 0.5, 0.7, 0.85, 0.95, 1.0),
) -> list[Bin]:
    """Stated confidence vs observed accuracy, per confidence band (last band includes 1.0)."""
    bins: list[Bin] = []
    for i, (lo, hi) in enumerate(zip(edges, edges[1:], strict=False)):
        last = i == len(edges) - 2
        members = [
            (p, o)
            for p, o in zip(probs, outcomes, strict=True)
            if lo <= p < hi or (last and p == hi)
        ]
        bins.append(
            Bin(
                lo,
                hi,
                len(members),
                mean([p for p, _ in members]) if members else None,
                mean([o for _, o in members]) if members else None,
            )
        )
    return bins
