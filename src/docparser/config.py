"""Strict bootstrap configuration contract."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class StrictConfigModel(BaseModel):
    """Base model shared by bootstrap configuration sections."""

    model_config = ConfigDict(extra="forbid", strict=True)


class PipelineConfig(StrictConfigModel):
    version: str = Field(min_length=1)
    primary_parser: str = Field(min_length=1)
    fallback_parsers: list[str]
    max_fallback_rounds: int = Field(ge=0)


class QualityConfig(StrictConfigModel):
    pass_threshold: float = Field(ge=0.0, le=1.0)
    fallback_trigger_score: float = Field(ge=0.0, le=1.0)
    partial_publish_threshold: float = Field(ge=0.0, le=1.0)
    ruleset_version: str = Field(min_length=1)


class ProcessingConfig(StrictConfigModel):
    max_pages: int = Field(gt=0)
    page_parallelism: int = Field(gt=0)
    gpu_batch_pages: int = Field(gt=0)
    checkpoint_pages: int = Field(gt=0)
    max_fallback_pages: int = Field(ge=0)
    max_fallback_area_ratio: float = Field(ge=0.0, le=1.0)


class StorageConfig(StrictConfigModel):
    backend: Literal["local"]
    path: str = Field(min_length=1)


class JobsConfig(StrictConfigModel):
    backend: Literal["sqlite"]
    database_url: str = Field(min_length=1)


class SecurityConfig(StrictConfigModel):
    max_file_size_bytes: int = Field(gt=0)
    parser_network: Literal["disabled"]


class BootstrapConfig(StrictConfigModel):
    pipeline: PipelineConfig
    quality: QualityConfig
    processing: ProcessingConfig
    storage: StorageConfig
    jobs: JobsConfig
    security: SecurityConfig


def load_config(path: Path) -> BootstrapConfig:
    """Load and validate a UTF-8 YAML configuration file."""

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return BootstrapConfig.model_validate(payload)

