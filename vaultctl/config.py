"""Определение пути к Obsidian vault."""

from __future__ import annotations

import os
from pathlib import Path


class VaultNotFoundError(RuntimeError):
    """Не удалось определить путь к vault."""


def resolve_vault_path(start: Path | None = None) -> Path:
    """Определяет путь к Obsidian vault.

    Порядок: переменная окружения ``OBSIDIAN_VAULT_PATH``, иначе — автопоиск
    ближайшей родительской директории, содержащей папку ``.obsidian/``.
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
