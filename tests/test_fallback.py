from types import SimpleNamespace

import httpx
import openai

from dave import brain as brain_mod
from dave.brain_openai import COOLDOWN_LIMIT, OpenAIBrain, Provider
from dave.providers import fallbacks_from_env

HIST = [{"role": "user", "content": "Anna: hi"}]


class Fake:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kw):
        self.calls.append(kw["model"])
        out = self.outcomes.pop(0) if self.outcomes else resp("ok")
        if isinstance(out, Exception):
            raise out
        return out


def resp(text):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=text))])


def limit():
    req = httpx.Request("POST", "https://x/v1/chat/completions")
    return openai.RateLimitError("limit", response=httpx.Response(429, request=req), body=None)


def make(main, *reserves, clock=None):
    t = clock or [0.0]
    fbs = [Provider(f"r{i}", "http://r", "k", ["rm"], client=c) for i, c in enumerate(reserves)]
    return OpenAIBrain("Du bist Dave.", models=["m1", "m2"], max_tokens=50, base_url="x", api_key="k",
                       client=main, fallbacks=fbs, clock=lambda: t[0]), t


async def test_switches_to_reserve_and_pauses_main():
    main = Fake(limit(), limit())
    reserve = Fake(resp("Hallo von der Reserve"))
    b, t = make(main, reserve)
    assert await b.reply(HIST, server="S", channel="c") == "Hallo von der Reserve"
    assert main.calls == ["m1", "m2"] and reserve.calls == ["rm"]
    # Hauptanbieter ist jetzt pausiert -> wird übersprungen
    assert await b.reply(HIST, server="S", channel="c") == "ok"
    assert main.calls == ["m1", "m2"]
    assert "Pause" in b.status()
    t[0] += COOLDOWN_LIMIT + 1  # Pause vorbei -> wieder zuerst der Hauptanbieter
    await b.reply(HIST, server="S", channel="c")
    assert main.calls == ["m1", "m2", "m1"]


async def test_all_limited():
    b, _ = make(Fake(limit(), limit()), Fake(limit()))
    assert await b.reply(HIST, server="S", channel="c") == brain_mod.LIMIT_ANTWORT


def test_fallbacks_from_env():
    env = {"DAVE_FALLBACK1_NAME": "groq", "DAVE_FALLBACK1_KEY": "gsk", "DAVE_FALLBACK1_MODELS": "a, b",
           "DAVE_FALLBACK2_NAME": "eigener", "DAVE_FALLBACK2_KEY": "k", "DAVE_FALLBACK2_MODELS": "x",
           "DAVE_FALLBACK3_NAME": "gemini", "DAVE_FALLBACK3_MODELS": "g"}  # 2: keine URL, 3: kein Key
    (p,) = fallbacks_from_env(env)
    assert p.name == "groq" and p.base_url == "https://api.groq.com/openai/v1" and p.models == ["a", "b"]


def busy():
    req = httpx.Request("POST", "https://x/v1/chat/completions")
    return openai.InternalServerError("overloaded", response=httpx.Response(503, request=req), body=None)


async def test_hedges_to_next_model_when_first_hangs():
    import asyncio

    class PerModel(Fake):
        cancelled = False

        async def create(self, **kw):
            self.calls.append(kw["model"])
            if kw["model"] == "m1":
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    PerModel.cancelled = True
                    raise
            return resp(f"Antwort von {kw['model']}")

    main = PerModel()
    reserve = Fake(resp("Reserve"))
    b, _ = make(main, reserve)
    b.hedge_after = 0.05
    assert await b.reply(HIST, server="S", channel="c") == "Antwort von m2"
    assert main.calls == ["m1", "m2"] and reserve.calls == []
    await asyncio.sleep(0)  # Abbruch des hängenden Aufrufs greift im nächsten Schritt der Event-Loop
    assert PerModel.cancelled


async def test_busy_model_is_skipped_for_a_while():
    from dave.brain_openai import COOLDOWN_BUSY

    main = Fake(busy(), resp("m2 springt ein"))
    b, t = make(main)
    assert await b.reply(HIST, server="S", channel="c") == "m2 springt ein"
    await b.reply(HIST, server="S", channel="c")
    assert main.calls == ["m1", "m2", "m2"]  # m1 ist überlastet -> erstmal ausgelassen
    t[0] += COOLDOWN_BUSY + 1
    await b.reply(HIST, server="S", channel="c")
    assert main.calls[-1] == "m1"


async def test_hedge_prefers_fastest_other_provider():
    import asyncio

    class Hang(Fake):
        async def create(self, **kw):
            self.calls.append(kw["model"])
            await asyncio.sleep(5)

    main = Hang()
    slow, fast = Fake(resp("lahm")), Fake(resp("flott"))
    b, _ = make(main, slow, fast)
    b.hedge_after = 0.05
    b.speed = {"r0": 20.0, "r1": 0.8}
    assert await b.reply(HIST, server="S", channel="c") == "flott"
    assert slow.calls == [] and fast.calls == ["rm"]


async def test_gone_model_is_skipped_long():
    from dave.brain_openai import COOLDOWN_BUSY

    req = httpx.Request("POST", "https://x/v1/chat/completions")
    gone = openai.NotFoundError("gone", response=httpx.Response(404, request=req), body=None)
    main = Fake(gone, resp("m2"))
    b, t = make(main)
    assert await b.reply(HIST, server="S", channel="c") == "m2"
    t[0] += COOLDOWN_BUSY + 1
    await b.reply(HIST, server="S", channel="c")
    assert main.calls == ["m1", "m2", "m2"]
