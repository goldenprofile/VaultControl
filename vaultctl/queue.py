"""Очередь задач vaultctl: состояние в `~/.local/state/vaultctl/`, без демона.

Один JSON-файл на задачу в `runs/`. Воркер — отсоединённая копия
`python -m vaultctl.worker`, которая разбирает очередь сама и завершается,
когда задач больше нет. Два лока:

- `worker.lock` — один воркер на систему (O_EXCL, pid владельца, устаревший
  перехватывается);
- `vault-<hash>.lock` — один агент на vault; его берёт и блокирующий запуск,
  чтобы два агента не писали в один vault одновременно.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

TERMINAL_STATUSES = ("done", "failed", "cancelled")


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def state_dir() -> Path:
    override = os.environ.get("VAULTCTL_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "vaultctl"


def runs_dir() -> Path:
    return state_dir() / "runs"


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------- элементы


def _item_path(task_id: int) -> Path:
    return runs_dir() / f"{task_id}.json"


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_item(task_id: int) -> dict | None:
    item = _read_json(_item_path(task_id))
    return item if isinstance(item, dict) and item.get("id") == task_id else None


def list_items() -> list[dict]:
    items = []
    for path in runs_dir().glob("*.json"):
        item = _read_json(path)
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            items.append(item)
    return sorted(items, key=lambda item: item["id"])


def create_item(task: str, vault: str, *, timeout: float | None, agent_cmd: str | None) -> dict:
    task_id = max((item["id"] for item in list_items()), default=0) + 1
    item = {
        "id": task_id,
        "task": task,
        "vault": vault,
        "agent_cmd": agent_cmd,
        "timeout": timeout,
        "status": "queued",
        "created": now_iso(),
        "started": None,
        "finished": None,
        "pid": None,
        "log": str(state_dir() / "logs" / f"{task_id}.log"),
        "exit_code": None,
        "result": None,
    }
    _write_json(_item_path(task_id), item)
    return item


def update_item(task_id: int, **fields: object) -> dict | None:
    item = load_item(task_id)
    if item is None:
        return None
    item.update(fields)
    _write_json(_item_path(task_id), item)
    return item


def delete_item(task_id: int) -> None:
    """Удаляет задачу и её лог (prune)."""
    item = load_item(task_id)
    if item and item.get("log"):
        Path(item["log"]).unlink(missing_ok=True)
    _item_path(task_id).unlink(missing_ok=True)


def reconcile() -> int:
    """`running` с мёртвым pid → `failed`. Возвращает число исправленных."""
    fixed = 0
    for item in list_items():
        if item["status"] == "running" and not pid_alive(item.get("pid")):
            update_item(
                item["id"],
                status="failed",
                finished=now_iso(),
                error="процесс воркера завершился, не доведя задачу",
            )
            fixed += 1
    return fixed


def next_queued() -> dict | None:
    for item in list_items():
        if item["status"] == "queued":
            return item
    return None


# ---------------------------------------------------------------- локи


class LockBusy(Exception):
    """Лок занят живым процессом; args[0] — информация о владельце."""

    def __init__(self, owner: dict):
        super().__init__(owner)
        self.owner = owner


def _locks_dir() -> Path:
    return state_dir() / "locks"


def _lock_path(name: str) -> Path:
    return _locks_dir() / f"{name}.lock"


def vault_lock_name(vault: str) -> str:
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(vault)).encode()).hexdigest()[:12]
    return f"vault-{digest}"


def acquire_lock(name: str) -> None:
    """Берёт лок или бросает LockBusy. Устаревший лок (мёртвый pid) перехватывает."""
    path = _lock_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        owner = _read_json(path) or {}
        if pid_alive(owner.get("pid")):
            raise LockBusy(owner) from None
        fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
    try:
        os.write(fd, json.dumps({"pid": os.getpid(), "time": now_iso()}).encode())
    finally:
        os.close(fd)


def release_lock(name: str) -> None:
    _lock_path(name).unlink(missing_ok=True)


def lock_info(name: str) -> dict | None:
    return _read_json(_lock_path(name))


# ---------------------------------------------------------------- воркер


def worker_busy() -> bool:
    """Живой воркер уже работает с очередью?"""
    owner = lock_info("worker")
    return pid_alive(owner.get("pid")) if owner else False


def spawn_worker() -> bool:
    """Запускает отсоединённого воркера, если очереди нужен исполнитель.

    Возвращает True, если воркер запущен. Гонка «два одновременных -d»
    безопасна: второй воркер на занятом worker.lock тихо завершится.
    """
    if worker_busy():
        return False
    try:
        acquire_lock("worker")
    except LockBusy:
        return False
    release_lock("worker")  # воркер возьмёт лок сам; кто первый — тот и жилец

    import subprocess

    command = [sys.executable, "-m", "vaultctl.worker", str(runs_dir())]
    worker_log = state_dir() / "logs" / "worker.log"
    worker_log.parent.mkdir(parents=True, exist_ok=True)
    with open(worker_log, "ab") as log:
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                command,
                creationflags=flags,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        else:
            subprocess.Popen(
                command,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    return True
