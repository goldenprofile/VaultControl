"""Запуск задачи через CLI-агента с навыком obsidian."""

from __future__ import annotations

import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, NoReturn

from .progress import (
    CLAUDE_STREAM_FLAGS,
    StreamRenderer,
    glyphs_for,
    print_status,
    supports_stream,
)

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
    stream: bool = False,
    quiet: bool = False,
    status_stream: IO[str] | None = None,
    output_stream: IO[str] | None = None,
) -> int:
    """Передаёт задачу CLI-агенту (Claude Code и т.п.), с рабочей директорией и
    OBSIDIAN_VAULT_PATH выставленными на vault — активацию навыка obsidian
    выполняет сам агент.
    """
    status_stream = status_stream if status_stream is not None else sys.stderr
    output_stream = output_stream if output_stream is not None else sys.stdout

    parts = parse_agent_cmd(agent_cmd)
    channel = resolve_agent_input(agent_input, parts, task)

    streaming = stream and supports_stream(parts)
    if stream and not streaming and not quiet:
        marks = glyphs_for(status_stream)
        print(
            f"{marks['fail']} формат потока событий известен только для Claude Code — "
            f"вывод «{parts[0]}» идёт как есть",
            file=status_stream,
        )

    if streaming:
        parts = _with_stream_flags(parts)
    command = parts if channel == "stdin" else [*parts, task]

    if not quiet:
        print_status(status_stream, vault_path, parts, task, agent_input=channel)

    env = {**os.environ, "OBSIDIAN_VAULT_PATH": str(vault_path)}
    stdin_text = task if channel == "stdin" else None
    try:
        if streaming:
            return _run_streaming(
                command, vault_path, env, timeout, stdin_text, output_stream
            )
        return _run_plain(command, vault_path, env, timeout, stdin_text)
    except FileNotFoundError as exc:
        raise AgentNotFoundError(
            f"CLI-агент «{command[0]}» не найден — проверьте --agent-cmd "
            f"или VAULTCTL_AGENT_CMD (путь с пробелами и обратными слэшами "
            f"нужно взять в кавычки)"
        ) from exc


def _with_stream_flags(parts: list[str]) -> list[str]:
    """Добавляет агенту флаги потока событий, не трогая уже заданные вручную."""
    if "--output-format" in parts:
        return parts
    flags = [flag for flag in CLAUDE_STREAM_FLAGS if flag not in parts]
    # Флаги идут сразу после имени команды: последним аргументом должна
    # остаться задача, а перед ней — флаг вроде -p, ожидающий её значением.
    return [parts[0], *flags, *parts[1:]]


def _run_plain(
    command: list[str],
    vault_path: Path,
    env: dict[str, str],
    timeout: float | None,
    stdin_text: str | None,
) -> int:
    """Запускает агента, отдав ему потоки вывода как есть."""
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
    except subprocess.TimeoutExpired as exc:
        raise AgentTimeoutError(
            f"CLI-агент не завершился за {timeout} с и был остановлен"
        ) from exc
    return result.returncode


def _run_streaming(
    command: list[str],
    vault_path: Path,
    env: dict[str, str],
    timeout: float | None,
    stdin_text: str | None,
    output_stream: IO[str],
) -> int:
    """Запускает агента и показывает его шаги по мере поступления."""
    # Потоки бинарные: кодировку задаём сами, а не отдаём на откуп локали, и
    # не даём Windows подменить \n на \r\n в задаче, уходящей агенту.
    process = subprocess.Popen(
        command,
        cwd=vault_path,
        env=env,
        stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=subprocess.PIPE,
    )

    if stdin_text is not None:
        # Пишем в отдельном потоке: задача может не поместиться в буфер пайпа,
        # а агент к этому моменту уже пишет в stdout — иначе взаимная блокировка.
        threading.Thread(
            target=_feed_stdin, args=(process, stdin_text), daemon=True
        ).start()

    renderer = StreamRenderer(output_stream, vault_path)
    lines: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(target=_read_lines, args=(process, lines), daemon=True)
    reader.start()

    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            _timeout(process, timeout)
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            _timeout(process, timeout)
        if line is None:
            break
        renderer.feed(line)

    remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
    try:
        return process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        _timeout(process, timeout)


def _feed_stdin(process: subprocess.Popen, text: str) -> None:
    try:
        assert process.stdin is not None
        process.stdin.write(text.encode("utf-8"))
        process.stdin.close()
    except (BrokenPipeError, OSError, ValueError):
        pass


def _read_lines(process: subprocess.Popen, lines: queue.Queue) -> None:
    try:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line.decode("utf-8", errors="replace"))
    finally:
        lines.put(None)


def _timeout(process: subprocess.Popen, timeout: float | None) -> NoReturn:
    """Снимает зависшего агента и поднимает ошибку таймаута."""
    process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    raise AgentTimeoutError(f"CLI-агент не завершился за {timeout} с и был остановлен")
