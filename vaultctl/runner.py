"""Запуск задачи через CLI-агента с навыком obsidian."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_AGENT_CMD = "claude -p"

#: Способы доставки задачи агенту.
AGENT_INPUT_MODES = ("auto", "arg", "stdin")

#: Запас под длину командной строки. Windows обрывает её на 32767 символах,
#: у POSIX лимит на порядки выше, но упираться в него тоже незачем.
ARG_LIMIT_WINDOWS = 30000
ARG_LIMIT_POSIX = 120000


class AgentNotFoundError(RuntimeError):
    """Не удалось запустить команду CLI-агента."""


class AgentTimeoutError(RuntimeError):
    """CLI-агент не уложился в отведённое время."""


def parse_agent_cmd(agent_cmd: str | None = None) -> list[str]:
    """Разбирает команду запуска агента без текста задачи.

    Команда разбирается через ``shlex``, поэтому на Windows путь с обратными
    слэшами нужно брать в кавычки — иначе слэши будут съедены.
    """
    cmd = agent_cmd or os.environ.get("VAULTCTL_AGENT_CMD") or DEFAULT_AGENT_CMD
    parts = shlex.split(cmd)
    if not parts:
        raise AgentNotFoundError(
            "команда CLI-агента пуста — задайте --agent-cmd или VAULTCTL_AGENT_CMD"
        )
    return parts


def build_command(task: str, agent_cmd: str | None = None) -> list[str]:
    """Собирает командную строку запуска агента с задачей последним аргументом."""
    return [*parse_agent_cmd(agent_cmd), task]


def arg_limit() -> int:
    """Предел длины командной строки для текущей платформы."""
    return ARG_LIMIT_WINDOWS if sys.platform == "win32" else ARG_LIMIT_POSIX


def resolve_agent_input(mode: str, parts: list[str], task: str) -> str:
    """Выбирает канал доставки задачи агенту: аргумент или stdin.

    В режиме ``auto`` задача идёт аргументом, пока командная строка помещается
    в платформенный лимит; длинный текст уходит в stdin, иначе запуск оборвётся
    на уровне ОС.
    """
    if mode not in AGENT_INPUT_MODES:
        raise ValueError(
            f"неизвестный способ передачи задачи: {mode!r} "
            f"(допустимо: {', '.join(AGENT_INPUT_MODES)})"
        )
    if mode != "auto":
        return mode
    length = sum(len(part) + 3 for part in parts) + len(task)
    return "stdin" if length > arg_limit() else "arg"


def run_task(
    task: str,
    vault_path: Path,
    agent_cmd: str | None = None,
    timeout: float | None = None,
    *,
    agent_input: str = "auto",
) -> int:
    """Передаёт задачу CLI-агенту (Claude Code и т.п.), с рабочей директорией и
    OBSIDIAN_VAULT_PATH выставленными на vault — активацию навыка obsidian
    выполняет сам агент.
    """
    parts = parse_agent_cmd(agent_cmd)
    channel = resolve_agent_input(agent_input, parts, task)
    command = parts if channel == "stdin" else [*parts, task]

    env = {**os.environ, "OBSIDIAN_VAULT_PATH": str(vault_path)}
    stdin_text = task if channel == "stdin" else None
    try:
        result = subprocess.run(
            command,
            cwd=vault_path,
            env=env,
            timeout=timeout,
            # Задача уходит байтами: в текстовом режиме Python на Windows
            # заменил бы \n на \r\n и агент получил бы не тот текст.
            input=None if stdin_text is None else stdin_text.encode("utf-8"),
        )
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
