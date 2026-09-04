"""Точка входа CLI: `vaultctl "<задача>"`."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import VaultNotFoundError, load_env, resolve_timeout, resolve_vault_path
from .console import flush_input, safe_print
from . import queue
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
EXIT_INTERRUPTED = 130
EXIT_VAULT_BUSY = 3

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
        "-d",
        "--detach",
        action="store_true",
        help=(
            "Отдать задачу в очередь и вернуть терминал: выполнит фоновый "
            "воркер (или VAULTCTL_DETACH)"
        ),
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
    args_list = sys.argv[1:] if argv is None else list(argv)
    if args_list[:1] == ["q"]:
        return queue_cli(args_list[1:])
    parser = build_parser()
    args = parser.parse_args(args_list)

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

    if args.detach or env_flag("VAULTCTL_DETACH"):
        if not args.keep_input:
            flush_input()
        return _submit_to_queue(args, task, vault_path, timeout)

    # Задача собрана — всё, что осталось в очереди ввода, это хвост вставки,
    # который шелл иначе выполнит как команды. Сбрасываем до запуска агента и
    # ещё раз после: вставить текст могли и пока агент работал.
    if not args.keep_input:
        flush_input()

    agent_input = args.agent_input or os.environ.get("VAULTCTL_AGENT_INPUT") or "auto"

    # Один агент на vault: лок не пускает и второй блокирующий запуск, и
    # параллельного воркера — два агента, пишущих в один vault, ломают файлы.
    vault_lock = queue.vault_lock_name(str(vault_path))
    try:
        queue.acquire_lock(vault_lock)
    except queue.LockBusy as busy:
        print(
            f"Ошибка: для этого vault уже работает агент (pid {busy.owner.get('pid')}). "
            "Дождитесь окончания или отдайте задачу через: vlt -d …",
            file=sys.stderr,
        )
        return EXIT_VAULT_BUSY
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
    except KeyboardInterrupt:
        # Ctrl+C во время работы агента: ребёнок получает тот же Ctrl+C
        # (общая консоль) и умирает сам — прибивать нечего.
        print("Прервано.", file=sys.stderr)
        return EXIT_INTERRUPTED
    finally:
        queue.release_lock(vault_lock)
        if not args.keep_input:
            flush_input()


def _submit_to_queue(args: argparse.Namespace, task: str, vault_path: Path, timeout: float | None) -> int:
    """Кладёт задачу в очередь и поднимает фонового воркера, если он не один."""
    item = queue.create_item(
        task, str(vault_path), timeout=timeout, agent_cmd=args.agent_cmd
    )
    spawned = queue.spawn_worker()
    first_line = task.splitlines()[0] if task else ""
    safe_print(f"[q#{item['id']}] в очереди: {first_line}")
    print(f"Лог: {item['log']}")
    if not spawned:
        print("Воркер уже работает — задача будет взята им.")
    print("Статус и логи: vlt q")
    return 0


def queue_cli(sub_args: list[str]) -> int:
    """`vlt q` — список задач, логи, ответы, отмена, чистка."""
    parser = argparse.ArgumentParser(
        prog="vlt q", description="Очередь задач vaultctl"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="показать задачи (по умолчанию)")
    log_parser = sub.add_parser("log", help="лог задачи: шаги агента")
    log_parser.add_argument("id", type=int)
    log_parser.add_argument("-f", "--follow", action="store_true", help="следить вживую")
    result_parser = sub.add_parser("result", help="финальный ответ агента")
    result_parser.add_argument("id", type=int)
    cancel_parser = sub.add_parser("cancel", help="отменить задачу")
    cancel_parser.add_argument("id", type=int)
    prune_parser = sub.add_parser("prune", help="удалить завершённые задачи")
    prune_parser.add_argument("--keep-days", type=int, default=7)
    args = parser.parse_args(sub_args)

    if args.command == "log":
        return _queue_log(args.id, follow=args.follow)
    if args.command == "result":
        return _queue_result(args.id)
    if args.command == "cancel":
        return _queue_cancel(args.id)
    if args.command == "prune":
        return _queue_prune(args.keep_days)
    _queue_list()
    return 0


def _queue_list() -> None:
    queue.reconcile()
    items = queue.list_items()
    if not items:
        print("Очередь пуста. Отдать задачу в фон: vlt -d 'задача'")
        return
    for item in items:
        stamp = (item.get("started") or item.get("created") or "")[11:16]
        first_line = (item.get("task") or "").splitlines()[0][:60]
        safe_print(f"[q#{item['id']}] {item['status']:<9} {stamp}  {first_line}")


def _queue_log(task_id: int, follow: bool) -> int:
    queue.reconcile()
    item = queue.load_item(task_id)
    if item is None:
        print(f"Ошибка: задача #{task_id} не найдена.", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    log = Path(item["log"])
    if not log.exists():
        print("Лога ещё нет — агент не начинал работу.")
        return 0
    try:
        with log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                safe_print(line.rstrip("\n"))
            while follow and (queue.load_item(task_id) or {}).get("status") == "running":
                line = fh.readline()
                if line:
                    safe_print(line.rstrip("\n"))
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    return 0


def _queue_result(task_id: int) -> int:
    queue.reconcile()
    item = queue.load_item(task_id)
    if item is None:
        print(f"Ошибка: задача #{task_id} не найдена.", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    status = item["status"]
    if status in ("queued", "running"):
        print(f"Задача ещё {status}. Финальный ответ появится после завершения.")
        return 0
    if item.get("result"):
        safe_print(item["result"])
    else:
        print(
            f"Ответа нет (статус: {status}, код: {item.get('exit_code')}). "
            f"Полный лог: {item['log']}"
        )
    return 0


def _queue_cancel(task_id: int) -> int:
    queue.reconcile()
    item = queue.load_item(task_id)
    if item is None:
        print(f"Ошибка: задача #{task_id} не найдена.", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    status = item["status"]
    if status in queue.TERMINAL_STATUSES:
        print(f"Задача уже завершена (статус: {status}).")
        return 0
    if status == "running" and item.get("pid"):
        pid = item["pid"]
        if sys.platform == "win32":
            # /T — дерево процессов: вместе с воркером умрут и его агенты.
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True
            )
        else:
            import signal

            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    queue.update_item(task_id, status="cancelled", finished=queue.now_iso())
    queue.release_lock(queue.vault_lock_name(item["vault"]))
    queue.spawn_worker()  # если в очереди осталось что-то — поднимем исполнителя
    print(f"[q#{task_id}] отменена.")
    return 0


def _queue_prune(keep_days: int) -> int:
    cutoff = dt.datetime.now() - dt.timedelta(days=keep_days)
    removed = 0
    for item in queue.list_items():
        if item["status"] not in queue.TERMINAL_STATUSES:
            continue
        finished = item.get("finished") or item.get("created") or ""
        try:
            done_at = dt.datetime.fromisoformat(finished)
        except ValueError:
            done_at = None
        if done_at is not None and done_at >= cutoff:
            continue
        queue.delete_item(item["id"])
        removed += 1
    print(f"Удалено задач: {removed}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
