# SPEC: stt_sense

## Суть работы

Распознавание речи (ASR) локально на GPU:

- `stt_asr.py` — `FastTranscriber` на `kairos_asr.KairosASR`;
- скачивание голосовых из Telegram Bot API и транскрибация из памяти;
- сериализация доступа к GPU через `asyncio.Lock`.

## Входные данные

- `audio_bytes: bytes` + расширение (по умолчанию `.ogg`).
- `transcribe_telegram_voice(bot, file_id)` — файл голосового в Telegram.
- Устройство: `cuda` (по умолчанию; требуется рабочий GPU).
- Модель Kairos ASR грузится синхронно при импорте пакета, выполняется прогрев.

## Выходные данные

- `transcribe(...) -> str` — полный текст (`result.full_text`).
- `transcribe_telegram_voice(...) -> str | None` — `None`, если файл не скачался.
- Глобальный синглтон `stt` из `__init__.py` (модель загружается один раз).

## Логика (Вариации)

1. `__init__`: `KairosASR(device)` + `_warmup()` — нулевой WAV 0.5 с прогоняется через модель.
2. `transcribe`: ожидание `asyncio.Lock` (очередь к GPU) → `run_in_executor` → `_transcribe_sync`.
3. `_transcribe_sync`: байты пишутся во временный файл, `asr.transcribe(path)`, файл удаляется в `finally`.
4. `download_telegram_file`: `bot.get_file` и `bot.download_file` с 3 попытками и паузой 1.5 с.
5. `transcribe_telegram_voice`: скачивание → `transcribe`.
6. Используется в двух местах: голос владельца боту (`JumisAgent`) и голосовые собеседников (`IngestionWorker`).

## Обработка ошибок

- Ошибка скачивания после 3 попыток → `None`; вызывающий отвечает «попробуй ещё раз» / пишет заглушку.
- Исключение транскрибации в ingest ловится, текст заменяется на `[Ошибка расшифровки голосового]`.
- В обработчике бота ошибка STT не перехвачена локально — уходит в общий `except` агента.
- Временный файл удаляется в `finally`; при сбое `_warmup` приложение не стартует.
