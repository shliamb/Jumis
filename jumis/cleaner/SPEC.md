# SPEC: cleaner

## Суть работы

Обслуживание истории переписки. Три части:

- **Слой 1 — `janitor`:** детерминированная уборка (0 токенов) — пустые сообщения, медиа без текста, болтовня («ок», «)», «привет»), точные дубли внутри «чат + день»; затем `VACUUM`.
- **Слой 2 — `digest`:** старая история порциями сжимается моделью в дайджест, сырые сообщения удаляются в той же транзакции.
- **Слой 3 — память (`memory.cleaner`):** ночная склейка дублей фактов и отложение прошедших событий. Реализация в `memory/cleaning.py`, крон общий: `Cleaner.nightly` вызывает её последней, если в `Cleaner.memory` передан модуль памяти.
- **Крон — `task`:** одна системная задача в `scheduled_tasks`, которая живёт в БД, видна агенту, не удаляется и восстанавливается при старте. Механика крона общая с модулем `pgbackup` и живёт в `utils/system_cron.py`.

Рабочие таблицы (`messages`, `users`, `scheduled_tasks`) не меняются: модуль работает с новой таблицей `chat_digests` и делает `UPDATE users.summary`. **DDL живёт в `database/create_tables.py`** — у модуля нет своей схемы; `Cleaner.ensure_schema()` делегирует в `database.create_tables.ensure_schema(db)`.

## Входные данные

- `db` (`Database`: `fetch`/`fetchval`/`execute`/`pool`), `db_users`, `db_tasks`, `llm` (`LLMWorker.simple_call`), `scheduler` (`register_system_task`).
- `messages`: `id`, `chat_id`, `sender_id`, `direction`, `content`, `msg_type`, `created_at`.
- `users`: `tg_id`, `summary`.
- Правила мусора — `cleaner/rules.py` (чистые функции, без БД).

## Выходные данные

- Строки `chat_digests`: `chat_id`, `period_from`, `period_to`, `msg_count`, `summary`, `facts`, `model`.
- Удалённые строки `messages` (мусор, дубли, сжатые батчи) и `VACUUM (ANALYZE)`.
- Обновлённый `users.summary` — роллинг-состояние отношений по каждому чату.
- Одна строка `scheduled_tasks`: `tg_id = NULL`, `task_type = 'system_cron'`, `title = 'Ночная чистка и самаризация БД'`, `cron_expression = '30 3 * * *'`.
- Инструменты агенту: `cleanup_scan`, `cleanup_run`, `digest_run`, `digest_list`; команды владельцу: `/cleanup`, `/cleanup_run`, `/digest_now`, `/digests`.
- Логи: `[Cleaner]`, `[Digest]`.

## Логика (Вариации)

1. `is_junk(content, msg_type)`: мусор = пустой текст, медиа без цифр/ссылок в подписи, сервисная заглушка `[...]`, только пунктуация/эмодзи, стоп-фраза, огрызок ≤2 символов. Любая цифра, ссылка, `@` или почта делают сообщение ценным. `is_searchable` — инверсия.
2. `Janitor.scan()` — сухой отчёт без удаления: `total`, `empty`, `media`, `chatter`, `dups`, `candidates`, `after`.
3. `Janitor.run()`: сначала дубли (не пересекаясь с мусором), потом мусор, чанками по 500; затем `VACUUM (ANALYZE)`; возвращает `deleted_junk`, `deleted_dups`, `total_before/after`, `size_before/after`.
4. Дубли ищутся SQL-окном: `PARTITION BY chat_id, created_at::date, lower(content)`, остаётся первая строка; берутся только тексты 1–40 символов без цифр, ссылок и `@`.
5. `Digester.run_night(max_batches)`: чаты с бэклогом (старше `QUARANTINE_DAYS = 7` и ≥10 сообщений) сортируются по бэклогу; батчи выдаются по кругу, не более `MAX_BATCHES_PER_CHAT = 10` на чат и `BATCHES_PER_NIGHT = 20` на ночь.
6. Батч: самые старые сообщения чата за карантином (`LIMIT MAX_BATCH_SIZE = 70`), обрезка по границе эпизода — пауза >30 минут закрывает кусок, но только после `MIN_BATCH_SIZE = 10`.
7. Один вызов модели на батч (`simple_call`, без tool-loop): на вход старый `users.summary` + сообщения, на выход — `СВОДКА:` / `ФАКТЫ:` / `СОСТОЯНИЕ:`; парсер при отсутствии маркеров кладёт весь текст в сводку.
8. Транзакция: `INSERT chat_digests` → `DELETE messages WHERE id = ANY(ids)` → `UPDATE users.summary`; при ошибке на любом шаге не удаляется ничего.
9. Удаление и есть курсор: дайджест покрывает ровно удалённый батч, отдельного watermark нет.
10. `Cleaner.nightly(task)` (по крону): уборка → самаризация; каждая часть в своём `try`, наружу не бросает; защита от повторного входа (`_nightly_running`).
10a. Шаг памяти (`Cleaner.nightly`): если задан `self.memory`, вызывается `memory.cleaner.run_night()` — до 20 групп дублей и один проход по устаревшим. Своя защита от повторного входа, ошибки ловятся отдельно: падение памяти не отменяет уборку истории и самаризацию.
11. `ensure_system_task()` (делегирует в `utils/system_cron.ensure_system_cron`): нет строки — создаёт; `expired`/`failed` — оживляет в `pending` с пересчётом времени; `cancelled` — не трогает (осознанная пауза); `scheduled_at` уехал в прошлое >12 ч — пересчитывает.
12. Защита в инструментах: `del_task` отказывает для `system_cron` (и по зарегистрированному названию); `update_task` разрешает только `status` (название, тип, cron и инструкцию менять нельзя).

## Обработка ошибок

- Ошибка БД в `scan`/`run` → лог, возврат с ошибкой; агент/админ видит текст исключения.
- `VACUUM` не выполнился → `vacuum: False` в отчёте, уборка не отменяется.
- Модель не ответила, вернула пусто или мусор без сводки → батч не тронут, `failed += 1`, сообщения остаются в истории.
- Ошибка транзакции → откат, ни дайджеста, ни удаления.
- `run_night` уже идёт → `{"skipped": true, "reason": "already_running"}`.
- Бэклог пуст → `{"skipped": true, "reason": "empty_backlog"}`.
- Уборка памяти упала → в отчёте ночного прогона `memory: {error: ...}`, остальные шаги уже выполнены; сам `run_night` памяти возвращает `{"skipped": true, "reason": "already_running"}` при повторном входе.
- Ночной прогон падает внутри — ловится и логируется, планировщик получает управление обратно.
- Крон не создан (нет `db_tasks`) → лог error, остальные сервисы работают.
