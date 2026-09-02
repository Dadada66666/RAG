from pathlib import Path

from docparser.evaluation.benchmark import load_manifest
from docparser.evaluation.models import GoldenDatasetManifest
from docparser.evaluation.parsebench.models import (
    ParseBenchSubsetManifest,
    SubsetSelectionStatus,
)
from docparser.evaluation.schema import (
    evaluation_schema_bytes,
    parsebench_subset_schema_bytes,
)


def test_development_manifest_schema_is_deterministic() -> None:
    assert evaluation_schema_bytes() == evaluation_schema_bytes()
    schema = GoldenDatasetManifest.model_json_schema()
    assert "GoldenDocument" in schema["$defs"]
    assert "PageAnnotation" in schema["$defs"]


def test_parsebench_subset_schema_and_unprovisioned_manifests_are_deterministic() -> None:
    assert parsebench_subset_schema_bytes() == parsebench_subset_schema_bytes()
    for split in ("dev", "holdout"):
        path = Path(f"tests/golden/manifests/parsebench-complex-v1-{split}.json")
        manifest = ParseBenchSubsetManifest.model_validate_json(path.read_text(encoding="utf-8"))
        assert manifest.selection_status is SubsetSelectionStatus.UNPROVISIONED
        assert manifest.selected_items == ()


def test_committed_empty_development_manifest_is_loadable_without_accuracy_claim() -> None:
    manifest = load_manifest(Path("tests/golden/parsing/development-manifest.yaml"))

    assert manifest.documents == ()
    assert manifest.target_page_count_min == 20
