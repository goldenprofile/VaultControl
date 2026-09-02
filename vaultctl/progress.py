"""Показ хода работы: статус запуска и разбор потока событий CLI-агента.

Агент в неинтерактивном режиме (``claude -p``) молчит от старта до финального
ответа, поэтому долгая задача неотличима от зависшей. Здесь — строки статуса
перед запуском и компактный рендер потока событий, когда агент умеет его
отдавать.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO

UNICODE_GLYPHS = {"run": "▶", "step": "·", "ok": "✔", "fail": "✖"}
ASCII_GLYPHS = {"run": ">", "step": "-", "ok": "+", "fail": "x"}

#: Флаги, включающие поток событий у Claude Code.
CLAUDE_STREAM_FLAGS = ("--output-format", "stream-json", "--verbose")

#: Ключи аргумента инструмента, которые стоит показать пользователю.
TOOL_ARG_KEYS = (
    "file_path",
    "url",
    "command",
    "path",
    "pattern",
    "notebook_path",
    "query",
    "prompt",
    "description",
    # Имя навыка: строка `Skill(obsidian)` показывает, что агент подхватил
    # навык, — ради этого vaultctl и запускается.
    "skill",
)

MAX_ARG_LEN = 70
MAX_TEXT_LEN = 200


def glyphs_for(stream: IO[str]) -> dict[str, str]:
    """Подбирает набор символов под кодировку потока.

    В консоли cp866 (обычная Windows-консоль) ``▶`` не кодируется — там
    выводим ASCII-эквиваленты вместо падения с UnicodeEncodeError.
    """
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return ASCII_GLYPHS
    try:
        "".join(UNICODE_GLYPHS.values()).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return ASCII_GLYPHS
    return UNICODE_GLYPHS


def _shorten(value: str, limit: int) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def describe_task(task: str) -> str:
    """Человекочитаемый размер задачи для строки статуса."""
    lines = task.count("\n") + 1
    if lines == 1:
        return f"{len(task)} символов"
    return f"{len(task)} символов, {lines} строк"


def print_status(
    stream: IO[str],
    vault_path: Path,
    agent_command: list[str],
    task: str,
    *,
    agent_input: str,
) -> None:
    """Печатает, что именно запускается, до того как агент замолчит надолго.

    ``agent_command`` — команда без текста задачи: сам текст описывается
    размером, иначе статус превратится в простыню на весь пост.
    """
    marks = glyphs_for(stream)
    run = marks["run"]
    channel = "stdin" if agent_input == "stdin" else "аргумент"
    print(f"{run} vault: {vault_path}", file=stream)
    print(f"{run} агент: {' '.join(agent_command)}", file=stream)
    print(f"{run} задача: {describe_task(task)} ({channel})", file=stream)
    stream.flush()


def supports_stream(command: list[str]) -> bool:
    """Отвечает, знаем ли мы формат потока событий этого агента.

    Разбирать умеем только JSONL Claude Code, поэтому смотрим на имя команды.
    """
    if not command:
        return False
    name = Path(command[0]).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        name = name.removesuffix(suffix)
    return name == "claude"


def _tool_argument(tool_input: object, vault_path: Path | None) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in TOOL_ARG_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return _shorten(_relative_to_vault(value, vault_path), MAX_ARG_LEN)
    return ""


def _relative_to_vault(value: str, vault_path: Path | None) -> str:
    """Укорачивает путь внутри vault до относительного — строка статуса короче."""
    if vault_path is None:
        return value
    try:
        return Path(value).relative_to(vault_path).as_posix()
    except (ValueError, OSError):
        return value


class StreamRenderer:
    """Переводит JSONL-поток Claude Code в компактные строки прогресса.

    Формат потока — не контракт, поэтому нераспознанные события молча
    пропускаются, а не ломают запуск. Строки, которые не разобрались как JSON,
    отдаются пользователю как есть: это обычный вывод агента.
    """

    def __init__(
        self, stream: IO[str], vault_path: Path | None = None, *, parse: bool = True
    ) -> None:
        self._stream = stream
        self._vault_path = vault_path
        self._parse = parse
        self._marks = glyphs_for(stream)
        # Финальный ответ приходит дважды: репликой ассистента и полем result.
        # Помним последнюю показанную реплику, чтобы не печатать её повторно.
        self._last_text: str | None = None

    def feed(self, line: str) -> None:
        """Обрабатывает одну строку вывода агента."""
        line = line.rstrip("\n")
        if not line.strip():
            return
        if not self._parse:
            self._write(line)
            return
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            self._write(line)
            return
        if isinstance(event, dict):
            self._render(event)

    def _write(self, text: str) -> None:
        print(text, file=self._stream)
        self._stream.flush()

    def _render(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "system":
            self._render_system(event)
        elif kind == "assistant":
            self._render_assistant(event)
        elif kind == "user":
            self._render_tool_results(event)
        elif kind == "result":
            self._render_result(event)

    def _render_system(self, event: dict) -> None:
        if event.get("subtype") != "init":
            return
        model = event.get("model")
        suffix = f" ({model})" if isinstance(model, str) and model else ""
        self._write(f"{self._marks['run']} агент запущен{suffix}")

    def _render_assistant(self, event: dict) -> None:
        for block in _content_blocks(event):
            kind = block.get("type")
            if kind == "tool_use":
                name = block.get("name", "tool")
                argument = _tool_argument(block.get("input"), self._vault_path)
                shown = f"{name}({argument})" if argument else str(name)
                self._write(f"  {self._marks['step']} {shown}")
            elif kind == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    shown = _shorten(text, MAX_TEXT_LEN)
                    self._last_text = text.strip() if shown == text.strip() else None
                    self._write(f"  {shown}")

    def _render_tool_results(self, event: dict) -> None:
        for block in _content_blocks(event):
            if block.get("type") == "tool_result" and block.get("is_error"):
                content = block.get("content")
                detail = content if isinstance(content, str) else ""
                suffix = f": {_shorten(detail, MAX_ARG_LEN)}" if detail else ""
                self._write(f"  {self._marks['fail']} ошибка инструмента{suffix}")

    def _render_result(self, event: dict) -> None:
        duration = event.get("duration_ms")
        parts: list[str] = []
        if isinstance(duration, (int, float)):
            parts.append(f"за {duration / 1000:.0f} с")
        cost = event.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            parts.append(f"${cost:.2f}")
        suffix = f" {', '.join(parts)}" if parts else ""

        failed = bool(event.get("is_error")) or event.get("subtype") != "success"
        mark = self._marks["fail"] if failed else self._marks["ok"]
        status = "агент завершился с ошибкой" if failed else "готово"
        self._write(f"{mark} {status}{suffix}")

        text = event.get("result")
        if isinstance(text, str) and text.strip() and text.strip() != self._last_text:
            self._write(text.strip())


def _content_blocks(event: dict) -> list[dict]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]
