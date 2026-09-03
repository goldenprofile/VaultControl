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
    resolve_agent_executable,
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
    # Имена агентов здесь не важны — важно, что аргумент сильнее переменной.
    monkeypatch.setenv("VAULTCTL_AGENT_CMD", "codex exec")
    assert build_command("задача", "gemini -p") == ["gemini", "-p", "задача"]


def test_build_command_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("VAULTCTL_AGENT_CMD", "")
    assert build_command("задача") == [*DEFAULT_AGENT_CMD.split(), "задача"]


def test_build_command_blank_command_is_rejected(monkeypatch):
    monkeypatch.delenv("VAULTCTL_AGENT_CMD", raising=False)
    with pytest.raises(AgentNotFoundError):
        build_command("задача", "   ")


def test_build_command_keeps_quoted_windows_path(monkeypatch):
    monkeypatch.delenv("VAULTCTL_AGENT_CMD", raising=False)
    command = build_command("задача", r'"C:\bin\my-agent.exe" -p')
    assert command[0] == r"C:\bin\my-agent.exe"


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
    assert resolve_agent_input("auto", ["dummy-agent", "-p"], "короткая задача") == "arg"


def test_resolve_agent_input_switches_to_stdin_on_long_task():
    task = "x" * (runner.arg_limit() + 1)
    assert resolve_agent_input("auto", ["dummy-agent", "-p"], task) == "stdin"


def test_resolve_agent_input_respects_explicit_mode():
    long_task = "x" * (runner.arg_limit() + 1)
    assert resolve_agent_input("arg", ["dummy-agent", "-p"], long_task) == "arg"
    assert resolve_agent_input("stdin", ["dummy-agent", "-p"], "коротко") == "stdin"


def test_resolve_agent_input_rejects_unknown_mode():
    with pytest.raises(ValueError, match="неизвестный способ"):
        resolve_agent_input("gui", ["dummy-agent", "-p"], "задача")


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

    assert "формат потока событий" in status.getvalue()
    assert "неизвестен" in status.getvalue()


def test_stream_flags_for_pi_use_mode_json():
    assert runner._with_stream_flags(["pi", "-p"]) == ["pi", "--mode", "json", "-p"]


def test_stream_flags_do_not_override_user_mode():
    parts = ["pi", "--mode", "rpc"]
    assert runner._with_stream_flags(parts) == parts


@pytest.mark.skipif(sys.platform == "win32", reason="вне Windows шимы не актуальны")
def test_resolve_is_identity_outside_windows():
    assert resolve_agent_executable(["pi", "-p"]) == ["pi", "-p"]


@pytest.mark.skipif(sys.platform != "win32", reason="шимы .cmd — только Windows")
def test_resolve_expands_npm_cmd_shim_to_node(tmp_path, monkeypatch):
    shim_dir = tmp_path / "npm"
    script = (
        shim_dir
        / "node_modules"
        / "@earendil-works"
        / "pi-coding-agent"
        / "dist"
        / "bundle"
        / "cli.js"
    )
    script.parent.mkdir(parents=True)
    script.write_text("// агент", encoding="utf-8")
    shim = shim_dir / "pi.cmd"
    # Форма шимы — как у npm 10: цель в кавычках с %dp0%.
    shim.write_text(
        '@ECHO off\r\n'
        'GOTO start\r\n'
        ':find_dp0\r\n'
        'SET dp0=%~dp0\r\n'
        'EXIT /b\r\n'
        ':start\r\n'
        'SETLOCAL\r\n'
        'CALL :find_dp0\r\n'
        'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & '
        '"%_prog%"  "%dp0%\\node_modules\\@earendil-works\\pi-coding-agent\\'
        'dist\\bundle\\cli.js" %*\r\n',
        encoding="utf-8",
    )

    def fake_which(name):
        return str(shim) if name == "pi" else sys.executable if name == "node" else None

    monkeypatch.setattr(runner, "which", fake_which)

    assert resolve_agent_executable(["pi", "-p", "задача"]) == [
        sys.executable,
        str(script),
        "-p",
        "задача",
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="шимы .cmd — только Windows")
def test_resolve_uses_which_path_for_native_exe(tmp_path, monkeypatch):
    exe = tmp_path / "agent.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(runner, "which", lambda name: str(exe) if name == "agent" else None)

    assert resolve_agent_executable(["agent", "-p"]) == [str(exe), "-p"]


@pytest.mark.skipif(sys.platform != "win32", reason="шимы .cmd — только Windows")
def test_resolve_keeps_command_when_which_finds_nothing(monkeypatch):
    monkeypatch.setattr(runner, "which", lambda name: None)

    assert resolve_agent_executable(["no-such-agent", "-p"]) == ["no-such-agent", "-p"]


@pytest.mark.skipif(sys.platform != "win32", reason="шимы .cmd — только Windows")
def test_not_found_error_hints_about_unparsable_shim(tmp_path, monkeypatch):
    shim = tmp_path / "pi.cmd"
    shim.write_text("@echo off\r\nrem ничего полезного\r\n", encoding="utf-8")
    monkeypatch.setattr(runner, "which", lambda name: str(shim) if name == "pi" else None)

    error = runner._not_found_error(["pi", "-p"])

    assert ".cmd-шима" in str(error)


def test_streaming_renders_pi_events(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    script = tmp_path / "agent.py"
    events = [
        {"type": "session", "version": 3, "id": "s"},
        {
            "type": "tool_execution_start",
            "toolCallId": "1",
            "toolName": "write",
            "args": {"path": "notes/x.md"},
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "сохранил"}],
                "model": "glm-5.3-flash",
                "usage": {"cost": {"total": 0.5}},
                "stopReason": "stop",
            },
        },
        {"type": "agent_end", "messages": []},
    ]
    script.write_text(
        "import sys\n"
        + "".join(f"print({json.dumps(json.dumps(e))})\n" for e in events),
        encoding="utf-8",
    )
    output = io.StringIO()

    code = runner._run_streaming(
        [sys.executable, str(script)],
        vault,
        dict(os.environ),
        None,
        None,
        output,
        agent="pi",
    )

    rendered = output.getvalue()
    assert code == 0
    assert "агент запущен" in rendered
    assert "write(notes/x.md)" in rendered
    assert "сохранил" in rendered
    assert "готово" in rendered
    assert "$0.50" in rendered
