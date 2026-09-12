"""Project command-line interface."""

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from docparser.application.parsing import (
    ParsingConfig,
    parse_document_with_diagnostics,
    write_parse_outputs,
)
from docparser.application.qa import QAResult, ask_document
from docparser.application.qa_batch import (
    QABatchConfig,
    QAQuestion,
    load_qa_batch,
    run_qa_batch,
)
from docparser.application.retrieval_ab import run_retrieval_ab
from docparser.application.robust import robust_parse_document, write_robust_outputs
from docparser.config import load_config
from docparser.domain.parser_contract import RuntimeDevice
from docparser.evaluation import (
    load_manifest,
    prepare_ohr_rag_core,
    run_parsing_benchmark,
    write_benchmark_report,
    write_ohr_subset,
)
from docparser.evaluation.parsebench import (
    load_subset_manifest,
    manifest_digest,
    prepare_parsebench_predictions,
    run_official_parsebench,
)
from docparser.evaluation.parsebench.models import (
    OfficialParseBenchResult,
    ParseBenchRunRequest,
)
from docparser.evaluation.parsebench.subset import (
    load_candidate_catalog,
    prepare_subset_manifests,
    write_subset_manifest,
)
from docparser.evaluation.qa import QAJudgment, evaluate_qa
from docparser.evaluation.schema import (
    DEFAULT_EVALUATION_SCHEMA,
    evaluation_schema_is_current,
    parsebench_subset_schema_is_current,
    write_evaluation_schema,
    write_parsebench_subset_schema,
)
from docparser.fallback import FallbackProfile
from docparser.ir.schema import (
    DEFAULT_SCHEMA_PATH,
    schema_is_current,
    write_document_ir_schema,
)
from docparser.ir.serialization import load_canonical_json
from docparser.ir.types import Sha256Digest
from docparser.quality import CalibrationProfile
from docparser.retrieval import FixedChunkConfig, StructureChunkConfig
from docparser.retrieval.answering import SiliconFlowChatModel, SiliconFlowConfig
from docparser.retrieval.context import ContextConfig
from docparser.retrieval.dense import BgeM3Runtime
from docparser.retrieval.index import build_evidence_index, load_evidence_index
from docparser.version import __version__

app = typer.Typer(
    name="docparser",
    help="Complex PDF parsing, evidence retrieval and cited question answering.",
    no_args_is_help=True,
)
schema_app = typer.Typer(help="Generate and verify committed wire schemas.")
app.add_typer(schema_app, name="schema")


@app.command("rag-evaluate")
def rag_evaluate(
    results_dir: Annotated[Path, typer.Option("--results", exists=True, file_okay=False)],
    judgments: Annotated[Path, typer.Option("--judgments", exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", dir_okay=False)],
) -> None:
    """Evaluate QA JSON files with independent JSONL correctness/citation judgments."""
    try:
        questions = None
        if (results_dir / "run.json").exists():
            manifest, results = load_qa_batch(results_dir)
            questions = manifest.questions
        else:
            results = tuple(
                QAResult.model_validate_json(path.read_bytes())
                for path in sorted(results_dir.glob("*.qa.json"))
            )
            typer.echo(
                "Exploratory evaluation: no manifest to verify planned question coverage.", err=True
            )
        annotations = tuple(
            QAJudgment.model_validate_json(line)
            for line in judgments.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        metrics = evaluate_qa(results, annotations, questions=questions)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(metrics.model_dump_json(indent=2), encoding="utf-8")
    except (OSError, ValueError) as error:
        typer.echo(f"QA evaluation failed: {error}", err=True)
        raise typer.Exit(code=2) from error
    typer.echo(metrics.model_dump_json(indent=2))


@app.command("rag-batch")
def rag_batch(
    questions_path: Annotated[Path, typer.Option("--questions", exists=True, dir_okay=False)],
    index_path: Annotated[Path, typer.Option("--index", exists=True, file_okay=False)],
    model_path: Annotated[Path, typer.Option("--model-path", exists=True, file_okay=False)],
    output: Annotated[Path, typer.Option("--output", file_okay=False)],
    device: Annotated[str, typer.Option("--device")] = "cpu",
    top_k: Annotated[int, typer.Option("--top-k", min=1)] = 5,
    context_tokens: Annotated[int, typer.Option("--context-tokens", min=1)] = 4096,
    expand_context: Annotated[bool, typer.Option("--expand-context/--no-expand-context")] = True,
    table_context: Annotated[
        bool,
        typer.Option("--table-context", help="Restore logical table rows after retrieval (M2)."),
    ] = False,
    model: Annotated[str, typer.Option("--chat-model")] = "Qwen/Qwen3.8-27B",
    base_url: Annotated[str, typer.Option("--base-url")] = "https://api.siliconflow.cn/v1",
) -> None:
    """Run a fixed question manifest with one model session and retain every provider failure."""
    try:
        questions = tuple(
            QAQuestion.model_validate_json(line)
            for line in questions_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        config = QABatchConfig(
            top_k=top_k,
            context=ContextConfig(
                max_tokens=context_tokens,
                expand_source_tokens=768 if expand_context else 0,
                include_related=expand_context,
                table_policy="LOGICAL_ROWS" if table_context else "SOURCE_SPANS",
            ),
            generation=SiliconFlowConfig(model=model, base_url=base_url),
        )
        session = load_evidence_index(index_path).session(BgeM3Runtime(model_path, device=device))
        run_qa_batch(questions, session, SiliconFlowChatModel(config.generation), output, config)
        _, results = load_qa_batch(output)
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(f"batch failed: {error}", err=True)
        raise typer.Exit(code=2) from error
    errors = sum(result.execution_error is not None for result in results)
    invalid = sum(
        result.answer is not None and result.answer.status == "INVALID_RESPONSE"
        for result in results
    )
    typer.echo(
        f"completed {len(results)} requests; provider errors={errors}, "
        f"invalid answers={invalid}: {output}"
    )
    if errors:
        raise typer.Exit(code=2)
    if invalid:
        raise typer.Exit(code=3)


@app.command("rag-index")
def rag_index(
    ir_root: Annotated[Path, typer.Option("--ir-root", exists=True, file_okay=False)],
    model_path: Annotated[Path, typer.Option("--model-path", exists=True, file_okay=False)],
    output: Annotated[Path, typer.Option("--output", file_okay=False)],
    device: Annotated[str, typer.Option("--device")] = "cpu",
) -> None:
    """Embed Fixed 512/64 once and save a reusable exact dense index."""
    try:
        documents = tuple(
            load_canonical_json(path.read_bytes())
            for path in sorted(ir_root.rglob("document.ir.json"))
        )
        index = build_evidence_index(documents, BgeM3Runtime(model_path, device=device), output)
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(f"index failed: {error}", err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"indexed {len(documents)} documents, {index.manifest.chunk_count} chunks: {output}")
    if index.manifest.table_alignment_counts:
        typer.echo(
            "table source maps: "
            + ", ".join(
                f"{status}={count}"
                for status, count in sorted(index.manifest.table_alignment_counts.items())
            )
        )


@app.command("rag-ask")
def rag_ask(
    question: Annotated[str, typer.Argument(help="Document question.")],
    index_path: Annotated[Path, typer.Option("--index", exists=True, file_okay=False)],
    model_path: Annotated[Path, typer.Option("--model-path", exists=True, file_okay=False)],
    output: Annotated[Path, typer.Option("--output", dir_okay=False)],
    device: Annotated[str, typer.Option("--device")] = "cpu",
    top_k: Annotated[int, typer.Option("--top-k", min=1)] = 5,
    context_tokens: Annotated[int, typer.Option("--context-tokens", min=1)] = 4096,
    expand_context: Annotated[
        bool,
        typer.Option(
            "--expand-context/--no-expand-context",
            help="Restore bounded source/context; disable for a controlled ablation.",
        ),
    ] = True,
    context_only: Annotated[
        bool, typer.Option("--context-only", help="Inspect local evidence without calling an API.")
    ] = False,
    table_context: Annotated[
        bool,
        typer.Option("--table-context", help="Restore logical table rows after retrieval (M2)."),
    ] = False,
    model: Annotated[str, typer.Option("--chat-model")] = "Qwen/Qwen3.8-27B",
    base_url: Annotated[str, typer.Option("--base-url")] = "https://api.siliconflow.cn/v1",
    document_ids: Annotated[list[str] | None, typer.Option("--document-id")] = None,
) -> None:
    """Answer from cited evidence using SiliconFlow and SILICONFLOW_API_KEY."""
    try:
        index = load_evidence_index(index_path)
        session = index.session(BgeM3Runtime(model_path, device=device))
        chat = (
            None
            if context_only
            else SiliconFlowChatModel(SiliconFlowConfig(model=model, base_url=base_url))
        )
        result = ask_document(
            question,
            session,
            model=chat,
            top_k=top_k,
            document_ids=tuple(document_ids or ()),
            context_config=ContextConfig(
                max_tokens=context_tokens,
                expand_source_tokens=768 if expand_context else 0,
                include_related=expand_context,
                table_policy="LOGICAL_ROWS" if table_context else "SOURCE_SPANS",
            ),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(f"question failed: {error}", err=True)
        raise typer.Exit(code=2) from error
    if result.execution_error is not None:
        typer.echo(f"{result.execution_error.code}: {result.execution_error.message}", err=True)
        typer.echo(f"retrieved evidence retained: {output}")
        raise typer.Exit(code=2)
    if result.answer is None:
        typer.echo(result.context.text)
    else:
        typer.echo(result.answer.status)
        for claim in result.answer.claims:
            refs = ", ".join(dict.fromkeys(citation.evidence_id for citation in claim.citations))
            typer.echo(f"{claim.text} [{refs}]")
        if result.answer.reason:
            typer.echo(result.answer.reason)
    typer.echo(f"evidence and citations: {output}")
    if result.answer is not None and result.answer.status == "INVALID_RESPONSE":
        raise typer.Exit(code=3)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Run document parsing platform commands."""


@app.command()
def doctor(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Bootstrap YAML configuration file.",
        ),
    ],
) -> None:
    """Validate bootstrap configuration without external side effects."""

    try:
        settings = load_config(config)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        typer.echo(f"configuration invalid: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(
        "configuration valid "
        f"(pipeline={settings.pipeline.version}, storage={settings.storage.backend})"
    )


@app.command("parse-local")
def parse_local(
    input_pdf: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Local PDF to parse.",
        ),
    ],
    parser: Annotated[str, typer.Option("--parser", help="Parser profile under evaluation.")] = (
        "docling-standard"
    ),
    device: Annotated[
        RuntimeDevice,
        typer.Option("--device", help="auto, cpu, or cuda."),
    ] = RuntimeDevice.AUTO,
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ] = Path("./output"),
    recover_structure: Annotated[
        bool,
        typer.Option(
            "--recover-structure",
            help="Keep partial source evidence when pages or logical table structures fail.",
        ),
    ] = False,
) -> None:
    """Parse a local PDF through the Phase 2.6 development/evaluation slice."""

    try:
        outcome = parse_document_with_diagnostics(
            input_pdf,
            ParsingConfig(parser=parser, device=device, recover_structure=recover_structure),
            raw_output_dir=output / "raw",
        )
        write_parse_outputs(outcome, output)
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"parse failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"parsed {outcome.diagnostics.pages_parsed}/{outcome.diagnostics.pages_requested} "
        f"pages with {outcome.parse_result.descriptor.parser_name} "
        f"on {outcome.diagnostics.device.value}; output={output}"
    )


@app.command("parse-robust")
def parse_robust(
    input_pdf: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Local PDF to parse through the calibrated risk gate.",
        ),
    ],
    parser: Annotated[
        str,
        typer.Option("--parser", help="Primary parser profile."),
    ] = "docling-standard",
    device: Annotated[
        RuntimeDevice,
        typer.Option("--device", help="auto, cpu, or cuda."),
    ] = RuntimeDevice.AUTO,
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ] = Path("./robust-output"),
    calibration_profile: Annotated[
        Path | None,
        typer.Option(
            "--calibration-profile",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ] = None,
    fallback_profile: Annotated[
        Path | None,
        typer.Option(
            "--fallback-profile",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ] = None,
    supported_slice: Annotated[
        str | None,
        typer.Option(
            "--supported-slice",
            help="Calibrated document slice to evaluate; independent of fallback configuration.",
        ),
    ] = None,
) -> None:
    """Parse, validate, optionally fall back, and emit the final evaluated IR."""

    try:
        calibration = (
            CalibrationProfile.model_validate_json(calibration_profile.read_bytes())
            if calibration_profile
            else None
        )
        fallback = (
            FallbackProfile.model_validate_json(fallback_profile.read_bytes())
            if fallback_profile
            else None
        )
        outcome = robust_parse_document(
            input_pdf,
            ParsingConfig(parser=parser, device=device),
            calibration=calibration,
            fallback_profile=fallback,
            supported_slice=supported_slice,
        )
        write_robust_outputs(outcome, output)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        typer.echo(f"robust parse failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    mode = outcome.final_quality_report.mode.value
    suffix = " / CALIBRATION_REQUIRED" if outcome.final_quality_report.calibration_required else ""
    typer.echo(
        f"robust parse decision={outcome.final_decision.value} mode={mode}{suffix}; output={output}"
    )


@app.command("benchmark-parsing")
def benchmark_parsing(
    manifest: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ] = Path("./benchmark-output"),
    device: Annotated[
        RuntimeDevice,
        typer.Option("--device", help="auto, cpu, or cuda."),
    ] = RuntimeDevice.AUTO,
) -> None:
    """Compare Docling and PaddleOCR-VL on the same local Golden manifest."""

    try:
        dataset = load_manifest(manifest)
        report = run_parsing_benchmark(
            dataset,
            manifest_dir=manifest.parent,
            device=device,
        )
        write_benchmark_report(report, output)
    except (OSError, RuntimeError, ValueError, yaml.YAMLError, ValidationError) as exc:
        typer.echo(f"benchmark failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"benchmark cases={len(report.results)} failures={len(report.failures)} "
        f"recommendation={report.recommendation}; output={output}"
    )


@app.command("prepare-parsebench-manifests")
def prepare_parsebench_manifests(
    candidate_catalog: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Local JSONL metadata catalog; no PDF data is downloaded.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ] = Path("./tests/golden/manifests"),
) -> None:
    """Freeze deterministic development/holdout IDs from a local candidate catalog."""

    try:
        development, holdout = prepare_subset_manifests(load_candidate_catalog(candidate_catalog))
        write_subset_manifest(development, output / "parsebench-complex-v1-dev.json")
        write_subset_manifest(holdout, output / "parsebench-complex-v1-holdout.json")
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"ParseBench manifest preparation failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"prepared {len(development.selected_items)} development and "
        f"{len(holdout.selected_items)} protected-holdout IDs"
    )


@app.command("prepare-ohr-rag-core")
def prepare_ohr_retrieval_subset(
    dataset_root: Annotated[
        Path,
        typer.Option(
            "--dataset-root",
            file_okay=False,
            resolve_path=True,
            help="Locally provisioned OHR-Bench root containing data/qas_v2.json.",
        ),
    ],
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            file_okay=False,
            resolve_path=True,
            help="External destination for subset artifacts; no data is written into Git.",
        ),
    ],
    source_dataset_revision: Annotated[
        str | None,
        typer.Option("--source-dataset-revision"),
    ] = None,
    source_commit: Annotated[
        str | None,
        typer.Option("--source-commit", help="Optional current RAG source commit for provenance."),
    ] = None,
) -> None:
    """Prepare deterministic OHR retrieval truth from local external metadata."""

    try:
        subset = prepare_ohr_rag_core(
            dataset_root=dataset_root,
            source_dataset_revision=source_dataset_revision,
            source_commit=source_commit,
        )
        write_ohr_subset(subset, output_dir)
    except (OSError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        typer.echo(f"OHR subset preparation failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    shortfall = " with quota shortfall" if subset.manifest.shortfalls else ""
    typer.echo(
        f"prepared {subset.manifest.selected_query_count} queries from "
        f"{subset.manifest.selected_document_count} documents{shortfall}; output={output_dir}"
    )


@app.command("rag-retrieval-ab")
def rag_retrieval_ab(
    ir_root: Annotated[
        Path,
        typer.Option("--ir-root", exists=True, file_okay=False, readable=True, resolve_path=True),
    ],
    queries: Annotated[
        Path,
        typer.Option("--queries", exists=True, file_okay=True, readable=True, resolve_path=True),
    ],
    model_path: Annotated[
        Path,
        typer.Option(
            "--model-path",
            exists=True,
            file_okay=False,
            readable=True,
            resolve_path=True,
            help="Locally provisioned BAAI/bge-m3 directory; never downloaded by this command.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ],
    fixed_target_tokens: Annotated[int, typer.Option("--fixed-target-tokens", min=1)] = 512,
    fixed_overlap_tokens: Annotated[int, typer.Option("--fixed-overlap-tokens", min=0)] = 64,
    structure_target_tokens: Annotated[int, typer.Option("--structure-target-tokens", min=1)] = 512,
    structure_hard_max_tokens: Annotated[
        int, typer.Option("--structure-hard-max-tokens", min=1)
    ] = 8000,
    structure_semantic_overlap_units: Annotated[
        int, typer.Option("--structure-semantic-overlap-units", min=0)
    ] = 1,
    top_k: Annotated[int, typer.Option("--top-k", min=10)] = 10,
    device: Annotated[str, typer.Option("--device", help="BGE-M3 torch device.")] = "cpu",
    batch_size: Annotated[int, typer.Option("--batch-size", min=1)] = 16,
    source_commit: Annotated[str | None, typer.Option("--source-commit")] = None,
) -> None:
    """Run the fixed-token versus structure-aware exact-dense retrieval experiment."""

    try:
        outcome = run_retrieval_ab(
            ir_root=ir_root,
            queries_path=queries,
            output_dir=output,
            model_path=model_path,
            fixed_config=FixedChunkConfig(
                target_tokens=fixed_target_tokens,
                overlap_tokens=fixed_overlap_tokens,
            ),
            structure_config=StructureChunkConfig(
                target_tokens=structure_target_tokens,
                hard_max_tokens=structure_hard_max_tokens,
                semantic_overlap_units=structure_semantic_overlap_units,
            ),
            top_k=top_k,
            device=device,
            batch_size=batch_size,
            source_commit=source_commit,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        typer.echo(f"RAG retrieval A/B failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"retrieval A/B completed for {len(outcome.eligible_queries)} queries; output={output}"
    )


@app.command("benchmark-parsebench-official")
def benchmark_parsebench_official(
    manifest: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    dataset_root: Annotated[
        Path,
        typer.Option("--dataset-root", exists=True, file_okay=False, readable=True),
    ],
    checkout: Annotated[
        Path,
        typer.Option("--checkout", exists=True, file_okay=False, readable=True),
    ],
    parsebench_python: Annotated[
        Path,
        typer.Option("--parsebench-python", exists=True, file_okay=True),
    ],
    environment_digest: Annotated[str, typer.Option("--environment-digest")],
    hardware_description: Annotated[str, typer.Option("--hardware-description")],
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False),
    ] = Path("./parsebench-output"),
    parser: Annotated[str, typer.Option("--parser")] = "docling-standard",
    device: Annotated[RuntimeDevice, typer.Option("--device")] = RuntimeDevice.AUTO,
    reuse_saved_ir: Annotated[
        bool,
        typer.Option(
            "--reuse-saved-ir",
            help="Re-export existing case IR without parser inference.",
        ),
    ] = False,
) -> None:
    """Parse a frozen local subset and invoke the pinned official ParseBench evaluator."""

    try:
        subset = load_subset_manifest(manifest)
        run_root = output / parser
        predictions = prepare_parsebench_predictions(
            subset,
            dataset_root=dataset_root,
            export_root=run_root / "predictions",
            cases_root=run_root / "cases",
            config=ParsingConfig(parser=parser, device=device),
            reuse_saved_ir=reuse_saved_ir,
        )
        request = ParseBenchRunRequest(
            benchmark_id=f"official-{subset.dataset_id}-{parser}",
            subset_id=subset.dataset_id,
            subset_manifest_digest=manifest_digest(manifest),
            checkout_path=checkout,
            parsebench_python=parsebench_python,
            dataset_root=dataset_root,
            export_root=run_root / "predictions",
            report_root=run_root / "official-report",
            environment_digest=Sha256Digest(environment_digest),
            hardware_description=hardware_description,
        )
        result = run_official_parsebench(request)
        _write_official_parsebench_result(result, run_root / "official-result.json")
    except (OSError, RuntimeError, ValueError, ValidationError) as exc:
        typer.echo(f"official ParseBench benchmark failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"official ParseBench predictions={len(predictions)} parser={parser}; output={run_root}"
    )


def _write_official_parsebench_result(result: OfficialParseBenchResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


@schema_app.command("generate")
def schema_generate(
    output: Annotated[
        Path,
        typer.Option("--output", dir_okay=False, help="Generated schema output path."),
    ] = DEFAULT_SCHEMA_PATH,
) -> None:
    """Generate the committed Document IR schema from Pydantic models."""

    write_document_ir_schema(output)
    if output == DEFAULT_SCHEMA_PATH:
        write_evaluation_schema()
        write_parsebench_subset_schema()
    typer.echo(f"generated {output.as_posix()}")


@schema_app.command("check")
def schema_check(
    schema_path: Annotated[
        Path,
        typer.Option("--schema", dir_okay=False, help="Committed schema path."),
    ] = DEFAULT_SCHEMA_PATH,
) -> None:
    """Fail when the committed schema differs from the generated contract."""

    if not schema_is_current(schema_path):
        typer.echo(f"schema drift detected: {schema_path.as_posix()}", err=True)
        raise typer.Exit(code=1)
    if schema_path == DEFAULT_SCHEMA_PATH and not evaluation_schema_is_current():
        typer.echo(f"schema drift detected: {DEFAULT_EVALUATION_SCHEMA.as_posix()}", err=True)
        raise typer.Exit(code=1)
    if schema_path == DEFAULT_SCHEMA_PATH and not parsebench_subset_schema_is_current():
        typer.echo("schema drift detected: ParseBench subset schema", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"schema current: {schema_path.as_posix()}")
