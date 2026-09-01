import pytest

from docparser.adapters.parsers.paddleocr_vl import runtime
from docparser.domain.parser_contract import ParserExecutionError, RuntimeDevice


def test_explicit_cuda_unavailable_is_a_clear_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "cuda_is_available", lambda: False)

    try:
        runtime.resolve_device(RuntimeDevice.CUDA)
    except ParserExecutionError as exc:
        assert exc.error.code == "RUNTIME_UNAVAILABLE"
        assert "CUDA" in str(exc)
    else:
        raise AssertionError("explicit unavailable CUDA must fail")


def test_auto_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "cuda_is_available", lambda: False)
    assert runtime.resolve_device(RuntimeDevice.AUTO) is RuntimeDevice.CPU
