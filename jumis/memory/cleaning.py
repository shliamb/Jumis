# jumis/memory/cleaning.py
"""
Ночная уборка памяти: склейка дублей и отложение устаревшего.

Ничего не удаляется. Дубли склеиваются в один канонический факт, а старые
версии получают `memory_status = 'superseded'` и `superseded_by = <новый id>`:
они остаются в базе как история, но не участвуют в поиске и рендере.

Порядок:
  1. Пары похожих живых фактов ищутся детерминированно по векторам
     (`find_similar_pairs`), группы собираются объединением множеств.
  2. Одну группу склеивает модель: она пишет канонический текст и говорит,
     какие id он покрывает. Модель может отказаться (`skip`), если факты
     на самом деле о разном.
  3. Факты с относительными датами («сегодня», «завтра»), которым больше
     STALE_DAYS дней, откладываются как `archived` — без замены.

Порционно, как ночная самаризация у cleaner: не больше MAX_GROUPS_PER_RUN
вызовов модели за прогон.
"""
import json
import re

from logs.set_logger import logger
from vector import embedder

__all__ = ["MemoryCleaner"]

MERGE_MIN_SIMILARITY = 0.93   # порог «это почти одно и то же»
MAX_GROUP_SIZE = 8            # больше — скорее цепочка, чем реальный дубль
MAX_GROUPS_PER_RUN = 20       # потолок вызовов модели за ночь
MIN_TEXT_CHARS = 20
MAX_TEXT_CHARS = 1500
STALE_DAYS = 21               # относительные даты старше этого — в архив
LLM_TIMEOUT = 120

# «Сегодня/завтра» в тексте — признак быстро гниющего факта.
# «сейчас» и «только что» сознательно не берём: они сплошь в устойчивых фактах
# («сейчас 550 ₽», «сейчас работает один») и дают ложные отложения.
RELATIVE_TIME_RE = re.compile(
    r"\b(сегодня|вчера|завтра|послезавтра|на этой неделе|на следующей неделе)\b",
    re.IGNORECASE,
)

# Решает модель: откладывать можно только прошедшее событие, не знание.
STALE_SYSTEM = (
    "You remove clutter from the memory of a personal secretary. Below are facts older than "
    "three weeks whose text contains words like today/yesterday/tomorrow.\n\n"
    "Return the ids of the facts that describe a PAST EVENT and are no longer useful as "
    "knowledge: «client picks it up tomorrow», «reply sent today», «finished the socket yesterday».\n\n"
    "Do NOT archive: rules and agreements, prices and services, biography, information about "
    "people, debts, future plans without a date.\n\n"
    "Answer with strict JSON, no explanations: {\"archive\": [12, 15]}"
)
STALE_BATCH = 20

SYSTEM_PROMPT = (
    "You clean up the memory of a personal secretary. You get several facts that the "
    "system considers similar.\n\n"
    "Decide: is this one fact in different wordings, or are these different facts?\n\n"
    "Rules:\n"
    "- Keep EVERY detail: addresses, numbers, prices, names, dates, conditions. Never invent.\n"
    "- Remove only repetitions and filler words.\n"
    "- If the facts are about different things (different people, different clients, "
    "different events) — do not merge them.\n"
    "- If the facts contradict each other — take the newer and more specific version, "
    "but mention the discrepancy.\n"
    "- Canonical text: 1-3 sentences, same language as the input, no «Факт:», no preamble.\n"
    "- Category: pick one from the list.\n"
    "- covered_ids: ids of the facts the new text fully covers.\n\n"
    "Answer with strict JSON, no explanations:\n"
    '{"action": "merge" | "skip", "content": "text", "category": "name", "covered_ids": [1, 2]}'
)


def _parse_json(raw: str) -> dict | None:
    """Достаёт JSON из ответа модели, даже если он в ```-блоке."""
    if not raw:
        return None
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _looks_like_error(raw: str) -> bool:
    """LLMWorker не бросает исключения — ошибку надо узнать по тексту."""
    if not raw:
        return True
    text = raw.strip().lower()
    return text.startswith(("ошибка", "error", "timed out", "timeout", "exception"))


def build_groups(pairs: list[dict]) -> list[list[int]]:
    """Объединяет пары в связные группы (union-find, без внешних зависимостей)."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for pair in pairs or []:
        left, right = pair.get("left_id"), pair.get("right_id")
        if left is None or right is None:
            continue
        union(int(left), int(right))

    groups: dict[int, list[int]] = {}
    for node in list(parent.keys()):
        groups.setdefault(find(node), []).append(node)
    return [sorted(members) for members in groups.values() if len(members) > 1]


class MemoryCleaner:
    """Склейка дублей памяти и отложение устаревших фактов."""

    def __init__(self, db_memory, llm):
        self.db_memory = db_memory
        self.llm = llm
        self._running = False

    # ------------------------------------------------------------- отчёт
    async def scan(self) -> dict:
        """Сухой отчёт: сколько групп и сколько гнилых фактов, без изменений."""
        pairs = await self.db_memory.find_similar_pairs(MERGE_MIN_SIMILARITY)
        groups = build_groups(pairs)
        stale = await self._stale_facts()
        stats = await self.db_memory.live_stats()
        return {
            "pairs": len(pairs),
            "groups": len(groups),
            "in_groups": sum(len(g) for g in groups),
            "stale": len(stale),
            "live": stats.get("live", 0),
            "archived": stats.get("archived", 0),
        }

    # -------------------------------------------------------------- прогон
    async def run_night(self, max_groups: int = MAX_GROUPS_PER_RUN) -> dict:
        """Одна ночная порция: склейка дублей + отложение устаревшего."""
        if self._running:
            return {"skipped": True, "reason": "already_running"}
        self._running = True

        report = {
            "groups": 0, "merged": 0, "skipped": 0,
            "superseded": 0, "stale": 0, "errors": 0,
        }
        try:
            groups = await self._groups()
            report["groups"] = len(groups)

            for ids in groups[:max_groups]:
                try:
                    result = await self._merge_group(ids)
                except Exception as e:
                    report["errors"] += 1
                    logger.error("[MemoryCleaner] Группа %s упала: %s", ids, e, exc_info=True)
                    continue

                if not result:
                    report["skipped"] += 1
                    continue
                report["merged"] += 1
                report["superseded"] += result

            try:
                report["stale"] = await self._archive_stale()
            except Exception as e:
                report["errors"] += 1
                logger.error("[MemoryCleaner] Отложение устаревшего упало: %s", e, exc_info=True)

            logger.info(
                "[MemoryCleaner] Уборка памяти: групп=%d, склеено=%d, отложено=%d, "
                "устаревших=%d, пропущено=%d, ошибок=%d",
                report["groups"], report["merged"], report["superseded"],
                report["stale"], report["skipped"], report["errors"],
            )
        finally:
            self._running = False

        return report

    # ------------------------------------------------------------- группы
    async def _groups(self) -> list[list[int]]:
        """Группы кандидатов: слишком большие группы делим (это цепочки, не дубли)."""
        pairs = await self.db_memory.find_similar_pairs(MERGE_MIN_SIMILARITY)
        groups = build_groups(pairs)
        result: list[list[int]] = []
        for group in groups:
            if len(group) <= MAX_GROUP_SIZE:
                result.append(group)
                continue
            logger.warning("[MemoryCleaner] Группа из %d фактов — режем по %d", len(group), MAX_GROUP_SIZE)
            for start in range(0, len(group), MAX_GROUP_SIZE):
                chunk = group[start:start + MAX_GROUP_SIZE]
                if len(chunk) > 1:
                    result.append(chunk)
        return result

    async def _facts_by_ids(self, ids: list[int]) -> list[dict]:
        rows = await self.db_memory.get_facts_by_ids(ids)
        return rows

    # -------------------------------------------------------------- склейка
    async def _merge_group(self, ids: list[int]) -> int:
        """Склеивает группу. Возвращает число отложенных фактов (0 — не склеили)."""
        facts = await self._facts_by_ids(ids)
        if len(facts) < 2:
            return 0

        categories = [c.get("name") for c in (self.db_memory.fact_categories or [])]
        lines = [
            f"#{f['id']} [{f.get('category')}] {f.get('content')}"
            for f in facts
        ]
        question = (
            f"Категории: {', '.join(categories)}\n\n"
            "Похожие факты:\n" + "\n".join(lines)
        )

        response = await self.llm.simple_call(
            system=SYSTEM_PROMPT,
            dialog=[],
            question=question,
            timeout=LLM_TIMEOUT,
        )
        raw = response.get("content") if isinstance(response, dict) else str(response or "")
        if _looks_like_error(raw):
            logger.warning("[MemoryCleaner] Модель не ответила по группе %s", ids)
            return 0

        data = _parse_json(raw)
        if not data or data.get("action") != "merge":
            logger.info("[MemoryCleaner] Группа %s не склеена (skip)", ids)
            return 0

        content = " ".join(str(data.get("content") or "").split())
        if not (MIN_TEXT_CHARS <= len(content) <= MAX_TEXT_CHARS):
            logger.warning("[MemoryCleaner] Плохой канонический текст в группе %s: %d симв.", ids, len(content))
            return 0

        category = str(data.get("category") or "").strip()
        prompt_sections = {
            c.get("name") for c in (self.db_memory.fact_categories or [])
            if c.get("in_prompt")
        }
        group_categories = [f.get("category") or "fact" for f in facts]
        # Если в группе есть факт из раздела промпта (правила/стиль/паспорт),
        # канон остаётся в разделе: иначе строка выпадет из промпта в архив.
        sections_in_group = [c for c in group_categories if c in prompt_sections]
        if sections_in_group:
            counts: dict[str, int] = {}
            for key in sections_in_group:
                counts[key] = counts.get(key, 0) + 1
            category = max(counts, key=counts.get)
        elif category not in categories:
            # Категория не из списка — берём самую частую в группе.
            counts = {}
            for key in group_categories:
                counts[key] = counts.get(key, 0) + 1
            category = max(counts, key=counts.get)

        covered = data.get("covered_ids")
        if not isinstance(covered, list):
            covered = []
        covered = [int(x) for x in covered if str(x).isdigit() or isinstance(x, int)]
        group_ids = {int(f["id"]) for f in facts}
        covered = [x for x in covered if x in group_ids]
        if len(covered) < 2:
            logger.info("[MemoryCleaner] Группа %s: модель не назвала покрытые id — пропуск", ids)
            return 0

        embedding = None
        if embedder:
            try:
                embedding = await embedder.get_embedding(content)
            except Exception as e:
                logger.error("[MemoryCleaner] Эмбеддинг канонического факта не получен: %s", e)

        fact_data = {"content": content, "category": category}
        if embedding:
            fact_data["embedding"] = embedding
        new_id = await self.db_memory.add_fact(fact_data)
        if not new_id:
            logger.error("[MemoryCleaner] Не удалось записать канонический факт для %s", ids)
            return 0

        await self.db_memory._refresh_categories()
        await self.db_memory.mark_superseded(covered, new_id, "superseded")
        logger.info(
            "[MemoryCleaner] Склеено %s -> #%s (%d фактов отложено)", covered, new_id, len(covered)
        )
        return len(covered)

    # --------------------------------------------------------- устаревшее
    async def _stale_facts(self) -> list[dict]:
        """Живые факты с относительными датами, которым больше STALE_DAYS дней."""
        rows = await self.db_memory.fetch_relative_dated(STALE_DAYS)
        return [r for r in rows if RELATIVE_TIME_RE.search(r.get("content") or "")]

    async def _archive_stale(self) -> int:
        """Откладывает прошедшие события. Решение «событие или знание» принимает модель."""
        stale = await self._stale_facts()
        if not stale:
            return 0

        to_archive: list[int] = []
        for start in range(0, len(stale), STALE_BATCH):
            batch = stale[start:start + STALE_BATCH]
            lines = [f"#{r['id']} [{r['category']}] {r['content']}" for r in batch]
            response = await self.llm.simple_call(
                system=STALE_SYSTEM,
                dialog=[],
                question="\n".join(lines),
                timeout=LLM_TIMEOUT,
            )
            raw = response.get("content") if isinstance(response, dict) else str(response or "")
            if _looks_like_error(raw):
                logger.warning("[MemoryCleaner] Модель не ответила по устаревшим — пропуск")
                continue
            data = _parse_json(raw)
            if not data:
                continue
            allowed = {int(r["id"]) for r in batch}
            picked = data.get("archive")
            if isinstance(picked, list):
                for value in picked:
                    try:
                        fact_id = int(value)
                    except (TypeError, ValueError):
                        continue
                    if fact_id in allowed:
                        to_archive.append(fact_id)

        if not to_archive:
            return 0

        archived = await self.db_memory.mark_superseded(to_archive, None, "archived")
        logger.info("[MemoryCleaner] Отложено прошедших событий: %d из %d", archived, len(stale))
        return archived
