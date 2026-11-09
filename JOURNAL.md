# JOURNAL

<!-- Append-only. Формат: [YYYY-MM-DD HH:MM] [Модуль] Краткое изменение. -->

[2026-11-08 23:31] [jumis] Создана документация Atlas: MEMORY.md (обзор, 14 модулей, 6 сценариев, roadmap, rules, style) и 14 SPEC.md по всем модулям; код, конфиги и README не изменялись.
[2026-11-08 23:58] [jumis] Удалены SOCKS5/MTProto прокси, bot_instance.py, pars.py и env-файлы прокси; run_bot создаёт Bot напрямую с защитным циклом сервисов, Telethon использует auto_reconnect + свой цикл; обновлены MEMORY/SPEC.
[2026-11-09 01:11] [jumis/logs] Логирование сведено к одному logger и файлу jumis.log (ротация + консоль, LOG_LEVEL из .env), setup один раз в run_bot; print заменены, ключевые события логируются, /dLogs очищает активный файл без порчи offset; обновлены SPEC/MEMORY.
[2026-11-09 01:16] [jumis/handlers] Проверены /logs и /dLogs на фейковом сообщении: /logs отправляет jumis.log (jumis_utf8.txt), /dLogs очищает активный лог до 0 байт; SPEC обновлён.
[2026-11-09 01:47] [jumis/llm] Добавлен инструмент think (scratchpad): схема, промпт-правило, статус в UI; у агента 18 инструментов, в FUNCTIONS 22; лог только длина мысли; тесты call_function и схемы пройдены.
[2026-11-09 01:48] [jumis/llm] Добавлена LLM_TEMPERATURE=0.1 в config.py; температура централизованно передаётся во все acompletion (stream и sync), None отключает; лог и SPEC/MEMORY обновлены, тесты пройдены.
