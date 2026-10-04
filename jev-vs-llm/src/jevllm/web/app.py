"""Decision Lab API.

  GET  /             landing page: what the lab does, plus the use cases
  GET  /lab          the lab itself
  GET  /api/cases    the use cases (tickets, expected team) shown on the landing page and offered as examples
  GET  /api/showcase the recorded results shown on the landing page (web/showcase.json), or {"recorded": false}
  GET  /api/dataset  the 40 labeled tickets (id, text, correct team), to load into the lab's ticket table
  POST /api/models   the model ids a user's OpenAI account or LiteLLM proxy offers
  GET  /api/price    public list price (USD per 1M tokens) for a model, if one is published
  POST /api/batch    a table of tickets (each with the user's ground truth) -> every contender, results streamed (SSE)

Everything a user needs is typed in the page and travels with the request: provider keys, the URL of their LiteLLM
proxy, the model to race and, optionally, a price. The server holds no provider keys and keeps
nothing after the request ends (so a shared deployment cannot spend anyone's tokens but their own), and it
operates no menu: models come from the user's own account, prices from a public list (prices.py).
Because the server calls the URLs users type, every one goes through net.check_url (see net.py for the rules).
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, field_validator

from .. import net, prices
from ..cases import SHOWCASE, load_cases
from ..dataset import load_dataset
from ..graph import pick_queue
from ..schema import Decision

STATIC = Path(__file__).parent / "static"
MODEL_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,80}$")
KEY = re.compile(r"^[!-~]{1,512}$")  # printable ASCII, no whitespace: also rules out header injection
TICKET_ID = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
MAX_TEXT, MAX_LANES, MAX_BATCH = 4000, 6, 40  # JEV + up to four models + the keyword baseline
JEV_URL, OPENAI_URL = "https://api.typesafe.ai", "https://api.openai.com/v1"

app = FastAPI(title="Decision Lab", docs_url=None, redoc_url=None)


class RevalidatedStatic(StaticFiles):
    """Static files are revalidated (ETag) on every load, so an upgrade is never masked by a stale cached app.js."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = "no-cache"
        return resp


# ------------------------------------------------------------------ lanes
def _secret_ok(v: SecretStr | None) -> SecretStr | None:
    if v is not None and not KEY.match(v.get_secret_value()):
        raise ValueError("API key has unexpected characters")
    return v


class Lane(BaseModel):
    """One contender: JEV, or the LLM (OpenAI or a LiteLLM proxy whose address and key the user supplies)."""

    kind: Literal["jev", "openai", "litellm", "mock"]
    model: str | None = None  # model id; JEV defaults to its pinned version
    url: str | None = Field(default=None, max_length=300)  # LiteLLM proxy address, checked by net.check_url
    key: SecretStr | None = None  # the LiteLLM proxy's key (JEV and OpenAI keys travel in `keys`)
    price_in: float | None = Field(default=None, ge=0, le=1e6)  # USD per 1M tokens, typed by the user
    price_out: float | None = Field(default=None, ge=0, le=1e6)

    @field_validator("model")
    @classmethod
    def _model(cls, v):
        if v is not None and not MODEL_ID.match(v):
            raise ValueError("model id may only contain letters, digits and . _ : / -")
        return v

    _key = field_validator("key")(lambda cls, v: _secret_ok(v))

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.model or ''}"

    def secrets(self) -> list[str]:
        return [self.key.get_secret_value()] if self.key else []


class Keys(BaseModel):
    """Keys for the two hosted providers, supplied by the user for this request only. SecretStr keeps them out of logs."""

    typesafe: SecretStr | None = None
    openai: SecretStr | None = None

    _key = field_validator("typesafe", "openai")(lambda cls, v: _secret_ok(v))

    def get(self, name: str) -> str | None:
        v = getattr(self, name)
        return v.get_secret_value() if v else None

    def secrets(self) -> list[str]:
        return [v for name in type(self).model_fields if (v := self.get(name))]


Team = Literal["billing", "infra", "account", "security"]  # kept equal to schema.TEAMS (a test checks it)


class Ticket(BaseModel):
    """A ticket to test plus the user's ground truth: the correct team, and whether it should page on-call."""

    id: str = Field(pattern=TICKET_ID.pattern)
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    team: Team | None = None  # None = no single right answer: the ticket is run but not scored
    page: bool | None = None  # None = not asked


class Batch(BaseModel):
    lanes: list[Lane] = Field(min_length=1, max_length=MAX_LANES)
    tickets: list[Ticket] = Field(min_length=1, max_length=MAX_BATCH)
    threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    keys: Keys = Field(default_factory=Keys)

    @field_validator("tickets")
    @classmethod
    def _unique(cls, v):
        if len({t.id for t in v}) != len(v):
            raise ValueError("ticket ids must be unique")
        return v


def resolve_price(lane: Lane) -> tuple[float, float] | None:
    """What the user typed wins; otherwise the public list; otherwise unknown (the page says so)."""
    if lane.price_in is not None and lane.price_out is not None:
        return lane.price_in, lane.price_out
    if lane.kind in ("jev", "openai", "litellm"):
        return prices.lookup(lane.kind, lane.model or ("jev-1.13.0" if lane.kind == "jev" else ""))
    return None


def make_backend(lane: Lane, keys: Keys):
    """Built per request (cheap), so a user's key is never cached or shared with another user."""
    if lane.kind == "mock":
        from ..backends.mock_backend import MockBackend

        return MockBackend()
    price = resolve_price(lane)
    if lane.kind == "jev":
        if not (key := keys.get("typesafe")):
            raise RuntimeError("Enter your TypeSafe API key first")
        from ..backends.systemone_http import SystemOneHTTPBackend

        return SystemOneHTTPBackend(
            name="jev", base_url=JEV_URL, api_key=key, model=lane.model, price_in=price[0] if price else None
        )
    if not lane.model:
        raise RuntimeError("Enter a model id")
    from ..backends.openai_backend import OpenAIBackend

    if lane.kind == "litellm":
        if not lane.key:
            raise RuntimeError("Enter the key for your LiteLLM proxy")
        return OpenAIBackend(
            lane.model,
            api_key=lane.key.get_secret_value(),
            base_url=net.check_url(lane.url or ""),
            vendor="litellm",
            price=price,
        )
    if not (key := keys.get("openai")):
        raise RuntimeError("Enter your OpenAI API key first")
    return OpenAIBackend(lane.model, api_key=key, price=price)


def label(lane: Lane) -> str:
    if lane.kind == "jev":
        return f"JEV · {lane.model or 'jev-1.13.0'}"
    if lane.kind == "openai":
        return f"OpenAI · {lane.model}"
    if lane.kind == "litellm":
        return f"LiteLLM · {lane.model}"
    return "Demo matcher · no model"


def payload(d: Decision, threshold: float) -> dict:
    return {
        "team": d.team,
        "team_probs": d.team_probs,
        "confidence": d.confidence,
        "urgent_prob": d.urgent_prob,
        "severity": d.severity,
        "queue": pick_queue(d, threshold),
        "latency_ms": d.latency_s * 1000,
        "cost_usd": d.cost_usd,
        "cost_known": d.cost_known,
        "input_tokens": d.input_tokens,
        "output_tokens": d.output_tokens,
        "tokens_estimated": d.tokens_estimated,
    }


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _err(e: Exception, secrets: list[str]) -> str:
    msg = f"{type(e).__name__}: {e}"
    for secret in secrets:  # a provider error may echo a key back
        msg = msg.replace(secret, "***")
    return msg[:300]


def _secrets(req: Batch) -> list[str]:
    return req.keys.secrets() + [s for ln in req.lanes for s in ln.secrets()]


# ------------------------------------------------------------------ endpoints
@app.get("/api/dataset")
def dataset() -> dict:
    """The labeled sample, shaped like the lab's ticket rows. `team` is the label the dataset was written with."""
    return {"tickets": [{"id": r["id"], "text": r["text"], "team": r["team"], "page": None} for r in load_dataset()]}


class ModelsReq(BaseModel):
    provider: Literal["openai", "litellm"]
    key: SecretStr
    url: str | None = Field(default=None, max_length=300)  # LiteLLM proxy; OpenAI is always api.openai.com

    _key = field_validator("key")(lambda cls, v: _secret_ok(v))


@app.post("/api/models")
def models(req: ModelsReq) -> dict:
    """The model ids the user's own account or proxy offers, fetched live with their key."""
    key = req.key.get_secret_value()
    try:
        base = OPENAI_URL if req.provider == "openai" else net.check_url(req.url or "")
        body = net.fetch_json(f"{base}/models" if req.provider == "openai" else _litellm_models_url(base), key)
        ids = sorted({m["id"] for m in body["data"] if isinstance(m.get("id"), str) and MODEL_ID.match(m["id"])})
    except (net.UnsafeURL, RuntimeError, KeyError, TypeError) as e:
        raise HTTPException(502, _err(e, [key])) from e
    return {"models": ids}


def _litellm_models_url(base: str) -> str:
    return f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"


@app.get("/api/price")
def price(kind: Literal["jev", "openai", "litellm"], model: str = "") -> dict:
    if not MODEL_ID.match(model or "jev-1.13.0"):
        raise HTTPException(422, "invalid model id")
    p = prices.lookup(kind, model or ("jev-1.13.0" if kind == "jev" else ""))
    return {"price_in": p[0], "price_out": p[1], "source": "LiteLLM public price list"} if p else {}


def _new_stats() -> dict:
    return {
        "done": 0,
        "scored": 0,
        "correct": 0,
        "page_scored": 0,
        "page_ok": 0,
        "errors": 0,
        "lat": [],
        "cost": 0.0,
        "cost_known": True,
    }


@app.post("/api/batch")
def batch(req: Batch):
    rows = req.tickets
    secrets = _secrets(req)

    def gen():
        q: queue.Queue = queue.Queue()
        cancel = threading.Event()
        stats = {ln.id: _new_stats() for ln in req.lanes}
        yield sse("start", {"lanes": [{"id": ln.id, "label": label(ln)} for ln in req.lanes], "total": len(rows)})

        def work(ln: Lane):
            try:
                be, setup_err = make_backend(ln, req.keys), None  # once per lane, not per ticket
            except Exception as e:
                be, setup_err = None, e
            for i, r in enumerate(rows):
                if cancel.is_set():
                    break
                try:
                    if be is None:
                        raise setup_err
                    d = be.decide(r.text)
                    q.put((ln, i, r, {"ok": True, **payload(d, req.threshold)}))
                except Exception as e:
                    q.put((ln, i, r, {"ok": False, "error": _err(e, secrets)}))
            q.put((ln, None, None, None))

        pool = ThreadPoolExecutor(max_workers=len(req.lanes))
        try:
            for ln in req.lanes:
                pool.submit(work, ln)
            finished = 0
            while finished < len(req.lanes):
                ln, i, r, res = q.get()
                if i is None:
                    finished += 1
                    continue
                s = stats[ln.id]
                s["done"] += 1
                correct = page_ok = None
                if res["ok"]:
                    if r.team is not None:  # tickets with no single right answer are run but not scored
                        correct = res["team"] == r.team
                        s["scored"] += 1
                        s["correct"] += correct
                    if r.page is not None:
                        page_ok = (res["queue"] == "page_oncall") == r.page
                        s["page_scored"] += 1
                        s["page_ok"] += page_ok
                    s["lat"].append(res["latency_ms"])
                    s["cost"] += res["cost_usd"]
                    s["cost_known"] = s["cost_known"] and res["cost_known"]
                else:
                    s["errors"] += 1
                lat = sorted(s["lat"])
                yield sse(
                    "item",
                    {
                        **res,
                        "id": ln.id,
                        "row": i,
                        "ticket": r.id,
                        "gold": r.team,
                        "gold_page": r.page,
                        "correct": correct,
                        "page_correct": page_ok,
                        "summary": {
                            "done": s["done"],
                            "errors": s["errors"],
                            "accuracy": s["correct"] / s["scored"] if s["scored"] else None,
                            "scored": s["scored"],
                            "correct": s["correct"],
                            "paging": [s["page_ok"], s["page_scored"]],
                            "p50_ms": lat[len(lat) // 2] if lat else None,
                            "cost_usd": s["cost"],
                            "cost_known": s["cost_known"],
                        },
                    },
                )
            yield sse("done", {})
        finally:
            cancel.set()  # client closed the stream: stop spending tokens
            pool.shutdown(wait=False, cancel_futures=True)

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"cache-control": "no-store", "x-accel-buffering": "no"}
    )


@app.get("/api/cases")
def cases() -> dict:
    return {"cases": load_cases()}


@app.get("/api/showcase")
def showcase() -> dict:
    path = SHOWCASE
    if not path.is_file():
        return {"recorded": False}
    return {"recorded": True, **json.loads(path.read_text())}


def page(name: str) -> FileResponse:
    return FileResponse(STATIC / name, headers={"cache-control": "no-cache"})


@app.get("/")
def landing():
    return page("landing.html")


@app.get("/lab")
def lab():
    return page("lab.html")


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": time.time()}


app.mount("/static", RevalidatedStatic(directory=STATIC), name="static")
