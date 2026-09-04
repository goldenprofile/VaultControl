"""Показ хода работы: статус запуска и разбор потока событий CLI-агента.

CLI-агент в неинтерактивном режиме молчит от старта до финального ответа,
поэтому долгая задача неотличима от зависшей. Здесь — строки статуса перед
запуском и компактный рендер потока событий, когда агент умеет его отдавать.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO

UNICODE_GLYPHS = {"run": "▶", "step": "·", "ok": "✔", "fail": "✖"}
ASCII_GLYPHS = {"run": ">", "step": "-", "ok": "+", "fail": "x"}

#: Флаги, включающие поток событий у Claude Code.
CLAUDE_STREAM_FLAGS = ("--output-format", "stream-json", "--verbose")

#: Флаги, включающие поток событий у pi.
PI_STREAM_FLAGS = ("--mode", "json")

#: Какому имени команды какой набор флагов включает знакомый нам поток событий.
#: Агент, чей формат умеем разбирать, добавляется одной записью здесь (плюс
#: веткой в StreamRenderer, если формат отличается от JSONL Claude Code).
STREAM_FLAGS_BY_AGENT: dict[str, tuple[str, ...]] = {
    "claude": CLAUDE_STREAM_FLAGS,
    "pi": PI_STREAM_FLAGS,
}

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


def agent_name(command: list[str]) -> str:
    """Имя исполняемого файла агента без пути и расширения: claude, pi, codex…"""
    if not command:
        return ""
    name = Path(command[0]).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        name = name.removesuffix(suffix)
    return name


def stream_flags(command: list[str]) -> tuple[str, ...] | None:
    """Флаги, включающие поток событий агента, если формат нам знаком."""
    return STREAM_FLAGS_BY_AGENT.get(agent_name(command))


def supports_stream(command: list[str]) -> bool:
    """Отвечает, умеем ли мы разбирать поток событий этого агента.

    Рендер понимает JSONL Claude Code и ``--mode json`` pi; чужие форматы
    не гадаем по событиям — смотри ``STREAM_FLAGS_BY_AGENT``.
    """
    return stream_flags(command) is not None


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
    """Переводит поток событий CLI-агента в компактные строки прогресса.

    Понимаются JSONL Claude Code (по умолчанию) и ``--mode json`` pi —
    формат выбирается аргументом ``agent``. Формат потока — не контракт,
    поэтому нераспознанные события молча пропускаются, а не ломают запуск.
    Строки, которые не разобрались как JSON, отдаются пользователю как есть:
    это обычный вывод агента.
    """

    def __init__(
        self,
        stream: IO[str],
        vault_path: Path | None = None,
        *,
        parse: bool = True,
        agent: str = "claude",
    ) -> None:
        self._stream = stream
        self._vault_path = vault_path
        self._parse = parse
        self._agent = agent
        self._marks = glyphs_for(stream)
        # Финальный ответ приходит дважды: репликой ассистента и полем result.
        # Помним последнюю показанную реплику, чтобы не печатать её повторно.
        self._last_text: str | None = None
        # В потоке pi нет итоговой сводки: модель, цена и признак ошибки
        # собираются из финальных сообщений ассистента по ходу работы.
        self._pi_model: str | None = None
        self._pi_cost: float | None = None
        self._pi_failed = False
        # Финальный ответ агента (для очереди: result задачи).
        self.final_text: str | None = None

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
        if self._agent == "pi":
            self._render_pi(event)
            return
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
        if isinstance(text, str) and text.strip():
            self.final_text = text.strip()
        elif self._last_text:
            self.final_text = self._last_text


    # ------------------------------------------------------------------
    # Поток pi (--mode json): схема событий описана в docs/json.md pi.
    # ------------------------------------------------------------------

    def _render_pi(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "session":
            self._write(f"{self._marks['run']} агент запущен")
        elif kind == "tool_execution_start":
            name = str(event.get("toolName") or "tool")
            argument = _tool_argument(event.get("args"), self._vault_path)
            shown = f"{name}({argument})" if argument else name
            self._write(f"  {self._marks['step']} {shown}")
        elif kind == "tool_execution_end":
            if event.get("isError"):
                detail = _pi_result_text(event.get("result"))
                suffix = f": {_shorten(detail, MAX_ARG_LEN)}" if detail else ""
                self._write(f"  {self._marks['fail']} ошибка инструмента{suffix}")
        elif kind == "message_end":
            self._remember_pi_message(event.get("message"))
        elif kind == "agent_end":
            self._write_pi_done()

    def _remember_pi_message(self, message: object) -> None:
        """Печатает финальный текст реплики и запоминает модель, цену, статус."""
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    self._write(f"  {_shorten(text, MAX_TEXT_LEN)}")
                    self.final_text = text.strip()
        model = message.get("model")
        if isinstance(model, str) and model:
            self._pi_model = model
        cost = _pi_cost(message.get("usage"))
        if cost is not None:
            self._pi_cost = cost
        self._pi_failed = message.get("stopReason") == "error"

    def _write_pi_done(self) -> None:
        parts: list[str] = []
        if self._pi_model:
            parts.append(self._pi_model)
        if self._pi_cost is not None:
            parts.append(f"${self._pi_cost:.2f}")
        suffix = f" {', '.join(parts)}" if parts else ""
        mark = self._marks["fail"] if self._pi_failed else self._marks["ok"]
        status = "агент завершился с ошибкой" if self._pi_failed else "готово"
        self._write(f"{mark} {status}{suffix}")


def _pi_cost(usage: object) -> float | None:
    """Достаёт суммарную стоимость реплики из usage потока pi."""
    if not isinstance(usage, dict):
        return None
    cost = usage.get("cost")
    if isinstance(cost, dict) and isinstance(cost.get("total"), (int, float)):
        return float(cost["total"])
    return None


def _pi_result_text(result: object) -> str:
    """Короткое описание результата инструмента pi для строки об ошибке."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            texts = [
                block["text"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ]
            if texts:
                return " ".join(texts)
        return json.dumps(result, ensure_ascii=False)
    return "" if result is None else str(result)


def _content_blocks(event: dict) -> list[dict]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]
