# jumis/memory/render.py
"""
Рендер памяти в системный промпт.

Чистые функции: ни БД, ни модели, ни сети — только форматирование.
Отсюда их легко проверять тестами и менять вид, не трогая логику.

Идея: разделы (правила, стили, паспорт) идут в промпт целиком, но с бюджетом;
архив — только картой полок; подхват по запросу — отдельным коротким блоком.
У каждого факта в скобках стоит его id: по нему агент правит факт, не ища заново.
"""

# Сколько символов отдаём на раздел. Паспорт — самый важный и самый длинный.
SECTION_LIMITS = {
    "rule": 2400,
    "profile": 6000,   # паспорт важнее всего: адрес, график, цены, семья, долги
    "style_owner": 1000,
    "style_client": 1000,
    "style_personal": 1000,
}
DEFAULT_SECTION_LIMIT = 1200

# Общий потолок на все разделы и карту: промпт не должен расти бесконечно.
TOTAL_LIMIT = 13000
MAP_LIMIT = 1600
RECALL_LIMIT = 1000


def clip(text: str, limit: int) -> str:
    """Обрезает строку по границе слова и ставит многоточие."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—")
    return f"{cut}…"


def render_sections(sections: list[dict], total_limit: int = TOTAL_LIMIT) -> str:
    """Разделы промпта: `## TITLE` + список фактов `- [#id] текст`.

    Ожидает список словарей вида:
        {"name": "profile", "title": "ВЛАДЕЛЕЦ", "facts": [{"id", "content"}, ...]}
    Пустые разделы не рендерятся: заголовок без содержания только сбивает модель.
    """
    blocks: list[str] = []
    used = 0

    for section in sections or []:
        facts = section.get("facts") or []
        if not facts:
            continue

        title = (section.get("title") or section.get("name") or "").strip()
        limit = SECTION_LIMITS.get(section.get("name"), DEFAULT_SECTION_LIMIT)

        lines: list[str] = []
        spent = 0
        skipped = 0
        for fact in facts:
            text = clip(fact.get("content"), 400)
            if not text:
                continue
            line = f"- [#{fact.get('id')}] {text}"
            if spent + len(line) > limit or used + len(line) > total_limit:
                skipped += 1
                continue
            lines.append(line)
            spent += len(line)
            used += len(line)

        if not lines:
            continue

        block = f"## {title}\n" + "\n".join(lines)
        if skipped:
            block += f"\n- …{skipped} more facts on this shelf, fetch them with `search_facts`"
        blocks.append(block)

    return "\n\n".join(blocks)


def render_map(rows: list[dict], limit: int = MAP_LIMIT) -> str:
    """Карта памяти: полки архива, а не их содержание."""
    lines: list[str] = []
    used = 0

    for row in rows or []:
        count = int(row.get("facts") or 0)
        if count <= 0:
            continue
        name = (row.get("name") or "").strip()
        description = clip(row.get("description") or "", 120)
        line = f"- **{name}** — {count} фактов" + (f": {description}" if description else "")
        if used + len(line) > limit:
            break
        lines.append(line)
        used += len(line)

    if not lines:
        return ""

    return (
        "## MEMORY MAP\n"
        "Archive table of contents: what exists at all. Not the content — "
        "go to `search_facts` for details.\n"
        + "\n".join(lines)
    )


def render_recall(facts: list[dict], limit: int = RECALL_LIMIT) -> str:
    """Подхват по запросу: несколько фактов, возможно, относящихся к делу."""
    lines: list[str] = []
    used = 0

    for fact in facts or []:
        text = clip(fact.get("content"), 300)
        if not text:
            continue
        category = (fact.get("category") or "").strip()
        prefix = f"[{category}] " if category else ""
        line = f"- [#{fact.get('id')}] {prefix}{text}"
        if used + len(line) > limit:
            break
        lines.append(line)
        used += len(line)

    if not lines:
        return ""

    return (
        "## POSSIBLY RELEVANT TO THIS REQUEST\n"
        "A search guess, not truth: verify it and do not state it as fact unless it holds.\n"
        + "\n".join(lines)
    )
