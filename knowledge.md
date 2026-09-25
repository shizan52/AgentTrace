# AI-Powered Network/HAR Capture Module — Development Knowledge Base

প্রতিটা Part আলাদাভাবে বানাও এবং Success Criteria দিয়ে টেস্ট করে তারপর পরের Part-এ যাও।

---

## Part 1 — Core Browser Automation Engine
**কাজ:** Playwright/Puppeteer দিয়ে headless/headful browser control এবং একটা page navigate করতে পারা।

**Success Criteria:** একটা script কোনো URL open করে page load সম্পূর্ণ না হওয়া পর্যন্ত wait করবে, তারপর page title/URL সঠিকভাবে print করবে। ৫টা ভিন্ন website-এ কোনো error ছাড়া কাজ করলে এই Part pass।

---

## Part 2 — Basic Network Capture (Per Page Load)
**কাজ:** একটা page visit করলেই সব network request/response capture হয়ে standard `.har` file-এ save হবে।

**Success Criteria:** Generated `.har` file একটা HAR validator-এ valid হিসেবে pass করবে, এবং browser DevTools-এর Network tab-এর request count-এর সাথে HAR file-এর entry count মিলবে।

---

## Part 3 — Interaction-Aware Capture (Before/After Click)
**কাজ:** কোনো click event-এর আগে ও পরের network state আলাদাভাবে capture করতে পারা।

**Success Criteria:** টেস্ট page-এ একটা button click করলে যে নতুন API call trigger হয়, সেটা post-click HAR-এ থাকবে কিন্তু pre-click HAR-এ থাকবে না — manually verify করে এই difference সঠিক পাওয়া গেলে pass।

---

## Part 4 — Reliability & Verification Layer
**কাজ:** প্রতিটা action-এর পর capture সঠিকভাবে সম্পন্ন হয়েছে কিনা automatic নিশ্চিত করা (network-idle detection, empty-capture check)।

**Success Criteria:** Delayed/slow API আছে এমন একটা page-এ ১০ বার পরপর রান করলে ১০ বারই সঠিক, non-empty capture আসবে (0% premature/empty HAR)।

---

## Part 5 — Multi-page Site Crawl Orchestration
**কাজ:** একাধিক page-এর list দিলে module নিজে নিজে সব page visit করে প্রতিটার জন্য আলাদা HAR তৈরি করবে।

**Success Criteria:** ৫–১০ page-এর list দিলে প্রতিটার জন্য আলাদা নামের HAR file তৈরি হবে, কোনো page skip হবে না, এবং শেষে visited/failed count-সহ summary পাওয়া যাবে।

---

## Part 6 — AI Agent Action Interface
**কাজ:** AI (Claude/Cline)-কে দেওয়ার জন্য high-level function সেট বানানো — `goto()`, `click()`, `captureSnapshot()` ইত্যাদি।

**Success Criteria:** Claude/Cline-কে শুধু plain instruction দিয়ে ("product page-এ যাও, add to cart click করো") বললে agent নিজে থেকে function call করে কাজ শেষ করবে এবং সঠিক HAR তৈরি হবে — কোনো manual selector/code ছাড়াই।

---

## Part 7 — MCP Server Wrapper
**কাজ:** পুরো module-কে MCP server হিসেবে expose করা যাতে Claude Desktop/Cline সরাসরি tool হিসেবে call করতে পারে।

**Success Criteria:** Claude Desktop-এ MCP server connect করে চ্যাট থেকে সরাসরি navigate + capture command দিলে tool call successful হবে এবং output HAR file-এর path ফেরত আসবে।

---

## Part 8 — Auth/Session Handling
**কাজ:** Login লাগে এমন site-এ auth state (cookie/token) save ও reuse করতে পারা।

**Success Criteria:** একবার login করে session save করার পর, module restart করলেও নতুন login ছাড়াই logged-in state-এ page access করা যাবে।

---

## Part 9 — Anti-detection / Stealth Layer
**কাজ:** Bot-detection থাকা site-এ block না হয়ে navigation + capture সম্পন্ন করা।

**Success Criteria:** bot-detection-সম্পন্ন কমপক্ষে ২–৩টা real-world site-এ block/CAPTCHA ছাড়া সফলভাবে capture সম্পন্ন হবে।

---

## Part 10 — Output Management (Dedup, Tagging, Report)
**কাজ:** Duplicate/noise request (analytics, ads, static assets) বাদ দিয়ে clean HAR + human-readable summary report তৈরি করা।

**Success Criteria:** Heavy analytics-traffic থাকা একটা site capture করলে output HAR-এ noise filter হয়ে শুধু relevant API call থাকবে, এবং শেষে page/request/fail count-সহ summary পাওয়া যাবে।

---

## Part 11 — Config & Extensibility
**কাজ:** JSON/YAML config দিয়ে site-specific login/page/action define করা এবং plugin দিয়ে custom logic যোগ করা।

**Success Criteria:** শুধু config file পরিবর্তন করে (core code পরিবর্তন ছাড়াই) সম্পূর্ণ নতুন একটা site-এ module কাজ করবে।

---

## Part 12 — Security & Data Redaction
**কাজ:** HAR output-এ sensitive data (Authorization header, cookie, token) automatically mask/redact করা।

**Success Criteria:** Auth-protected site থেকে capture করা HAR file third-party-কে দিলেও তাতে raw token/password/cookie value দেখা যাবে না — শুধু masked placeholder থাকবে।

---

## Part 13 — Action & Network Correlation Engine
**কাজ:** কোন browser action (click, type, navigation, submit) করার কারণে কোন network request/response তৈরি হয়েছে সেটা automatically correlate করা।

**Success Criteria:** Test page-এ একটি নির্দিষ্ট button click করলে pre-action ও post-action network activity compare করে অন্তত ৯৫% ক্ষেত্রে click-এর সাথে সম্পর্কিত target request সঠিকভাবে identify করতে পারবে।

---

## Part 14 — Advanced HAR Validation Engine
**কাজ:** শুধু HAR file valid কিনা নয়, expected request/response capture হয়েছে কিনা, request body/header/response এবং timing তথ্য complete কিনা সেটা automatically verify করা।

**Success Criteria:** Known test workflow-এর expected API request-এর জন্য request URL, method, payload এবং response উপস্থিত থাকলে validation pass এবং যেকোনো required field missing থাকলে clear failure reason দেখাবে।

---

## Part 15 — Browser State & Page State Inspection
**কাজ:** AI বা automation layer-এর জন্য current URL, title, DOM state, visible elements, active tab, iframe, cookies এবং relevant storage state structured format-এ expose করা।

**Success Criteria:** একটি dynamic website-এ যেকোনো action-এর আগে module current browser/page state-এর structured snapshot দিতে পারবে এবং তা দিয়ে target element ও current navigation state identify করা যাবে।

---

## Part 16 — Intelligent Wait & Event Synchronization
**কাজ:** fixed `sleep()`-এর পরিবর্তে DOM change, selector state, network response, navigation, download এবং loading state অনুযায়ী intelligent waiting এবং synchronization করা।

**Success Criteria:** fast, medium এবং slow response-এর একই workflow ২০ বার চালালেও premature action বা race-condition ছাড়া সফলভাবে complete হবে।

---

## Part 17 — Retry, Recovery & Failure Handling
**কাজ:** timeout, navigation failure, detached element, failed request, unexpected popup বা temporary network error হলে automatic retry, recovery এবং controlled failure handling করা।

**Success Criteria:** intentionally injected transient failure থাকা test workflow ২০ বার run করলে predefined retry policy অনুযায়ী কমপক্ষে ৯৫% run manual intervention ছাড়া successfully recover করতে পারবে।

---

## Part 18 — Download & File Capture Engine
**কাজ:** browser action থেকে file download হলে download event detect করে file, filename, MIME type, size এবং related network request-এর metadata সংরক্ষণ করা।

**Success Criteria:** CSV, JSON এবং PDF download trigger করা একটি test workflow-তে প্রতিটি file successfully save হবে এবং corresponding download metadata ও network information report-এ পাওয়া যাবে।

---

## Part 19 — Console, Error & Runtime Monitoring
**কাজ:** browser console log, JavaScript error, uncaught exception, failed resource এবং page crash event capture করে workflow execution-এর সাথে associate করা।

**Success Criteria:** test page-এ intentionally generated console error এবং JavaScript exception থাকলে উভয়ই সঠিক timestamp ও page/action context সহ execution report-এ পাওয়া যাবে।

---

## Part 20 — Workflow Recording Engine
**কাজ:** user-এর manual browser interaction record করে navigation, click, typing, selection, download এবং network activity থেকে structured workflow তৈরি করা।

**Success Criteria:** user manually একটি ১০-step workflow complete করলে module কমপক্ষে ৯০% meaningful action সঠিক order ও target information সহ record করে replayable workflow তৈরি করবে।

---

## Part 21 — Workflow Replay Engine
**কাজ:** previously recorded বা AI-generated workflow আবার execute করতে পারা এবং প্রতিটি step-এর result ও captured network data verify করা।

**Success Criteria:** একই workflow ১০ বার replay করলে ১০ বারই defined step-order বজায় থাকবে এবং expected action ও network capture validation result পাওয়া যাবে।

---

## Part 22 — Self-Healing Element Resolution
**কাজ:** selector পরিবর্তন, dynamic DOM বা minor UI change হলেও text, role, attributes, DOM relationship এবং অন্যান্য available signals ব্যবহার করে target element পুনরায় identify করা।

**Success Criteria:** test page-এর selector পরিবর্তন করে একই workflow চালালে predefined target element-এর অন্তত ৯০% action manual selector update ছাড়া successfully execute হবে।

---

## Part 23 — Pagination & Dynamic Content Engine
**কাজ:** normal pagination, next/previous button, load-more এবং infinite-scroll type dynamic content automatically detect ও handle করা।

**Success Criteria:** একটি ২০-page pagination test এবং একটি infinite-scroll test-এ module সব expected content/page process করবে এবং প্রতিটি relevant navigation/action-এর network capture রাখবে।

---

## Part 24 — API & Endpoint Discovery
**কাজ:** captured network traffic analyse করে REST, GraphQL এবং অন্যান্য relevant API endpoint, HTTP method, parameters, request payload ও response structure structured format-এ identify করা।

**Success Criteria:** predefined test application-এর known API endpoint-এর অন্তত ৯০% সঠিক method, URL এবং relevant request information সহ discovery report-এ পাওয়া যাবে।

---

## Part 25 — Network Noise Classification & Smart Filtering
**কাজ:** analytics, advertisement, tracking, static asset এবং application-relevant request আলাদা করে classify করা এবং configurable rules অনুযায়ী output থেকে noise বাদ দেওয়া।

**Success Criteria:** analytics-heavy test website-এ relevant application API সব retain থাকবে এবং configured noise category-এর অন্তত ৯৫% request filtered output থেকে বাদ যাবে।

---

## Part 26 — AI-Friendly Structured Output
**কাজ:** raw HAR-এর পাশাপাশি AI-এর জন্য compact structured output তৈরি করা যেখানে page, action, related request, response summary, errors এবং validation result সহজে বুঝতে পারবে।

**Success Criteria:** একটি captured workflow-এর জন্য AI-friendly JSON output থেকে প্রতিটি action-এর target, related request এবং validation status raw HAR manually parse না করেই নির্ভুলভাবে identify করা যাবে।

---

## Part 27 — Task Orchestration & State Management
**কাজ:** বড় AI task-কে ছোট action/step-এ ভাগ করে execution state, current step, completed step, failed step এবং resume point maintain করা।

**Success Criteria:** ৩০-step workflow-এর মাঝখানে process বন্ধ করে restart করলে module last successful checkpoint থেকে resume করতে পারবে এবং ইতিমধ্যে completed step duplicate করবে না।

---

## Part 28 — Screenshot & Evidence Capture
**কাজ:** গুরুত্বপূর্ণ action, failure এবং validation point-এর সময় screenshot, DOM snapshot এবং related HAR/network information একই execution context-এর সাথে save করা।

**Success Criteria:** predefined workflow-এর প্রতিটি গুরুত্বপূর্ণ action-এর জন্য screenshot + action metadata + related network capture একই identifier দিয়ে locate করা যাবে।

---

## Part 29 — Execution Timeline & Audit Report
**কাজ:** সম্পূর্ণ workflow-এর timestamp, navigation, action, request, response, download, error, retry এবং validation result chronological timeline হিসেবে report করা।

**Success Criteria:** একটি সম্পূর্ণ workflow শেষ হলে report থেকে যেকোনো step-এর start time, end time, action, related network activity এবং final status আলাদা করে identify করা যাবে।

---

## Part 30 — Network Regression & Change Detection
**কাজ:** আগের successful workflow-এর network behavior-এর সাথে নতুন execution compare করে endpoint, method, payload structure, response schema বা request pattern-এর পরিবর্তন detect করা।

**Success Criteria:** test application-এর একটি known API endpoint পরিবর্তন করলে module regression report-এ সেই endpoint-এর পরিবর্তন clearly identify করতে পারবে।

---

## Part 31 — Project & Session Isolation
**কাজ:** আলাদা project, browser session, authentication state, workflow, HAR এবং output data একে অপরের থেকে isolate করে manage করা।

**Success Criteria:** একই সময়ে অন্তত ৩টি independent project/session চালালে এক project-এর cookie, workflow, HAR বা output data অন্য project-এ leak হবে না।

---

## Part 32 — Configurable Hooks & Plugin Architecture
**কাজ:** core engine পরিবর্তন না করে custom pre-action, post-action, request, response, validation এবং output processing logic plugin/hook হিসেবে যোগ করার ব্যবস্থা করা।

**Success Criteria:** core code modify না করে একটি custom plugin দিয়ে pre-click এবং post-request logic যোগ করলে নতুন behavior সফলভাবে execute হবে।

---

## Part 33 — Observability & Debug Mode
**কাজ:** development/debugging-এর জন্য structured logs, action IDs, request IDs, session IDs, execution metrics এবং optional verbose tracing প্রদান করা।

**Success Criteria:** একটি failed workflow চালানোর পর শুধু execution log ব্যবহার করে কোন action, request এবং error-এর কারণে failure হয়েছে সেটা reproduce না করেও identify করা যাবে।

---

## Part 34 — Concurrent Task & Resource Management
**কাজ:** একাধিক AI task/browser session safely parallel execute করা এবং CPU, memory, browser context ও network resource-এর উপর configurable concurrency limit রাখা।

**Success Criteria:** resource limit-এর মধ্যে অন্তত ৫টি independent task parallel run করলে কোন task-এর state mix না হয়ে প্রত্যেকটির আলাদা result, HAR এবং report পাওয়া যাবে।

---

## Part 35 — End-to-End AI Task Execution
**কাজ:** natural-language goal থেকে planning, browser interaction, network capture, validation, error recovery এবং final structured report পর্যন্ত পুরো pipeline একসাথে execute করা।

**Success Criteria:** একটি multi-page test task-এ AI শুধু goal দেওয়ার পর manual selector বা browser code ছাড়া module নিজে workflow execute করবে, expected pages/actions complete করবে, valid HAR তৈরি করবে এবং final report-এ success/failure ও evidence দেখাবে।

---

## Part 36 — SPA / Client-side Route Change Detection
**কাজ:** Client-side routing (`pushState`/`popstate`/hash change)-এর কারণে URL বা view পরিবর্তন হলে সেটাকে আলাদা "virtual page" হিসেবে detect করে network capture সঠিকভাবে segment করা, যাতে full page reload ছাড়া React/Vue/Next.js-এর মতো SPA-তেও per-page HAR পাওয়া যায়।

**Success Criteria:** React Router ব্যবহৃত একটা SPA test app-এ ৫টা ভিন্ন route-এ navigate করলে (কোনো full reload ছাড়াই) module প্রতিটা route change-কে আলাদা virtual page হিসেবে চিহ্নিত করে প্রতিটার জন্য আলাদা network segment/HAR তৈরি করবে।

---

## Part 37 — CAPTCHA & Blocking Detection with Human-in-the-loop Escalation
**কাজ:** CAPTCHA, bot-wall, বা hard block আসলে সেটা detect করে execution pause করা, operator-কে notify করা, এবং manual solve-এর পর একই session/state থেকে automatically resume করার ব্যবস্থা রাখা (বা optional third-party solver integrate করা)।

**Success Criteria:** ইচ্ছাকৃতভাবে CAPTCHA-protected test page-এ hit করলে module automatically block detect করে execution pause করবে এবং manual solve সম্পন্ন হওয়ার পর session/state ঠিক রেখে workflow থেকে resume করতে পারবে।

---

## Part 38 — AI Agent Guardrails (Loop Prevention & Budget Control)
**কাজ:** AI agent যাতে infinite retry/loop-এ আটকে না যায় বা অপ্রয়োজনীয় বেশি step/time/cost খরচ না করে, তার জন্য max-step limit, max-time budget এবং repeated-failure detection দিয়ে safe abort mechanism রাখা।

**Success Criteria:** ইচ্ছাকৃতভাবে unsolvable একটা selector/action দিয়ে test করলে module predefined max-attempt/time limit-এর পর নিজে থেকে execution বন্ধ করে clear reason-সহ failure report দেবে, infinite loop-এ যাবে না।

---

## Part 39 — Structured API Export (Postman/OpenAPI & Reusable Client Stub)
**কাজ:** Captured/discovered endpoint থেকে সরাসরি reusable Postman collection বা OpenAPI-style spec, এবং প্রয়োজনে basic scraper/API-client code stub generate করা, যাতে captured data শুধু analysis-এর জন্য না, পরবর্তী automation-এও সরাসরি ব্যবহার করা যায়।

**Success Criteria:** একটা captured workflow থেকে generate করা Postman collection import করে অন্তত ৯০% request Postman-এ সরাসরি (বা সামান্য পরিবর্তনে) সফলভাবে replay করা যাবে।

---

## Part 40 — Target-Site Load & Rate Respect
**কাজ:** Target website overload/ban এড়াতে per-domain configurable concurrency limit ও delay রাখা, এবং server-পাঠানো `429`/`Retry-After` header respect করে automatic backoff করা।

**Success Criteria:** rate-limit response (`429` + `Retry-After`) পাঠানো একটা test server-এ চালালে module নির্দেশিত wait time মেনে backoff করবে এবং hardcoded delay দিয়ে সেটা override করে সার্ভারে excessive request পাঠাবে না।

---

## Part 41 — Streaming / SSE & Long-lived Connection Capture
**কাজ:** Server-Sent Events, chunked response, বা long-polling connection-এর data সঠিকভাবে capture করে HAR/structured output-এ represent করা, যাতে normal request/response capture-এ এই ধরনের streaming data miss না হয়।

**Success Criteria:** SSE ব্যবহার করা একটা test endpoint-এ stream করা প্রতিটা event/chunk module-এর capture output-এ সঠিক sequence ও timestamp সহ পাওয়া যাবে।


---

## Part 42 — WebSocket & Bi-directional Network Capture
**কাজ:** WebSocket connection detect করে handshake request, connection lifecycle এবং client/server message capture করা, যাতে real-time application-এর network behavior সম্পূর্ণভাবে analyze করা যায়।

**Success Criteria:** Test application-এ একটি WebSocket connection establish করে client ও server উভয় দিক থেকে অন্তত ১০টি message পাঠালে সব message সঠিক direction, timestamp এবং connection ID সহ structured output-এ পাওয়া যাবে।

---

## Part 43 — Network Interception, Mocking & Controlled Response
**কাজ:** নির্দিষ্ট request intercept করে inspect, modify, block বা mock response দেওয়ার ব্যবস্থা রাখা, যাতে failure scenario, unavailable API এবং controlled testing environment তৈরি করা যায়।

**Success Criteria:** Test app-এর একটি API request intercept করে predefined mock response দিলে browser application সেই mocked response ব্যবহার করবে এবং execution report-এ original request ও injected response দুটোই clearly recorded থাকবে।

---

## Part 44 — Request/Response Body Integrity & Content Decoding
**কাজ:** JSON, form-data, multipart, text, binary এবং compressed/encoded response-এর body safely capture ও decode করা, এবং body truncated/missing হলে সেটা detect করা।

**Success Criteria:** JSON, multipart এবং binary response থাকা test workflow-এ captured body expected content-এর সাথে byte/content-level validation pass করবে, এবং intentionally truncated body হলে validation clear failure দেখাবে।

---

## Part 45 — Multi-Tab, Popup, Window & Frame Lifecycle Management
**কাজ:** নতুন tab/window, popup, iframe এবং nested frame open/close হলে সেগুলো automatically track করে সঠিক session, page এবং network activity-এর সাথে associate করা।

**Success Criteria:** Test workflow-এ main page থেকে popup, নতুন tab এবং nested iframe open/close করলে module প্রতিটি context-এর lifecycle track করবে এবং কোন context-এর কোন request ছিল সেটা আলাদাভাবে identify করা যাবে।

---

## Part 46 — Complete Browser Storage State Management
**কাজ:** Cookie-এর পাশাপাশি LocalStorage, SessionStorage, IndexedDB এবং অন্যান্য relevant client-side storage inspect, export, restore এবং project/session অনুযায়ী isolate করার ব্যবস্থা রাখা।

**Success Criteria:** Test application-এ cookie + LocalStorage + IndexedDB-তে state তৈরি করে browser restart করার পর exported state restore করলে application একই logical logged-in/session state পুনরায় পাবে।

---

## Part 47 — Proxy, Network Profile & Environment Control
**কাজ:** Per-project/per-session proxy, custom headers, user-agent, timezone, locale এবং network condition configure করার ব্যবস্থা রাখা, যাতে reproducible environment-এ workflow execute করা যায়।

**Success Criteria:** দুইটি independent session-এ আলাদা configured network profile ব্যবহার করলে প্রতিটির outgoing request expected proxy/header/locale configuration অনুযায়ী যায় এবং কোনো session-এর configuration অন্য session-এ leak না হয়।

---

## Part 48 — Artifact Storage, Manifest & Versioned Execution History
**কাজ:** HAR, screenshots, DOM snapshots, logs, workflow, reports এবং discovered API data-কে একটি consistent artifact structure ও manifest-এর মাধ্যমে store করা, এবং একই workflow-এর multiple execution/version compare করা।

**Success Criteria:** একটি workflow ৫ বার execute করলে প্রতিটি run-এর জন্য unique execution ID সহ HAR, screenshot, log ও report locate করা যাবে এবং manifest থেকে run-to-artifact relationship ও version history নির্ভুলভাবে reconstruct করা যাবে।

---

## Part 49 — Deterministic Test Fixtures & Reproducible Replay
**কাজ:** controlled test data, mockable network conditions, fixed browser configuration এবং repeatable workflow state ব্যবহার করে একই scenario বারবার reproducibly test করা।

**Success Criteria:** একই test fixture ও workflow কমপক্ষে ২০ বার run করলে action sequence, expected network assertions এবং validation result deterministicভাবে consistent থাকবে; intentional fixture change করলে শুধু expected differences detect হবে।

---
