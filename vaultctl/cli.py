"""Точка входа CLI: `vaultctl "<задача>"`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import VaultNotFoundError, load_env, resolve_vault_path
from .runner import run_task


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaultctl",
        description=(
            "vaultctl — CLI, передающий задачи над Obsidian vault "
            "CLI-агенту с установленным навыком obsidian."
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    task = " ".join(args.task)

    try:
        load_env(Path(args.env_file) if args.env_file else None)
        vault_path = resolve_vault_path()
    except (FileNotFoundError, VaultNotFoundError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    return run_task(task, vault_path, args.agent_cmd)


if __name__ == "__main__":
    raise SystemExit(main())
