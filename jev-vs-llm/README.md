# jev-vs-llm: Decision Lab

Same task (support-ticket triage), same routing policy, **interchangeable decision backends**. A web app that races a decision model ("System One": JEV, Kev, Laya…) against generative LLMs, so the comparison is made with measurements instead of intuition. Web only: credentials are typed in the page.

The lab races a decision model (JEV) against **one or more LLMs**, up to four at a time.

| Contender | What it is | What the user brings, in the page |
|---|---|---|
| JEV | TypeSafe's hosted decision model, called through the `/v1/systemone` contract | A TypeSafe key |
| LLM, via OpenAI | Any chat model the user's account can call, strict structured output | An OpenAI key and a model id (loadable from their account) |
| LLM, via LiteLLM | Any model behind the user's LiteLLM proxy (OpenAI-compatible), strict structured output | The proxy's address and key, and a model id (loadable from the proxy) |
| Keyword baseline | A free keyword matcher, optional, for trying the lab without a key. Says nothing about any model | Nothing |

The LLMs come from OpenAI **or** from one LiteLLM proxy (not both at once), and you can add several models from that provider. The server has no model menu, price file, endpoint list or provider key: everything comes from the user or from a public source.

## Run

```bash
uv sync                                  # from the repo root
uv run jevllm-web                        # http://127.0.0.1:8765
uv run pytest jev-vs-llm                 # tests, no keys or network needed
```

- `/` is the landing page: what the lab does, plus three use cases with editable example tickets.
- `/lab` is the lab, in three steps: connect the contenders, choose the tickets (each with the answer you expect), run. Tickets come from a use case, the 40-ticket labeled dataset, a single example you type, or a batch you paste (JSON lines or CSV with a header row `text,team,page`).

## What the lab does

- **Bring your own keys and address.** Keys travel in the body of each request, only the ones the selected models need, and the server uses them for that request and forgets them: it holds no provider keys, never stores or logs them, and redacts them from error messages. A shared deployment therefore cannot spend anyone's tokens but their own. The page keeps keys and settings in the tab (`sessionStorage`) or, if you tick *Remember on this device*, in `localStorage`. Serve it over HTTPS if it is not on localhost (the page warns you otherwise).
- **Ground truth:** every ticket carries the user's expected answer: the correct team (billing, infra, account, security, or *no single answer*, which runs the ticket without scoring it) and optionally whether it should page on-call. Accuracy and paging accuracy are computed against it.
- **Models:** typed by the user, or loaded from their own account (`GET /v1/models` on OpenAI or on their LiteLLM proxy) into the box's suggestions.
- **Prices:** USD per 1M tokens from [LiteLLM's public price list](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json), fetched on first use and refreshed daily (`prices.py`). It cites each vendor's own pricing page, and covers OpenAI models and JEV. A price the user types always wins; a model with no price shows *no price set* instead of $0, and tokens are estimated (len/4) when a response carries no `usage`.
- **The LiteLLM address the user types** is called by the server, so `net.py` checks each one: `http`/`https` only, no credentials in the URL, no redirects followed, never link-local addresses (cloud metadata). Loopback and LAN addresses are allowed because a homelab proxy lives there; set `BLOCK_PRIVATE_URLS=1` on an instance other people can reach. Not covered: DNS rebinding.
- **Safety:** model ids, key format, URLs and text lengths are validated; closing the page stops a run so you stop paying for tokens.
- **Look:** brass, eggshell and dark grey with teal and byzantium as secondary colours; Montserrat and IBM Plex Mono, both self-hosted (SIL OFL, licenses in `static/fonts/`), so the page makes no requests to Google. Text in brass on light grounds uses a darker brass (`#7A6034`) because the plain brass is only 3:1 on eggshell; brass stays untouched for lines, bars and fills.
- **Confidence is not the same quantity:** JEV returns a probability per option; OpenAI and LiteLLM models return a stated confidence. The page shows both, but compare them with care.

## Use cases and recorded results

`src/jevllm/web/cases.json` holds the landing-page cases (tickets, the correct team, whether it should page on-call, and the trap). `src/jevllm/web/showcase.json` holds the results shown next to them: a static file with what each contender decided, recorded on a date, decisions only. To refresh it, run the tickets in the lab with your own keys and replace the file with a new one of the same shape. A test checks it still matches the cases.

## Layout

```
src/jevllm/
  questions.py        # the 3 questions: one wording shared by every backend
  graph.py            # LangGraph: decide → conditional edge → queue (pure policy: pick_queue)
  backends/           # openai (also LiteLLM), systemone_http, mock
  cases.py            # use cases and the recorded-results path
  dataset.py          # loads data/tickets.jsonl
  prices.py           # public price list, cached
  net.py              # checks and calls for addresses users type
  web/                # FastAPI server + static landing page (landing.*) and lab (lab.*)
data/tickets.jsonl    # 40 labeled tickets (4 teams × 10; negations and ambiguous cases)
tests/
```

## Caveats (read before drawing conclusions)

1. **The keyword baseline says nothing about JEV or any LLM.** It exists only to run the app without keys.
2. **The JEV, OpenAI and LiteLLM adapters have not been run against real servers by the author.** They are written against the documented APIs and tested with stubs and a local fake server. If something fails, start with `backends/systemone_http.py` and `backends/openai_backend.py`.
3. **Structured output:** OpenAI and LiteLLM lanes ask for strict `json_schema` output. A model behind LiteLLM that does not support it fails with the provider's error.
4. **LiteLLM caching.** A proxy with caching on answers repeated requests in milliseconds, which makes latency (and cost, which is computed from the reported tokens) meaningless. Each LiteLLM request therefore carries the documented `cache: {no-cache, no-store}` controls. If your proxy is set to ignore client cache controls, a repeat run will still be an order of magnitude faster than the first: that is the sign to turn its cache off for the model you are testing.
5. **Prices** come from a third-party list that follows vendors' pricing pages; verify anything that matters, or type your own price.
6. **40 tickets is a demo, not a benchmark.** Two or three tickets move several points. Measure on YOUR domain: independent audits vary enormously between tasks.
7. This compares **bounded decisions only**. An LLM reasons, plans and writes; Jev does not.
