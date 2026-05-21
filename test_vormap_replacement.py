"""Tests for vormap_replacement."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from vormap_replacement import (
    ReplacementAdvisor,
    SensorAsset,
    to_json,
    to_markdown,
    to_text,
)


FIXED_NOW = datetime(2026, 5, 21, tzinfo=timezone.utc)


def _now():
    return FIXED_NOW


def _adv(risk="balanced"):
    return ReplacementAdvisor(risk_appetite=risk, now_fn=_now)


def test_empty_fleet_grades_a_with_observability_action():
    rep = _adv().analyze([])
    assert rep.grade == "A"
    assert any(a.id == "MAINTAIN_OBSERVABILITY" for a in rep.playbook)
    assert "EMPTY_FLEET" in rep.insights


def test_catastrophic_failure_with_backup_swaps():
    asset = SensorAsset(
        sensor_id="X",
        install_date="2018-01-01",
        failure_count=6,
        error_count_30d=80,
        reading_count_30d=200,
        has_backup=True,
        criticality=5,
        downtime_hours_if_failed=10,
    )
    rep = _adv().analyze([asset])
    f = rep.findings[0]
    assert f.verdict == "SWAP_TO_BACKUP"
    assert f.priority == "P0"
    assert any(a.id == "SWAP_TO_BACKUP_UNITS" for a in rep.playbook)
    assert rep.grade == "F"


def test_catastrophic_failure_without_backup_replaces():
    asset = SensorAsset(
        sensor_id="X",
        install_date="2018-01-01",
        failure_count=6,
        error_count_30d=80,
        reading_count_30d=200,
        has_backup=False,
        criticality=5,
    )
    rep = _adv().analyze([asset])
    f = rep.findings[0]
    assert f.verdict == "REPLACE_NOW"
    assert any(a.id == "EXECUTE_EMERGENCY_REPLACEMENT" for a in rep.playbook)
    assert any(a.id == "EXPEDITE_BACKUP_PROCUREMENT" for a in rep.playbook)


def test_healthy_new_sensor_monitor():
    asset = SensorAsset(
        sensor_id="Y",
        install_date="2025-06-01",
        failure_count=0,
        error_count_30d=1,
        reading_count_30d=500,
        criticality=2,
    )
    rep = _adv().analyze([asset])
    assert rep.findings[0].verdict == "MONITOR"
    assert rep.grade == "A"


def test_near_eol_with_errors_replace_soon():
    asset = SensorAsset(
        sensor_id="Z",
        install_date="2021-01-01",  # ~5y old; lifetime 1825d default
        failure_count=1,
        error_count_30d=50,
        reading_count_30d=300,
        criticality=3,
    )
    rep = _adv().analyze([asset])
    assert rep.findings[0].verdict in ("REPLACE_SOON", "REPLACE_NOW")


def test_insufficient_data_path():
    asset = SensorAsset(sensor_id="?")  # no install date, no telemetry, no failures
    rep = _adv().analyze([asset])
    f = rep.findings[0]
    assert f.verdict == "INSUFFICIENT_DATA"
    assert f.priority == "P3"
    assert "NO_TELEMETRY_OR_INSTALL_RECORD" in f.reasons


def test_decommission_low_value():
    asset = SensorAsset(
        sensor_id="DEC",
        install_date="2024-01-01",  # young
        failure_count=0,
        error_count_30d=0,
        reading_count_30d=100,
        criticality=1,
        replacement_cost_usd=3000,
        downtime_hours_if_failed=1,
    )
    rep = _adv().analyze([asset])
    # Healthy + low criticality + cost-outweighs-value => DECOMMISSION
    assert rep.findings[0].verdict == "DECOMMISSION"


def test_risk_appetite_monotonic():
    asset = SensorAsset(
        sensor_id="M",
        install_date="2020-01-01",
        failure_count=2,
        error_count_30d=30,
        reading_count_30d=400,
        criticality=3,
    )
    c = _adv("cautious").analyze([asset]).findings[0].risk_score
    b = _adv("balanced").analyze([asset]).findings[0].risk_score
    a = _adv("aggressive").analyze([asset]).findings[0].risk_score
    assert c >= b >= a


def test_critical_p0_forces_f_grade():
    asset = SensorAsset(
        sensor_id="C",
        install_date="2017-01-01",
        failure_count=10,
        criticality=5,
        has_backup=False,
    )
    rep = _adv().analyze([asset])
    assert rep.grade == "F"


def test_multiple_p0_opens_war_room():
    assets = [
        SensorAsset(sensor_id=f"S{i}",
                    install_date="2017-01-01",
                    failure_count=10,
                    criticality=5)
        for i in range(3)
    ]
    rep = _adv().analyze(assets)
    assert any(a.id == "OPEN_REPLACEMENT_WAR_ROOM" for a in rep.playbook)


def test_findings_sorted_p0_first():
    assets = [
        SensorAsset(sensor_id="A_healthy",
                    install_date="2025-01-01", criticality=2),
        SensorAsset(sensor_id="Z_dead",
                    install_date="2017-01-01", failure_count=10, criticality=5),
    ]
    rep = _adv().analyze(assets)
    assert rep.findings[0].sensor_id == "Z_dead"
    assert rep.findings[0].priority == "P0"


def test_json_byte_stable():
    asset = SensorAsset(
        sensor_id="J",
        install_date="2021-01-01",
        failure_count=1,
        error_count_30d=10,
        reading_count_30d=200,
        criticality=3,
    )
    a = to_json(_adv().analyze([asset]))
    b = to_json(_adv().analyze([asset]))
    assert a == b
    parsed = json.loads(a)
    assert parsed["grade"] in {"A", "B", "C", "D", "F"}


def test_markdown_has_sections():
    asset = SensorAsset(sensor_id="M",
                        install_date="2020-01-01",
                        failure_count=1, criticality=3)
    md = to_markdown(_adv().analyze([asset]))
    assert "## Summary" in md
    assert "## Findings" in md
    assert "## Playbook" in md
    assert "## Insights" in md


def test_text_render_has_headline():
    rep = _adv().analyze([])
    text = to_text(rep)
    assert text.startswith("VERDICT:")
    assert "Playbook:" in text
    assert "Insights:" in text


def test_input_not_mutated():
    asset = SensorAsset(sensor_id="I",
                        install_date="2020-01-01",
                        failure_count=1, criticality=3)
    snapshot = (asset.sensor_id, asset.failure_count, asset.criticality)
    _adv().analyze([asset])
    assert (asset.sensor_id, asset.failure_count, asset.criticality) == snapshot


def test_invalid_risk_appetite_raises():
    with pytest.raises(ValueError):
        ReplacementAdvisor(risk_appetite="paranoid", now_fn=_now)


def test_cautious_adds_audit_when_degraded():
    asset = SensorAsset(sensor_id="S",
                        install_date="2019-01-01",
                        failure_count=2, error_count_30d=30,
                        reading_count_30d=300, criticality=3)
    rep = _adv("cautious").analyze([asset])
    if rep.grade in ("C", "D", "F"):
        assert any(a.id == "SCHEDULE_FLEET_REPLACEMENT_AUDIT"
                   for a in rep.playbook)


def test_aggressive_trims_p3_fallback():
    assets = [
        SensorAsset(sensor_id="A",
                    install_date="2017-01-01",
                    failure_count=10, criticality=5),
        SensorAsset(sensor_id="B",
                    install_date="2025-01-01", criticality=2),
    ]
    rep = _adv("aggressive").analyze(assets)
    p3 = [a for a in rep.playbook if a.priority == "P3"]
    assert p3 == []


def test_from_record_roundtrip():
    row = {
        "sensor_id": "R1",
        "install_date": "2020-01-01",
        "failure_count": "2",
        "criticality": "4",
        "has_backup": "true",
        "replacement_cost_usd": "500",
    }
    a = SensorAsset.from_record(row)
    assert a.sensor_id == "R1"
    assert a.failure_count == 2
    assert a.criticality == 4
    assert a.has_backup is True
    assert a.replacement_cost_usd == 500.0
