"""Точка входа CLI: `vaultctl "<задача>"`."""

from __future__ import annotations

import argparse
import sys

from .config import VaultNotFoundError, resolve_vault_path
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    task = " ".join(args.task)

    try:
        vault_path = resolve_vault_path()
    except VaultNotFoundError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    return run_task(task, vault_path, args.agent_cmd)


if __name__ == "__main__":
    raise SystemExit(main())
