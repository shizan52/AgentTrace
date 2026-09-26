<div align="center">

```
 █████╗  ██████╗ ███████╗███╗   ██╗████████╗████████╗██████╗  █████╗  ██████╗███████╗
██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝╚══██╔══╝██╔══██╗██╔══██╗██╔════╝██╔════╝
███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║      ██║   ██████╔╝███████║██║     █████╗
██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║      ██║   ██╔══██╗██╔══██║██║     ██╔══╝
██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║      ██║   ██║  ██║██║  ██║╚██████╗███████╗
╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝      ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚══════╝
```

```
               ██
               ██                ░▒▓█ the AI's web-scraping sidekick █▓▒░
   ▄       ▄████████▄       ▄
    ▀▄    ███  ██  ███    ▄▀       ONE file ........ agenttrace.py
      ▀▀▄▄████████████▄▄▀▀         drives .......... a real Chromium (Playwright)
  ▄▀▀▀▀▄▄██████████████▄▄▀▀▀▀▄     records ......... every request → verified HAR
     ▄▀ ▄██████████████▄ ▀▄        finds ........... the JSON API behind the page
   ▄▀ ▄▀  ▀██████████▀  ▀▄ ▀▄      speaks .......... CLI · Python · MCP (Claude)
     ▄▀      ▀▀▀▀▀▀      ▀▄        proves .......... 51 real-world self-tests
```

</div>

> **AgentTrace** is an AI helper module for web scraping, shipped as **one Python file**.
> An AI agent (or you) gives it a URL or a plain-language goal; it drives a real browser,
> captures and verifies the network traffic of every page / click / SPA route, tells you
> **how the site should be scraped** (JSON API? hydration blob? HTML list?), extracts the data,
> and exports reusable API clients — politely, reproducibly, with evidence.

```
╔══════════════════════════════════════════════════════════════════════════════════════╗
║  pip install playwright  &&  python -m playwright install chromium                   ║
║  python agenttrace.py doctor            # is everything ready?                       ║
║  python agenttrace.py recon  <URL>      # how should this site be scraped?           ║
║  python agenttrace.py extract <URL> --paginate --output data.csv                     ║
╚══════════════════════════════════════════════════════════════════════════════════════╝
```

---

## ░▒▓ Contents

| # | Section | For |
|---|---|---|
| 1 | [🤖 AI agents: read this first](#-1-ai-agents-read-this-first) | the decision flow + golden rules |
| 2 | [📦 Install](#-2-install) | Windows / macOS / Linux |
| 3 | [🗺️ Task → tool map](#️-3-task--tool-map) | "I want to … → use …" |
| 4 | [🍳 Recipes](#-4-recipes-copy-paste) | copy-paste solutions for common scraping jobs |
| 5 | [🐍 Python API](#-5-python-api-reference) | `Session` and helpers |
| 6 | [⌨️ CLI](#️-6-cli-reference) | every command, flags, exit codes |
| 7 | [🧩 Workflow configs](#-7-workflow-configs-json--yaml--toml) | no-code site configs, checkpoints |
| 8 | [🔌 MCP server](#-8-mcp-server-claude-code--claude-desktop--cline) | use it as tools from Claude |
| 9 | [📁 What a run writes](#-9-what-a-run-writes) | HARs, reports, evidence |
| 10 | [🩺 Troubleshooting](#-10-troubleshooting) | error → fix |
| 11 | [🧪 Tested](#-11-tested-how-and-results) | how it was tested + results |
| 12 | [🧱 Inside the file](#-12-inside-the-single-file) | map of `agenttrace.py` ↔ `knowledge.md` |
| 13 | [🇧🇩 বাংলায় সংক্ষেপে](#-13-বাংলায়-সংক্ষেপে) | Bengali summary |

---

## 🤖 1. AI agents: read this first

You are working in a project that contains `agenttrace.py`. **Use it instead of writing raw
Playwright/requests code.** It already solves waiting, network capture, retries, CAPTCHA pauses,
rate limits, pagination, login reuse and reporting. Its outputs are designed for you: short JSON
results, `report.json` (no HAR parsing needed), `endpoints.json`, `recon.md`.

### 1.1 The decision flow

```
                            ┌────────────────────────────────┐
                            │ python agenttrace.py doctor    │  ← once per machine
                            └───────────────┬────────────────┘
                                            ▼
                            ┌────────────────────────────────┐
                            │ python agenttrace.py recon URL │  → recon.json + recon.md
                            └───────────────┬────────────────┘
                                            │ strategy.approach = ?
   ┌───────────────┬────────────────┬───────┴────────┬────────────────┬────────────────┐
   ▼               ▼                ▼                ▼                ▼                ▼
 "api"        "hydration"       "jsonld"        "html-list"     "interactive"   "unblock-first"
 JSON API     __NEXT_DATA__ /   schema.org      repeated        no list here    CAPTCHA / WAF
 feeds page   __NUXT__ blob     in the page     CSS items       (search/login)  / access denied
   │               │                │                │                │                │
   ▼               ▼                ▼                ▼                ▼                ▼
 s.fetch(api,  s.hydration_     s.jsonld()      extract CLI /   s.observe() →   stealth=True,
 params=page)  data()[path]                     s.paginate()    click / fill →  headless=False,
 or export →   (path is in                      (next / load-   then recon      hitl="wait",
 api_client.py  recon.md)                       more / scroll)  again           save_state()
   │               │                │                │                │                │
   └───────────────┴────────────────┴───────┬────────┴────────────────┴────────────────┘
                                            ▼
                   validate (expect=…), check report.json, save data (CSV/JSON)
```

The `api`, `hydration`, `html-list` and `unblock-first` branches are verified on eight kinds of
test pages (§11, Part 50) — server-rendered catalogue → `html-list`, WordPress-style news →
`html-list`, infinite-scroll feed → `api` (cursor), load-more deals → `api` (page), JS search →
`api`, React SPA → `api`, Next.js page → `hydration`, CAPTCHA wall → `unblock-first` — and following
each recommendation really returned the complete data.

### 1.2 Golden rules

1. **Recon before code.** `recon` usually finds a JSON API or a hydration blob — far more
   reliable and complete than scraping HTML. Read `recon.md`, then pick the recipe in §4.
2. **Ask before anything irreversible.** Scraping means *reading*. Never place orders, pay, book,
   send messages/forms to people, delete data or change account settings unless the user explicitly
   confirmed that exact action in the current conversation. `expect=` only checks a request *after*
   it happened; to make such clicks impossible, load a veto plugin (§5.4).
3. **Targets are plain words, not selectors.** `s.click("Add to cart")`, `s.fill("Email", "…")`,
   `s.click("second result link")`, `s.click("e12")` (a ref from `s.observe()`). CSS also works.
4. **Never `sleep()`.** Every action already waits until the page *and its API calls* settle.
   To wait for something specific: `s.wait_for({"response": "/api/cart"})`, `{"text": "…"}`.
5. **Validate with `expect=`** so a silent failure becomes a clear error:
   `s.click("Add to cart", expect={"url": "/api/cart", "method": "POST", "status": 201})`.
6. **Read results, not HARs.** Each action returns `ok`, `error`, `hint`, `network.primary`
   (the request it caused), `validation`, `candidates` (when a target was not found). With the
   default `strict=True` a failed action raises `ActionError` whose `.details` hold the same dict;
   agents that prefer return values use `Session(strict=False)`.
   After `finish()`, `report.json` / `report.md` / `endpoints.json` summarise everything.
7. **Log in once, reuse it — and keep it out of git.** `s.save_state("agenttrace_state/site.json")`,
   then `Session(storage_state="agenttrace_state/site.json")` (cookies + localStorage + IndexedDB +
   sessionStorage); manual logins: `login` command. State files and HARs hold **live cookies and
   tokens**: keep them under `agenttrace_state/` and `agenttrace_runs/` (git-ignored, §2), never
   paste them into chats or issues, share HARs only after `redact`, and never type passwords on a
   command line (`login` + `--state`, or `${secret:NAME}` in a config).
8. **Be polite.** A server's 429/503 `Retry-After` is always obeyed (it overrides your delay),
   navigations are paced per host, recon reports robots.txt `Crawl-delay`. Add
   `rate_limit={"min_interval_s": 1}` for bulk jobs. Respect each site's terms.
9. **Blocked?** Don't loop. `detect_block()` / recon says so → `stealth=True`, `headless=False`,
   `hitl="wait"` (a human solves the CAPTCHA once), then `save_state()` and reuse the state.
10. **Stop on your own.** Guardrails abort loops (`max_repeats`), repeated failures on one target
   (`max_failures_per_target`), and step/time budgets with a clear `GuardrailViolation` reason.
11. **Leave evidence.** `finish()` writes HARs, screenshots of failures, `log.jsonl`, timeline —
    point the user to `report.md` when you are done.

### 1.3 Minimal agent loop (Python)

```python
import agenttrace as at

with at.Session(out_dir="agenttrace_runs/books", strict=False) as s:   # failures → ok=False
    s.goto("https://books.toscrape.com/")
    page = s.observe()                                 # what can I click? (refs e1, e2 …)
    r = s.click("Travel")                              # plain-word target
    if not r["ok"]:
        print(r["error"], r["hint"], r.get("candidates"))
    data = s.paginate(mode="auto", max_pages=5)        # items across pages
    print(len(data["items"]), data["fields"])
# → agenttrace_runs/books/report.md, session.har, endpoints.json, …
```

### 1.4 Paste this into your project's `CLAUDE.md` / `AGENTS.md`

```markdown
## Web scraping in this repo
- Use `agenttrace.py` (one file, see its README) for anything that touches websites.
- First `python agenttrace.py doctor`, then `python agenttrace.py recon <URL>` and follow
  `strategy` in recon.md (api → `s.fetch()`/export/HttpClient, hydration → `hydration_data()`,
  html-list → extract/paginate, unblock-first → stealth + headed + hitl).
- Prefer `Session` actions with plain-word targets; never add sleeps; use `expect=` to validate.
- Never place orders, pay, send messages, delete data or change account settings without the
  user's explicit confirmation of that exact action in this conversation — scraping is read-only.
- Login state (`save_state()`) goes to `agenttrace_state/`, runs/HARs to `agenttrace_runs/`; both
  hold live cookies/tokens and stay git-ignored (also ignore `*.har`). Never put passwords on a
  command line; use `login` + `--state` or `${secret:NAME}`. Share HARs only after `redact`.
- Keep rate limits; verify the module on this machine with `python agenttrace.py selftest --quick`.
```

---

## 📦 2. Install

```bash
# Windows (PowerShell)                       # macOS / Linux
py -m pip install playwright                 python3 -m pip install playwright
py -m playwright install chromium            python3 -m playwright install chromium
py agenttrace.py doctor                      python3 agenttrace.py doctor
```

* Python **3.9+** (full suite on 3.11 and 3.13; imports + CLI checked on 3.10 and 3.12), Playwright **1.45+** (tested with 1.56; newer releases are expected to work).
* Optional: `pip install pyyaml` (YAML configs), `psutil` (memory-aware task pool),
  `npm i -g newman` (lets the self-test replay exported Postman collections with real Newman).
* Use it as a **module** (`import agenttrace as at`, keep the file next to your code or on
  `PYTHONPATH`) or as a **CLI** (`python agenttrace.py …`). Nothing else to install.
* Add these lines to your project's `.gitignore` — login states and HARs contain live cookies/tokens:

  ```gitignore
  agenttrace_runs/
  agenttrace_state/
  *.har
  ```
* If the bundled browser cannot start, `doctor` says why; AgentTrace also falls back to a locally
  installed Chrome/Edge.

---

## 🗺️ 3. Task → tool map

| I want to … | CLI | Python |
|---|---|---|
| check the setup | `doctor` | — |
| know how to scrape a site | `recon URL` | `at.recon(s, url)` |
| get a list (products, posts, results) | `extract URL [--paginate] --output x.csv` | `s.extract_list()`, `s.paginate()` |
| follow next / load-more / infinite scroll | `extract URL --paginate --mode next\|load_more\|scroll` | `s.paginate(mode=…)` |
| use the site's JSON API directly | `capture` → `endpoints` → `export` | `s.fetch(url, params=)` (browser cookies), `s.endpoints()`, `at.export_all(...)`, `at.HttpClient` |
| read a Next.js / Nuxt data blob | `recon URL` (gives the path) | `s.hydration_data()` (MCP: `page_data`) |
| read schema.org data | — | `s.jsonld()` |
| log in once and reuse it | `login URL --save agenttrace_state/site.json` | `s.save_state()`, `Session(storage_state=…)` |
| click / type / select like a human | — | `s.click("…")`, `s.fill("…", "…")`, `s.select(…)` |
| verify an action hit the right API | `validate HAR --expect "POST /api/cart"` | `expect={…}` on any action |
| save a verified HAR of a page | `capture URL --har agenttrace_runs/page.har` | `s.capture(url)`, `s.save_har()` |
| crawl a list of URLs | `crawl URL… \| --file urls.txt` | `at.crawl(s, urls)` |
| run a repeatable, resumable job | `run config.json [--resume]` | `at.run_config(cfg)` |
| record my clicks, replay them | `record URL --out wf.json` · `replay wf.json` | `at.WorkflowRecorder`, `at.replay` |
| do a task from plain English | `goal "search for lamp, open the first result …" --url URL` | `at.run_goal(...)` |
| survive a CAPTCHA | `--headed --stealth` | `hitl="wait"`, `stealth=True`, `headless=False` |
| mock / block / change requests | — | `s.mock()`, `s.block()`, `s.modify_request()`, `s.modify_response()` |
| capture SSE / WebSocket traffic | (automatic) | `s.streams()`, `s.websockets()` |
| check bodies are complete | — | `s.integrity(problems_only=True)` |
| compare today's API with last week's | `diff runA runB --md` (exit 4 = breaking) | `at.compare_endpoints()` |
| share a HAR safely | `redact in.har --out safe.har` | `at.Redactor().redact_har(har)` |
| strip analytics/ads noise | `noise HAR --out clean.har` | `at.filter_records()` |
| run many sites in parallel | — | `at.TaskPool(max_workers=3)`, one `Session` per task |
| give Claude these tools | `mcp` | `at.tool_catalog("anthropic")` |

---

## 🍳 4. Recipes (copy-paste)

### 4.1 API-first scraping (the best outcome of `recon`)

```bash
python agenttrace.py recon https://shop.example/deals --out agenttrace_runs/deals
#  strategy: api  endpoint: GET https://shop.example/api/v1/deals  pagination: {"params": ["page"]}
python agenttrace.py export agenttrace_runs/deals/session.har --out agenttrace_runs/deals/api
#  → postman_collection.json · openapi.json/.yaml · api_client.py · requests.sh
```

```python
import agenttrace as at
client = at.HttpClient()          # polite by default: per-host pacing, Retry-After, retries, cookies
rows, page = [], 1
while True:
    data = client.get(f"https://shop.example/api/v1/deals?page={page}").json()
    rows += data["items"]
    if not data.get("has_more"):
        break
    page += 1
```

Logged in, or the API needs the site's cookies/headers? Call it through the browser instead:

```python
with at.Session(storage_state="agenttrace_state/shop.json") as s:
    s.goto("https://shop.example/deals")                           # sets cookies, CSRF, etc.
    page2 = s.fetch("/api/v1/deals", params={"page": 2})           # same cookies/proxy/UA, rate-limited, in the HAR
    rows = page2["json"]["items"]                                  # ok=False on HTTP errors (accept_status=[404] to allow)
```

The generated `api_client.py` is standalone (stdlib only), polite by default, and has one method
per endpoint plus `iterate_pages()` for paginated ones.

### 4.2 HTML list across pages

```bash
python agenttrace.py extract "https://books.toscrape.com/" --paginate --max-pages 10 --output books.csv
python agenttrace.py extract URL --item "li.col" --field title="h3 a@title" --field price=".price_color" --output x.json
```

```python
with at.Session() as s:
    s.goto("https://books.toscrape.com/")
    res = s.paginate(mode="next", max_pages=10)          # auto-detects the item + fields
    rows = res["items"]                                    # [{title, price, url, image, …}, …]
    # explicit version:
    res = s.paginate(mode="next", item="article.product_pod",
                     fields={"title": "h3 a@title", "price": ".price_color", "url": "h3 a@href"})
```

Field specs: `"css"` (text), `"css@attr"` (attribute, URLs made absolute), `"css::html"`, `""` (the item itself).

### 4.3 Log in once, reuse the session

```bash
python agenttrace.py login https://site.example/login --save agenttrace_state/site.json   # a window opens, log in, press Enter
python agenttrace.py extract https://site.example/orders --state agenttrace_state/site.json --output orders.csv
```

```python
import os
import agenttrace as at

with at.Session() as s:
    s.goto("https://site.example/login")
    s.fill("Email", "me@example.com"); s.fill("Password", os.environ["SITE_PASS"])
    s.click("Sign in", expect={"status": "2xx"})
    s.save_state("agenttrace_state/site.json")   # cookies + localStorage + IndexedDB + sessionStorage
with at.Session(storage_state="agenttrace_state/site.json") as s:
    s.goto("https://site.example/account")               # already logged in
```

### 4.4 SPA / infinite scroll / load-more

```python
with at.Session() as s:
    s.goto("https://spa.example/")
    s.click("Products")                  # client-side route → its own "virtual page"
    print(s.routes())                    # every route with its own requests
    s.scroll(to="bottom")                # triggers infinite loaders, waits for the new API calls
    items = s.paginate(mode="scroll", max_pages=10)["items"]
# finish() writes routes/NN_<route>.har — one HAR per SPA route
```

### 4.5 Next.js / Nuxt / Redux data blobs

```python
with at.Session() as s:
    s.goto("https://store.example/mugs")
    data = s.hydration_data()            # {"__NEXT_DATA__": {...}} — recon.md tells the exact path
    rows = data["__NEXT_DATA__"]["props"]["pageProps"]["products"]
```

### 4.6 CAPTCHA / bot walls

```python
with at.Session(stealth=True, headless=False, hitl="wait") as s:   # human solves it once
    s.goto("https://protected.example/")      # pauses, writes PAUSED.json, beeps, waits
    s.save_state("agenttrace_state/clearance.json")   # reuse the clearance later
```

`hitl` options: `"wait"` (default: pause until solved; create a `RESUME` file in the run folder to
continue manually), `"fail"` (raise `BlockedError` immediately), `"off"`, or a dict
`{"mode": "wait", "timeout_s": 300, "notify": callback, "on_pause": callback}`.
Detected vendors: Cloudflare, DataDome, PerimeterX, Imperva, Akamai, reCAPTCHA, hCaptcha, Turnstile, Arkose, GeeTest.

### 4.7 Politeness & rate limits

```python
s = at.Session(rate_limit={"min_interval_s": 1.5, "max_concurrency_per_domain": 2,
                           "per_domain": {"api.example.com": 3.0}})
```

A server `429`/`503` with `Retry-After` always wins over your delay; the limiter also *learns* the
pace that triggered a 429 and never goes faster again. A `Retry-After` longer than
`max_retry_after_s` (15 min) stops with `RateLimitError` instead of hammering.

### 4.8 A repeatable, resumable job (no code)

```bash
python agenttrace.py run site.json --out agenttrace_runs/site            # killed? just add --resume
python agenttrace.py run site.json --out agenttrace_runs/site --resume   # continues after the last finished step
```

See §7 for the config format (login with secrets, steps, pagination, extra pages, exports).

### 4.9 Record by hand, replay forever

```bash
python agenttrace.py record https://shop.example/ --out checkout.json   # browse, press Enter to stop
python agenttrace.py replay checkout.json --times 3                     # validates network per step
```

Recorded targets carry a fingerprint (text, role, attributes, position), so replays survive
renamed ids/classes and moved elements (self-healing).

### 4.10 Plain-language goals

```bash
python agenttrace.py login https://shop.example/login --save agenttrace_state/shop.json    # log in by hand, once
python agenttrace.py goal "search for 'lamp', open the first result, add it to the cart, open the cart, verify 'Your cart' is shown, go to the catalogue and extract all product names and prices" --url https://shop.example/ --state agenttrace_state/shop.json
```

Never write a real password into a goal (or any command line): shell history, process lists and AI
transcripts keep it. Log in with `login` + `--state` as above, or put the login steps in a config
with `${secret:NAME}` (§7).

Understood phrases: open/visit URL · log in as U with password P (throw-away test accounts only) · search for "q" · fill "v" in F ·
select "v" from F · check/uncheck X · add to cart / wishlist · open the first/second/Nth X ·
next page · download X · scroll to the bottom · wait for "t" · verify "t" · extract/scrape/collect X ·
go back · submit · go to X page · click X. For anything smarter, plug in your own planner:
`at.run_goal(goal, planner=my_llm_planner)` or `at.run_agent_loop(goal, decide)`.

### 4.11 API reverse-engineering & regression

```bash
python agenttrace.py capture https://app.example/ --har agenttrace_runs/app.har   # verified: waits for late APIs
python agenttrace.py endpoints agenttrace_runs/app.har --md                       # REST + GraphQL, params, schemas, auth, paging
python agenttrace.py diff agenttrace_runs/monday agenttrace_runs/today --md       # exit 4 = breaking API change
```

### 4.12 Mocking for tests

```python
with at.Session() as s:
    s.mock("/api/recommendations", json_body={"items": []})           # app gets this instead
    s.block("googletagmanager.com")                                    # request fails as blocked
    s.modify_response("/api/products", transform=lambda d: {**d, "items": d["items"][:2]})
    s.goto("https://shop.example/")
    print(s.interceptions())             # original request + injected response, per hit
```

### 4.13 Many sites in parallel, fully isolated

```python
pool = at.TaskPool(max_workers=3)
def job(url):
    def run():
        with at.Session(out_dir=f"agenttrace_runs/{at.slugify(url)}") as s:   # own browser, cookies, HARs
            s.goto(url)
            return s.extract_list()["items"]
    return (url, run)
results = pool.run([job(u) for u in urls])      # TaskResult(name, ok, value, error, …)
```

Use `at.Project("client-a")` to keep each project's login state, runs and exports in its own folder.

### 4.14 Offline, reproducible replays

```python
with at.Session(body_policy="all") as s:            # record once, with every body
    ...; har = s.finish()["paths"]["har"]
with at.Session(replay_har=har, deterministic=True) as s:   # later: served from the HAR, fixed clock/random
    ...
```

---

## 🐍 5. Python API reference

### 5.1 `Session(**options)`

| option | default | meaning |
|---|---|---|
| `out_dir` | `agenttrace_runs/<id>` | where artifacts go |
| `headless` / `stealth` | `True` / `False` | visible window · anti-detection profile |
| `storage_state` | — | login state file from `save_state()` |
| `rate_limit` | no delay, 4 navigations per host, Retry-After on | `float` seconds or dict (see §4.7) |
| `retry` | 3 attempts, backoff 0.4 s ×2 | `int` or `{"max_attempts", "backoff_ms", "factor", "retry_on"}` |
| `guardrails` | 1000 actions, 1 h, 5 fails in a row, 3 per target, 6 repeats | dict of those limits |
| `hitl` | `"wait"` | CAPTCHA handling (§4.6) |
| `evidence` | `"failures"` | `"important"` / `"all"`: screenshot + DOM + meta + HAR per action |
| `strict` | `True` | failed action raises `ActionError` (False → returns `ok: False`) |
| `redact` | `False` | mask secrets in written HARs |
| `body_policy` | `"auto"` | `"all"` keeps scripts/images too, `"none"` keeps no bodies |
| `debug` / `trace` | `False` | every request in `log.jsonl` · Playwright `trace.zip` |
| `deterministic` | `False` | fixed locale/timezone/viewport/clock/`Math.random` |
| `replay_har` | — | answer every request from a recorded HAR |
| `plugins` / `hooks` | — | plugin files/objects (§5.4) |
| `noise_rules` | built-in | extra noise/keep patterns for the clean HAR |
| profile fields | — | `proxy`, `user_agent`, `locale`, `timezone_id`, `viewport`, `device`, `extra_headers`, `throttle`, `geolocation`, `http_credentials`, `host_map`, `navigation_timeout_ms`, `action_timeout_ms`, `browser` (`chromium`/`firefox`/`webkit`), `channel`, `executable_path`, … |

### 5.2 Methods

| group | methods |
|---|---|
| navigate | `goto(url, expect=)` · `back()` · `forward()` · `reload()` · `capture(url)` (verified one-shot HAR) |
| act | `click(target)` · `fill(target, text, submit=)` · `type()` · `press(key)` · `select(target, value \| label=)` · `check()` · `uncheck()` · `hover()` · `upload(target, files)` · `download(target)` · `submit()` · `scroll(to="bottom")` · `dismiss_overlays()` |
| wait | `wait_for({"text" \| "selector" \| "url" \| "response" \| "request" \| "function" \| "idle" \| "download": …})` · `settle()` · `wait_for_response(pattern)` · `wait_for_human(reason)` |
| look | `observe()` (elements with refs) · `find(target)` · `inspect()` (forms, tabs, frames, cookies, storage, block status) · `text()` · `html()` · `screenshot()` · `evaluate(js)` |
| data | `extract(fields, item=)` · `extract_list()` · `detect_items()` · `detect_pagination()` · `paginate(mode=, max_pages=)` · `jsonld()` · `hydration_data()` |
| network | `fetch(url, params=, json_body=)` (API call with the browser's cookies) · `requests(pattern)` · `records(pattern)` · `response_json(pattern)` · `endpoints()` · `streams()` (SSE/chunks) · `websockets()` · `integrity()` · `routes()` · `har()` · `save_har(path)` · `save_route_hars(dir)` |
| control | `mock()` · `block()` · `modify_request()` · `modify_response()` · `unroute()` · `interceptions()` |
| state | `save_state(path)` · `storage()` (cookies, local/session storage, IndexedDB, Cache Storage) · `cookies()` · `pages()` · `switch_to("latest" \| "opener" \| "p2" \| url_part)` · `close_page()` · `detect_block()` |
| results | `actions()` · `report()` · `finish()` (writes everything, returns paths) · `close()` |

**Targets** (`click`, `fill`, …): plain words (`"Add to cart"`, `"Email"`, `"second result link"`,
`"Buy button for Linen Pillow"`), a ref from `observe()` (`"e12"`), CSS/XPath, or a recorded
fingerprint dict.

**Every action returns** (also in `report.json`):

```jsonc
{"id": "a008", "action": "click", "target": "Add to cart", "ok": true,
 "url": "http://shop…/product/walnut-desk-lamp_1/", "title": "Walnut Desk Lamp | ShopLab",
 "navigated": false, "duration_ms": 599, "attempts": 1,
 "resolved": {"strategy": "text", "score": 1.04, "ref": "e15", "role": "button", "name": "Add to cart",
              "context": "Walnut Desk Lamp £83.08 In stock …", "why": ["name='Add to cart'", "exact-case"]},
 "network": {"requests": 4,
             "primary": {"method": "POST", "url": "http://shop…/api/v1/cart", "status": 201, "ms": 13.2,
                         "request_shape": {"product_id": "number", "qty": "number"},
                         "response_shape": {"items": {"array": 1, "item": {…}}, "total": "number"}},
             "related": [{"method": "GET", "url": "…/api/v1/cart", "status": 200}], "background_or_noise": 2, "failed": []},
 "validation": {"ok": true, "checks": […]},
 // only when something went wrong:
 "error": "…", "error_kind": "not_found", "hint": "…", "candidates": […], "retries": […]}
```
(a real result from the self-test; `…` shortened)

`expect` accepts a string (`"POST /api/cart"`) or dicts with: `url`, `method`, `status` (`201`,
`"2xx"`, list), `request_json`, `request_has`, `request_form`, `request_headers`, `request_files`
(multipart: `{name: {"sha256"|"size"|"filename": …}}`), `response_json` (subset match, `"<int>"`,
`"re:…"` placeholders), `response_has`, `response_contains`, `response_headers`, `response_sha256`,
`response_size`, `body_complete`, `require_timings`, `min_count`, `max_count`.

### 5.3 Other building blocks

| area | names |
|---|---|
| discovery & export | `discover_endpoints(records)` · `endpoints_markdown()` · `export_all(records, dir)` · `to_postman()` · `to_openapi()` · `to_python_client()` · `to_curl()` · `replay_postman()` |
| HAR | `load_har` · `save_har` · `build_har` · `har_to_records` · `validate_har` · `validate_har_file` · `body_integrity` · `parse_multipart` |
| analysis | `validate_expectations` · `correlate` · `compare_endpoints` · `regression_markdown` · `run_fingerprint` · `diff_fingerprints` · `infer_schema` |
| noise & privacy | `NoiseRules` · `classify_records` · `filter_records` · `noise_report` · `Redactor` · `redact_har` |
| crawling | `crawl(session, urls)` · `capture_url` · `verify_capture` · `recon(session, url)` · `find_item_lists(json)` |
| workflows | `run_config` · `WorkflowRunner` · `WorkflowRecorder` · `replay` · `plan_goal` · `run_goal` · `run_agent_loop` · `load_config` |
| scale & safety | `HttpClient` · `RateLimiter` · `TaskPool` · `Guardrails` · `RetryPolicy` · `HumanInTheLoop` · `detect_block` |
| storage | `ArtifactStore` (versioned runs, manifests, verify, compare) · `Project` (isolated workspace) |
| engine | `BrowserEngine` · `BrowserProfile` · `NetworkRecorder` · `HookManager` · `EventLog` |
| AI tools | `TOOLS` · `tool_catalog("anthropic" \| "openai" \| "mcp")` · `dispatch_tool(session, name, args)` · `MCPServer` |
| errors | `AgentTraceError` → `ActionError`, `ElementNotFoundError`, `NavigationError`, `BlockedError`, `GuardrailViolation`, `ActionVetoed`, `RateLimitError`, `ConfigError`, `ValidationFailed` — each has `.hint` and `.to_dict()` |

### 5.4 Hooks & plugins (extend without touching the file)

```python
# safety_plugin.py — load with Session(plugins=["safety_plugin.py"]) or "plugins": [...] in a config
import re

IRREVERSIBLE = re.compile(r"place order|buy now|checkout|pay\b|confirm purchase|book now|send|submit order"
                          r"|delete|remove account|unsubscribe|transfer", re.I)

def pre_action(session, action):              # before every action; return {"veto": "why"} to block it
    if action["action"] in ("click", "press") and IRREVERSIBLE.search(action["target"]):  # submit() runs as click/press
        return {"veto": "irreversible action - needs the user's explicit confirmation"}

def request_finished(session, record):        # after every request, body available
    if "/api/" in record.url:
        record.tags.append("api")             # shows up in the HAR (_agenttrace.tags)

def output(session, paths, report):           # after all artifacts are written
    ...
```

Events: `session_start`, `session_end`, `pre_action`, `post_action`, `request`, `response`,
`request_finished`, `validation`, `output`, `error`, `blocked`, `page`, `download`.

---

## ⌨️ 6. CLI reference

`python agenttrace.py <command> [options]` — every command prints one JSON object on stdout
(logs go to stderr), so an AI can parse the result directly.

| command | what it does | key options |
|---|---|---|
| `doctor` | checks Python, Playwright, browser launch, write access | `--online` |
| `recon URL` | strategy + lists + pagination + JSON APIs + robots.txt → `recon.md` (stealth on) | `--scroll N --no-stealth` |
| `extract URL` | list extraction (auto or `--item/--field`), optional pagination | `--paginate --mode --max-pages --limit --output x.csv\|json\|jsonl` |
| `capture URL` | verified HAR of one page | `--har --expect PATTERN --quiet-ms` |
| `crawl URL…` | one verified HAR per page | `--file urls.txt` |
| `run CONFIG` | run a workflow/site config | `--resume --headed --debug --trace --out` |
| `record URL` | record your browsing into a workflow | `--out wf.json` |
| `replay WF` | replay with per-step network validation | `--times N` |
| `goal "…"` | plain-language task | `--url START` |
| `login URL` | manual login in a window, save state | `--save agenttrace_state/site.json` |
| `endpoints HAR` | API endpoints of any HAR (also DevTools HARs) | `--md --out` |
| `export HAR` | Postman + OpenAPI + Python client + curl | `--out DIR --name` |
| `redact HAR` | mask secrets for sharing | `--out` |
| `validate HAR` | structure check or `--expect "POST /api/cart"` / `--spec file.json` | |
| `diff A B` | API regression between two HARs / run dirs | `--md` |
| `noise HAR` | noise report, `--out` clean HAR | |
| `mcp` | MCP server over stdio | `--headed --stealth --log-file` |
| `tools` | AI tool catalog | `--format anthropic\|openai\|mcp` |
| `selftest` | the real-world test-suite | `--quick --part N -k NAME --live auto\|on\|off --out` |

Browser options on most commands: `--headed --stealth --proxy URL --state FILE --header "K: V"
--ua --locale --timezone --rate SECONDS --profile JSON|FILE --out DIR`.

Exit codes: `0` ok · `1` usage/setup problem · `2` failed (action, validation, workflow, empty
extraction) · `3` redaction found leaks · `4` breaking API change (`diff`) · `130` interrupted.

---

## 🧩 7. Workflow configs (JSON / YAML / TOML)

```json
{
  "name": "daily-news",
  "base_url": "https://news.example/",
  "rate_limit": {"min_interval_s": 1},
  "auth": {
    "state_file": "agenttrace_state/news-auth.json",
    "check": {"url": "/members/", "text": "Members area"},
    "login": [
      {"goto": "/wp-login.php"},
      {"fill": {"target": "Username or Email Address", "text": "${env:NEWS_USER}"}},
      {"fill": {"target": "Password", "text": "${secret:NEWS_PASS}"}},
      {"click": "Log In"}
    ]
  },
  "steps": [
    {"goto": "/"},
    {"paginate": {"mode": "next", "max_pages": 5, "item": "article.post",
                  "fields": {"title": "h2 a", "url": "h2 a@href", "date": "time@datetime"}},
     "save_as": "articles"},
    {"click": "Subscribe", "optional": true},
    {"goto": "/members/", "expect": {"url": "/members/", "status": 200}}
  ],
  "pages": ["https://news.example/page/5/"],
  "outputs": {"export": true, "redact": true}
}
```

* **Step actions:** `goto click fill type select check uncheck press hover scroll wait_for extract
  extract_list paginate download screenshot save_state capture assert back reload switch_tab
  close_tab mock block sleep_ms upload submit set dismiss_overlays wait_for_human`.
  Short form works too: `"click Add to cart"`.
* **Step options:** `id name expect wait optional retry save_as comment timeout_ms`.
* **Top-level keys:** `name description start_url base_url profile auth rate_limit noise retry
  guardrails hitl plugins steps pages outputs vars evidence settle_quiet_ms deterministic debug
  trace redact body_policy secrets_file`.
* **Variables:** `${env:NAME}`, `${secret:NAME}` (from `AGENTTRACE_SECRET_NAME` or `secrets_file`),
  `${name}` / `${name.field}` (from `vars` or an earlier step's `save_as`).
* **Checkpoints:** every finished step is saved to `state.json`; `--resume` restores cookies/storage
  and continues after the last finished step — nothing already done runs twice.
* Output: `run_summary.json`, `data.json` (all `save_as` data), `session/…` (§9), `export/…`.

---

## 🔌 8. MCP server (Claude Code · Claude Desktop · Cline)

```bash
claude mcp add agenttrace -- python /abs/path/agenttrace.py mcp          # Claude Code
```

```json
{ "mcpServers": { "agenttrace": {
    "command": "python",
    "args": ["C:\\path\\to\\agenttrace.py", "mcp", "--out", "C:\\path\\to\\runs"] } } }
```

Tools (26): `goto observe click fill select check press scroll wait_for inspect extract
paginate requests response_json endpoints capture_page recon page_data fetch screenshot back
switch_tab save_state detect_block export finish`.

One browser session per server; results are compact JSON; `finish` writes all artifacts and returns
their paths. Add `--headed` to watch, `--stealth` for protected sites. The same tools can be given
to any LLM API directly: `at.tool_catalog("anthropic" | "openai")` + `at.dispatch_tool(s, name, args)`.

---

## 📁 9. What a run writes

```
agenttrace_runs/<session-id>/
├── report.md / report.json     ← start here: actions, targets, requests, validation, errors + hints
├── session.har                 ← everything (HAR 1.2, bodies, _agenttrace metadata, WebSocket messages)
├── clean.har                   ← without analytics/ads/trackers (deduplicated, tagged)
├── actions/a003_click.har      ← one HAR per action
├── routes/03_s3_products.har   ← one HAR per page / SPA route (+ index.json)
├── endpoints.json / .md        ← discovered REST/GraphQL APIs with schemas, params, auth, paging
├── timeline.jsonl              ← every event in order (actions, requests, routes, dialogs, downloads …)
├── log.jsonl                   ← structured log; failed actions name the request + error
├── console.json                ← console errors, page errors, failed resources, dialogs
├── pages.json                  ← tabs/popups/frames lifecycle + per-page request counts
├── streams.json / websockets.json / interceptions.json   ← when present
├── evidence/a004/              ← screenshot.png + dom.html + meta.json + network.har
├── downloads/                  ← files + downloads.json (name, MIME, size, sha256, request)
├── recon.json / recon.md       ← after recon
└── manifest.json               ← every file with size + sha256
```

`ArtifactStore` adds `<store>/<workflow>/<run_id>/manifest.json`, `workflow.json`,
`fingerprint.json` and a global `index.json` (history, versions, compare, verify).

⚠️ HARs of logged-in runs contain live cookies, tokens and request bodies. They are meant for your
machine: keep the run folders git-ignored, and share only a masked copy
(`python agenttrace.py redact session.har --out safe.har`, or record with `Session(redact=True)`).

---

## 🩺 10. Troubleshooting

| symptom | fix |
|---|---|
| `doctor`: chromium launch FAIL | `python -m playwright install chromium` (Linux: `install --with-deps`) |
| `ElementNotFoundError` | read `candidates` in the error; call `s.observe()`; use a ref `"e12"` or more words |
| action "succeeds" but nothing happened | add `expect={…}` to that action; check `network.primary` |
| `BlockedError` / `unblock-first` | `stealth=True, headless=False, hitl="wait"`, slower `rate_limit`, reuse `save_state()` |
| `GuardrailViolation` | the agent was looping — re-plan with `observe()` instead of repeating |
| `RateLimitError` | the server asked for a very long pause; try later or raise `max_retry_after_s` |
| HAR has no body for images/scripts | default `body_policy="auto"`; use `body_policy="all"` |
| login lost between runs | `save_state()` after login; `Session(storage_state=…)`; IndexedDB is included |
| `Sync API inside asyncio loop` (Jupyter) | run from a normal script/thread, or `TaskPool` |
| Windows console shows `?` | `set PYTHONIOENCODING=utf-8` (outputs are UTF-8 files anyway) |
| a run died half-way | `run CONFIG --resume` |

---

## 🧪 11. Tested: how and results

`python agenttrace.py selftest` starts a local **fixture internet** inside the file — an e-commerce
shop (400 products, REST + GraphQL, CSRF, carts, orders, logins), a WordPress-style news site, a
React Router SPA (real React bundles), a Next.js-style page, a Cloudflare-style CAPTCHA wall, an
IndexedDB login app, a lab with SSE, WebSocket, flaky/slow/429 endpoints, frames and popups, plus
25 real third-party tracker hostnames — and Chromium reaches all of them under their real-looking
names. Every test maps to one success criterion of `knowledge.md`; tests marked *live* use real
websites and run automatically when the internet is reachable.

```
python agenttrace.py selftest              # everything (~18 min)
python agenttrace.py selftest --quick      # fewer repetitions
python agenttrace.py selftest --part 39    # one part     ·   -k websocket   (by name)
```

**Result of the single-file build in this repository** (Linux x86-64, Python 3.11.15, Playwright 1.56.0, Chromium 141 headless, 2026-09-26):
**51 passed, 0 failed, 2 skipped** in 18 min — and the same file on
Python 3.13 (`--quick`): **51 passed, 0 failed, 2 skipped (live)**.

| part | spec | test | result | time | measured |
|---:|---|---|:---:|---:|---|
| 1 | Core Browser Automation Engine | `engine_six_sites` — Launch browser, open 6 different sites, wait for full load, report title/URL correctly | ✅ pass | 1.3s | sites=6 |
| 1 | Core Browser Automation Engine | `engine_live_sites` — Live: 5 real websites load with correct title/URL (no errors) | ⏭️ skip (live) | 0s | needs the public internet (blocked by this build environment's network policy) |
| 2 | Basic Network Capture (Per Page Load) | `har_matches_devtools` — Page visit → valid HAR 1.2 whose entry count equals the DevTools (CDP) request count | ✅ pass | 7.8s | entries_per_page=[46,56,39,1,37] |
| 3 | Interaction-Aware Capture (Before/After Click) | `click_before_after` — Click-triggered API call appears in the post-click HAR and not in the pre-click HAR | ✅ pass | 6.3s |  |
| 4 | Reliability & Verification Layer | `reliability_slow_api` — Delayed + slow API page: 10 consecutive captures are complete (0% premature/empty HAR) | ✅ pass | 42.2s | complete=10/10, premature_rate=0%, waited_ms=3818-3860 |
| 5 | Multi-page Site Crawl Orchestration | `crawl_eight_pages` — Crawl 8 pages → 8 distinctly named HARs, none skipped, visited/failed summary | ✅ pass | 9.6s | pages=8 |
| 5 | Multi-page Site Crawl Orchestration | `crawl_with_failure` — Crawl with an unreachable page: it is attempted and reported as failed, nothing skipped | ✅ pass | 6.1s |  |
| 6 | AI Agent Action Interface | `agent_tool_calls` — Plain instruction via tool calls only (no selectors): product page → add to cart → correct HAR | ✅ pass | 3.4s |  |
| 7 | MCP Server Wrapper | `mcp_server` — MCP stdio server: handshake, tools/list, navigate+capture returns a HAR path, error handling | ✅ pass | 6.6s | tools=26 |
| 8 | Auth/Session Handling | `auth_restart` — Log in once, save state, restart (new process) → logged in without logging in again | ✅ pass | 5.9s |  |
| 9 | Anti-detection / Stealth Layer | `stealth_botcheck` — Bot-detection page: plain headless is blocked, stealth profile passes with 0 automation tells | ✅ pass | 2.4s | tells_plain=7, tells_stealth=0 |
| 9 | Anti-detection / Stealth Layer | `stealth_live` — Live: stealth capture on real bot-detection sites without block/CAPTCHA (≥2 of 3) | ⏭️ skip (live) | 0s | needs the public internet (blocked by this build environment's network policy) |
| 10 | Output Management (Dedup, Tagging, Report) | `output_clean_har` — Analytics-heavy site: clean HAR keeps only relevant calls (deduped, tagged) + counts summary | ✅ pass | 3.4s | full_entries=126, clean_entries=9, third_party_noise=95, dedup_groups=1 |
| 11 | Config & Extensibility | `config_new_site` — Config-only onboarding: a brand-new site (WordPress-like) works from a config file, no code changes | ✅ pass | 14.7s | articles=50 |
| 12 | Security & Data Redaction | `redaction` — Auth-protected capture → redacted HAR contains no raw password/token/cookie values | ✅ pass | 3.5s | secrets_masked=9 |
| 13 | Action & Network Correlation Engine | `correlation_accuracy` — Background polling + trackers + lazy images: click→target request identified in ≥95% of trials | ✅ pass | 55.7s | accuracy=100.0% (40/40), background_polls=136 |
| 14 | Advanced HAR Validation Engine | `advanced_validation` — Expected request/payload/response/timings validate; each missing field yields a clear reason | ✅ pass | 2.1s | mutations_detected=8/8 |
| 15 | Browser State & Page State Inspection | `state_inspection` — Structured state snapshot (tabs, frames, forms, cookies, storage, elements) identifies target + step | ✅ pass | 4.5s | elements_step1=6 |
| 16 | Intelligent Wait & Event Synchronization | `intelligent_waits` — Same workflow ×20 with fast/medium/slow backends: no premature action, no race (stale results) | ✅ pass | 94.4s | runs=20, avg_s={"fast":2.68,"medium":4.11,"slow":7.68} |
| 17 | Retry, Recovery & Failure Handling | `retry_recovery` — Injected 503/reset/timeout/detached/overlay/alert/500 failures ×20 runs → ≥95% recover automatically | ✅ pass | 314.7s | recovered=20/20 (100%), retries_by_kind={"http_5xx":20,"network":20,"timeout":20,"expectation":20}, in_attempt_recoveries={"detached":20,"overlay":20} |
| 18 | Download & File Capture Engine | `downloads` — CSV / JSON / PDF (+blob, +POST, +auth exports) downloads saved with filename/MIME/size/network metadata | ✅ pass | 9.1s | downloads=8 |
| 19 | Console, Error & Runtime Monitoring | `console_monitoring` — Console errors + uncaught exceptions + failed resources captured with timestamp & action context | ✅ pass | 1.7s | console_entries=9 |
| 20 | Workflow Recording Engine | `workflow_recording` — Manual 10-step session (real mouse/keyboard) → ≥90% of actions recorded in order with targets; replayable | ✅ pass | 14.0s | recorded_in_order=10/10 |
| 21 | Workflow Replay Engine | `replay_ten_times` — Replay a recorded workflow ×10: identical step order and all network validations pass every time | ✅ pass | 90.3s | passed=10/10, validations=50/50 |
| 22 | Self-Healing Element Resolution | `self_healing` — Selectors/ids/classes/structure changed: ≥90% of recorded actions still hit the right element | ✅ pass | 8.8s | healed_ok=11/11 (100%) |
| 23 | Pagination & Dynamic Content Engine | `pagination` — 20-page pagination (400 items) + infinite scroll (100 items) + load-more: all content, per-step capture | ✅ pass | 30.3s | catalogue_items=400, feed_items=100 |
| 24 | API & Endpoint Discovery | `api_discovery` — Discovery report finds ≥90% of the app's known REST + GraphQL endpoints with method/URL/params | ✅ pass | 17.5s | found=15/15 (100%) |
| 25 | Network Noise Classification & Smart Filtering | `noise_filtering` — Analytics-heavy pages: 100% of app API calls kept, ≥95% of noise filtered; rules are configurable | ✅ pass | 3.4s | noise_requests=92, noise_filtered=100.0%, app_api_kept=10/10 |
| 26 | AI-Friendly Structured Output | `ai_output` — report.json alone (no HAR parsing) gives each action's target, related request and validation status | ✅ pass | 5.0s | report_vs_har=9.1% |
| 27 | Task Orchestration & State Management | `checkpoint_resume` — 30-step workflow killed mid-run, restarted with --resume: continues from checkpoint, no completed step repeated | ✅ pass | 19.8s | killed_after_steps=12, server_counts_before_resume=12 |
| 28 | Screenshot & Evidence Capture | `evidence` — Every important action has screenshot + DOM + metadata + network HAR under the same identifier | ✅ pass | 6.2s |  |
| 29 | Execution Timeline & Audit Report | `timeline` — Timeline/report give start, end, action, network activity and final status of any step | ✅ pass | 5.1s | events=63 |
| 30 | Network Regression & Change Detection | `regression` — A known API change (price number→string, rating→stars, +currency) is pinpointed; identical runs report nothing | ✅ pass | 11.5s | endpoints=7, flagged=3/3, breaking=6 |
| 31 | Project & Session Isolation | `isolation` — 3 projects run concurrently (own login, cart, workflow, HAR, outputs): nothing leaks between them | ✅ pass | 10.6s | projects=3, peak_parallel=3 |
| 32 | Configurable Hooks & Plugin Architecture | `plugins` — Plugin file (config only, no core change) adds pre-click + post-request + output logic that really runs | ✅ pass | 3.9s | api_calls_tagged=7 |
| 33 | Observability & Debug Mode | `observability` — Failed workflow: log.jsonl alone names the failing step, action, request and error (no replay needed) | ✅ pass | 7.2s | log_lines=309 |
| 34 | Concurrent Task & Resource Management | `concurrency` — 5 independent browser tasks with max 3 in parallel: separate results, HARs and reports, no state mixing | ✅ pass | 6.0s | wall_s=6.0, serial_s=13.1, peak=3 |
| 35 | End-to-End AI Task Execution | `goal_e2e` — Plain-language multi-page goal → plan, act, capture, validate, report with evidence (no selectors/browser code) | ✅ pass | 11.1s | steps=12, rows=20 |
| 36 | SPA / Client-side Route Change Detection | `spa_routes` — React Router SPA: 6 route changes without reload → 6 virtual pages, each with its own network segment + HAR | ✅ pass | 4.4s | framework=react-router, virtual_pages=6 |
| 37 | CAPTCHA & Blocking Detection with Human-in-the-loop Escalation | `captcha_hitl` — CAPTCHA wall → detected, paused, operator notified; after a human solve the same session resumes the workflow | ✅ pass | 10.0s | hitl_waited_s=0.83 |
| 38 | AI Agent Guardrails (Loop Prevention & Budget Control) | `guardrails` — Unsolvable target, no-op loops, step/time budgets: execution stops by itself with a clear reason | ✅ pass | 19.4s | agent_steps=4 |
| 39 | Structured API Export (Postman/OpenAPI & Reusable Client Stub) | `export` — Captured workflow → Postman collection replays ≥90% (built-in + real Newman); OpenAPI + Python client + curl work | ✅ pass | 15.0s | newman=14/14, postman_items=14, replay_rate=1.0 |
| 40 | Target-Site Load & Rate Respect | `rate_limits` — 429 + Retry-After is obeyed over a too-fast configured delay: 0 requests inside the ban, per-host cap holds | ✅ pass | 26.5s | violations=0, retry_after_gaps=[2.02,2.02] |
| 41 | Streaming / SSE & Long-lived Connection Capture | `sse_streams` — SSE + chunked stream: every event/chunk captured in sequence with real-time timestamps (HAR + streams.json) | ✅ pass | 2.4s | sse_events=11, chunks=5 |
| 42 | WebSocket & Bi-directional Network Capture | `websocket` — WebSocket: handshake, lifecycle and ≥10 messages each way with direction, timestamp and connection id | ✅ pass | 1.1s | sent=10, received=13 |
| 43 | Network Interception, Mocking & Controlled Response | `mocking` — Mock / block / modify: the app uses the injected data; report shows original request + injected response | ✅ pass | 1.9s | rules=4 |
| 44 | Request/Response Body Integrity & Content Decoding | `body_integrity` — gzip JSON, PNG, multipart upload, chunked text: byte-exact; truncated bodies fail validation with a clear reason | ✅ pass | 1.3s | byte_exact=4, flagged=2 |
| 45 | Multi-Tab, Popup, Window & Frame Lifecycle Management | `tabs_frames` — Popup + new tab + nested iframes: lifecycle tracked and every request tied to its page/frame | ✅ pass | 3.2s | pages=3, frames_p1=3 |
| 46 | Complete Browser Storage State Management | `storage_state` — Cookie + localStorage + IndexedDB (+sessionStorage) login survives a browser restart via exported state | ✅ pass | 8.2s | stores=["cookie","localStorage","IndexedDB","sessionStorage"] |
| 47 | Proxy, Network Profile & Environment Control | `network_profiles` — Two sessions, two proxies/headers/UA/locale/timezone/throttle profiles at once: each goes out as configured, no leak | ✅ pass | 7.3s | alpha_requests=100, beta_requests=102 |
| 48 | Artifact Storage, Manifest & Versioned Execution History | `artifact_store` — Workflow run 5× (2 versions): every run's HAR/screenshot/log/report locatable; history rebuilt from manifests | ✅ pass | 15.3s | runs=5, versions=2 |
| 49 | Deterministic Test Fixtures & Reproducible Replay | `deterministic` — Same fixture + workflow ×20: identical actions, network and validations; an intentional change shows only itself | ✅ pass | 113.7s | runs=20, distinct_fingerprints=1 |
| 50 | AI scraping assistant (recon → strategy → data) | `ai_assistant` — AI assistant: doctor OK; recon picks the right strategy on 8 kinds of pages and each strategy yields the data | ✅ pass | 20.3s | strategies={"catalogue":"html-list","feed":"api","deals":"api","spa":"api","next":"hydration","captcha":"unblock-first","news":"html-list","search":"api"} |

> ⚠️ **Live tests** (5 real sites incl. books.toscrape.com, python.org, wikipedia.org and real
> bot-detection pages) were **skipped in this build environment** because its network policy
> blocks the public internet. On a normal machine `selftest` runs them automatically
> (`--live on` forces them). Everything else ran for real: real Chromium, real HTTP, real
> React, real Newman (`npm i -g newman`) — nothing is mocked inside the module.

Bugs found by these tests and fixed on the way include: CDP attach racing the first navigation
(lost SSE/WebSocket/initiator events), `FormData` file uploads missing from HARs, `route.fetch`
ignoring `host_map`, blocked-event logging crashing on a keyword clash, popups' first URL not
recorded, rate limiter re-probing a pace that already earned a 429, and `fill()` not firing
`change` handlers (quantity boxes, filters).

---

## 🧱 12. Inside the single file

`agenttrace.py` is organised in sections (search for `# <name>.py —`):

```
┌────────────────┬──────────────────────────────────────────────────────┬─────────────────────┐
│ section        │ what                                                 │ knowledge.md parts  │
├────────────────┼──────────────────────────────────────────────────────┼─────────────────────┤
│ core           │ errors, time/ids, files, URLs, logging, events, hooks│ 11, 32, 33          │
│ har            │ records, HAR 1.2 build/load/validate, integrity      │ 2, 10, 44           │
│ stealth        │ anti-detection                                       │ 9                   │
│ noise          │ analytics/ads/tracker classification, clean HAR      │ 10, 25              │
│ redact         │ secret masking, leak finder                          │ 12                  │
│ engine         │ browser launch + profiles (proxy, UA, locale, …)     │ 1, 47               │
│ recorder       │ requests, pages, frames, SPA routes, SSE, WS, console│ 2, 3, 19, 36, 41-45 │
│ blocking       │ CAPTCHA / WAF detection, human-in-the-loop           │ 37                  │
│ concurrency    │ rate limiter, Retry-After, HttpClient, TaskPool      │ 34, 40              │
│ elements       │ plain-word targets, refs, self-healing               │ 6, 22               │
│ waits          │ network/DOM/timer-aware settling                     │ 4, 16               │
│ analysis       │ correlation, expectations, API discovery, diffs      │ 13, 14, 24, 30, 49  │
│ crawl          │ verified capture, crawl, lists, pagination           │ 4, 5, 23            │
│ session        │ Session: the AI-facing API                           │ 3, 6, 8, 15, 17, 18,│
│                │                                                      │ 26, 28, 29, 43, 46  │
│ export         │ Postman, OpenAPI, Python client, curl                │ 39                  │
│ recon          │ scraping strategy advisor                            │ (AI assistant)      │
│ mcp            │ tool catalog + MCP stdio server                      │ 6, 7                │
│ workflow       │ configs, checkpoints, record/replay, goals           │ 11, 20, 21, 27, 35  │
│ artifacts      │ projects, artifact store, manifests, history         │ 31, 48              │
│ fixture_*      │ the local test internet                              │ (self-test)         │
│ selftest / cli │ test-suite, command line                             │ all                 │
└────────────────┴──────────────────────────────────────────────────────┴─────────────────────┘
```

`knowledge.md` in this repository is the specification (49 parts, each with success criteria);
the self-test is its executable form.

---

## 🇧🇩 13. বাংলায় সংক্ষেপে

**AgentTrace** একটাই Python file (`agenttrace.py`) — web scraping-এর জন্য AI-এর সহকারী module।

* **কী করে:** আসল Chromium browser চালায়, প্রতিটি page/click/SPA route-এর network traffic
  verify করে HAR-এ রাখে, site-টা কীভাবে scrape করা উচিত (`recon`: JSON API / hydration /
  HTML list / CAPTCHA) বলে দেয়, data extract করে (pagination সহ), API-র Postman/OpenAPI/Python
  client বানায়, login state save/restore করে, CAPTCHA এলে মানুষের জন্য অপেক্ষা করে, 429/Retry-After
  মেনে ধীরে চলে, SSE/WebSocket capture করে, এবং প্রতিটা কাজের report/evidence রেখে যায়।
* **শুরু করতে:** `pip install playwright` → `python -m playwright install chromium` →
  `python agenttrace.py doctor` → `python agenttrace.py recon <URL>` → recon.md-এর strategy মেনে কাজ।
* **AI-কে কী বলবেন:** "agenttrace.py ব্যবহার করো, আগে recon চালাও, README-এর §1 নিয়ম মেনে চলো।"
  §1.4-এর অংশটা আপনার project-এর `CLAUDE.md`-তে paste করে দিন।
* **Test:** `python agenttrace.py selftest` — knowledge.md-এর ৪৯টা part + AI-assistant part-এর
  success criteria real browser-এ যাচাই করে। এই repository-র build-এ 51টা test pass
  করেছে; live (আসল website) test-গুলো এই environment-এর network policy-র কারণে skip হয়েছে —
  আপনার computer-এ internet থাকলে নিজে থেকেই চলবে।

---

<div align="center">

```
 ░░▒▒▓▓██  scrape politely · respect robots.txt and terms of service · keep secrets out of HARs  ██▓▓▒▒░░
```

</div>
