# SPEC: llm

## Суть работы

Слой работы с LLM через LiteLLM:

- `llm_router.py` — `LLMWorker`: каталог моделей по активным ключам, выбор default/cheap/smart, диалог, стриминг, вызов инструментов, валидация и откат истории. `simple_call`/`_call_request` принимают необязательный `timeout` — персональный лимит на вызов для длинных генераций (по умолчанию общий `LLM_TIMEOUT`).
- `agents.py` — загрузка системного промпта из Markdown (`prompts/jumis_agent.md`), список инструментов `jumis_agent`, человекочитаемые статусы тулзов.
- `prompts/jumis_agent.md` — сам текст промпта: характер, запреты, формат, дисциплина памяти и инструментов. Правится руками, без питона; при отсутствии файла `load_prompt` отдаёт короткую заглушку.
- `functions.py` — реализация 33 инструментов + JSON-схемы `FUNCTIONS` (30 подключены к агенту).
- `token_usege.py` — `DBTokenLogger` (callback LiteLLM) для учёта расхода токенов.

## Входные данные

- `.env.llm`: `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY` (наличие ключа включает провайдера).
- `AGENTS["jumis_agent"]`: `system` (из `prompts/jumis_agent.md`) + `tools` (30 инструментов; не подключены только служебные `get_pending_queue`, `get_categories_facts`, `get_categories_users`).
- `FUNCTIONS`: `description`, `function`, `schema` (JSON Schema) на каждый инструмент.
- Настройки: `HISTORY_LIMIT` (30), `LLM_TIMEOUT` (30 с), `LLM_TEMPERATURE` (0.1, `None` — не передавать), `DEFAULT_FALLBACK_MODEL`.
- Аргументы инструментов приходят от LLM; категории (`facts_category`, `user_category`) подставляются динамически из БД.

## Выходные данные

- `tools` в формате OpenAI: `{"type": "function", "function": {name, description, parameters}}` с заполненными `enum` категорий.
- `refine_stream_tools` — асинхронный генератор `{"type": "text"|"tool"|"error", ...}`; tool-сообщение содержит `{call_id: {name, arguments, id}}`.
- `call_function` — результат инструмента (dict, str или `{"error": ...}`).
- `get_tools(agent)` → `(system, tools)`; `get_agent_info` → конфиг агента.
- `simple_call` / `call_with_tools` — dict `{'type', 'content', ...}`.
- `available_models`, `model_prices`, `current_model` — состояние воркера.

## Логика (Вариации)

1. `refresh_catalog`: из `litellm.model_cost` берутся только `mode == "chat"` и провайдеры с ключами; исключаются прокси-шлюзы (`openrouter/`, `groq/`, …), имена с `:` и `anthropic.`; сохраняются цены и лимиты.
2. `refresh_llm_models`: профиль админа из `users` → `model_default/model_cheap/model_smart`, `current_model = model_default`; при отсутствии профиля — `DEFAULT_FALLBACK_MODEL`.
3. `change_model("default"|"cheap"|"smart"|"provider/model")`: переключает `current_model`, возвращает bool.
4. `get_tools_for_agent`: deepcopy схемы, подстановка enum категорий из `db_memory.fact_categories` / `db_users.users_categories` (фолбэки `fact` / `not_defined`).
4a. `get_tools("jumis_agent")`: к промпту из файла добавляются блоки памяти (`Memory.prompt_blocks`): разделы правил/стилей/паспорта и карта архива. Собираются из БД на каждый запрос, поэтому правка факта видна уже в следующем ответе; сбой памяти даёт пустой блок, промпт не ломается.
5. `call_function`: по сигнатуре функции автоматически внедряет `db_memory`, `db_users`, `db_messages`, `db_tasks`, `scheduler`, `mytelethon`, `jumis_agent`, `queue_new_mess`, `llm`, `cleaner`, `backups`, `rss`, `memory`; поддерживает callable и `{"function": ...}`.
6. `_call_request`: валидация `dialog` → при битом JSON откат на `old_dialog`; system добавляется первым; `backup_dialog` для отката при ошибке; стрим через `stream_wrapper`, успешное завершение сохраняет `old_dialog = deepcopy(dialog)`.
7. `_safe_trim` режет историю до `HISTORY_LIMIT`, начиная с ближайшего `user` (не рвёт tool-пары).
8. `refine_stream_tools` накапливает tool_calls по `index`, отдаёт их одним блоком после конца стрима.
9. `litellm.cache = Cache(type="local")`, `litellm.callbacks = [DBTokenLogger()]`.

10. `think` — служебный scratchpad: принимает `thought`, ничего не меняет, возвращает `"OK"`; в лог пишется только длина, сама мысль не сохраняется и не показывается пользователю (остаётся в истории как `assistant.tool_calls`). Промпт не ограничивает момент и частоту вызова — агент решает сам; пустая мысль → `"Error: 'thought' must not be empty."`. В чате на время вызова показывается статус `"Thinking it over..."`.

11. Температура: `LLM_TEMPERATURE` из `config.py` (0.1) хранится в `self.llm_temperature` и передаётся в **каждый** `acompletion` (stream и не-stream) через `request_kwargs`; `None` отключает параметр для моделей, которые его не принимают. Значение выводится в логе `LLM request`.

12. `ask_options` — финальный шаг ответа: принимает `question` (необязательный, до 300 символов) и `options` (2–6 уникальных непустых строк, каждая ≤ 60 символов). Нормализует строки, при успехе возвращает `{"status": "options_shown", "question": ..., "options": [...]}` — маркер для `JumisAgent`: отрисовать кнопки и завершить итерацию. Невалидный ввод → `{"status": "error", "message": ...}`, цикл при этом не прерывается и модель может исправиться. Сам инструмент ничего не отправляет в Telegram.

13. `update_user`: `is_blocked=true` только помечает пользователя в БД — сообщения продолжают сохраняться в историю, но `ingest` их в виджет не пускает; описание поля в схеме инструмента прямо об этом предупреждает, чтобы агент не обещал «полный игнор».

14. Инструменты обслуживания истории (модуль `cleaner`): `cleanup_scan` — сухой отчёт уборки; `cleanup_run(confirm=true)` — без `confirm` только объясняет, что удаление необратимо; `digest_run(max_batches=3)` — порционная самаризация старых батчей (сырое удаляется после дайджеста); `digest_list(chat_id, limit=5)` — сохранённые дайджесты.
15. Инструменты бэкапа (модуль `pgbackup`): `backup_now` — снять дамп сейчас (безопасно: только чтение базы + файл, старые копии сворачиваются до 3); `backup_list(limit=10)` — список файлов с размером и датой + подсказка по восстановлению. Подтверждения не требуют.

15. Защита системного крона: `del_task` возвращает `[FORBIDDEN]` для `task_type='system_cron'` (строку нельзя удалить, только поставить на паузу); `update_task` для неё разрешает менять только `status` — название, тип, `cron_expression` и инструкция защищены.

16. Новости (`rss`): `rss_digest(hours, fresh)` → сводка + сохранение в `rss_digests`; `rss_archive(days, query, limit, digest_id)` → короткие выжимки или полная по id; `rss_feeds()` → список с состоянием; `rss_feeds_edit(add, remove, note)` → добавление/удаление одного фида. Внедрение сервиса — по имени параметра `rss` (как `cleaner` и `backups`): `LLMWorker.rss` ставится в `run_bot` после создания модуля.
17. Правило в системном промпте: сводку владельцу отдавать как есть — со сносками в скобках и блоком выводов, не пересказывая своими словами и не добавляя фактов, которых в сводке нет.

18. Память (`memory`): `write_fact` перед вставкой считает вектор один раз и ищет живые факты с близостью ≥ 0.93; при совпадении НЕ пишет, а возвращает `NOT SAVED: … #id … update_fact(id=…)` — так память перестаёт плодить копии. Вектор при записи сохраняется сразу, фоновая задача остаётся только фолбэком на случай сбоя эмбеддера.
19. `update_fact` подключён агенту: правка факта пересчитывает его вектор, поэтому попадание в поиск не деградирует. `search_facts` кодирует запрос как `query` (у e5 разные префиксы `query:`/`passage:`), возвращает только живые факты и всегда печатает `#id`, чтобы следующий шаг мог быть `update_fact`.
20. Категории в enum и в промпте совпадают: пять разделов (`rule`, `style_owner`, `style_client`, `style_personal`, `profile`) помечены `in_prompt` и рендерятся в system, остальные — полки карты памяти.

## Обработка ошибок

- Нет ключей: предупреждение, `available_models = []`; `refresh_llm_models` вернёт `False` и `DEFAULT_FALLBACK_MODEL`.
- Таймаут/`JSONDecodeError`/прочие исключения стрима → восстановление `dialog` из backup и текстовый чанк с пометкой ошибки.
- Синхронный вызов при ошибке → `_error_net(...)` (объект-заглушка), диалог откатывается.
- Битый диалог субагента → контекст сбрасывается в `[]`; главного — откат на `old_dialog`.
- `call_function`: неизвестная функция → `{"error": ...}`, исключение внутри → `{"error": "Function '<name>' failed: ..."}`.
- `del_task`/`update_task`: если проверить тип задачи не удалось, операция продолжается (fail-open) с warning в лог.
- `ask_options`: `options` не массив, меньше 2 валидных вариантов или вариант длиннее 60 символов → `{"status": "error", ...}`; агент не останавливает цикл и может вызвать инструмент повторно.
- `get_tools` с неизвестным агентом → `KeyError` с перечнем доступных.
- `simple_call`/`call_with_tools` без `system` → `ValueError`.
- `refresh_llm_models` без `db_users` → `False` + лог.

- `simple_call` без `system` → `ValueError`; сбой модели приходит текстом («Ошибка сети: ...») в `content`, поэтому вызывающий код обязан отличать ошибку от ответа (модуль `rss` делает это через `analyst.looks_like_error`).
- Персональный `timeout` вызова переопределяет `LLM_TIMEOUT` только для этого запроса; сообщение о таймауте в логе показывает фактический лимит.
- Файл промпта не читается → `load_prompt` пишет `logger.error` и возвращает `FALLBACK_SYSTEM`: агент остаётся работоспособным.
- Сбой `Memory.prompt_blocks` → блок памяти не добавляется (лог), остальной промпт и инструменты работают.
- `write_fact` без эмбеддера: факт пишется без вектора и догоняется фоновой задачей; при сбое эмбеддинга — тот же путь.
