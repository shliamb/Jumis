# SPEC: scheduler

## Суть работы

Асинхронный планировщик напоминаний, будильников и системных крон-задач:

- следит за ближайшей `pending`-задачей в БД;
- исполняет её в назначенный момент и передаёт агенту или системному воркеру;
- пересчитывает cron-задачи и переводит статусы.

## Входные данные

- `scheduled_tasks`: `id`, `tg_id`, `task_type`, `title`, `agent_instruction`, `scheduled_at` (naive), `cron_expression`, `status`.
- `task_type`: `alarm`, `reminder`, `agent_action`, `system_cron`.
- `db_tasks` (репозиторий), `agent` (`JumisAgent`), `ADMIN_ID`.
- Реестр `system_handlers: {title -> coroutine(task)}` — заполняется модулями через `register_system_task`.
- Сигнал `notify_new_task()` при создании/изменении/удалении задачи.

## Выходные данные

- Обновления в `scheduled_tasks`: `running` → `completed` / `pending` (cron) / `expired` / `failed`.
- Запуск `JumisAgent.process_agent_request(chat_id=recipient_id, prompt_text=...)` с системным триггером.
- Сообщения в чат владельца или стороннему пользователю (через инструменты агента).
- Логи о следующем запуске и статусах.

## Логика (Вариации)

1. `run()`: `cleanup_stale_tasks(12h)` → три задачи: `_agent_worker`, `_cron_worker`, `_main_loop`.
2. `_main_loop`: `get_pending_task()`; нет задач — сон до 1 часа или до `notify_new_task`; есть — расчёт `delay = scheduled_at - now`.
3. `delay <= 0` → `_execute_task`; иначе сон ровно до срока (`_sleep_or_event`).
4. `_execute_task`: разовая задача, просроченная >1 ч, → `expired`; иначе `running`, затем маршрут: `system_cron` → `cron_queue`, остальные → `agent_queue`.
5. `_agent_worker`: строит `prompt_to_agent` по типу (владельцу / внешнему пользователю / автономное действие), вызывает агента, затем cron → `pending` + следующий `scheduled_at`, разовая → `completed`.
6. `_cron_worker`: `execute_system_cron` ищет обработчик в `self.system_handlers` по `title` задачи и вызывает его; cron → следующий запуск + `pending`, разовая → `completed`.
7. `register_system_task(title, handler)` — модуль (например, `cleaner`) регистрирует свой системный крон по названию задачи. Нет обработчика → error в лог, задача не падает.
8. `notify_new_task()` взводит `asyncio.Event` — таймер сна пересчитывается без ожидания.
9. В коде описан будущий режим «Дятла» (repeat/nag) — пока не реализован.

## Обработка ошибок

- Ошибка в `_main_loop` → лог + sleep 5 с, цикл продолжается.
- Не удалось поставить `running` → задача пропускается с warning.
- Исключение в `_agent_worker`/`_cron_worker`: разовая задача → `failed`; **cron-задача → `pending` + следующий `scheduled_at`** (одна ошибка не убивает повторяющуюся задачу), `notify_new_task`, лог.
- Для `system_cron` без зарегистрированного обработчика → error в лог, задача остаётся в `running` до `cleanup_stale_tasks` (при старте вернётся в `pending`/`expired`).
- Неизвестный `task_type` → warning и пропуск (задача остаётся в `running` до `cleanup_stale_tasks`).
- `agent`/`bot` не установлены → лог об ошибке, задача пропускается.
- `stop()` отменяет воркеры и ждёт их завершения.
