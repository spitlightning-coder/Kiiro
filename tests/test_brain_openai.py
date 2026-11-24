from types import SimpleNamespace

import httpx
import openai

from dave import brain as brain_mod
from dave.brain_openai import OpenAIBrain, clean

HIST = [{"role": "user", "content": "Anna: hi Dave"}]


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def make(*outcomes, models=("m1", "m2")):
    fake = FakeCompletions(outcomes)
    client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    b = OpenAIBrain("Du bist Dave.", models=list(models), max_tokens=300, base_url="x", api_key="k", client=client)
    return b, fake


def resp(text, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=text))])


def err(cls, status):
    req = httpx.Request("POST", "https://router.example/v1/chat/completions")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


async def test_reply_and_request_shape():
    b, fake = make(resp("Moin!"))
    assert await b.reply(HIST, server="S", channel="allgemein") == "Moin!"
    call = fake.calls[0]
    assert call["model"] == "m1"
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][0]["content"].startswith("Du bist Dave.")
    assert "#allgemein" in call["messages"][0]["content"]
    assert call["messages"][1:] == HIST


async def test_falls_back_to_next_model_on_rate_limit():
    b, fake = make(err(openai.RateLimitError, 429), resp("Hier Modell 2"))
    assert await b.reply(HIST, server="S", channel="c") == "Hier Modell 2"
    assert [c["model"] for c in fake.calls] == ["m1", "m2"]


async def test_all_rate_limited():
    b, _ = make(err(openai.RateLimitError, 429), err(openai.RateLimitError, 429))
    assert await b.reply(HIST, server="S", channel="c") == brain_mod.LIMIT_ANTWORT


async def test_all_errors_friendly():
    b, _ = make(err(openai.InternalServerError, 500), resp("   "))
    assert await b.reply(HIST, server="S", channel="c") == brain_mod.FEHLER_ANTWORT


async def test_empty_history_no_call():
    b, fake = make()
    assert await b.reply([], server="S", channel="c") is None
    assert fake.calls == []


def test_clean_strips_think_and_prefix():
    assert clean("<think>hmm</think>\nDave: Jo.") == "Jo."
    assert clean("bla bla</think>Antwort") == "Antwort"
    assert clean("**Dave:** Hi") == "Hi"


def test_request_extras_only_for_gemini_flash():
    from dave.brain_openai import Provider, request_extras

    g = Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "k", [])
    r = Provider("requesty", "https://router.requesty.ai/v1", "k", [])
    assert request_extras(g, "gemini-3.5-flash") == {"reasoning_effort": "low"}
    assert request_extras(g, "gemini-3.5-flash-lite") == {}
    assert request_extras(r, "google/gemini-3.5-flash") == {}


async def test_sends_temperature():
    b, fake = make(resp("ok"))
    await b.reply(HIST, server="S", channel="c")
    assert fake.calls[0]["temperature"] == 0.7


async def test_background_work_yields_when_busy():
    b, fake = make(resp("JSON"))
    b.model_cooldown[("haupt", "m1")] = b._clock() + 100
    assert await b.complete("s", "u", background=True) is None
    assert fake.calls == []
    assert await b.complete("s", "u") == "JSON"  # Vordergrund läuft trotzdem
