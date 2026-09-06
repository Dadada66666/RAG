"""External pinned ParseBench evaluator boundary; no official formulas live here."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from docparser.evaluation.parsebench.models import (
    PARSEBENCH_ADAPTER_VERSION,
    PARSEBENCH_COMMIT,
    OfficialParseBenchResult,
    ParseBenchRunRequest,
)
from docparser.ir.types import Sha256Digest

CommandExecutor = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])


def _execute(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=3600,
    )


_SHARED_EVALUATION_GROUPS = {"text": ("text_content", "text_formatting")}


def official_evaluation_groups(export_root: Path) -> tuple[str, ...]:
    """Mirror the pinned ParseBench pipeline's inference-to-evaluation routing."""

    inference_groups = {
        result_path.parent.name for result_path in export_root.rglob("*.result.json")
    }
    groups: set[str] = set()
    for inference_group in inference_groups:
        groups.update(_SHARED_EVALUATION_GROUPS.get(inference_group, (inference_group,)))
    return tuple(sorted(groups))


def _validated_json_object(path: Path) -> dict[str, JsonValue]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("official ParseBench result artifact must contain a JSON object")
    return _JSON_OBJECT_ADAPTER.validate_python(payload)


def official_evaluator_command(
    request: ParseBenchRunRequest,
    *,
    group: str,
    report_root: Path,
) -> tuple[str, ...]:
    """Build the pinned ParseBench 0.2.0 evaluation-only command."""

    return (
        str(request.parsebench_python),
        "-m",
        "parse_bench.cli",
        "evaluation",
        "run",
        "--output_dir",
        str(request.export_root),
        "--test_cases_dir",
        str(request.dataset_root),
        "--product_type",
        "parse",
        "--report_dir",
        str(report_root),
        "--group",
        group,
        "--force=True",
    )


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
    groups = official_evaluation_groups(request.export_root)
    if not groups:
        raise ValueError("ParseBench prediction root contains no result files")
    commands: list[tuple[str, ...]] = []
    reports: dict[str, JsonValue] = {}
    report_payloads: dict[str, bytes] = {}
    for group in groups:
        group_report_root = request.report_root / group
        command = official_evaluator_command(
            request,
            group=group,
            report_root=group_report_root,
        )
        commands.append(command)
        completed = executor(command, request.checkout_path)
        if completed.returncode != 0:
            raise RuntimeError(
                f"official ParseBench evaluator failed for {group}: {completed.stderr.strip()}"
            )
        official_result_path = group_report_root / "_evaluation_report.json"
        if not official_result_path.is_file():
            raise RuntimeError(
                f"official ParseBench evaluator did not produce the {group} result"
            )
        reports[group] = _validated_json_object(official_result_path)
        report_payloads[group] = official_result_path.read_bytes()
    report_bundle = json.dumps(
        reports,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    single_group = len(groups) == 1
    if single_group:
        single_report = reports[groups[0]]
        assert isinstance(single_report, dict)
        official_metrics = single_report
    else:
        official_metrics = reports
    digest_payload = report_payloads[groups[0]] if single_group else report_bundle
    return OfficialParseBenchResult(
        repository_commit=request.repository_commit,
        dataset_revision=request.dataset_revision,
        benchmark_id=request.benchmark_id,
        subset_id=request.subset_id,
        subset_manifest_digest=request.subset_manifest_digest,
        evaluator_version=request.evaluator_version,
        evaluator_command=commands[0],
        evaluator_commands=tuple(commands),
        evaluation_groups=groups,
        adapter_version=PARSEBENCH_ADAPTER_VERSION,
        environment_digest=request.environment_digest,
        hardware_description=request.hardware_description,
        official_result_digest=Sha256Digest(
            f"sha256:{hashlib.sha256(digest_payload).hexdigest()}"
        ),
        official_metrics=official_metrics,
    )
