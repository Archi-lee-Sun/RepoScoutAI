import importlib.util
import sys
import types
from types import SimpleNamespace

import pytest


if importlib.util.find_spec("aiogram") is None:
    aiogram = types.ModuleType("aiogram")
    types_mod = types.ModuleType("aiogram.types")

    class FakeBot:
        def __init__(self, token=None):
            self.token = token
            self.session = SimpleNamespace(close=self._close)

        async def _close(self):
            return None

        async def send_message(self, **kwargs):
            return None

    class FakeDispatcher:
        def callback_query(self, *args, **kwargs):
            return lambda handler: handler

        async def start_polling(self, bot):
            return None

    class FakeButton:
        def __init__(self, text, callback_data):
            self.text = text
            self.callback_data = callback_data

    class FakeMarkup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard

    aiogram.Bot = FakeBot
    aiogram.Dispatcher = FakeDispatcher
    aiogram.F = SimpleNamespace(data=SimpleNamespace(startswith=lambda value: True))
    types_mod.CallbackQuery = object
    types_mod.InlineKeyboardButton = FakeButton
    types_mod.InlineKeyboardMarkup = FakeMarkup
    sys.modules["aiogram"] = aiogram
    sys.modules["aiogram.types"] = types_mod


@pytest.fixture
def fake_pipeline(monkeypatch):
    import pipeline

    class State:
        seen = []
        checkpoints = []

        def mark_seen_batch(self, names):
            self.seen.extend(names)

        def commit_checkpoints(self, checkpoints):
            self.checkpoints.append(dict(checkpoints))

    State.seen = []
    State.checkpoints = []
    monkeypatch.setattr(pipeline, "PollerState", State)
    monkeypatch.setattr(pipeline, "generate_meta_prompt", lambda memory: "meta")
    monkeypatch.setattr(pipeline, "validate_batch", lambda candidates, prompt: candidates)
    monkeypatch.setattr(pipeline, "fetch_batch", lambda candidates: candidates)
    monkeypatch.setattr(pipeline, "select_batch", lambda candidates, prompt: candidates)
    monkeypatch.setattr(pipeline, "explain_batch", lambda candidates: candidates)
    monkeypatch.setattr(pipeline, "translate_batch", lambda candidates: candidates)
    return pipeline, State
