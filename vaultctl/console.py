"""Очистка ввода, накопившегося в терминале за время работы агента.

Многострочная вставка в PowerShell разбирается построчно: первую строку шелл
берёт как команду, остальные остаются в очереди ввода консоли и выполняются
как отдельные команды, когда команда завершится. После долгого запуска агента
это выглядит как шквал ошибок вида ``Missing expression after unary operator``
из текста, который пользователь всего лишь хотел передать в vault.

Очередь ввода общая для процессов в одной консоли, поэтому её можно сбросить
изнутри vaultctl — до запуска агента и после его завершения.
"""

from __future__ import annotations

import sys
from typing import IO

#: STD_INPUT_HANDLE (-10) в виде DWORD, как его ждёт GetStdHandle.
STD_INPUT_HANDLE = 0xFFFFFFF6


def safe_print(text: str, stream: IO[str] | None = None) -> None:
    """print, переживающий символы вне кодировки потока.

    Текст от агента может содержать эмодзи, которых нет в cp1251 обычной
    Windows-консоли — обычный print уронит прогон на показе результата.
    Непредставимые символы заменяются на ``?``.
    """
    stream = stream if stream is not None else sys.stdout
    encoding = getattr(stream, "encoding", None)
    if encoding:
        try:
            text.encode(encoding)
        except UnicodeEncodeError:
            text = text.encode(encoding, errors="replace").decode(encoding)
    print(text, file=stream)


def flush_input(stream: IO[str] | None = None) -> bool:
    """Сбрасывает непрочитанный ввод терминала.

    Возвращает ``True``, если сброс выполнен. Пайп, перенаправленный файл и
    любая неконсольная ситуация оставляются нетронутыми: там ввод — это данные,
    а не случайный хвост вставки.
    """
    stream = stream if stream is not None else sys.stdin
    if not _is_console(stream):
        return False
    if sys.platform == "win32":
        return _flush_windows()
    return _flush_posix(stream)


def _is_console(stream: IO[str]) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _flush_windows() -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Типы обязательны: без restype ctypes считает результат 32-битным int
        # и усекает 64-битный HANDLE, после чего сброс тихо не срабатывает.
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.FlushConsoleInputBuffer.argtypes = [wintypes.HANDLE]
        kernel32.FlushConsoleInputBuffer.restype = wintypes.BOOL

        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return False
        return bool(kernel32.FlushConsoleInputBuffer(handle))
    except (OSError, AttributeError, ImportError, ValueError):
        return False


def _flush_posix(stream: IO[str]) -> bool:
    try:
        import termios

        termios.tcflush(stream.fileno(), termios.TCIFLUSH)
        return True
    except (OSError, ImportError, ValueError, AttributeError):
        return False
