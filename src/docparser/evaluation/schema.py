"""Deterministic generated schema for development Golden manifests."""

from __future__ import annotations

import json
from pathlib import Path

from docparser.evaluation.models import GoldenDatasetManifest
from docparser.evaluation.parsebench.models import ParseBenchSubsetManifest

DEFAULT_EVALUATION_SCHEMA = Path("schemas/evaluation/parsing-golden.schema.json")
DEFAULT_PARSEBENCH_SUBSET_SCHEMA = Path("schemas/evaluation/parsebench-subset.schema.json")


def evaluation_schema_bytes() -> bytes:
    schema = GoldenDatasetManifest.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://schemas.docparser.example/evaluation/parsing-golden-v1.json"
    return (
        json.dumps(
            schema,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def evaluation_schema_is_current(path: Path = DEFAULT_EVALUATION_SCHEMA) -> bool:
    return path.is_file() and path.read_bytes() == evaluation_schema_bytes()


def write_evaluation_schema(path: Path = DEFAULT_EVALUATION_SCHEMA) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(evaluation_schema_bytes())


def parsebench_subset_schema_bytes() -> bytes:
    schema = ParseBenchSubsetManifest.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://schemas.docparser.example/evaluation/parsebench-subset-v1.json"
    return (
        json.dumps(
            schema,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def parsebench_subset_schema_is_current(
    path: Path = DEFAULT_PARSEBENCH_SUBSET_SCHEMA,
) -> bool:
    return path.is_file() and path.read_bytes() == parsebench_subset_schema_bytes()


def write_parsebench_subset_schema(
    path: Path = DEFAULT_PARSEBENCH_SUBSET_SCHEMA,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(parsebench_subset_schema_bytes())
