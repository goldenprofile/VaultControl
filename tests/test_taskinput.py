import io
import subprocess
from pathlib import Path

import pytest

from vaultctl import taskinput
from vaultctl.taskinput import (
    TaskInputError,
    compose_task,
    read_clipboard,
    read_task_file,
    resolve_task,
)

POST = "Нейродайджест (#128)\n\n- GLM 5.3 Flash — $0,15/$0,5 за миллион токенов."


class FakeTty(io.StringIO):
    """Поток, притворяющийся терминалом: ввод идёт с клавиатуры, не пайпом."""

    def isatty(self) -> bool:
        return True


def test_compose_task_puts_instruction_before_body():
    assert compose_task("clip:", "текст") == "clip:\n\nтекст"


def test_compose_task_without_body_keeps_instruction():
    assert compose_task("сохрани ссылку", "") == "сохрани ссылку"


def test_compose_task_without_instruction_keeps_body():
    assert compose_task("", POST) == POST


def test_resolve_task_joins_positional_arguments():
    task = resolve_task(["сохрани", "ссылку"], stdin=FakeTty())
    assert task == "сохрани ссылку"


def test_resolve_task_reads_piped_stdin_without_flag():
    task = resolve_task(["clip:"], stdin=io.StringIO(POST))
    assert task == f"clip:\n\n{POST}"


class FakePiped(io.StringIO):
    """Пайп как его видит реальный stdin: текстовый слой с .buffer внутри."""

    def __init__(self, raw: bytes) -> None:
        super().__init__()
        self.buffer = io.BytesIO(raw)

    def isatty(self) -> bool:
        return False


def test_resolve_task_reads_piped_stdin_as_utf8_bytes():
    piped = FakePiped(POST.encode("utf-8"))

    task = resolve_task([], stdin=piped)

    assert task == POST


def test_resolve_task_keeps_dollar_and_newlines_intact():
    task = resolve_task([], stdin=io.StringIO(POST))
    assert "$0,15/$0,5" in task
    assert task.count("\n") == 2


def test_resolve_task_normalizes_crlf():
    task = resolve_task([], stdin=io.StringIO("первая\r\nвторая\r\n"))
    assert task == "первая\nвторая"


def test_resolve_task_from_file(tmp_path: Path):
    source = tmp_path / "post.txt"
    source.write_text(POST, encoding="utf-8")

    task = resolve_task(["clip:"], task_file=source, stdin=FakeTty())

    assert task == f"clip:\n\n{POST}"


def test_resolve_task_from_clipboard(monkeypatch):
    monkeypatch.setattr(taskinput, "read_clipboard", lambda: POST)

    task = resolve_task(["clip:"], clipboard=True, stdin=FakeTty())

    assert task == f"clip:\n\n{POST}"


def test_resolve_task_prompts_when_nothing_given():
    stderr = io.StringIO()

    task = resolve_task([], stdin=FakeTty(POST), stderr=stderr)

    assert task == POST
    assert "Ctrl" in stderr.getvalue()


def test_resolve_task_rejects_two_sources(tmp_path: Path):
    with pytest.raises(TaskInputError, match="взаимоисключающие"):
        resolve_task([], clipboard=True, task_file=tmp_path / "post.txt")


def test_resolve_task_rejects_empty_input():
    with pytest.raises(TaskInputError, match="пуста"):
        resolve_task([], stdin=io.StringIO("   \n"))


def test_read_task_file_missing(tmp_path: Path):
    with pytest.raises(TaskInputError, match="не найден"):
        read_task_file(tmp_path / "нет.txt")


def test_read_task_file_empty(tmp_path: Path):
    source = tmp_path / "post.txt"
    source.write_text("\n\n", encoding="utf-8")

    with pytest.raises(TaskInputError, match="пуст"):
        read_task_file(source)


def test_read_clipboard_decodes_utf8(monkeypatch):
    monkeypatch.setattr(taskinput.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        taskinput.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 0, POST.encode("utf-8") + b"\r\n", b""
        ),
    )

    assert read_clipboard() == POST


def test_read_clipboard_without_backend(monkeypatch):
    monkeypatch.setattr(taskinput.shutil, "which", lambda name: None)

    with pytest.raises(TaskInputError, match="утилита чтения буфера обмена"):
        read_clipboard()


def test_read_clipboard_empty(monkeypatch):
    monkeypatch.setattr(taskinput.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        taskinput.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, b"", b""),
    )

    with pytest.raises(TaskInputError, match="пуст"):
        read_clipboard()


def test_read_clipboard_falls_through_to_next_backend(monkeypatch):
    monkeypatch.setattr(taskinput.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls: list[str] = []

    def fake_run(command, **kwargs):
        calls.append(command[0])
        if len(calls) == 1:
            raise OSError("бэкенд недоступен")
        return subprocess.CompletedProcess(command, 0, POST.encode("utf-8"), b"")

    monkeypatch.setattr(taskinput.subprocess, "run", fake_run)

    assert read_clipboard() == POST
    assert len(calls) == 2
