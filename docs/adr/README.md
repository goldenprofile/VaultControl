# Решения по архитектуре (ADR)

Короткие записи о принятых решениях: контекст, само решение, отвергнутые альтернативы и
последствия. Формат — лёгкий MADR. Принятое решение не редактируется задним числом:
передумали — новый ADR, старый получает статус `Superseded`.

| №    | Решение                                                                                  | Статус   |
|------|------------------------------------------------------------------------------------------|----------|
| 0001 | [vaultctl запускает CLI-агента, а не работает с vault сам](0001-run-cli-agent-instead-of-touching-vault.md) | Accepted |
| 0002 | [Задача уходит агенту аргументом, длинная — в stdin](0002-deliver-long-tasks-through-stdin.md) | Accepted |
| 0003 | [vaultctl сбрасывает очередь ввода консоли вокруг запуска агента](0003-flush-console-input-queue.md) | Accepted |
| 0004 | [npm-шимы `.cmd` запускаются через node, а не через cmd.exe](0004-launch-npm-cmd-shims-through-node.md) | Accepted |
| 0005 | [Детерминизм и guardrails pi-прогонов — расширением vlt-bridge](0005-vlt-bridge-extension-for-deterministic-runs.md) | Accepted |
| 0006 | [Очередь задач и detach — файловое состояние, без демона](0006-detach-and-task-queue.md) | Accepted |
