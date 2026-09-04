"""Отсоединённый воркер очереди: запускается как `python -m vaultctl.worker`.

Берёт `worker.lock` (занят — тихо выходит: воркер уже есть), разбирает очередь
по одной задаче и завершается, когда задач больше нет. Каждую задачу выполняет
как обычный прогон vlt: тот же run_task, стрим событий в лог задачи, vault-лок
от блокирующих запусков уважается (ждёт освобождения).
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from . import queue
from .runner import run_task

POLL_SECONDS = 2.0


def main(argv: list[str]) -> int:
    runs = Path(argv[0]) if argv else queue.runs_dir()
    try:
        queue.acquire_lock("worker")
    except queue.LockBusy:
        return 0  # воркер уже есть — второй не нужен
    try:
        queue.reconcile()
        while True:
            claimed = queue.next_queued()
            if claimed is None:
                return 0
            _run_one(int(claimed["id"]), runs)
    finally:
        queue.release_lock("worker")


def _run_one(task_id: int, runs: Path) -> None:
    item = queue.update_item(
        task_id, status="running", started=queue.now_iso(), pid=os.getpid()
    )
    if item is None:
        return
    log_path = Path(item["log"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    vault_lock = queue.vault_lock_name(item["vault"])
    try:
        with open(log_path, "a", encoding="utf-8") as log:
            # vault может быть занят блокирующим запуском — очередь ждёт,
            # попутно замечая отмену задачи.
            while True:
                try:
                    queue.acquire_lock(vault_lock)
                    break
                except queue.LockBusy:
                    if queue.load_item(task_id)["status"] != "running":
                        log.write("задача отменена в ожидании freed vault\n")
                        return
                    time.sleep(POLL_SECONDS)

            try:
                if queue.load_item(task_id)["status"] != "running":
                    return  # отменили, пока брали лок

                holder: dict = {}
                code = run_task(
                    item["task"],
                    Path(item["vault"]),
                    agent_cmd=item.get("agent_cmd"),
                    timeout=item.get("timeout"),
                    stream=True,
                    status_stream=log,
                    output_stream=log,
                    result_out=holder,
                )
                queue.update_item(
                    task_id,
                    status="done" if code == 0 else "failed",
                    exit_code=code,
                    finished=queue.now_iso(),
                    result=holder.get("text"),
                )
            except BaseException as exc:  # таймаут, отказ запуска, отмена
                queue.update_item(
                    task_id,
                    status="failed",
                    finished=queue.now_iso(),
                    error=str(exc) or type(exc).__name__,
                )
                if isinstance(exc, Exception):
                    traceback.print_exc(file=log)
            finally:
                queue.release_lock(vault_lock)
    except OSError as exc:
        # лог недоступен — редкий, но фатальный случай; фиксируем в элементе
        queue.update_item(task_id, status="failed", finished=queue.now_iso(), error=str(exc))


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except BaseException:
        traceback.print_exc()
        raise
