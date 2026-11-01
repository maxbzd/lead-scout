"""ИИ решает «заказ или нет» пачками и учится на ваших примерах; правила только страховка."""

import asyncio

import pytest

from leadscout import classify, store
from leadscout.classify import Verdict
from leadscout.llm.base import LLMUnavailable
from leadscout.llm.claude import FallbackLLM
from leadscout.llm.openai_compat import OpenAICompatLLM
from leadscout.llm.rules import RuleLLM
from leadscout.pipeline import Pipeline
from leadscout.profile import Profile

from .conftest import H, make_raw

ORDER = "Нужен специалист, чтобы настроить бота в Telegram для записи клиентов. Бюджет 40 000 ₽, срок 5 дней. @cust_one"
QUIET = "Коллеги, подскажите пожалуйста, как лучше организовать работу в команде из пяти человек на удалёнке?"


class FakeAI(RuleLLM):
    """ИИ-двойник: решает по подстроке, считает вызовы и запоминает, что ему показали."""

    name = "ai"
    deferred = False

    def __init__(self, fail=False, unsure=()):
        self.calls = []
        self.fail = fail
        self.unsure = unsure

    async def classify(self, texts, profile, yes, no):
        self.calls.append({"texts": list(texts), "yes": yes, "no": no})
        if self.fail:
            raise LLMUnavailable("лимит")
        return [Verdict("order", any(u in t for u in self.unsure)) if "Нужен" in t or "ищу исполнителя" in t.lower() else Verdict("chat") for t in texts]


@pytest.fixture()
def ai_pipe(db, settings):
    p = Pipeline(db, FakeAI(), settings)
    p.ai_on = lambda: True
    return p


def run(coro):
    return asyncio.run(coro)


async def ingest(p, chat_id, texts):
    raws = [make_raw(chat_id, t, author=f"u{i}", author_id=10 + i) for i, t in enumerate(texts)]
    await p.prejudge(raws)
    return [await p.process(r) for r in raws]


def test_ai_decides_and_chatter_never_reaches_the_feed(ai_pipe, chat_id, db):
    ids = run(ingest(ai_pipe, chat_id, [ORDER, QUIET]))
    assert ids[0] and ids[1] is None
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE status='noise'") == 1


def test_messages_are_read_in_batches_to_save_free_limits(ai_pipe, chat_id):
    texts = [f"Нужен разработчик для проекта номер {n}, бюджет {20 + n} 000 руб, пишите в личку @cust{n}x" for n in range(37)]
    run(ingest(ai_pipe, chat_id, texts))
    assert len(ai_pipe.llm.calls) == 3  # 15 + 15 + 7
    assert sum(len(c["texts"]) for c in ai_pipe.llm.calls) == 37


def test_ai_order_without_any_trigger_words_is_still_found(ai_pipe, chat_id):
    text = "Мне бы кого-нибудь, кто разберётся с моим интернет-магазином и наведёт там порядок с заявками. Ищу исполнителя, @shop_owner"
    ids = run(ingest(ai_pipe, chat_id, [text]))
    assert ids[0], "это заказ по смыслу, даже если правила слов его не узнали бы"


def test_unsure_ai_order_is_kept_with_a_doubt_flag_in_recall_mode(db, settings, chat_id):
    p = Pipeline(db, FakeAI(unsure=("Нужен",)), settings)
    p.ai_on = lambda: True
    ids = run(ingest(p, chat_id, [ORDER]))
    assert ids[0]
    flags = db.scalar("SELECT red_flags FROM orders WHERE id=?", (ids[0],))
    assert "ИИ не уверен" in flags


def test_ai_failure_falls_back_to_rules_and_nothing_is_lost(db, settings, chat_id):
    p = Pipeline(db, FakeAI(fail=True), settings)
    p.ai_on = lambda: True
    ids = run(ingest(p, chat_id, [ORDER, QUIET]))
    assert ids[0] and ids[1] is None  # правила отобрали так же, как раньше


def test_paused_ai_is_not_called_at_all(db, settings, chat_id):
    p = Pipeline(db, FakeAI(), settings)
    p.ai_on = lambda: False
    run(ingest(p, chat_id, [ORDER]))
    assert p.llm.calls == []


def test_supply_ads_and_rules_never_reach_the_ai(ai_pipe, chat_id):
    from .test_supply import RULES, SITES, TECH

    ids = run(ingest(ai_pipe, chat_id, [TECH, SITES, RULES, ORDER]))
    assert ids[:3] == [None, None, None] and ids[3]
    sent = [t for c in ai_pipe.llm.calls for t in c["texts"]]
    assert sent == [ORDER]  # лишних запросов не тратим


def test_same_text_from_two_chats_is_read_once(ai_pipe, chat_id, db):
    other = store.add_chat(db, "Другой чат", username="other", members=10)

    async def go():
        raws = [make_raw(chat_id, ORDER, author="a", author_id=1), make_raw(other, ORDER, author="a", author_id=1)]
        await ai_pipe.prejudge(raws)
        return [await ai_pipe.process(r) for r in raws]

    ids = run(go())
    assert sum(len(c["texts"]) for c in ai_pipe.llm.calls) == 1
    assert ids[0] and ids[1] is None  # второй чат склеился с первым


def test_your_decisions_become_examples_for_the_ai(ai_pipe, chat_id, db):
    ai_pipe.teach("Ищу компанию, которая будет вести мой бухгалтерский учёт каждый месяц", False)
    ai_pipe.teach("Кто-нибудь сделает мне сайт-визитку недорого? Бюджет 15000, @site_owner", True)
    run(ingest(ai_pipe, chat_id, [ORDER]))
    seen = ai_pipe.llm.calls[0]
    assert any("бухгалтерский" in t for t in seen["no"])
    assert any("сайт-визитку" in t for t in seen["yes"])


def test_examples_are_deduplicated_and_capped(db):
    for i in range(classify.MAX_EXAMPLES + 20):
        classify.teach(db, f"Пример номер {i} с достаточно длинным текстом для запоминания", i % 2 == 0)
    classify.teach(db, "Пример номер 500 с достаточно длинным текстом для запоминания", True)
    classify.teach(db, "Пример номер 500 с достаточно длинным текстом для запоминания", False)  # передумали: заменяет прежний
    items = db.get_setting(classify.EXAMPLES_KEY)
    assert len(items) <= classify.MAX_EXAMPLES
    assert sum(1 for i in items if "номер 500" in i["t"]) == 1
    classify.teach(db, "коротко", True)
    assert all(len(i["t"]) >= 20 for i in db.get_setting(classify.EXAMPLES_KEY))


def test_prompt_contains_profile_examples_and_definitions():
    system, user = classify.build_prompt(["текст один", "текст два"], Profile(skills=["n8n", "боты"]), ["пример заказ"], ["пример не заказ"])
    assert "n8n" in system and "боты" in system and "#помогу" in system
    assert "пример заказ" in system and "пример не заказ" in system
    assert "[1] текст один" in user and "[2] текст два" in user


def test_parse_verdicts_tolerates_garbage():
    out = classify.parse_verdicts({"results": [{"i": 1, "kind": "order", "unsure": True}, {"i": 3, "kind": "weird"}, {"i": "x"}, "junk", {"i": 9, "kind": "chat"}]}, 3)
    assert out[0] == Verdict("order", True) and out[1] is None and out[2] is None
    assert classify.parse_verdicts({}, 2) == [None, None]
    assert classify.parse_verdicts({"results": "no"}, 2) == [None, None]


def test_openai_compatible_model_gets_json_and_answers_are_parsed(db):
    class Fake(OpenAICompatLLM):
        async def _call(self, model, system, user, **kw):
            assert kw.get("wants_json") and "[1]" in user
            return '```json\n{"results":[{"i":1,"kind":"offer","unsure":false},{"i":2,"kind":"order","unsure":false}]}\n```'

    llm = Fake("https://x/v1", "k", "m", db=db)
    out = run(llm.classify(["я делаю сайты", "нужен сайт"], Profile(), [], []))
    assert [v.kind for v in out] == ["offer", "order"]


def test_fallback_wrapper_returns_unknown_when_ai_is_down(db):
    class Down(OpenAICompatLLM):
        async def _call(self, *a, **k):
            raise LLMUnavailable("нет связи")

    out = run(FallbackLLM(Down("https://x/v1", "k", "m", db=db)).classify(["а", "б"], Profile(), [], []))
    assert out == [None, None]


def test_not_an_order_button_teaches_the_ai(authed):
    authed.post("/api/scan", headers=H)
    rt = authed.app.state.rt
    oid = rt.db.scalar("SELECT id FROM orders ORDER BY id LIMIT 1")
    assert oid, "в демо есть заказы"
    r = authed.post(f"/api/orders/{oid}/feedback", json={"kind": "down", "reason": "notorder"}, headers=H)
    assert r.status_code == 200
    yes, no = classify.examples(rt.db)
    assert len(no) == 1
    assert authed.post(f"/api/orders/{oid}/feedback", json={"kind": "down", "reason": "bogus"}, headers=H).status_code == 422


def test_thumbs_up_teaches_a_positive_example(authed):
    authed.post("/api/scan", headers=H)
    rt = authed.app.state.rt
    oid = rt.db.scalar("SELECT id FROM orders ORDER BY id LIMIT 1")
    authed.post(f"/api/orders/{oid}/feedback", json={"kind": "up"}, headers=H)
    yes, no = classify.examples(rt.db)
    assert len(yes) == 1 and not no


class Quota:
    """Двойник счётчика бесплатного ИИ."""

    def __init__(self, rpd, used):
        self.rpd, self._used = rpd, used

    def used_today(self):
        return self._used


def many(n):
    return [f"Нужен разработчик для проекта номер {i}, бюджет {20 + i} 000 руб, пишите в личку @cust{i}x" for i in range(n)]


def test_free_daily_limit_keeps_a_reserve_for_scoring(db, settings, chat_id):
    p = Pipeline(db, FakeAI(), settings)
    p.ai_on = lambda: True
    p.llm.primary = Quota(rpd=900, used=700)  # осталось 200, резерв 270: на чтение пачками ничего не выделяем
    run(ingest(p, chat_id, many(30)))
    assert p.llm.calls == []


def test_free_limit_reads_the_newest_messages_first(db, settings, chat_id):
    p = Pipeline(db, FakeAI(), settings)
    p.ai_on = lambda: True
    p.llm.primary = Quota(rpd=900, used=900 - 270 - 1)  # ровно одна пачка сверх резерва
    texts = many(40)

    async def go():
        raws = [make_raw(chat_id, t, minutes_ago=100 - i, author=f"u{i}", author_id=10 + i) for i, t in enumerate(texts)]  # чем больше номер, тем свежее
        await p.prejudge(raws)

    run(go())
    assert len(p.llm.calls) == 1
    sent = p.llm.calls[0]["texts"]
    assert len(sent) == classify.BATCH and texts[-1] in sent and texts[0] not in sent


def test_paid_claude_is_capped_per_scan_not_per_day(db, settings, chat_id):
    p = Pipeline(db, FakeAI(), settings)
    p.ai_on = lambda: True
    assert p._classify_budget() == classify.MAX_BATCHES


def test_progress_is_reported_while_the_ai_reads_and_cleared_after(db, settings, chat_id):
    p = Pipeline(db, FakeAI(), settings)
    p.ai_on = lambda: True
    seen = []
    p.on_progress = lambda: seen.append(p.progress)
    run(ingest(p, chat_id, many(40)))
    assert seen[:3] == [(0, 3), (1, 3), (2, 3)] and p.progress is None and seen[-1] is None


def test_ai_is_shown_the_examples_most_similar_to_what_it_reads(db):
    classify.teach(db, "Коллеги, посоветуйте нотариуса в Казани для оформления доверенности на квартиру срочно", False)
    for i in range(10):
        classify.teach(db, f"Продаю курс по бухгалтерии номер {i}, скидка пятьдесят процентов только сегодня подписывайтесь", False)
    for i in range(10):
        classify.teach(db, f"Нужен бот для записи клиентов номер {i} бюджет сорок тысяч рублей пишите в личку", True)
    _, no = classify.examples(db, ["Ищу нотариуса в Казани, подскажите хорошего специалиста по доверенностям"])
    assert any("нотариуса" in t for t in no), "самый похожий пример попадает в подсказку, даже если он не самый свежий"
    yes, no2 = classify.examples(db)
    assert len(yes) == classify.SHOW_EACH and not any("нотариуса" in t for t in no2)  # без пачки берутся самые свежие
