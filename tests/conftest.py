from __future__ import annotations

import inspect
from typing import Any

from typer.testing import CliRunner

_original_cli_runner_init = CliRunner.__init__
_original_cli_runner_invoke = CliRunner.invoke
_cli_runner_init_parameters = inspect.signature(CliRunner.__init__).parameters


def _cli_runner_init_with_separate_stderr(self: CliRunner, *args: Any, **kwargs: Any) -> None:
    if "mix_stderr" in _cli_runner_init_parameters:
        kwargs.setdefault("mix_stderr", False)
    _original_cli_runner_init(self, *args, **kwargs)


def _cli_runner_invoke_with_legacy_output(self: CliRunner, *args: Any, **kwargs: Any) -> Any:
    env = dict(kwargs.get("env") or {})
    env.setdefault("COLUMNS", "120")
    kwargs["env"] = env
    result = _original_cli_runner_invoke(self, *args, **kwargs)
    if result.stderr_bytes and result.stderr_bytes not in result.stdout_bytes:
        result.stdout_bytes += result.stderr_bytes
    return result


CliRunner.__init__ = _cli_runner_init_with_separate_stderr
CliRunner.invoke = _cli_runner_invoke_with_legacy_output
