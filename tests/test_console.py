import io
import sys

from vaultctl import console
from vaultctl.console import flush_input


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        raise OSError("нет дескриптора")


def test_flush_input_skips_pipes():
    """Пайп — это данные задачи, а не хвост вставки: трогать его нельзя."""
    assert flush_input(io.StringIO("текст задачи")) is False


def test_flush_input_survives_stream_without_descriptor(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert flush_input(FakeTty()) is False


def test_flush_input_uses_console_api_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[str] = []

    def fake_flush() -> bool:
        calls.append("flush")
        return True

    monkeypatch.setattr(console, "_flush_windows", fake_flush)

    assert flush_input(FakeTty()) is True
    assert calls == ["flush"]


def test_flush_input_on_closed_stream():
    stream = io.StringIO()
    stream.close()
    assert flush_input(stream) is False
