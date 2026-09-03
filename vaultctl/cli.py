"""Точка входа CLI: `vaultctl "<задача>"`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import VaultNotFoundError, load_env, resolve_timeout, resolve_vault_path
from .console import flush_input
from .runner import (
    AGENT_INPUT_MODES,
    DEFAULT_AGENT_CMD,
    AgentNotFoundError,
    AgentTimeoutError,
    run_task,
)
from .taskinput import TaskInputError, resolve_task

EXIT_CONFIG_ERROR = 1
EXIT_TIMEOUT = 124
EXIT_AGENT_NOT_FOUND = 127

TRUTHY = {"1", "true", "yes", "on"}


def env_flag(name: str) -> bool:
    """Читает булеву переменную окружения."""
    return os.environ.get(name, "").strip().lower() in TRUTHY


def build_parser() -> argparse.ArgumentParser:
    """Собирает разбор аргументов CLI."""
    parser = argparse.ArgumentParser(
        # prog не задаём: argparse возьмёт фактическое имя вызова —
        # `vaultctl` или короткий алиас `vlt`.
        description=(
            "vaultctl — CLI, передающий задачи над Obsidian vault "
            "CLI-агенту с установленным навыком obsidian."
        ),
        epilog=(
            "Длинный текст не передавайте аргументом: шелл ломает многострочную "
            "вставку, а PowerShell раскрывает $0,15 в ,15. Используйте "
            "--clipboard, --file или пайп."
        ),
    )
    parser.add_argument(
        "task",
        nargs="*",
        help=(
            "Текст задачи или инструкция к тексту из --clipboard/--file/stdin, "
            "например: сохрани ссылку https://example.com/post"
        ),
    )
    parser.add_argument(
        "-c",
        "--clipboard",
        action="store_true",
        help="Взять текст задачи из буфера обмена",
    )
    parser.add_argument(
        "-f",
        "--file",
        dest="task_file",
        help="Взять текст задачи из файла",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Взять текст задачи из stdin (при пайпе включается сам)",
    )
    parser.add_argument(
        "--agent-cmd",
        help=(
            "Команда запуска CLI-агента: способ выбрать агента "
            f"(по умолчанию: '{DEFAULT_AGENT_CMD}', или VAULTCTL_AGENT_CMD)"
        ),
    )
    parser.add_argument(
        "--agent-input",
        choices=AGENT_INPUT_MODES,
        help=(
            "Как передать задачу агенту: arg — последним аргументом, "
            "stdin — потоком, auto — по длине (по умолчанию, или VAULTCTL_AGENT_INPUT)"
        ),
    )
    parser.add_argument(
        "--env-file",
        help="Путь к .env (по умолчанию ищется от текущей директории вверх по дереву)",
    )
    parser.add_argument(
        "--timeout",
        help="Ограничение на работу агента в секундах (или VAULTCTL_TIMEOUT)",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help=(
            "Показывать шаги агента по мере работы, если формат его потока "
            "событий известен (или VAULTCTL_STREAM)"
        ),
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Не печатать строки статуса перед запуском агента",
    )
    parser.add_argument(
        "--keep-input",
        action="store_true",
        help=(
            "Не очищать очередь ввода терминала — иначе хвост многострочной "
            "вставки шелл выполнит как команды"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Проходит запуск по шагам: конфигурация, текст задачи, агент.

    Порядок неслучаен: сначала то, что может отказать дёшево (vault, таймаут,
    источник текста), и только потом долгий запуск агента. Каждый класс отказа
    получает свой код возврата, чтобы вызывающая сторона отличала нехватку
    конфигурации от таймаута и отсутствующего агента.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        load_env(Path(args.env_file) if args.env_file else None)
        timeout = resolve_timeout(args.timeout)
        vault_path = resolve_vault_path()
    except (FileNotFoundError, VaultNotFoundError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        task = resolve_task(
            args.task,
            clipboard=args.clipboard,
            task_file=Path(args.task_file) if args.task_file else None,
            use_stdin=args.stdin,
        )
    except TaskInputError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except KeyboardInterrupt:
        print("Отменено.", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # Задача собрана — всё, что осталось в очереди ввода, это хвост вставки,
    # который шелл иначе выполнит как команды. Сбрасываем до запуска агента и
    # ещё раз после: вставить текст могли и пока агент работал.
    if not args.keep_input:
        flush_input()

    agent_input = args.agent_input or os.environ.get("VAULTCTL_AGENT_INPUT") or "auto"
    try:
        return run_task(
            task,
            vault_path,
            args.agent_cmd,
            timeout,
            agent_input=agent_input,
            stream=args.stream or env_flag("VAULTCTL_STREAM"),
            quiet=args.quiet,
        )
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except AgentNotFoundError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_AGENT_NOT_FOUND
    except AgentTimeoutError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_TIMEOUT
    finally:
        if not args.keep_input:
            flush_input()


if __name__ == "__main__":
    raise SystemExit(main())
