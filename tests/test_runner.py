import sys

import pytest

from vaultctl.runner import (
    DEFAULT_AGENT_CMD,
    AgentNotFoundError,
    AgentTimeoutError,
    build_command,
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

    code = run_task("сохрани ссылку", vault, quoted(sys.executable, script, out))

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

    assert run_task("задача", vault, quoted(sys.executable, script)) == 3


def test_run_task_missing_agent_raises(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(AgentNotFoundError):
        run_task("задача", vault, "vaultctl-no-such-agent-xyz")


def test_run_task_timeout_raises(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    script = tmp_path / "agent.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    with pytest.raises(AgentTimeoutError):
        run_task("задача", vault, quoted(sys.executable, script), timeout=1)
