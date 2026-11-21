from types import SimpleNamespace

import httpx2 as httpx
import anthropic

from dave import brain as brain_mod
from dave.brain import Brain


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def make(response=None, error=None, model="claude-opus-5-5"):
    fake = FakeMessages(response, error)
    client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    return Brain("Du bist Dave.", model=model, effort="low", max_tokens=500, client=client), fake


def resp(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=text),
    ])


HIST = [{"role": "user", "content": "Anna: hi Dave"}]


async def test_reply_text_and_request_shape():
    b, fake = make(resp("Moin!"))
    assert await b.reply(HIST, server="S", channel="allgemein") == "Moin!"
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["messages"] == HIST
    assert call["output_config"] == {"effort": "low"}
    assert call["fallbacks"] == "default"
    assert call["system"][0]["text"] == "Du bist Dave."
    assert "#allgemein" in call["system"][1]["text"]
    assert "thinking" not in call  # Opus 5.5: Thinking nicht abschaltbar, Parameter weglassen


async def test_no_fallbacks_for_other_models():
    b, fake = make(resp("ok"), model="claude-haiku-5-5")
    await b.reply(HIST, server="S", channel="c")
    assert "fallbacks" not in fake.calls[0]


async def test_refusal():
    b, _ = make(resp("", stop="refusal"))
    assert await b.reply(HIST, server="S", channel="c") == brain_mod.ABLEHNUNG_ANTWORT


async def test_empty_history_no_call():
    b, fake = make(resp("x"))
    assert await b.reply([], server="S", channel="c") is None
    assert fake.calls == []


async def test_api_error_friendly():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.InternalServerError("boom", response=httpx.Response(500, request=req), body=None)
    b, _ = make(error=err)
    assert await b.reply(HIST, server="S", channel="c") == brain_mod.FEHLER_ANTWORT
