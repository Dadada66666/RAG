from pathlib import Path

from docparser.evaluation.benchmark import load_manifest
from docparser.evaluation.models import GoldenDatasetManifest
from docparser.evaluation.schema import evaluation_schema_bytes


def test_development_manifest_schema_is_deterministic() -> None:
    assert evaluation_schema_bytes() == evaluation_schema_bytes()
    schema = GoldenDatasetManifest.model_json_schema()
    assert "GoldenDocument" in schema["$defs"]
    assert "PageAnnotation" in schema["$defs"]


def test_committed_empty_development_manifest_is_loadable_without_accuracy_claim() -> None:
    manifest = load_manifest(Path("tests/golden/parsing/development-manifest.yaml"))

    assert manifest.documents == ()
    assert manifest.target_page_count_min == 20
