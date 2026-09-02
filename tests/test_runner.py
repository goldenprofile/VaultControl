import io
import json
import os
import sys

import pytest

from vaultctl import runner
from vaultctl.runner import (
    DEFAULT_AGENT_CMD,
    AgentNotFoundError,
    AgentTimeoutError,
    build_command,
    resolve_agent_input,
    run_task,
)


def quoted(*parts) -> str:
    """Собирает команду с кавычками — на Windows без них shlex съест слэши."""
    return " ".join(f'"{p}"' for p in parts)


def test_build_command_appends_task_last(monkeypatch):
    monkeypatch.delenv("VAULTCTL_AGENT_CMD", raising=False)
    assert build_command("сохрани ссылку") == [
        *DEFAULT_AGENT_CMD.split(),
        "сохрани ссылку",
    ]


def test_build_command_from_env(monkeypatch):
    monkeypatch.setenv("VAULTCTL_AGENT_CMD", "codex exec")
    assert build_command("задача") == ["codex", "exec", "задача"]


def test_build_command_argument_beats_env(monkeypatch):
    monkeypatch.setenv("VAULTCTL_AGENT_CMD", "codex exec")
    assert build_command("задача", "claude -p") == ["claude", "-p", "задача"]


def test_build_command_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("VAULTCTL_AGENT_CMD", "")
    assert build_command("задача") == [*DEFAULT_AGENT_CMD.split(), "задача"]


def test_build_command_blank_command_is_rejected(monkeypatch):
    monkeypatch.delenv("VAULTCTL_AGENT_CMD", raising=False)
    with pytest.raises(AgentNotFoundError):
        build_command("задача", "   ")


def test_build_command_keeps_quoted_windows_path(monkeypatch):
    monkeypatch.delenv("VAULTCTL_AGENT_CMD", raising=False)
    command = build_command("задача", r'"C:\bin\claude.exe" -p')
    assert command[0] == r"C:\bin\claude.exe"


def test_run_task_runs_agent_in_vault_with_env(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    out = tmp_path / "out.txt"
    script = tmp_path / "agent.py"
    script.write_text(
        "import os, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(\n"
        "    os.getcwd() + '\\n' + os.environ['OBSIDIAN_VAULT_PATH'] + '\\n' + sys.argv[2],\n"
        "    encoding='utf-8',\n"
        ")\n",
        encoding="utf-8",
    )

    code = run_task(
        "сохрани ссылку", vault, quoted(sys.executable, script, out), quiet=True
    )

    cwd, env_vault, task = out.read_text(encoding="utf-8").splitlines()
    assert code == 0
    assert cwd == str(vault)
    assert env_vault == str(vault)
    assert task == "сохрани ссылку"


def test_run_task_propagates_agent_exit_code(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    script = tmp_path / "agent.py"
    script.write_text("raise SystemExit(3)\n", encoding="utf-8")

    assert run_task("задача", vault, quoted(sys.executable, script), quiet=True) == 3


def test_run_task_missing_agent_raises(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(AgentNotFoundError):
        run_task("задача", vault, "vaultctl-no-such-agent-xyz", quiet=True)


def test_run_task_timeout_raises(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    script = tmp_path / "agent.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    with pytest.raises(AgentTimeoutError):
        run_task("задача", vault, quoted(sys.executable, script), timeout=1, quiet=True)


def test_run_task_prints_status_before_launch(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    script = tmp_path / "agent.py"
    script.write_text("pass\n", encoding="utf-8")
    status = io.StringIO()

    run_task(
        "задача", vault, quoted(sys.executable, script), status_stream=status
    )

    assert str(vault) in status.getvalue()
    assert "6 символов" in status.getvalue()


def test_run_task_quiet_prints_nothing(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    script = tmp_path / "agent.py"
    script.write_text("pass\n", encoding="utf-8")
    status = io.StringIO()

    run_task(
        "задача", vault, quoted(sys.executable, script), quiet=True, status_stream=status
    )

    assert status.getvalue() == ""


def test_resolve_agent_input_defaults_to_argument():
    assert resolve_agent_input("auto", ["claude", "-p"], "короткая задача") == "arg"


def test_resolve_agent_input_switches_to_stdin_on_long_task():
    task = "x" * (runner.arg_limit() + 1)
    assert resolve_agent_input("auto", ["claude", "-p"], task) == "stdin"


def test_resolve_agent_input_respects_explicit_mode():
    long_task = "x" * (runner.arg_limit() + 1)
    assert resolve_agent_input("arg", ["claude", "-p"], long_task) == "arg"
    assert resolve_agent_input("stdin", ["claude", "-p"], "коротко") == "stdin"


def test_resolve_agent_input_rejects_unknown_mode():
    with pytest.raises(ValueError, match="неизвестный способ"):
        resolve_agent_input("gui", ["claude", "-p"], "задача")


def test_run_task_can_send_task_through_stdin(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    out = tmp_path / "out.txt"
    script = tmp_path / "agent.py"
    script.write_text(
        "import sys, pathlib\n"
        "received = sys.stdin.buffer.read().decode('utf-8')\n"
        "pathlib.Path(sys.argv[1]).write_text(\n"
        "    received + '\\n---\\n' + ' '.join(sys.argv[2:]), encoding='utf-8'\n"
        ")\n",
        encoding="utf-8",
    )
    task = "clip:\n\nНейродайджест — $0,15/$0,5"

    code = run_task(
        task,
        vault,
        quoted(sys.executable, script, out),
        agent_input="stdin",
        quiet=True,
    )

    received, extra = out.read_text(encoding="utf-8").split("\n---\n")
    assert code == 0
    assert received == task
    assert extra == ""


def test_stream_flags_are_inserted_before_other_arguments():
    parts = runner._with_stream_flags(["claude", "--allowedTools", "Read,Write", "-p"])

    assert parts[0] == "claude"
    assert parts[-1] == "-p"
    assert parts[1:4] == ["--output-format", "stream-json", "--verbose"]


def test_stream_flags_are_not_duplicated():
    parts = ["claude", "--output-format", "json", "-p"]
    assert runner._with_stream_flags(parts) == parts


def test_streaming_renders_agent_events(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    script = tmp_path / "agent.py"
    events = [
        {"type": "system", "subtype": "init", "model": "claude-opus-5"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "WebFetch", "input": {"url": "t.me/1"}}
                ]
            },
        },
        {"type": "result", "subtype": "success", "duration_ms": 2000},
    ]
    script.write_text(
        "import sys\n"
        + "".join(f"print({json.dumps(json.dumps(e))})\n" for e in events),
        encoding="utf-8",
    )
    output = io.StringIO()

    code = runner._run_streaming(
        [sys.executable, str(script)], vault, dict(os.environ), None, None, output
    )

    rendered = output.getvalue()
    assert code == 0
    assert "агент запущен" in rendered
    assert "WebFetch(t.me/1)" in rendered
    assert "готово" in rendered


def test_streaming_times_out_on_silent_agent(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    script = tmp_path / "agent.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    with pytest.raises(AgentTimeoutError):
        runner._run_streaming(
            [sys.executable, str(script)],
            vault,
            dict(os.environ),
            1,
            None,
            io.StringIO(),
        )


def test_stream_warns_when_agent_format_is_unknown(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    script = tmp_path / "agent.py"
    script.write_text("pass\n", encoding="utf-8")
    status = io.StringIO()

    run_task(
        "задача",
        vault,
        quoted(sys.executable, script),
        stream=True,
        status_stream=status,
    )

    assert "только для Claude Code" in status.getvalue()
