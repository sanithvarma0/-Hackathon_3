"""The eval sequence (BUILD_PLAN.md 11.2): paired, interleaved, transfer, discrimination."""

from collections import Counter

import pytest

from backend.eval.sequence import CLASSES, build_sequence


def test_same_seed_same_sequence_different_seed_different_sequence():
    assert build_sequence(1) == build_sequence(1)  # the paired ON/OFF runs face this exactly
    assert build_sequence(1) != build_sequence(2)


@pytest.mark.parametrize("seed", range(20))
def test_design_constraints_hold_for_every_seed(seed):
    seq = build_sequence(seed, 24)
    types = [p.type for p in seq]
    assert Counter(types) == {c: 6 for c in CLASSES}
    assert all(a != b for a, b in zip(types, types[1:], strict=False))  # classes alternate
    for p in seq:
        assert p.held_out == (p.exposure > 2)
        assert p.machine in (("M4", "M5") if p.held_out else ("M1", "M2", "M3"))
        assert 2 * 3600 <= p.gap_sim_s <= 12 * 3600
    probes = [p for p in seq if p.after_config_regression]
    assert len(probes) == 6 and all(p.type == "sensor_drift" for p in probes)
    assert [p.position for p in seq] == list(range(1, 25))


def test_quick_run_still_covers_transfer():
    seq = build_sequence(0, 12)
    assert any(p.held_out for p in seq) and max(p.exposure for p in seq) == 3


def test_sequence_length_must_cover_every_class_equally():
    with pytest.raises(ValueError):
        build_sequence(0, 10)
