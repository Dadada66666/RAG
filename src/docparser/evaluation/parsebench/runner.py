"""External pinned ParseBench evaluator boundary; no official formulas live here."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import TypeAdapter

from docparser.evaluation.parsebench.models import (
    PARSEBENCH_ADAPTER_VERSION,
    PARSEBENCH_COMMIT,
    OfficialParseBenchResult,
    ParseBenchRunRequest,
)
from docparser.ir.types import BoundedJsonObject, Sha256Digest

CommandExecutor = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]
_JSON_OBJECT_ADAPTER = TypeAdapter(BoundedJsonObject)


def _execute(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=3600,
    )


def _digest(path: Path) -> Sha256Digest:
    return Sha256Digest(f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}")


def _validated_json_object(path: Path) -> BoundedJsonObject:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("official ParseBench result artifact must contain a JSON object")
    return _JSON_OBJECT_ADAPTER.validate_python(payload)


def run_official_parsebench(
    request: ParseBenchRunRequest,
    *,
    executor: CommandExecutor = _execute,
) -> OfficialParseBenchResult:
    """Run an explicitly configured official evaluator from an exact checkout."""

    if str(request.repository_commit) != PARSEBENCH_COMMIT:
        raise ValueError("ParseBench request does not use the supported pinned commit")
    revision = executor(
        ("git", "rev-parse", "HEAD"),
        request.checkout_path,
    )
    if revision.returncode != 0:
        raise RuntimeError(f"cannot inspect ParseBench checkout: {revision.stderr.strip()}")
    if revision.stdout.strip() != PARSEBENCH_COMMIT:
        raise ValueError("ParseBench checkout HEAD does not match the pinned commit")
    completed = executor(request.evaluator_command, request.checkout_path)
    if completed.returncode != 0:
        raise RuntimeError(f"official ParseBench evaluator failed: {completed.stderr.strip()}")
    if not request.official_result_path.is_file():
        raise RuntimeError("official ParseBench evaluator did not produce the declared result")
    metrics = _validated_json_object(request.official_result_path)
    return OfficialParseBenchResult(
        repository_commit=request.repository_commit,
        dataset_revision=request.dataset_revision,
        benchmark_id=request.benchmark_id,
        subset_id=request.subset_id,
        subset_manifest_digest=request.subset_manifest_digest,
        evaluator_version=request.evaluator_version,
        evaluator_command=request.evaluator_command,
        adapter_version=PARSEBENCH_ADAPTER_VERSION,
        environment_digest=request.environment_digest,
        hardware_description=request.hardware_description,
        official_result_digest=_digest(request.official_result_path),
        official_metrics=metrics,
    )
