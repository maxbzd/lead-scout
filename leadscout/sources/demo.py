"""Демо-источник: правдоподобные сообщения из «чатов», чтобы увидеть приложение в работе до подключения Telegram."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .. import store
from ..db import Database
from ..models import RawMessage

DEMO_CHATS = [
    ("ai_zakazy", "AI Заказы", 12400, True),
    ("neuro_freelance", "Нейро Фриланс", 8900, True),
    ("frl_bots", "Фриланс · Боты", 41000, True),
    ("prompt_work", "Prompt Work", 3200, True),
    ("n8n_orders", "n8n Заказы", 5600, False),
]

# (chat, id, минут назад, автор, id автора, текст, ответ на id)
SEED: list[tuple[str, int, int, str, int, str, int | None]] = [
    ("ai_zakazy", 1001, 20, "lumiere_admin", 501,
     "Нужен телеграм-бот для салона красоты.\nБот должен отвечать клиентам с помощью GPT, предлагать свободное время и записывать в YClients.\n"
     "Срок: до 7 дней. Бюджет: 45 000 ₽, оплата 50/50.\nПортфолио обязательно. Писать: @lumiere_admin", None),
    ("frl_bots", 2001, 18, "lumiere_admin", 501,
     "СРОЧНО! Нужен телеграм бот для салона красоты. Бот отвечает клиентам через GPT, предлагает время и записывает в YClients. "
     "Срок до 7 дней, бюджет 45000 ₽, оплата 50/50. Нужно портфолио. Писать @lumiere_admin", None),
    ("ai_zakazy", 1002, 17, "alexey_k", 611, "Могу сделать, есть похожий кейс. Пишите в личку", 1001),
    ("ai_zakazy", 1003, 12, "dev_marina", 612, "Беру! Делаю ботов на GPT уже 2 года, примеры пришлю", 1001),
    ("n8n_orders", 3001, 25, "orbita_studio", 502,
     "Требуется разработчик для настройки автоворонки в n8n.\nЗаявки из Telegram-канала попадают в amoCRM, нейросеть отвечает на первичные "
     "вопросы, менеджеру приходит уведомление. Срок 3 дня, 30 000 ₽. ТЗ есть, скину в личку.\n@orbita_studio", None),
    ("prompt_work", 4001, 49, "teplydom_shop", 503,
     "Магазин товаров для дома ищет специалиста по промптам.\nНужно настроить ИИ-ассистента поддержки: тон, ответы на частые вопросы, передача "
     "оператору. Срок 5 дней, бюджет 25 000 ₽. Примеры работ присылайте в личку: @teplydom_shop", None),
    ("prompt_work", 4002, 46, "u_ivanov", 621, "Готов взяться, пришлю примеры", 4001),
    ("prompt_work", 4003, 45, "kate_prompts", 622, "Сделаю за 3 дня, есть опыт с поддержкой", 4001),
    ("prompt_work", 4004, 40, "sergey_ai", 623, "Беру, пишу в личку", 4001),
    ("prompt_work", 4005, 38, "nina_dev", 624, "Мне подходит, отправила сообщение", 4001),
    ("prompt_work", 4006, 33, "pavel_n8n", 625, "Могу сегодня начать, пишите", 4001),
    ("prompt_work", 4007, 30, "artem_gpt", 626, "Готов обсудить, напишу вам", 4001),
    ("ai_zakazy", 1004, 63, "vector_law", 504,
     "Юрфирме нужен чат-бот по внутренней базе документов (около 4000 файлов), RAG.\nСрок 3 недели, бюджет от 80 до 120 тыс. ₽ по результатам "
     "оценки.\nНужна оценка срока и стека в первом сообщении. @vector_law", None),
    ("neuro_freelance", 5001, 102, "school_video", 505,
     "Онлайн-школе нужно 10 коротких обучающих роликов: генерация картинок, ИИ-озвучка, монтаж. Бюджет 18 000 ₽, срок 4 дня. Портфолио: @school_video", None),
    ("frl_bots", 2002, 133, "mkt_parser", 506,
     "Нужен парсер постов из 20 Telegram-каналов в Google Таблицу. Срок 2 дня, бюджет 12 000 ₽. @mkt_parser", None),
    ("neuro_freelance", 5002, 167, "easy_money77", 507,
     "Ищем копирайтеров на нейросетях! Доход до 100 000 ₽ в месяц, без опыта! Для старта внесите залог 5 000 ₽, вернём после первого заказа. Пишите в личку!", None),
    ("frl_bots", 2003, 75, "shop_owner_pro", 508,
     "Нужен чат-бот для интернет-магазина на GPT: отвечает на вопросы про доставку и наличие, передаёт сложные случаи менеджеру. "
     "Бюджет обсудим, кто может — пишите @shop_owner_pro", None),
    ("ai_zakazy", 1005, 90, "global_travel", 509,
     "Need a Telegram bot with OpenAI for appointment booking. Budget $400, 5 days. Contact @global_travel", None),
    ("neuro_freelance", 5003, 55, "support_hub", 510,
     "Ищем специалиста по ИИ для разработки ассистента поддержки в Telegram. Оплата 2500 ₽ в час, около 40 часов. Писать @support_hub", None),
    # шум: предложения услуг, реклама, болтовня
    ("ai_zakazy", 1006, 8, "freelancer_a", 701,
     "Ищу заказы по разработке телеграм-ботов и автоматизации. Делаю на заказ быстро, портфолио в профиле, пишите в личку", None),
    ("frl_bots", 2004, 11, "promo_bot", 702,
     "Подписывайтесь на наш канал! Скидка 50% по промокоду на курс по нейросетям, набор на курс стартует в понедельник", None),
    ("prompt_work", 4008, 14, "chatter_1", 703, "Всем привет! Кто-нибудь пробовал новую версию Claude? Расскажите, как впечатления, интересно мнение", None),
    ("n8n_orders", 3002, 16, "chatter_2", 704, "Подскажите, как в n8n обработать ошибку HTTP-запроса, чтобы сценарий не падал целиком?", None),
    ("neuro_freelance", 5004, 22, "chatter_3", 705, "Спасибо всем за помощь с вопросом по Midjourney, всё получилось!", None),
]

# свежие заказы: появляются по одному при ручной проверке
TICKS: list[tuple[str, int, str, int, str]] = [
    ("ai_zakazy", 1101, "britva_shop", 801,
     "Барбершопу нужен бот-консультант в Telegram.\nОтвечает на вопросы о ценах и мастерах, предлагает время, записывает. Срок 6 дней, бюджет 40 000 ₽. Нужны примеры. @britva_shop"),
    ("n8n_orders", 3101, "pharma_flow", 802,
     "Нужна автоматизация в n8n + GPT: разбирать входящие письма с заявками, раскладывать по типам и заносить в Google Таблицу. Срок 4 дня, оплата 55 000 ₽. Писать @pharma_flow"),
    ("prompt_work", 4101, "agency_zen", 803,
     "Агентству нужен специалист по промптам для генерации описаний товаров (около 300 карточек). Срок неделя, бюджет 35 000 ₽. Контакт @agency_zen"),
    ("frl_bots", 2101, "saas_doc", 804,
     "Нужен RAG-бот по документации для SaaS-сервиса: отвечает клиентам, ссылается на разделы. Срок 14 дней, бюджет 90 000 ₽. Пишите @saas_doc"),
    ("ai_zakazy", 1102, "kurs_online", 805,
     "Нужно настроить Telegram-бота с оплатой и выдачей доступа к закрытому каналу после покупки курса. Срок 5 дней, бюджет 28 000 ₽. @kurs_online"),
]


class DemoSource:
    name = "demo"

    def __init__(self, db: Database):
        self.db = db
        self._tick = 0
        self._pending = 0
        self.state = "ready"
        self.error = None
        self.me = {"name": "Демо", "username": None, "id": 0}
        self.listener = None

    def status(self) -> dict:
        return {"state": "demo", "error": None, "me": self.me}

    async def start(self) -> None:
        self._tick = self.db.get_setting("demo_tick", 0)

    async def stop(self) -> None:
        return None

    def request_tick(self) -> bool:
        if self._tick + self._pending < len(TICKS):
            self._pending += 1
            return True
        return False

    def ensure_chats(self) -> dict[str, int]:
        ids: dict[str, int] = {}
        for username, title, members, enabled in DEMO_CHATS:
            ids[username] = store.add_chat(self.db, title, username=username, members=members, is_demo=True, enabled=enabled)
        return ids

    async def fetch(self) -> list[RawMessage]:
        ids = self.ensure_chats()
        out: list[RawMessage] = []
        now = datetime.now(UTC)
        if not self.db.scalar("SELECT COUNT(*) FROM messages", default=0):
            for chat, mid, ago, user, uid, text, reply in SEED:
                out.append(RawMessage(
                    chat_id=ids[chat], tg_msg_id=mid, text=text, posted_at=now - timedelta(minutes=ago), author_id=uid,
                    author_username=user, author_name=user.replace("_", " ").title(), reply_to=reply,
                ))
            out.sort(key=lambda m: m.posted_at)
        while self._pending > 0 and self._tick < len(TICKS):
            chat, mid, user, uid, text = TICKS[self._tick]
            out.append(RawMessage(
                chat_id=ids[chat], tg_msg_id=mid, text=text, posted_at=now - timedelta(minutes=1), author_id=uid,
                author_username=user, author_name=user.replace("_", " ").title(),
            ))
            self._tick += 1
            self._pending -= 1
            self.db.set_setting("demo_tick", self._tick)
        return out

    async def list_dialogs(self) -> list[dict]:
        return []

    async def add_by_link(self, ref: str) -> int:
        parsed = store.parse_chat_ref(ref)
        if parsed["kind"] == "bad":
            from .telegram import TelegramProblem

            raise TelegramProblem("Не похоже на ссылку на чат. Пример: https://t.me/имя_чата")
        title = parsed.get("username") or "Новый чат"
        return store.add_chat(self.db, title, link=parsed["link"], username=parsed.get("username"), is_demo=True, enabled=True)
