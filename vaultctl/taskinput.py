"""Сбор текста задачи: аргументы, stdin, буфер обмена, файл, интерактивный ввод.

Аргументы командной строки — плохой канал для длинного текста: шелл ломает
многострочную вставку, PowerShell раскрывает ``$0,15`` в ``,15``, а Windows
обрывает командную строку на 32767 символах. Поэтому текст задачи можно подать
мимо argv — из буфера обмена, файла, пайпа или интерактивного ввода.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import IO

CLIPBOARD_TIMEOUT = 20


class TaskInputError(RuntimeError):
    """Не удалось собрать текст задачи."""


def _normalize(text: str) -> str:
    """Приводит переводы строк к ``\n`` и снимает краевые пробелы."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _clipboard_commands() -> list[list[str]]:
    """Команды чтения буфера обмена, в порядке предпочтения для платформы."""
    if sys.platform == "win32":
        # Windows PowerShell 5.1 при перенаправлении отдаёт OEM-кодировку
        # (cp866), поэтому вывод форсируем в UTF-8. В pwsh 7 это уже умолчание.
        script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"
        return [
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
        ]
    if sys.platform == "darwin":
        return [["pbpaste"]]
    return [
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
    ]


def read_clipboard() -> str:
    """Читает текст из буфера обмена системными утилитами.

    Пробует бэкенды по очереди, чтобы не тянуть зависимость ради одной операции.
    """
    tried: list[str] = []
    for command in _clipboard_commands():
        if shutil.which(command[0]) is None:
            continue
        tried.append(command[0])
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=CLIPBOARD_TIMEOUT
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        text = _normalize(result.stdout.decode("utf-8", errors="replace"))
        if text:
            return text
        raise TaskInputError("буфер обмена пуст или не содержит текста")

    if not tried:
        raise TaskInputError(
            "не найдена утилита чтения буфера обмена "
            f"({', '.join(cmd[0] for cmd in _clipboard_commands())}) — "
            "передайте текст через --file или пайпом"
        )
    raise TaskInputError(
        f"не удалось прочитать буфер обмена (пробовали: {', '.join(tried)})"
    )


def read_task_file(path: Path) -> str:
    """Читает текст задачи из файла."""
    resolved = path.expanduser()
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise TaskInputError(f"файл с задачей не найден: {resolved}") from None
    except UnicodeDecodeError:
        raise TaskInputError(
            f"файл с задачей не читается как UTF-8: {resolved}"
        ) from None
    text = _normalize(text)
    if not text:
        raise TaskInputError(f"файл с задачей пуст: {resolved}")
    return text


def read_stream(stream: IO[str]) -> str:
    """Читает текст задачи из потока (пайп или интерактивный ввод) до EOF."""
    return _normalize(stream.read())


def _interactive_hint() -> str:
    end_key = "Ctrl+Z, Enter" if sys.platform == "win32" else "Ctrl+D"
    return f"Текст задачи ({end_key} — запустить, Ctrl+C — отмена):"


def prompt_interactive(stdin: IO[str], stderr: IO[str]) -> str:
    """Просит ввести многострочную задачу прямо в терминале.

    Вставка идёт мимо разбора шелла, поэтому кавычки, ``$`` и переводы строк
    доезжают до агента как есть.
    """
    print(_interactive_hint(), file=stderr)
    stderr.flush()
    try:
        text = read_stream(stdin)
    except KeyboardInterrupt:
        raise TaskInputError("ввод задачи отменён") from None
    if not text:
        raise TaskInputError("задача пуста")
    return text


def compose_task(prefix: str, body: str) -> str:
    """Склеивает инструкцию из argv с телом задачи.

    Инструкция идёт первой строкой, тело — отдельным блоком: агент видит
    «что сделать» до того, как начнёт разбирать сам текст.
    """
    prefix = prefix.strip()
    if not prefix:
        return body
    if not body:
        return prefix
    return f"{prefix}\n\n{body}"


def resolve_task(
    parts: list[str],
    *,
    clipboard: bool = False,
    task_file: Path | None = None,
    use_stdin: bool = False,
    stdin: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> str:
    """Определяет текст задачи из доступных источников.

    Тело задачи берётся ровно из одного источника: буфер обмена, файл или
    stdin. Позиционные аргументы становятся инструкцией перед этим телом, а
    когда тела нет — самой задачей. Пустой вызов в терминале открывает
    интерактивный ввод.
    """
    stdin = stdin if stdin is not None else sys.stdin
    stderr = stderr if stderr is not None else sys.stderr

    sources = [
        name
        for name, active in (
            ("--clipboard", clipboard),
            ("--file", task_file is not None),
            ("--stdin", use_stdin),
        )
        if active
    ]
    if len(sources) > 1:
        raise TaskInputError(
            f"источники задачи взаимоисключающие, указано сразу: {', '.join(sources)}"
        )

    prefix = " ".join(parts)
    piped = _stdin_is_piped(stdin)

    if clipboard:
        body = read_clipboard()
    elif task_file is not None:
        body = read_task_file(task_file)
    elif use_stdin or piped:
        body = read_stream(stdin)
        if not body and not prefix:
            raise TaskInputError("задача пуста: stdin не содержит текста")
    elif prefix:
        body = ""
    else:
        body = prompt_interactive(stdin, stderr)

    task = compose_task(prefix, body)
    if not task:
        raise TaskInputError("задача пуста")
    return task


def _stdin_is_piped(stdin: IO[str]) -> bool:
    """Отвечает, пришли ли данные пайпом, а не с клавиатуры."""
    try:
        return not stdin.isatty()
    except (AttributeError, ValueError):
        return False
