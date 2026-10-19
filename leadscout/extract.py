"""Разбор текста заказа без нейросети: бюджет, срок, контакты, признаки заказа и мошенничества, дубли.

Эти правила работают всегда: и как защита, и как запасной вариант, если ИИ недоступен.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .util import squash

# ---------------------------------------------------------------- бюджет ----

_NUM = r"\d{1,3}(?:[   ]\d{3})+|\d+(?:[.,]\d+)?"
_MULT = r"(?:\s?(?:к|k|тыс\.?|тысяч\w*|т\.\s?р\.?|млн|mln)(?![\w]))?"
_CUR = r"(?:\s?(?:₽|руб\w*|р(?![\w])\.?|rub|\$|usd|usdt|долл\w*|€|eur|евро))?"
_MONEY = re.compile(
    rf"(?P<pre>[$€₽])?\s?(?P<num>{_NUM})(?P<mult>{_MULT})(?P<cur>{_CUR})",
    re.IGNORECASE,
)
_RANGE_SEP = re.compile(r"^\s*(?:-|–|—|…|до|по|\.\.)\s*$", re.IGNORECASE)
_CONTEXT = re.compile(
    r"(бюджет|цена|стоимост|оплат|плач|заплат|гонорар|ставка|вознагражд|готов\s+платить|предлага\w*\s+\d|"
    r"за\s+проект|за\s+работу|фикс|₽|руб|\$|usd|€|eur)",
    re.IGNORECASE,
)
_TIME_AFTER = re.compile(r"^\s*(дн|день|дней|дня|сут|недел|нед\b|мес|час|ч\b|мин|лет|года|год|%|шт|чел|слов|символ|страниц|стр\b)", re.IGNORECASE)


@dataclass
class Budget:
    min: float | None = None
    max: float | None = None
    currency: str = "RUB"
    period: str = "fixed"  # fixed | hour | day | month

    @property
    def known(self) -> bool:
        return self.min is not None or self.max is not None


def _to_number(raw: str) -> float:
    s = raw.replace(" ", "").replace(" ", "").replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):
        s = s.replace(",", "").replace(".", "")
    else:
        s = s.replace(",", ".")
    return float(s)


def _currency(pre: str | None, cur: str | None) -> str | None:
    token = ((pre or "") + " " + (cur or "")).strip().lower()
    if not token:
        return None
    if "$" in token or "usd" in token or "долл" in token or "usdt" in token:
        return "USD"
    if "€" in token or "eur" in token or "евро" in token:
        return "EUR"
    return "RUB"


def _period(window: str) -> str:
    w = window.lower()
    if re.search(r"(в\s+час|/\s?час|за\s+час|/\s?ч\b|per hour|/hr|почасов)", w):
        return "hour"
    if re.search(r"(в\s+месяц|/\s?мес|ежемесяч|в\s+мес\b|per month)", w):
        return "month"
    if re.search(r"(в\s+день|/\s?день|за\s+день|в\s+сутки|посуточ)", w):
        return "day"
    return "fixed"


def parse_budget(text: str) -> Budget:
    t = text.replace("\u00a0", " ")
    cands: list[dict] = []
    for m in _MONEY.finditer(t):
        try:
            value = _to_number(m.group("num"))
        except ValueError:
            continue
        mult = (m.group("mult") or "").strip().lower().rstrip(".")
        cur = _currency(m.group("pre"), m.group("cur"))
        if mult in {"к", "k", "тыс"} or mult.startswith("тысяч") or mult.startswith("т."):
            value *= 1000
        elif mult in {"млн", "mln"}:
            value *= 1_000_000
        after = t[m.end(): m.end() + 14]
        before = t[max(0, m.start() - 30): m.start()]
        strong = bool(cur or mult or _CONTEXT.search(before) or _CONTEXT.search(after[:6]))
        if _TIME_AFTER.match(after) and not (cur or mult):
            strong = False
        if not cur and not mult and 1900 <= value <= 2100:
            strong = False
        if value < 50 and not cur and mult not in {"к", "k"}:
            strong = False
        cands.append({"start": m.start(), "end": m.end(), "value": value, "cur": cur, "mult": bool(mult), "strong": strong})

    def sep_between(a: dict, b: dict) -> bool:
        return bool(_RANGE_SEP.match(t[a["end"]: b["start"]]))

    mentions: list[dict] = []
    for i, c in enumerate(cands):
        if c["strong"]:
            mentions.append(c)
            continue
        # слабое число рядом с сильным через «до» или тире: «от 30 до 50 тыс.», «30-50к»
        nxt = cands[i + 1] if i + 1 < len(cands) else None
        if nxt and nxt["strong"] and sep_between(c, nxt) and not _TIME_AFTER.match(t[c["end"]: c["end"] + 14].lstrip(" -–—до")):
            mentions.append(c)
    if not mentions:
        return Budget()

    first = mentions[0]
    low = high = first["value"]
    cur = first["cur"]
    last_end = first["end"]
    if len(mentions) > 1 and sep_between(first, mentions[1]):
        second = mentions[1]
        low, high = sorted([first["value"], second["value"]])
        cur = cur or second["cur"]
        last_end = second["end"]
        # «30-50к»: множитель относится к обоим числам
        if second["mult"] and not first["mult"] and first["value"] < 1000 <= second["value"]:
            low, high = sorted([first["value"] * 1000, second["value"]])
    window = t[max(0, first["start"] - 40): last_end + 40]
    pre_is_ot = bool(re.search(r"(от|from)\s*$", t[max(0, first["start"] - 6): first["start"]], re.IGNORECASE))
    period = _period(window)
    cur = cur or "RUB"
    if low == high and pre_is_ot:
        return Budget(min=low, max=None, currency=cur, period=period)
    return Budget(min=low, max=high, currency=cur, period=period)


def to_rub(amount: float | None, currency: str, usd_rub: float, eur_rub: float) -> float | None:
    if amount is None:
        return None
    if currency == "USD":
        return amount * usd_rub
    if currency == "EUR":
        return amount * eur_rub
    return amount


# ----------------------------------------------------------------- срок ----

_DEADLINE = re.compile(
    r"(?:срок\w*|за|в\s+течени\w+|до|в\s+теч\.?|займет|займёт|уложить\w*\s+в|успеть\s+за|дедлайн)?\s*"
    r"(?P<n>\d{1,3})\s*(?:-\s*\d{1,3}\s*)?(?P<u>дн\w*|день|суток|сут\w*|недел\w*|нед\b\.?|мес\w*)",
    re.IGNORECASE,
)


def parse_deadline_days(text: str) -> int | None:
    t = text.lower()
    for m in _DEADLINE.finditer(t):
        lead = t[max(0, m.start() - 12): m.start()]
        n = int(m.group("n"))
        u = m.group("u")
        # «2 недели опыта» и «3 мес. работы» это не срок: нужен слово-маркер срока рядом
        marker = bool(re.search(r"(срок|за|течени|до|займ|уложи|успе|дедлайн)", t[m.start(): m.start() + 12] + lead))
        if not marker:
            continue
        if u.startswith(("дн", "день", "сут")):
            return max(1, n)
        if u.startswith("нед"):
            return max(1, n * 7)
        if u.startswith("мес"):
            return max(1, n * 30)
    if re.search(r"(за|на)\s+(?:одну\s+)?недел", t) or "в течение недели" in t:
        return 7
    if re.search(r"(за|на)\s+(?:пару|несколько|2-3|два-три)\s+дн", t):
        return 3
    if re.search(r"(за|в течение)\s+(сутки|день|24\s?ч)", t) or "до завтра" in t:
        return 1
    if "срочно" in t or "asap" in t:
        return 2
    if re.search(r"(за|на)\s+месяц", t):
        return 30
    return None


# ------------------------------------------------------------- контакты ----

_AT = re.compile(r"(?<![\w@./])@([A-Za-z][A-Za-z0-9_]{4,31})\b")
_TME = re.compile(r"(?:https?://)?t\.me/(?!joinchat|\+|c/|addlist|share|proxy|socks)([A-Za-z][A-Za-z0-9_]{4,31})(?:/\d+)?\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")
_WRITE_HINT = re.compile(r"(пиш\w*|связ\w*|лс|личк|контакт|тг|tg|telegram|телеграм|отклик|обращ|напиш)", re.IGNORECASE)


@dataclass
class Contact:
    type: str = "none"  # telegram | email | url | phone | author | none
    value: str = ""
    url: str = ""


def parse_contact(text: str, author_username: str | None = None) -> Contact:
    emails = _EMAIL.findall(text)
    email_spans = [m.span() for m in _EMAIL.finditer(text)]

    def inside_email(pos: int) -> bool:
        return any(a <= pos < b for a, b in email_spans)

    ats = [(m.start(), m.group(1)) for m in _AT.finditer(text) if not inside_email(m.start())]
    tmes = [(m.start(), m.group(1)) for m in _TME.finditer(text)]
    candidates = sorted(ats + tmes)
    best: str | None = None
    for pos, name in candidates:
        ctx = text[max(0, pos - 45): pos + 3]
        if _WRITE_HINT.search(ctx):
            best = name
            break
    if best is None and candidates:
        best = candidates[-1][1] if len(candidates) > 1 and not _WRITE_HINT.search(text) else candidates[0][1]
    if best and best.lower().endswith("bot") and author_username:
        best = None
    if best:
        return Contact("telegram", "@" + best, f"https://t.me/{best}")
    if emails:
        return Contact("email", emails[0], f"mailto:{emails[0]}")
    if author_username:
        u = author_username.lstrip("@")
        return Contact("author", "@" + u, f"https://t.me/{u}")
    phone = _PHONE.search(text)
    if phone:
        return Contact("phone", squash(phone.group(0)), "")
    return Contact()


def find_urls(text: str) -> list[str]:
    return [u.rstrip(".,;)") for u in _URL.findall(text) if "t.me/" not in u][:5]


# ---------------------------------------------------- «это заказ или нет» ----

_POSITIVE = re.compile(
    r"(нужен|нужна|нужно|нужны|ищем|ищу\s+(?:специалист|исполнител|разработчик|человек|того|кого|подрядчик)|требуется|требуются|"
    r"надо\s+(?:сделать|разработ|настро|написа)|необходим\w*|заказ\w*|задач\w*|проект\w*|разработ\w*|сделать|сделайте|"
    r"напис\w*|настро\w*|подключи\w*|создать|реализ\w*|доработ\w*|доработать|автоматизир\w*|внедр\w*|интегрир\w*|подготов\w*|кто\s+(?:может|сможет|возьм)|возьмётесь|возьметесь|"
    r"looking\s+for|need\s+(?:a|an|someone)|hiring|вакансия|подработк|ищу\s+программист|бюджет|оплат\w*|за\s+\d)",
    re.IGNORECASE,
)
_SEEKER = re.compile(
    r"(ищу\s+(?:заказ|проект|клиент|работу|подработ)|возьму\s+(?:заказ|проект)|беру\s+(?:заказ|проект)|принимаю\s+заказ|"
    r"предлагаю\s+(?:услуг|свои)|мои\s+услуг|мо[её]\s+портфолио|открыт\s+для\s+(?:заказ|предложен)|свободен\s+для|"
    r"резюме|готов\s+(?:взяться|выполнить|помочь)|выполню|сделаю\s+(?:быстро|недорого|качественно)|"
    r"делаю\s+(?:на\s+заказ|под\s+ключ)|помогу\s+(?:вам|с)|в\s+поиске\s+(?:заказ|работ|проект))",
    re.IGNORECASE,
)
_ADS = re.compile(
    r"(подписывайтесь|подпишись|промокод|скидк\w*|акци[яи]|розыгрыш|вебинар|набор\s+на\s+курс|курс\s+стартует|"
    r"купить|продам|продаю|бесплатн\w+\s+(?:урок|консультац|мастер)|реклама|партн[её]рск|реферал|инвайт|"
    r"заработок\s+в\s+интернете|канал\s+про)",
    re.IGNORECASE,
)


_HANDLE = re.compile(r"(?<![\w.])@[A-Za-z][\w]{3,31}")


def order_signals(text: str) -> tuple[int, int, int]:
    """(положительные, «ищу заказы», реклама): сколько признаков каждого вида нашли."""
    return (
        len(_POSITIVE.findall(text)),
        len(_SEEKER.findall(text)),
        len(_ADS.findall(text)),
    )


def looks_like_order(text: str) -> bool:
    t = squash(text)
    if len(t) < 40:
        return False
    pos, seeker, ads = order_signals(t)
    if seeker >= 1 and pos < 4:
        return False
    if ads >= 2 and pos < 4:
        return False
    if pos >= 2:
        return True
    # одна просьба («нужен специалист по n8n…») тоже заказ, если рядом есть конкретика: цена, срок или контакт
    return pos == 1 and ads == 0 and (parse_budget(t).known or parse_deadline_days(t) is not None or bool(_HANDLE.search(t)))


# ------------------------------------------------------------ мошенничество --


@dataclass
class Flag:
    code: str
    text: str
    level: str  # high | medium | low

    def as_dict(self) -> dict:
        return {"code": self.code, "text": self.text, "level": self.level}


_SCAM_RULES: list[tuple[str, str, str, re.Pattern[str]]] = [
    ("deposit", "Просят деньги вперёд: залог, взнос или оплату доступа", "high",
     re.compile(r"(залог|депозит|вступительн\w+\s+взнос|страхов\w+\s+взнос|гарантийн\w+\s+(?:взнос|платеж|платёж)|"
                r"внес\w+\s+\d|оплатить\s+(?:доступ|вход|обучение|курс|регистрац)|предоплат\w+\s+от\s+исполнител)", re.IGNORECASE)),
    ("training", "Нужно платить за обучение или курс", "high",
     re.compile(r"(платное\s+обучение|(?:предоплат\w+|оплат\w+|взнос\w*)\s+за\s+обучени\w+|обучение\s+платное|пройд\w+\s+(?:платное\s+)?обучение|купи\w*\s+курс|курс\s+\d[\d\s]*(?:₽|руб)|обучим\s+за)", re.IGNORECASE)),
    ("income", "Обещают нереальный доход без опыта", "high",
     re.compile(r"(доход\s+до\s+\d|доход\s+\d[\d\s]*(?:₽|руб|\$|к|тыс)\w*\s+в\s+(?:день|неделю)|заработ\w+\s+(?:от\s+)?\d[\d\s]*(?:₽|руб|\$|к|тыс)\w*\s+в\s+(?:день|неделю)|"
                r"лёгкие\s+деньги|легкие\s+деньги|пассивный\s+доход|без\s+опыта.{0,40}(?:₽|руб|\$|тыс))", re.IGNORECASE)),
    ("illegal", "Сомнительная или запрещённая задача", "high",
     re.compile(r"(взлом|пробив|доксинг|doxing|кардинг|обнал|дроп(?:ы|ов|ами)?\b|накрутк\w+\s+отзыв|крипт\w*\s+арбитраж|"
                r"казино|букмекер|1xbet|ставк\w+\s+на\s+спорт|фишинг|спам-рассылк)", re.IGNORECASE)),
    ("unpaid", "Работа без оплаты: «за портфолио», «за долю» или «после продаж»", "medium",
     re.compile(r"(бесплатно|безвозмездно|за\s+отзыв|ради\s+портфолио|за\s+опыт|без\s+оплаты|за\s+долю|долю\s+в\s+проекте|"
                r"оплата\s+после\s+(?:запуска|продаж|результат)|за\s+процент\s+от\s+продаж|на\s+энтузиазме)", re.IGNORECASE)),
    ("trial", "Тестовое задание без оплаты", "medium",
     re.compile(r"(тестов\w+\s+(?:задани\w+\s+)?(?:бесплатно|без\s+оплаты|неоплачиваем\w+|не\s+оплачива\w+))", re.IGNORECASE)),
]


def scam_flags(text: str, author_flags: dict | None = None) -> list[Flag]:
    flags: list[Flag] = []
    for code, msg, level, rx in _SCAM_RULES:
        if rx.search(text):
            flags.append(Flag(code, msg, level))
    if author_flags:
        if author_flags.get("scam"):
            flags.append(Flag("tg_scam", "Telegram пометил автора как мошенника", "high"))
        elif author_flags.get("fake"):
            flags.append(Flag("tg_fake", "Telegram пометил аккаунт автора как поддельный", "high"))
        elif author_flags.get("bot"):
            flags.append(Flag("tg_bot", "Пишет бот, а не человек", "medium"))
    return flags


def stop_word_hit(text: str, stop_words: list[str]) -> str | None:
    low = text.lower()
    for w in stop_words:
        w = w.strip().lower()
        if w and re.search(rf"(?<![\w]){re.escape(w)}", low):
            return w
    return None


# ----------------------------------------------------------------- дубли ----

_NOISE = re.compile(r"(https?://\S+|@\w+|[^\w\s]|_)", re.UNICODE)
_DIGIT_GROUP = re.compile(r"(?<=\d)[ \u00a0\u202f](?=\d{3}\b)")


def normalize(text: str) -> list[str]:
    t = _DIGIT_GROUP.sub("", text.lower().replace("ё", "е"))
    t = _NOISE.sub(" ", t)
    return [w for w in t.split() if len(w) >= 3]


def content_hash(text: str) -> str:
    return hashlib.sha1(" ".join(normalize(text)).encode("utf-8")).hexdigest()


def shingles(tokens: list[str], n: int = 3) -> set[str]:
    if len(tokens) < n + 2:
        return set(tokens)
    return {" ".join(tokens[i: i + n]) for i in range(len(tokens) - n + 1)}


def similarity(a: str, b: str) -> float:
    ta, tb = normalize(a), normalize(b)
    sa, sb = shingles(ta), shingles(tb)
    if not sa or not sb:
        return 0.0
    shingle = len(sa & sb) / len(sa | sb)
    wa, wb = set(ta), set(tb)
    words = len(wa & wb) / len(wa | wb) if wa and wb else 0.0
    return max(shingle, words - 0.1)


# ---------------------------------------------------------------- заголовок --


_LEAD = re.compile(
    r"^(?:[\W_]*(?:срочно|ищем|ищу|нужен|нужна|нужно|нужны|требуется|требуются|вакансия|заказ|задача|проект|"
    r"#\w+)[!:,\s\-–—]*)+",
    re.IGNORECASE,
)


def make_title(text: str, limit: int = 78) -> str:
    lines = [ln.strip(" \t*_-–—•·#") for ln in text.strip().splitlines() if ln.strip()]
    first = lines[0] if lines else text.strip()
    first = re.sub(r"https?://\S+", "", first)
    first = re.sub(r"[\U0001F000-\U0001FFFF☀-➿️]+", "", first).strip(" .:;!-–—")
    stripped = _LEAD.sub("", first).strip(" .:;!-–—")
    if len(stripped) >= 12:
        first = stripped
    sentence = re.split(r"(?<=[.!?])\s+", first)[0].rstrip(".!? ")
    if len(sentence) >= 14:
        first = sentence
    if len(first) > limit:
        cut = first[:limit].rsplit(" ", 1)[0]
        first = cut.rstrip(",.;:-–— ") + "…"
    return (first[:1].upper() + first[1:]) if first else "Заказ без названия"


@dataclass
class Parsed:
    """Всё, что удалось достать из сообщения правилами."""

    title: str
    budget: Budget
    deadline_days: int | None
    skills: list[str] = field(default_factory=list)
    contact: Contact = field(default_factory=Contact)
    urls: list[str] = field(default_factory=list)
