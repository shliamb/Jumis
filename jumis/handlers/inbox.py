# jumis/handlers/inbox.py
"""Кнопка «Очистить» под виджетом входящих.

Нажатие чистит очередь неотвеченных и удаляет виджет БЕЗ запуска агента:
0 токенов и один запрос к Telegram. Агента запускать не нужно вообще —
`check_notif_mess` в следующий раз просто нечего будет инжектить в диалог.
"""

from aiogram import Router, types, F
from aiogram.exceptions import TelegramBadRequest
from config import ADMIN_ID
from logs.set_logger import logger
from jumis_agent.jumis_agent import JumisAgent

router = Router()


# Тост-подтверждение после удаления виджета (внутренний язык владельца)
TOASTS = {
    "ru": "🧹 Очищено",
    "en": "🧹 Cleared",
}


@router.callback_query(F.data == "inbox:clear")
async def clear_inbox_callback(callback: types.CallbackQuery, jumis_agent: JumisAgent):
    """Очистка очереди входящих по кнопке (только владелец)."""

    user_id = callback.from_user.id

    # 1. Права: кнопка живёт только в чате владельца, но проверяем явно
    if user_id != ADMIN_ID:
        logger.error("[inbox] Кнопку очистки нажал посторонний: %s", user_id)
        await callback.answer("🔐 Нет доступа", show_alert=True)
        return

    # 2. Тост сразу — спиннер на кнопке не должен висеть
    await callback.answer(TOASTS[jumis_agent.admin_lang])

    # 3. Очистка: pending_peers + удаление виджета (та же логика, что у инструмента clear_inbox_notifs)
    peers = len(jumis_agent.pending_peers)
    await jumis_agent.clear_pending_queue()
    logger.info("[inbox] Очистка по кнопке: сброшено диалогов=%d", peers)

    # 4. Страховка: сносим именно то сообщение, на котором нажали.
    #    Нужна для «устаревшего» виджета (после рестарта notif_msg_id = None)
    #    и когда сообщение уже удалено внутри clear_pending_queue.
    if isinstance(callback.message, types.Message):
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass  # уже удалено — это норма
        except Exception as e:
            logger.warning("[inbox] Не удалось удалить виджет: %s", e)
