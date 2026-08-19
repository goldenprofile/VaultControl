"""Точка входа CLI: `vaultctl "<задача>"`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import VaultNotFoundError, load_env, resolve_timeout, resolve_vault_path
from .runner import AgentNotFoundError, AgentTimeoutError, run_task

EXIT_CONFIG_ERROR = 1
EXIT_TIMEOUT = 124
EXIT_AGENT_NOT_FOUND = 127


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaultctl",
        description=(
            "vaultctl — CLI, передающий задачи над Obsidian vault "
            "CLI-агенту с установленным навыком obsidian."
        ),
        epilog=(
            "Текст задачи добавляется последним аргументом команды агента, "
            "поэтому флаг со списком значений (например --allowedTools у Claude "
            "Code) не должен стоять в конце --agent-cmd — он проглотит задачу."
        ),
    )
    parser.add_argument(
        "task",
        nargs="+",
        help="Текст задачи, например: сохрани ссылку https://example.com/post",
    )
    parser.add_argument(
        "--agent-cmd",
        help="Команда запуска CLI-агента (по умолчанию: 'claude -p' или VAULTCTL_AGENT_CMD)",
    )
    parser.add_argument(
        "--env-file",
        help="Путь к .env (по умолчанию ищется от текущей директории вверх по дереву)",
    )
    parser.add_argument(
        "--timeout",
        help="Ограничение на работу агента в секундах (или VAULTCTL_TIMEOUT)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    task = " ".join(args.task)

    try:
        load_env(Path(args.env_file) if args.env_file else None)
        timeout = resolve_timeout(args.timeout)
        vault_path = resolve_vault_path()
    except (FileNotFoundError, VaultNotFoundError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        return run_task(task, vault_path, args.agent_cmd, timeout)
    except AgentNotFoundError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_AGENT_NOT_FOUND
    except AgentTimeoutError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_TIMEOUT


if __name__ == "__main__":
    raise SystemExit(main())
