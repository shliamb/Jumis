from datetime import datetime
import json

def clean_task(task: dict) -> dict:
    """
    Очищает и приводит задачу к формату, подходящему для БД.
    Все преобразования выполняются молча, без ошибок.
    """
    cleaned = task.copy()

    # 1. params: словарь -> JSON строка; если уже строка, оставляем как есть
    if 'params' in cleaned and cleaned['params'] is not None:
        if isinstance(cleaned['params'], dict):
            cleaned['params'] = json.dumps(cleaned['params'], ensure_ascii=False)
        elif not isinstance(cleaned['params'], str):
            cleaned['params'] = str(cleaned['params'])

    # 2. run_at: строка -> datetime
    if 'run_at' in cleaned and cleaned['run_at'] is not None:
        if isinstance(cleaned['run_at'], str):
            # Пробуем разные форматы
            for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M'):
                try:
                    cleaned['run_at'] = datetime.strptime(cleaned['run_at'], fmt)
                    break
                except ValueError:
                    continue
            # Если не распарсилось, оставляем как есть? Лучше всё же попытаться получить datetime.
            # Для простоты можно оставить None, но модель сама подберёт.
            if not isinstance(cleaned['run_at'], datetime):
                cleaned['run_at'] = None
        elif not isinstance(cleaned['run_at'], datetime):
            cleaned['run_at'] = None

    # 3. schedule_data: если строка -> попробовать JSON
    if 'schedule_data' in cleaned and cleaned['schedule_data']:
        if isinstance(cleaned['schedule_data'], str):
            try:
                cleaned['schedule_data'] = json.loads(cleaned['schedule_data'])
            except:
                pass

    # 4. Приведение числовых полей к int
    for field in ['priority', 'max_retries', 'retry_count']:
        if field in cleaned and cleaned[field] is not None:
            try:
                cleaned[field] = int(cleaned[field])
            except (ValueError, TypeError):
                cleaned[field] = 0

    return cleaned



def clean_worker(data: dict) -> dict:
    """
    Очищает и приводит словарь с данными воркера к формату, подходящему для БД.
    Все преобразования молчаливы, ошибки игнорируются.
    """
    cleaned = data.copy()

    # 1. balance: если число, оставляем; если строка -> float; иначе None
    if 'balance' in cleaned and cleaned['balance'] is not None:
        try:
            cleaned['balance'] = float(cleaned['balance'])
        except (ValueError, TypeError):
            cleaned['balance'] = None

    # # 2. balance_history: если строка -> попробовать JSON; если список/словарь -> оставляем; иначе []
    # if 'balance_history' in cleaned and cleaned['balance_history'] is not None:
    #     if isinstance(cleaned['balance_history'], str):
    #         try:
    #             cleaned['balance_history'] = json.loads(cleaned['balance_history'])
    #         except:
    #             cleaned['balance_history'] = []
    #     elif not isinstance(cleaned['balance_history'], (list, dict)):
    #         cleaned['balance_history'] = []
    # else:
    #     cleaned.pop('balance_history', None)  # не обновляем, если нет

    # 3. confirmed: приводим к bool
    if 'confirmed' in cleaned and cleaned['confirmed'] is not None:
        cleaned['confirmed'] = bool(cleaned['confirmed'])

    # 4. name, location, comment: если не строка -> приводим к строке или None
    for field in ('name', 'location', 'comment'):
        if field in cleaned and cleaned[field] is not None:
            if not isinstance(cleaned[field], str):
                cleaned[field] = str(cleaned[field])

    # 5. telegram_id, crypto_key, created_at — не трогаем (или удаляем, если они есть в data для обновления)
    # Обычно эти поля не обновляются, поэтому лучше их удалить
    for field in ('telegram_id', 'crypto_key', 'created_at'):
        cleaned.pop(field, None)

    # 6. id  — первичный ключ, не обновляем, но если он нужен для идентификации, оставляем как есть
    # (id используется в WHERE, его значение не меняется)
    # Поэтому ничего не делаем с id

    return cleaned


def clean_device(data: dict) -> dict:
    """
    Очищает и приводит словарь с данными устройства к формату, подходящему для БД.
    Удаляет поля, которые нельзя обновлять. Преобразует типы.
    """
    cleaned = data.copy()

    # 1. status: проверка допустимых значений (опционально)
    if 'status' in cleaned and cleaned['status'] is not None:
        valid = {'available', 'busy', 'disabled', 'maintenance'}
        if cleaned['status'] not in valid:
            # Можно установить значение по умолчанию или оставить как есть
            cleaned['status'] = 'disabled'

    # 2. hidden, connected -> bool
    for field in ('hidden', 'connected'):
        if field in cleaned and cleaned[field] is not None:
            cleaned[field] = bool(cleaned[field])

    # 3. location, comment -> str (если не строка)
    for field in ('location', 'comment', 'device_persona'):
        if field in cleaned and cleaned[field] is not None:
            if not isinstance(cleaned[field], str):
                cleaned[field] = str(cleaned[field])

    # 4. Удаляем поля, которые нельзя обновлять (только для чтения)
    readonly = ('sn', 'worker_id', 'model', 'manufacturer', 'version_sdk',
                'version_android', 'build', 'last_seen', 'created_at', 'updated_at')
    for field in readonly:
        cleaned.pop(field, None)

    # 5. id не удаляем, но он не должен быть в cleaned для SET (edit_device_id его извлечёт)
    # Поэтому оставляем как есть, а edit_device_id сам его вытащит.
    return cleaned