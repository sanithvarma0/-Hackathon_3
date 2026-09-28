import pytest
from pydantic import ValidationError

from backend.schemas import ACTIONS, METRICS, CustomIncidentSpec, MetricPoint


def test_action_whitelist_is_exactly_six():
    assert ACTIONS == (
        "ROLLBACK_CONFIG",
        "RESTART_MACHINE",
        "RECALIBRATE_SENSOR",
        "RESTART_GATEWAY",
        "CLEAR_CACHE",
        "ESCALATE_HUMAN",
    )


def test_metrics_cover_every_signal_class():
    for metric in ("throughput_pct", "sensor_variance", "packet_loss_pct", "memory_pct"):
        assert metric in METRICS


@pytest.mark.parametrize(
    "bad",
    [
        dict(throughput_delta=-60),
        dict(throughput_delta=0),
        dict(minutes_before=0),
        dict(error_rate=25),
        dict(temperature="scorching"),
        dict(network="flaky"),
        dict(notes="free text is not allowed"),
    ],
)
def test_custom_spec_rejects_out_of_range_and_free_text(bad):
    with pytest.raises(ValidationError):
        CustomIncidentSpec.model_validate({"machine": "M1", **bad})


def test_custom_spec_forbids_unknown_fields_by_default():
    assert CustomIncidentSpec.model_config.get("extra") == "forbid"


def test_models_are_immutable():
    point = MetricPoint(ts=1, value=2.0)
    with pytest.raises(ValidationError):
        point.value = 3.0  # type: ignore[misc]
