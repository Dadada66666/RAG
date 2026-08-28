# Enterprise Document Parsing & RAG Ingestion Platform

This repository implements the architecture contracts under [`docs/`](docs/). Development is
incremental: every phase must remain installable, runnable, and testable before the next phase
starts.

## Current scope

Phase 1 provides the strict Canonical Document IR spine: opaque IDs, canonical geometry,
page/block/provenance models, deterministic serialization and a generated JSON Schema. Parser,
storage, orchestration, quality, fallback, chunking, and GPU runtime code remain deferred.

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
.\.venv\Scripts\docparser.exe schema check
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe -m pytest
```

`doctor` validates the configuration contract only. It does not create storage paths, connect to
a job database, load a parser, or make network requests.
