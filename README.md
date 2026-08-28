# Enterprise Document Parsing & RAG Ingestion Platform

This repository implements the architecture contracts under [`docs/`](docs/). Development is
incremental: every phase must remain installable, runnable, and testable before the next phase
starts.

## Current scope

Phase 0 provides only the Python package shell, strict configuration validation, quality gates,
and a no-side-effect diagnostic CLI. Parser, Document IR, storage, orchestration, and GPU runtime
code are intentionally deferred to their implementation phases.

## Requirements

- Python 3.12+
- No GPU, model download, database, or network access is required for the default test suite.

## Reproducible setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
```

## Quality gates

```powershell
.\.venv\Scripts\docparser.exe --version
.\.venv\Scripts\docparser.exe doctor --config configs/default.yaml
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe -m pytest
```

`doctor` validates the configuration contract only. It does not create storage paths, connect to
a job database, load a parser, or make network requests.
