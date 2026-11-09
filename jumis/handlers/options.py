# jumis/handlers/options.py
import html

from aiogram import F, Router, types

from config import ADMIN_ID
from jumis_agent.jumis_agent import JumisAgent
from logs.set_logger import logger

router = Router()


@router.callback_query(F.data.startswith("opt:"))
async def handle_option_choice(callback: types.CallbackQuery, jumis_agent: JumisAgent):
    """Нажатие на вариант ответа ask_options.

    Выбор становится следующим сообщением пользователя для агента: кнопки убираются,
    выбранный вариант помечается в сообщении, после чего стартует новый запрос.
    """
    if callback.from_user.id != ADMIN_ID:
        logger.error("Option press denied: user=%s", callback.from_user.id)
        await callback.answer("🔐 Вы не имеете доступа!", show_alert=False)
        return

    if jumis_agent.agent_busy:
        await callback.answer("Ещё думаю над прошлым ответом…", show_alert=False)
        return

    data = jumis_agent.consume_option(callback.data[4:])
    if not data:
        await callback.answer("Вариант устарел — просто ответь текстом.", show_alert=False)
        return

    await callback.answer()

    chat_id = data["chat_id"]
    chosen = data["option"]
    logger.info("Option chosen: chat_id=%s", chat_id)

    # Убираем клавиатуру и оставляем видимую отметку выбора в чате
    if callback.message:
        try:
            base = getattr(callback.message, "html_text", None) or callback.message.text or ""
            await callback.message.edit_text(
                f"{base}\n\n▸ <b>{html.escape(chosen)}</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception as e:
            logger.warning("Option message edit failed: %s", e)
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

    await jumis_agent.process_agent_request(chat_id=chat_id, prompt_text=chosen)
