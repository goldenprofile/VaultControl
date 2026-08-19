"""Запуск задачи через CLI-агента с навыком obsidian."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

DEFAULT_AGENT_CMD = "claude -p"


def build_command(task: str, agent_cmd: str | None = None) -> list[str]:
    cmd = agent_cmd or os.environ.get("VAULTCTL_AGENT_CMD", DEFAULT_AGENT_CMD)
    return [*shlex.split(cmd), task]


def run_task(task: str, vault_path: Path, agent_cmd: str | None = None) -> int:
    """Передаёт задачу CLI-агенту (Claude Code и т.п.), с рабочей директорией и
    OBSIDIAN_VAULT_PATH выставленными на vault — активацию навыка obsidian
    выполняет сам агент.
    """
    command = build_command(task, agent_cmd)
    env = {**os.environ, "OBSIDIAN_VAULT_PATH": str(vault_path)}
    result = subprocess.run(command, cwd=vault_path, env=env)
    return result.returncode
