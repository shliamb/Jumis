# jumis/memory/__init__.py
"""
Память как три слоя, а не одна куча фактов.

1. Разделы (правила, стили, паспорт) — живут в `memories`, но помечены
   в `facts_categories` как `in_prompt` и рендерятся в системный промпт
   на КАЖДЫЙ запрос. Поэтому правка факта видна уже в следующем ответе.
2. Карта памяти — генерируемое оглавление архива: полки и число фактов.
3. Архив — обычный векторный поиск (`search_facts`), плюс «подхват»:
   несколько фактов, возможно относящихся к входящему сообщению,
   подмешиваются в промпт без вызова инструментов.

Дубли не удаляются, а помечаются (`memory_status = 'superseded'`):
история остаётся, а поиск и рендер видят только живое.

Всё, что здесь делается, не должно ломать агента: любая ошибка памяти
превращается в пустой блок и запись в лог.
"""
from logs.set_logger import logger
from vector import embedder
from memory import render
from memory.cleaning import MemoryCleaner

__all__ = ["Memory"]

# Порог «это уже есть в памяти» для защиты от дублей при записи.
DUP_SIMILARITY = 0.93
DUP_LIMIT = 3

# Подхват по входящему сообщению: выше порога — факт в промпт.
# 0.85 откалиброван на живой базе: реальные попадания 0.85–0.88,
# шум — 0.78–0.84. Ниже порога в промпт лезет случайная выборка.
RECALL_TOP = 3
RECALL_MIN_SIMILARITY = 0.85
MIN_FACT_CHARS = 20


class Memory:
    """Фасад памяти: рендер блоков, подхват, поиск похожих, сводка."""

    def __init__(self, db_memory, llm=None):
        self.db_memory = db_memory
        self.llm = llm
        # Уборка дублей: склейка через модель, отложение устаревшего
        self.cleaner = MemoryCleaner(db_memory, llm) if llm else None

    # ------------------------------------------------------------- промпт
    async def prompt_blocks(self) -> str:
        """Разделы + карта памяти: то, что должно быть в system всегда."""
        try:
            sections = []
            for category in await self.db_memory.get_prompt_sections():
                name = category.get("name")
                facts = await self.db_memory.get_section_facts(name)
                sections.append({
                    "name": name,
                    "title": category.get("prompt_title") or name,
                    "facts": facts,
                })

            blocks = [render.render_sections(sections)]
            blocks.append(render.render_map(await self.db_memory.get_map_rows()))

            text = "\n\n".join(block for block in blocks if block)
            if text:
                logger.debug("[Memory] Блоки промпта: %d символов", len(text))
            return text
        except Exception as e:
            logger.error("[Memory] Не удалось собрать блоки промпта: %s", e, exc_info=True)
            return ""

    # ------------------------------------------------------------ подхват
    async def recall(self, text: str) -> str:
        """Несколько фактов архива, возможно относящихся к тексту запроса.

        Разделы исключены: они и так в промпте. Системные триггеры не трогаем —
        это не вопросы владельца, подхватывать там нечего.
        """
        clean = (text or "").strip()
        if len(clean) < 3 or clean.startswith("[SYSTEM TRIGGER"):
            return ""

        try:
            vector = await embedder.get_embedding(clean, is_query=True)
            facts = await self.db_memory.search_vectors(
                embedding=vector,
                limit=RECALL_TOP,
                min_similarity=RECALL_MIN_SIMILARITY,
                include_sections=False,
            )
            facts = [f for f in facts if len((f.get("content") or "").strip()) >= MIN_FACT_CHARS]
            return render.render_recall(facts)
        except Exception as e:
            logger.error("[Memory] Подхват не сработал: %s", e, exc_info=True)
            return ""

    # ------------------------------------------------------------- поиск
    async def similar(
        self,
        content: str | None = None,
        embedding: list | None = None,
        min_similarity: float = DUP_SIMILARITY,
        limit: int = DUP_LIMIT,
    ) -> list[dict]:
        """Похожие живые факты. Вектор можно передать готовым, чтобы не считать дважды."""
        try:
            if embedding is None:
                if not content:
                    return []
                embedding = await embedder.get_embedding(content)
            return await self.db_memory.search_vectors(
                embedding=embedding,
                limit=limit,
                min_similarity=min_similarity,
            )
        except Exception as e:
            logger.error("[Memory] Поиск похожих не сработал: %s", e, exc_info=True)
            return []

    # ------------------------------------------------------------ сводка
    async def stats(self) -> dict:
        """Живые и отложенные факты — для отчётов и админки."""
        try:
            stats = await self.db_memory.live_stats()
            stats["categories"] = len(self.db_memory.fact_categories or [])
            return stats
        except Exception as e:
            logger.error("[Memory] Сводка не собрана: %s", e)
            return {"live": 0, "archived": 0, "total": 0, "categories": 0}
