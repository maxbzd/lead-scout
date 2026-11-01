"""«Заказ это или нет» решает ИИ: читает сообщения пачками и учится на ваших примерах.

Слова и правила остаются только страховкой, когда ИИ недоступен. Примеры берутся из ваших действий:
«Это заказ» и 👍 (настоящие заказы), «Это не заказ» (ошибки бота). Они подставляются в подсказку для ИИ.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .db import Database
from .extract import similarity
from .profile import Profile
from .util import clip, squash

MAX_BATCHES = 200  # потолок за одну проверку: бережёт и бесплатный лимит, и деньги за Claude
BATCH = 15  # сколько сообщений читает один запрос: меньше обращений к ИИ, а бесплатные лимиты считают именно их
MSG_CHARS = 600
EXAMPLES_KEY = "classify_examples"
MAX_EXAMPLES = 80
SHOW_EACH = 6  # сколько примеров каждого вида показываем ИИ

KINDS = ("order", "offer", "vacancy", "ad", "chat", "rules")

CLASSIFY_SYSTEM = """Ты помощник фрилансера. Из Telegram-чатов приходят сообщения, и тебе нужно понять про каждое: это ЗАКАЗ или нет.

Типы (kind):
- order: человек или компания ищет исполнителя на конкретную задачу или проект с оплатой (разовая работа, проект, подработка). Часто с тегом #ищу, словами «нужен», «требуется», «кто сможет», «подскажите, кто делает…», бюджетом или сроком. Заказ бывает коротким и без цены.
- offer: исполнитель рассказывает о себе, предлагает свои услуги, ищет заказы или клиентов, выкладывает прайс, портфолио, «я делаю…», «меня зовут…, я специалист», тег #помогу. Это НЕ заказ, даже если текст длинный и в нём перечислены проекты, настройки, интеграции и боты: тут исполнитель продаёт себя, а не ищет исполнителя.
- vacancy: постоянная работа в штат, а не разовый заказ.
- ad: реклама, курсы, вебинары, продажа, розыгрыши, приглашения.
- chat: обсуждение, вопрос про что угодно, благодарность, болтовня, новости.
- rules: правила чата, закреплённые сообщения, сообщения от администрации.

Смотри на смысл: кто кого ищет. Слова «настройка», «проект», «бот», «CRM» есть и в заказах, и в предложениях услуг, они ничего не решают.
Если правда сомневаешься, заказ это или нет, ставь order и unsure=true: пропущенный заказ хуже лишнего. Но не ставь order, когда ясно, что исполнитель предлагает себя.

Ответь ТОЛЬКО JSON-объектом без пояснений: {"results":[{"i":1,"kind":"order","unsure":false}, ...]}
Для каждого сообщения ровно один элемент с его номером i."""

CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "unsure": {"type": "boolean"},
                },
                "required": ["i", "kind", "unsure"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


@dataclass
class Verdict:
    kind: str
    unsure: bool = False

    @property
    def order(self) -> bool:
        return self.kind == "order"


# ---------------------------------------------------------------- примеры ----
def _hash(text: str) -> str:
    return hashlib.sha1(squash(text).lower().encode("utf-8")).hexdigest()[:12]


def teach(db: Database, text: str, is_order: bool) -> None:
    """Запоминаем ваше решение как пример. Свежий пример вытесняет старый такой же и более ранние."""
    text = squash(text or "")
    if len(text) < 20:
        return
    items = db.get_setting(EXAMPLES_KEY, []) or []
    h = _hash(text)
    items = [i for i in items if i.get("h") != h]
    items.append({"h": h, "t": clip(text, 320), "y": bool(is_order)})
    db.set_setting(EXAMPLES_KEY, items[-MAX_EXAMPLES:])


def examples(db: Database, texts: list[str] | None = None) -> tuple[list[str], list[str]]:
    """(настоящие заказы, не заказы). Для читаемой пачки берём самые похожие на неё примеры, иначе самые свежие:
    так ИИ видит ваше решение именно про такой тип сообщений (например, шаблонные объявления исполнителей одного чата)."""
    items = db.get_setting(EXAMPLES_KEY, []) or []

    def pick(want: bool) -> list[str]:
        pool = [i["t"] for i in items if bool(i.get("y")) is want]
        if not texts or len(pool) <= SHOW_EACH:
            return pool[-SHOW_EACH:]
        ranked = sorted(range(len(pool)), key=lambda n: (max(similarity(pool[n], t) for t in texts), n), reverse=True)
        return [pool[n] for n in sorted(ranked[:SHOW_EACH])]  # порядок показа: от старых к новым

    return pick(True), pick(False)


# ------------------------------------------------------------ подсказка ----
def build_prompt(texts: list[str], profile: Profile, yes: list[str], no: list[str]) -> tuple[str, str]:
    system = CLASSIFY_SYSTEM
    system += f"\n\nФрилансер, для которого ты отбираешь заказы: навыки: {', '.join(profile.skills) or 'не указаны'}."
    if profile.bio:
        system += f" О себе: {clip(profile.bio, 300)}"
    system += "\nЗаказ не обязан совпадать с навыками: это решает другая оценка. Здесь важно только «заказ или не заказ»."
    if yes:
        system += "\n\nПримеры, которые фрилансер подтвердил как НАСТОЯЩИЕ ЗАКАЗЫ:\n" + "\n".join(f"- {t}" for t in yes)
    if no:
        system += "\n\nПримеры, которые фрилансер отметил как НЕ ЗАКАЗЫ (бот ошибся):\n" + "\n".join(f"- {t}" for t in no)
    user = "Сообщения:\n" + "\n".join(f"[{n}] {clip(squash(t), MSG_CHARS)}" for n, t in enumerate(texts, 1))
    return system, user


def parse_verdicts(data: dict, count: int) -> list[Verdict | None]:
    """Ответ ИИ в список по порядку сообщений. Чего ИИ не назвал или назвал странно, то None: решат правила."""
    out: list[Verdict | None] = [None] * count
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            n = int(it.get("i"))
        except (TypeError, ValueError):
            continue
        kind = str(it.get("kind", "")).strip().lower()
        if 1 <= n <= count and kind in KINDS:
            out[n - 1] = Verdict(kind, bool(it.get("unsure")))
    return out
