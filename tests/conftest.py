from datetime import UTC, datetime, timedelta

import pytest

from leadscout.config import Settings
from leadscout.db import Database
from leadscout.llm.rules import RuleLLM
from leadscout.models import RawMessage
from leadscout.pipeline import Pipeline
from leadscout import store


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "t.db")


@pytest.fixture()
def settings(tmp_path):
    return Settings(data_dir=tmp_path, password="pw", demo=True)


@pytest.fixture()
def pipe(db, settings):
    return Pipeline(db, RuleLLM(), settings)


@pytest.fixture()
def chat_id(db):
    return store.add_chat(db, "AI Заказы", username="ai_zakazy", members=1000)


_counter = {"n": 100}


def make_raw(chat_id: int, text: str, minutes_ago: int = 5, author="ivan_k", **kw) -> RawMessage:
    _counter["n"] += 1
    return RawMessage(
        chat_id=chat_id, tg_msg_id=kw.pop("tg_msg_id", _counter["n"]), text=text,
        posted_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        author_id=kw.pop("author_id", 42), author_username=author, author_name="Иван", **kw,
    )
