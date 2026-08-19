"""Запуск задачи через CLI-агента с навыком obsidian."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

DEFAULT_AGENT_CMD = "claude -p"


class AgentNotFoundError(RuntimeError):
    """Не удалось запустить команду CLI-агента."""


class AgentTimeoutError(RuntimeError):
    """CLI-агент не уложился в отведённое время."""


def build_command(task: str, agent_cmd: str | None = None) -> list[str]:
    """Собирает командную строку запуска агента с задачей последним аргументом.

    Команда разбирается через ``shlex``, поэтому на Windows путь с обратными
    слэшами нужно брать в кавычки — иначе слэши будут съедены.
    """
    cmd = agent_cmd or os.environ.get("VAULTCTL_AGENT_CMD") or DEFAULT_AGENT_CMD
    parts = shlex.split(cmd)
    if not parts:
        raise AgentNotFoundError(
            "команда CLI-агента пуста — задайте --agent-cmd или VAULTCTL_AGENT_CMD"
        )
    return [*parts, task]


def run_task(
    task: str,
    vault_path: Path,
    agent_cmd: str | None = None,
    timeout: float | None = None,
) -> int:
    """Передаёт задачу CLI-агенту (Claude Code и т.п.), с рабочей директорией и
    OBSIDIAN_VAULT_PATH выставленными на vault — активацию навыка obsidian
    выполняет сам агент.
    """
    command = build_command(task, agent_cmd)
    env = {**os.environ, "OBSIDIAN_VAULT_PATH": str(vault_path)}
    try:
        result = subprocess.run(command, cwd=vault_path, env=env, timeout=timeout)
    except FileNotFoundError as exc:
        raise AgentNotFoundError(
            f"CLI-агент «{command[0]}» не найден — проверьте --agent-cmd "
            f"или VAULTCTL_AGENT_CMD (путь с пробелами и обратными слэшами "
            f"нужно взять в кавычки)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AgentTimeoutError(
            f"CLI-агент не завершился за {timeout} с и был остановлен"
        ) from exc
    return result.returncode
