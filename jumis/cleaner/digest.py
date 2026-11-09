# jumis/cleaner/digest.py
"""
Слой 2: порционная самаризация истории.

Берём самый старый «ещё не съеденный» кусок диалога (не моложе карантина),
сжимаем его моделью в дайджест, кладём дайджест в chat_digests и в ТОЙ ЖЕ
транзакции удаляем сырые сообщения.

Удаление и есть курсор: завтра «самый старый кусок» — это уже следующие
сообщения, никакого отдельного watermark хранить не нужно.

Рабочие таблицы не меняются: пишем только в новую chat_digests и
в существующее поле users.summary (UPDATE, не ALTER).
"""
from collections import defaultdict

from logs.set_logger import logger
from utils.common import get_now_datetime, to_app_tz_naive


# Карантин: сообщения моложе этого срока не трогаем вообще.
QUARANTINE_DAYS = 7
# Верхняя граница батча, если тянем до границы эпизода.
MAX_BATCH_SIZE = 70
# Минимум сообщений, чтобы расширение по эпизоду вообще имело смысл.
MIN_BATCH_SIZE = 10
# Разрыв, после которого считаем, что начался новый эпизод.
EPISODE_GAP_MINUTES = 30
# Меньше этого числа сообщений в чате — сжимать нечего.
MIN_CHAT_MESSAGES = 10
# Сколько вызовов модели за одну ночь (1 вызов = 1 батч = ~50 сообщений).
BATCHES_PER_NIGHT = 20
# Сколько батчей максимум отдаём одному чату за ночь.
MAX_BATCHES_PER_CHAT = 10
# Сколько чатов вообще рассматриваем.
MAX_CHATS = 20


SYSTEM_PROMPT = (
    "You are an archivist for a personal assistant. You receive a batch of chat messages "
    "between the owner and one contact. After your answer the raw messages are DELETED, "
    "so your digest becomes the only record of this period.\n\n"
    "Write in Russian. Be terse and factual. No greetings, no meta commentary, no speculation.\n"
    "Keep exact numbers, amounts, dates, addresses, names and phone numbers verbatim.\n\n"
    "Output EXACTLY this structure and nothing else:\n\n"
    "СВОДКА:\n"
    "- 2-5 пунктов: что происходило, кто чего хотел, что решили.\n\n"
    "ФАКТЫ:\n"
    "- дословные обрывки с цифрами, суммами, сроками, адресами. 0-5 пунктов.\n"
    "- Если точных данных нет, напиши: - нет\n\n"
    "СОСТОЯНИЕ:\n"
    "- 1-2 предложения: в каком состоянии отношения и разговор сейчас, "
    "с учётом предыдущего состояния ниже.\n"
)

_MARKERS = {
    "summary": "СВОДКА:",
    "facts": "ФАКТЫ:",
    "state": "СОСТОЯНИЕ:",
}


def _strip_markers(text: str) -> dict:
    """Разбирает ответ модели на три части. При неудаче всё уходит в сводку."""
    result = {"summary": "", "facts": "", "state": ""}
    if not text:
        return result

    current = None
    lines = defaultdict(list)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        upper = line.upper()
        matched = False
        for key, marker in _MARKERS.items():
            if upper.startswith(marker):
                current = key
                rest = line[len(marker):].strip()
                if rest:
                    lines[current].append(rest)
                matched = True
                break
        if matched:
            continue
        if current:
            lines[current].append(raw_line.rstrip())

    for key in result:
        result[key] = "\n".join(lines[key]).strip()

    if not result["summary"]:
        result["summary"] = text.strip()
    return result


def _render_message(row) -> str:
    direction = "ИСХОД" if row["direction"] == "outbound_owner" else "ВХОД"
    stamp = to_app_tz_naive(row["created_at"]).strftime("%d.%m %H:%M")
    text = (row["content"] or "").replace("\n", " ").strip()
    if not text:
        text = f"[{row['msg_type']}]"
    return f"[{stamp}] {direction}: {text}"


class Digester:
    """Сжимает старую историю в дайджесты. Один вызов модели на батч."""

    def __init__(self, db, db_users, llm):
        self.db = db
        self.db_users = db_users
        self.llm = llm
        self._busy = False

    # --------------------------------------------------------------- публичное
    @property
    def busy(self) -> bool:
        return self._busy

    async def run_night(self, max_batches: int | None = None) -> dict:
        """Ночной прогон: до `max_batches` вызовов модели, по чуть-чуть на чат."""
        if self._busy:
            logger.warning("[Digest] Прогон уже идёт — пропуск.")
            return {"skipped": True, "reason": "already_running"}

        if not self.llm:
            logger.error("[Digest] LLM не передан — самаризация невозможна.")
            return {"skipped": True, "reason": "no_llm"}

        self._busy = True
        try:
            budget = int(max_batches or BATCHES_PER_NIGHT)
            chats = await self._backlog_chats()
            if not chats:
                logger.info("[Digest] Перерабатывать нечего: бэклог пуст.")
                return {"skipped": True, "reason": "empty_backlog", "batches": 0}

            done = 0
            failed = 0
            per_chat: dict[int, int] = defaultdict(int)
            messages_digested = 0

            for _round in range(MAX_BATCHES_PER_CHAT):
                progressed = False
                for chat in chats:
                    if budget <= 0:
                        break
                    chat_id = chat["chat_id"]
                    if per_chat[chat_id] >= MAX_BATCHES_PER_CHAT:
                        continue

                    result = await self._digest_one(chat_id)
                    if not result:
                        failed += 1
                        continue

                    budget -= 1
                    done += 1
                    messages_digested += result["msg_count"]
                    per_chat[chat_id] += 1
                    progressed = True

                if not progressed or budget <= 0:
                    break

            report = {
                "batches": done,
                "failed": failed,
                "messages": messages_digested,
                "chats": len(per_chat),
            }
            logger.info(
                "[Digest] Ночной прогон завершён: батчей=%d, сообщений=%d, чатов=%d, ошибок=%d",
                done, messages_digested, len(per_chat), failed,
            )
            return report

        except Exception as e:
            logger.error("[Digest] Критическая ошибка ночного прогона: %s", e, exc_info=True)
            return {"error": str(e)}
        finally:
            self._busy = False

    async def list_digests(self, chat_id: int | None = None, limit: int = 5) -> list[dict]:
        """Последние дайджесты (для показа владельцу и агенту)."""
        if chat_id:
            rows = await self.db.fetch(
                """
                SELECT id, chat_id, period_from, period_to, msg_count, summary, facts, created_at
                FROM chat_digests WHERE chat_id = $1 ORDER BY period_to DESC LIMIT $2
                """,
                chat_id, int(limit),
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT id, chat_id, period_from, period_to, msg_count, summary, facts, created_at
                FROM chat_digests ORDER BY id DESC LIMIT $1
                """,
                int(limit),
            )
        return [dict(r) for r in rows] if rows else []

    # ---------------------------------------------------------------- внутреннее
    async def _backlog_chats(self) -> list[dict]:
        """Чаты, где есть что сжимать: старше карантина и не меньше MIN_CHAT_MESSAGES."""
        rows = await self.db.fetch(
            f"""
            SELECT chat_id, count(*) AS backlog
            FROM messages
            WHERE created_at < now() - interval '{QUARANTINE_DAYS} days'
            GROUP BY chat_id
            HAVING count(*) >= $1
            ORDER BY backlog DESC
            LIMIT $2
            """,
            MIN_CHAT_MESSAGES, MAX_CHATS,
        )
        return [dict(r) for r in rows] if rows else []

    async def _next_batch(self, chat_id: int) -> list[dict]:
        """Самые старые сообщения чата за карантином, порезанные по границе эпизода."""
        rows = await self.db.fetch(
            f"""
            SELECT id, sender_id, direction, content, msg_type, created_at
            FROM messages
            WHERE chat_id = $1
              AND created_at < now() - interval '{QUARANTINE_DAYS} days'
            ORDER BY created_at ASC, id ASC
            LIMIT $2
            """,
            chat_id, MAX_BATCH_SIZE,
        )
        if not rows:
            return []

        batch = [dict(rows[0])]
        for prev, row in zip(rows, rows[1:]):
            gap = (row["created_at"] - prev["created_at"]).total_seconds() / 60.0
            # Граница эпизода: пауза больше получаса закрывает кусок,
            # но только если уже набрали минимум (иначе рвём разговор на старте).
            if gap > EPISODE_GAP_MINUTES and len(batch) >= MIN_BATCH_SIZE:
                break
            batch.append(dict(row))
            if len(batch) >= MAX_BATCH_SIZE:
                break

        return batch

    async def _digest_one(self, chat_id: int) -> dict | None:
        """Один батч: модель -> транзакция (дайджест + удаление)."""
        batch = await self._next_batch(chat_id)
        if not batch:
            return None

        ids = [row["id"] for row in batch]
        period_from = batch[0]["created_at"]
        period_to = batch[-1]["created_at"]

        try:
            raw = await self._ask_model(chat_id, batch)
        except Exception as e:
            logger.error("[Digest] Модель не ответила для chat_id=%s: %s", chat_id, e, exc_info=True)
            return None

        if not raw or not raw.strip():
            logger.warning("[Digest] Пустой ответ модели для chat_id=%s — батч не тронут.", chat_id)
            return None

        parts = _strip_markers(raw)
        if not parts["summary"]:
            logger.warning("[Digest] Не удалось разобрать ответ для chat_id=%s — батч не тронут.", chat_id)
            return None

        try:
            await self._commit(
                chat_id=chat_id,
                ids=ids,
                period_from=period_from,
                period_to=period_to,
                msg_count=len(batch),
                summary=parts["summary"],
                facts=parts["facts"] or None,
                state=parts["state"] or None,
            )
        except Exception as e:
            logger.error("[Digest] Транзакция не прошла для chat_id=%s: %s", chat_id, e, exc_info=True)
            return None

        logger.info(
            "[Digest] chat_id=%s: %d сообщений -> дайджест (%d симв.), сырое удалено",
            chat_id, len(batch), len(parts["summary"]),
        )
        return {"chat_id": chat_id, "msg_count": len(batch)}

    async def _ask_model(self, chat_id: int, batch: list[dict]) -> str:
        """Формирует запрос и зовёт модель напрямую (без tool-loop агента)."""
        old_state = ""
        try:
            users = await self.db_users.search_users(tg_id=chat_id)
            if users:
                old_state = (users[0].get("summary") or "").strip()
        except Exception as e:
            logger.warning("[Digest] Не удалось прочитать старый summary для %s: %s", chat_id, e)

        header = (
            f"КОНТАКТ: tg_id={chat_id}\n"
            f"ПЕРИОД: {to_app_tz_naive(batch[0]['created_at']):%d.%m.%Y} — "
            f"{to_app_tz_naive(batch[-1]['created_at']):%d.%m.%Y}\n"
            f"СООБЩЕНИЙ: {len(batch)}\n\n"
            f"ПРЕДЫДУЩЕЕ СОСТОЯНИЕ (слить с новым):\n{old_state or 'нет'}\n\n"
            f"СООБЩЕНИЯ:\n"
        )
        body = "\n".join(_render_message(row) for row in batch)

        response = await self.llm.simple_call(system=SYSTEM_PROMPT, question=header + body)
        if isinstance(response, dict):
            return response.get("content") or ""
        return str(response or "")

    async def _commit(
        self,
        chat_id: int,
        ids: list[int],
        period_from,
        period_to,
        msg_count: int,
        summary: str,
        facts: str | None,
        state: str | None,
    ) -> None:
        """Атомарно: дайджест записан и сырые сообщения удалены."""
        if self.db.pool is None:
            await self.db.connect()
        model_name = str(getattr(self.llm, "current_model", "") or "")[:100] or None

        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO chat_digests
                        (chat_id, period_from, period_to, msg_count, summary, facts, model)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    chat_id, period_from, period_to, msg_count, summary, facts, model_name,
                )
                await conn.execute(
                    "DELETE FROM messages WHERE id = ANY($1::bigint[])", ids
                )
                if state:
                    await conn.execute(
                        "UPDATE users SET summary = $1, updated_at = NOW() WHERE tg_id = $2",
                        state, chat_id,
                    )
