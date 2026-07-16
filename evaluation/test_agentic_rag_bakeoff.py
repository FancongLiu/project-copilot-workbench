from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import duckdb
import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = REPOSITORY_ROOT / "scripts" / "generate_agentic_hvac_bakeoff.py"
QUESTION_MANIFEST = REPOSITORY_ROOT / "evaluation" / "agentic_rag_bakeoff.json"
COMMITTED_CORPUS = REPOSITORY_ROOT / "examples" / "agentic_hvac_bakeoff"


def _load_generator():
    assert GENERATOR_PATH.is_file(), "Agentic HVAC bake-off generator is missing"
    spec = importlib.util.spec_from_file_location(
        "agentic_hvac_generator", GENERATOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        item
        for item in root.rglob("*")
        if item.is_file()
        and item.suffix.casefold() != ".duckdb"
        and item.name not in {"telemetry.csv", "telemetry.parquet"}
    ):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@pytest.fixture(scope="module")
def generated_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    generator = _load_generator()
    generated = tmp_path_factory.mktemp("agentic-hvac") / "generated"
    generator.generate(generated)
    return generated


def test_generator_produces_a_deterministic_multi_asset_ten_second_corpus(
    generated_corpus: Path,
) -> None:
    assert _tree_digest(generated_corpus) == _tree_digest(COMMITTED_CORPUS)
    manifest = json.loads(
        (generated_corpus / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["fully_synthetic"] is True
    assert manifest["license"] == "CC0-1.0"
    assert manifest["sample_interval_seconds"] == 10
    assert manifest["duration_hours"] >= 72
    assert len(manifest["assets"]) == 4
    assert manifest["expected_raw_rows"] == 103_650
    assert manifest["expected_unique_rows"] == 103_620
    assert manifest["expected_missing_grid_points"] == 60
    assert manifest["engineering_use"] == "evaluation_only_not_engineering_guidance"
    connection = duckdb.connect(
        str(generated_corpus / "datasets" / "hvac_bakeoff.duckdb"), read_only=True
    )
    try:
        assert (
            connection.execute("SELECT count(*) FROM telemetry_raw").fetchone()[0]
            == 103_650
        )
        assert (
            connection.execute("SELECT count(*) FROM telemetry_clean").fetchone()[0]
            == 103_620
        )
        assert {row[0] for row in connection.execute("SHOW TABLES").fetchall()} >= {
            "assets",
            "config_history",
            "point_aliases",
            "telemetry_raw",
        }
    finally:
        connection.close()


def test_generated_telemetry_covers_hvac_points_and_known_data_quality_faults(
    generated_corpus: Path,
) -> None:
    telemetry_path = generated_corpus / "datasets" / "telemetry.csv"
    with telemetry_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    required_columns = {
        "timestamp",
        "asset_id",
        "operating_mode",
        "ingest_seq",
        "ambient_temp_c",
        "ambient_rh_pct",
        "return_air_temp_c",
        "supply_air_temp_c",
        "supply_air_sp_c",
        "suction_pressure_kpa_g",
        "discharge_pressure_kpa_g",
        "suction_temp_c",
        "discharge_temp_c",
        "liquid_temp_c",
        "outdoor_coil_temp_c",
        "superheat_k",
        "subcooling_k",
        "compressor_cmd_hz",
        "compressor_fb_hz",
        "outdoor_fan_cmd_pct",
        "outdoor_fan_fb_pct",
        "eev_cmd_pct",
        "eev_fb_pct",
        "electric_power_kw",
        "thermal_output_kw",
        "cop",
        "alarm_code",
        "quality_code",
    }
    assert rows
    assert required_columns <= set(rows[0])
    assert len({row["asset_id"] for row in rows}) == 4
    assert len(rows) == 103_650

    quality_counts = Counter(row["quality_code"] for row in rows)
    assert quality_counts["duplicate"] >= 1
    assert quality_counts["out_of_order"] >= 1

    parsed = [datetime.fromisoformat(row["timestamp"]) for row in rows]
    assert any(current < previous for previous, current in zip(parsed, parsed[1:]))


def test_generated_hidden_truth_labels_general_hvac_events_not_only_defrost(
    generated_corpus: Path,
) -> None:
    truth = json.loads(
        (generated_corpus / "hidden_truth" / "events.json").read_text(encoding="utf-8")
    )
    assert truth["expected_raw_rows"] == 103_650
    assert truth["expected_unique_rows"] == 103_620
    assert truth["expected_missing_grid_points"] == 60
    event_types = {event["event_type"] for event in truth["events"]}
    assert len(event_types) >= 10
    assert "defrost_sequence" in event_types
    assert {
        "sensor_drift",
        "command_feedback_mismatch",
        "high_discharge_temperature",
        "short_cycling",
        "efficiency_degradation",
        "configuration_change",
    } <= event_types
    assert (
        sum(event["event_type"] == "defrost_sequence" for event in truth["events"]) <= 2
    )
    assert all(event["start"] < event["end"] for event in truth["events"])
    assert all(event["expected_observation"] for event in truth["events"])


def test_generated_knowledge_corpus_contains_versioned_engineering_context(
    generated_corpus: Path,
) -> None:
    required = {
        "SYNTHETIC_DATA_PROVENANCE.md",
        "docs/source/background/project-overview.md",
        "docs/source/background/asset-register.md",
        "docs/source/configuration/point-dictionary.csv",
        "docs/source/configuration/current-unit-configuration.md",
        "docs/source/configuration/superseded-unit-configuration.md",
        "docs/source/controls/control-sequence.md",
        "docs/source/meetings/controls-review.md",
        "docs/source/decisions/change-register.md",
        "docs/source/service/service-work-orders.md",
        "docs/source/sops/data-analysis-sop.md",
        "datasets/assets.csv",
        "datasets/config_history.csv",
        "datasets/point_aliases.csv",
        "datasets/hvac_bakeoff.duckdb",
        "datasets/telemetry.parquet",
    }
    existing = {
        path.relative_to(generated_corpus).as_posix()
        for path in generated_corpus.rglob("*")
        if path.is_file()
    }
    assert required <= existing
    provenance = (generated_corpus / "SYNTHETIC_DATA_PROVENANCE.md").read_text(
        encoding="utf-8"
    )
    assert "fully synthetic" in provenance.casefold()
    assert "not engineering guidance" in provenance.casefold()

    point_dictionary = (
        generated_corpus / "docs" / "source" / "configuration" / "point-dictionary.csv"
    ).read_text(encoding="utf-8")
    assert "aliases" in point_dictionary.splitlines()[0]
    assert "P_SUC" in point_dictionary


def test_candidate_evidence_contract_never_requires_hidden_truth() -> None:
    manifest = json.loads(QUESTION_MANIFEST.read_text(encoding="utf-8"))

    for case in manifest["cases"]:
        assert all(
            "events.json" not in evidence and "hidden_truth" not in evidence
            for evidence in case["evidence_contract"]
        ), case["id"]


def test_bakeoff_manifest_covers_knowledge_data_combined_ux_and_safety() -> None:
    assert QUESTION_MANIFEST.is_file(), "Agentic RAG bake-off manifest is missing"
    payload = json.loads(QUESTION_MANIFEST.read_text(encoding="utf-8"))
    assert payload["fully_synthetic"] is True
    assert payload["candidate_neutral"] is True
    cases = payload["cases"]
    category_counts = Counter(case["category"] for case in cases)
    assert category_counts["knowledge"] >= 10
    assert category_counts["data"] >= 15
    assert category_counts["combined"] >= 10
    assert category_counts["clarification"] >= 4
    assert category_counts["safety"] >= 4
    assert category_counts["presentation"] >= 4
    assert len(cases) >= 50
    assert all(case["question"].strip() for case in cases)
    assert all(case["expected"] for case in cases)
    assert all(case["evidence_contract"] for case in cases)
