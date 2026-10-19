"""Словарь навыков: из свободного текста (заказа или профиля) получаем устойчивые идентификаторы."""

from __future__ import annotations

import re

# id -> (название для показа, список основ слов в нижнем регистре)
TAXONOMY: dict[str, tuple[str, list[str]]] = {
    "telegram": ("Telegram", ["telegram", "телеграм", "тг", "tg", "t.me", "тгк"]),
    "bot": ("Бот", ["бот", "bot", "чат-бот", "чатбот", "ассистент", "консультант"]),
    "llm": (
        "LLM",
        ["llm", "gpt", "chatgpt", "claude", "gemini", "нейросет", "нейро", "ии", "ai", "openai",
         "anthropic", "llama", "yandexgpt", "gigachat", "языков", "генеративн"],
    ),
    "prompt": ("Prompt", ["промпт", "prompt", "промт"]),
    "rag": ("RAG", ["rag", "векторн", "эмбеддинг", "embedding", "база знаний", "базе знаний", "базы знаний", "по документ"]),
    "n8n": ("n8n", ["n8n", "н8н"]),
    "make": ("Make", ["make.com", "make", "integromat", "zapier"]),
    "automation": (
        "Автоматизация",
        ["автоматизац", "автоворонк", "воронк", "webhook", "вебхук", "рутин", "no-code", "nocode", "low-code"],
    ),
    "crm": ("CRM", ["crm", "црм", "amocrm", "амо", "битрикс", "bitrix", "retailcrm", "yclients", "ремонлайн"]),
    "api": ("API", ["api", "апи", "интеграц", "rest"]),
    "python": ("Python", ["python", "питон", "aiogram", "fastapi", "django", "flask", "pandas", "telethon"]),
    "js": ("JavaScript", ["javascript", "typescript", "node.js", "nodejs", "react", "next.js", "vue", "js"]),
    "parsing": ("Парсинг", ["парс", "scrap", "скрейп", "краул"]),
    "image": (
        "Картинки ИИ",
        ["midjourney", "stable diffusion", "dall-e", "dalle", "flux", "нейрокартин", "генерация изображ",
         "генерации изображ", "генерация картин", "генерации картин", "иллюстрац"],
    ),
    "video": ("Видео", ["видео", "ролик", "монтаж", "reels", "рилс", "shorts", "heygen", "runway", "hedra"]),
    "voice": ("Озвучка", ["озвуч", "голос", "tts", "elevenlabs", "синтез речи", "транскриб"]),
    "copy": ("Копирайтинг", ["копирайт", "рерайт", "статей", "статьи", "seo-текст", "тексты", "контент-план"]),
    "design": ("Дизайн", ["дизайн", "figma", "фигма", "логотип", "баннер", "презентац"]),
    "site": ("Сайты", ["сайт", "лендинг", "landing", "tilda", "тильда", "wordpress", "вёрстк", "верстк", "интернет-магазин"]),
    "data": ("Аналитика", ["аналитик", "sql", "дашборд", "bi", "excel", "эксель", "таблиц", "google sheets", "power bi"]),
    "mobile": ("Мобильные", ["мобильн", "ios", "android", "flutter", "swift", "kotlin"]),
    "support": ("Поддержка", ["поддержк", "саппорт", "техподдерж", "faq"]),
}


def _compile(stem: str) -> str:
    esc = re.escape(stem)
    # короткие слова ищем целиком, длинные по началу (чтобы ловить «промпты», «бота»)
    strict = (len(stem) <= 3 and stem != "бот") or stem in {"make", "bot", "api", "rest", "ios", "sql", "crm"}
    tail = r"(?![\w])" if strict else ""
    return rf"(?<![\w]){esc}{tail}"


_PATTERNS: dict[str, re.Pattern[str]] = {
    sid: re.compile("|".join(_compile(k) for k in stems), re.IGNORECASE)
    for sid, (_, stems) in TAXONOMY.items()
}


def skill_ids(text: str) -> list[str]:
    """Идентификаторы навыков, найденные в тексте (в порядке словаря)."""
    if not text:
        return []
    return [sid for sid, rx in _PATTERNS.items() if rx.search(text)]


def label(sid: str) -> str:
    return TAXONOMY[sid][0] if sid in TAXONOMY else sid


def labels(ids: list[str]) -> list[str]:
    return [label(i) for i in ids]


def profile_keys(skills: list[str]) -> set[str]:
    """Ключи профиля: идентификаторы из словаря плюс сами слова, если словарь их не знает."""
    keys: set[str] = set()
    for s in skills:
        ids = skill_ids(s)
        if ids:
            keys.update(ids)
        else:
            keys.add(s.strip().lower())
    return keys


# как называть навык внутри предложения: «работаю с ботами и LLM»
DRAFT_PHRASE = {
    "telegram": "Telegram", "bot": "ботами", "llm": "LLM", "prompt": "промптами", "rag": "RAG", "n8n": "n8n",
    "make": "Make", "automation": "автоматизацией", "crm": "CRM", "api": "API-интеграциями", "python": "Python",
    "js": "JavaScript", "parsing": "парсингом", "image": "генерацией картинок", "video": "видео", "voice": "озвучкой",
    "copy": "текстами", "design": "дизайном", "site": "сайтами", "data": "аналитикой", "mobile": "мобильными приложениями",
    "support": "поддержкой",
}


def order_ids(keys) -> list[str]:
    """Ключи в порядке словаря, затем незнакомые слова по алфавиту."""
    known = [k for k in TAXONOMY if k in keys]
    return known + sorted(k for k in keys if k not in TAXONOMY)


def join_phrase(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " и " + items[-1]
