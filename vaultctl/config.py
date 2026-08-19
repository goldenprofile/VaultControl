"""Конфигурация: загрузка ``.env`` и определение пути к Obsidian vault."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


class VaultNotFoundError(RuntimeError):
    """Не удалось определить путь к vault."""


def load_env(env_file: Path | None = None) -> Path | None:
    """Подгружает переменные окружения из ``.env``.

    Без аргумента ищет ``.env`` от текущей рабочей директории вверх по дереву.
    Уже заданные переменные окружения приоритетнее и не перезаписываются.

    Возвращает путь к загруженному файлу или ``None``, если файл не найден.
    Если ``env_file`` задан явно, но не существует — поднимает ``FileNotFoundError``.
    """
    if env_file is not None:
        path = env_file.expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"файл .env не найден: {path}")
        load_dotenv(path, override=False)
        return path

    found = find_dotenv(usecwd=True)
    if not found:
        return None
    load_dotenv(found, override=False)
    return Path(found)


def resolve_vault_path(start: Path | None = None) -> Path:
    """Определяет путь к Obsidian vault.

    Порядок: переменная окружения ``OBSIDIAN_VAULT_PATH`` (в том числе взятая
    из ``.env``), иначе — автопоиск ближайшей родительской директории,
    содержащей папку ``.obsidian/``.
    """
    env_path = os.environ.get("OBSIDIAN_VAULT_PATH")
    if env_path:
        path = Path(env_path).expanduser().resolve()
        if not path.is_dir():
            raise VaultNotFoundError(
                f"OBSIDIAN_VAULT_PATH указывает на несуществующую директорию: {path}"
            )
        return path

    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".obsidian").is_dir():
            return candidate

    raise VaultNotFoundError(
        "Не удалось определить vault: задайте OBSIDIAN_VAULT_PATH "
        "или запустите команду внутри vault (должна быть папка .obsidian/)."
    )
