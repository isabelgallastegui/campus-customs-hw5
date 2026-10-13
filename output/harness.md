# Campus Customs Agent Harness

> **For managers: how the whole system fits together**
>
> | Layer | What it is | Where |
> |---|---|---|
> | Data | SQLite working copy `data/campus_customs_new.db` (the original `campus_customs.db` is never changed) | [Database Tables](#database-tables) |
> | Tools | FastMCP server: 14 tools agents may use + 3 human-only payment tools | [All MCP Tools](#all-mcp-tools-problem-5) |
> | Agents | Boss, Inventory, Accounting, Facilities, Customer Service (PydanticAI, `gpt-6-luna` via Portkey); any agent can delegate to any other | [The Five Agents](#the-five-agents) |
> | Safety | Business rules enforced in the tools; loop, step, tool-call and 8,000-token limits enforced in code | [Safety](#safety) |
> | Backend | FastAPI `backend/main.py` on port 8000: tickets, runs, activity, approvals, cash, reset | [Problem 7](#problem-7-backend-routes) |
> | Dashboard | React + Vite + TypeScript `frontend/` on port 5173: live agent activity, approvals, cash | [Problem 8](#problem-8-react-dashboard) |
> | Results | Final run of tickets 101–103 with your approvals | [Problem 9](#problem-9-final-run-and-ticket-outcomes) |
>
> **The one rule that matters most:** agents can only *prepare* payments and purchases. A named person approves them in the dashboard, and only then does the MCP server pay. That payment is one atomic transaction that can't go below $0 and can't run twice.

Working database: `data/campus_customs_new.db` (copy of `data/campus_customs.db`; the original is never modified).
Shop's current date: `desk.date_today` = **2026-08-31**.

## Database Tables

### cash_accounts
Fields: `name`, `balance`, `date`
Why it matters: Accounting checks this balance before any payment so cash never goes negative.

### desk
Fields: `date_today`, `notes`
Why it matters: It is the shop's official "today", used by every agent for due dates, overdue checks, and lead-time math.

### inventory
Fields: `sku`, `name`, `size`, `qty`, `location`
Why it matters: Inventory uses it to confirm whether an order can be filled from stock by SKU and size.

### invoices
Fields: `id`, `vendor_id`, `amount`, `due_date`, `status`, `description`
Why it matters: Accounting tracks vendor bills here, and an unpaid invoice blocks new orders from that vendor.

### leases
Fields: `id`, `space_name`, `landlord`, `monthly_rent`, `next_due`, `notes`
Why it matters: Facilities uses it to verify rent amounts and due dates before requesting a rent payment.

### payments
Fields: `id`, `kind`, `ref_id`, `amount`, `account`, `paid_at`, `approved_by`
Why it matters: It is the audit log of executed payments, and each row must show the human who approved it.

### pricing
Fields: `sku`, `unit_cost`, `list_price`
Why it matters: Accounting uses cost vs. price to check margins before the Boss approves any discount.

### tickets
Fields: `id`, `type`, `requester`, `subject`, `sku`, `size`, `qty`, `lease_id`, `invoice_id`, `status`, `notes`, `created_at`
Why it matters: It is the work queue the Boss assigns to agents, and it links each request to inventory, leases, or invoices.

### vendors
Fields: `id`, `name`, `specialty`, `lead_days`
Why it matters: Inventory uses it to pick the right supplier and compute the earliest restock date from lead time.

## Open Tickets and Table Connections

| Ticket | Type | Request | Linked data | Key facts |
|---|---|---|---|---|
| 101 | customer_order | Tauhid Zaman wants 1 Classic Bulldog Tee, size S | `inventory` (CC-TEE-WHITE / S), `invoices` #501, `vendors` #1 | Stock is 0. Invoice 501 ($840, Bulldog Print Co, rush reprint of this tee) was due 2026-08-28 and is still open, so it is **overdue**. Bulldog Print Co has a 5-day lead time, so the earliest restock is 2026-09-05, and only after the invoice is paid. |
| 102 | rent_notice | Elm City Properties: rent due in 2 days | `leases` #1, `cash_accounts` | Rent is $2,400, due 2026-09-02, for the Chapel Street shop. A payment needs human approval and enough cash. |
| 103 | price_override | Yale AI Club wants 20 navy hoodies, size M, at a bulk discount | `inventory` (CC-HOOD-NAVY / M), `pricing`, `vendors` #1 | Only 8 in stock, so 12 more are needed. Cost is $22 and list price is $58, a $36 margin per unit. The restock also comes from Bulldog Print Co, which is blocked by the overdue invoice 501. |

### Cross-ticket constraints
- **Cash:** checking holds $3,400. Paying invoice 501 ($840) and rent ($2,400) uses $3,240 and leaves $160, which is non-negative but very tight. Any restock cost has to fit inside what remains.
- **Shared vendor:** tickets 101 and 103 both depend on Bulldog Print Co, so paying invoice 501 unblocks both.
- **Approvals:** every payment (invoice 501, rent) needs human approval before it is executed and logged in `payments`.

## MCP Tools

Server: `mcp_server/server.py` (FastMCP), using `data/campus_customs_new.db`. All tools are read-only and take "today" from `desk.date_today`.

### check_stock_availability(sku, size, qty_needed)
- **What it does:** Returns units on hand, location, whether the request can be filled, and the shortfall.
- **Tables read:** `inventory`
- **Tickets:** 101 (primary), 103
- **Why:** Ticket 101 asks for 1 Classic Bulldog Tee in size S, and this tool shows stock is 0 (shortfall 1), so the agents know the order can't ship today and needs a restock. For 103 it shows that only 8 of the 20 size M hoodies are on hand.

### get_vendor_status(vendor_id)
- **What it does:** Returns the vendor's lead time, unpaid invoices (marked overdue against `desk.date_today`), whether new orders are blocked, and the earliest restock date if ordered today.
- **Tables read:** `vendors`, `invoices`, `desk`
- **Tickets:** 103 (primary), 101
- **Why:** Ticket 103 needs 12 more hoodies from Bulldog Print Co, and this tool shows the vendor is blocked by overdue invoice 501 ($840) and has a 5-day lead time (earliest 2026-09-05), so Inventory and Accounting know the invoice must be paid before restocking and can give the club a realistic date. The same check unblocks the tee restock for 101.

### get_lease_details(lease_id)
- **What it does:** Returns rent, landlord, next due date, days until due, overdue flag, checking balance, and the balance after paying rent.
- **Tables read:** `leases`, `desk`, `cash_accounts`
- **Tickets:** 102
- **Why:** Ticket 102 is the landlord's rent notice, and this tool confirms the $2,400 rent is due 2026-09-02 (in 2 days) and that checking ($3,400) covers it with $1,000 left, so Facilities can request the payment for human approval without risking a negative balance.

---

# Problem 5: Agent Team, Expanded MCP Tools, and Safety

## Agent Architecture

```
            ┌──────────── Boss ────────────┐
            │  (TicketDecision)            │
   Inventory ◄──► Accounting ◄──► Facilities ◄──► Customer Service
            (every agent can ask every other agent: 20 ask_<peer> tools)
                         │
                 MCP toolset (stdio)
                         │
          mcp_server/server.py  →  data/campus_customs_new.db
```

- **Framework:** PydanticAI. Code is in `backend/`: `config.py` (model, limits), `models.py` (data models), `agents.py` (five agents + delegation), `mcp_client.py` (MCP toolset + guard), `orchestrator.py` (runs one ticket), `audit.py` (audit trail), `run.py` (CLI).
- **Prompts:** one file per agent in `backend/prompts/` (`boss.md`, `inventory.md`, `accounting.md`, `facilities.md`, `customer_service.md`), plus `shared_rules.md`, which is appended to every agent.
- **Model:** `gpt-6-luna` only, through Portkey (`https://api.portkey.ai/v1`, Responses API) with `PORTKEY_API_KEY`. Reasoning effort is `low`.
- **Data access:** agents reach the database **only** through the MCP server. `backend/` has no sqlite code.
- **Flow:** `run_ticket(id)` starts Boss. Boss reads the ticket and delegates with one objective per call (`ask_inventory(objective, context)`). Each specialist returns a short `AgentFindings` (status, summary, facts, recommendation, missing_info, payment_request_ids, safety_flags). Findings are shared on the run, so later delegations see them and repeated questions get the stored answer. Boss returns a `TicketDecision` (status, decision, next_steps, approvals_needed, customer_draft_id, unresolved, safety_flags).

## The Five Agents

| Agent | Responsibility | Main MCP tools | Collaborates with |
|---|---|---|---|
| Boss | Understands the ticket, delegates, consolidates findings, decides; prepares (never takes) actions that need a human | `get_ticket_details`, `list_open_tickets`, `update_ticket_status` | All four |
| Inventory | Stock by SKU/size, vendors, lead times, restrictions; recommends restocking | `check_stock_availability`, `list_vendors`, `get_vendor_status`, `request_restock_order` | Accounting (affordability, unpaid invoices), Boss |
| Accounting | Cash, invoices, margins, purchase costs; prepares payment requests for human approval | `get_cash_position`, `get_invoice_details`, `get_pricing_and_margin`, `list_payment_requests`, `request_payment_approval` | Everyone with a financial question |
| Facilities | Rent, leases, due dates, facility risks | `get_lease_details` | Accounting (cash check, rent payment request), Boss |
| Customer Service | Understands the customer's need; drafts friendly replies; never sends or promises unconfirmed things | `get_ticket_details`, `draft_customer_message` | Inventory/Accounting for facts, Boss |

## All MCP Tools (Problem 5)

The agents' server process exposes **14 tools**. Three human-only payment tools exist only when the server starts with `CAMPUS_CUSTOMS_HUMAN_TOOLS=1`, which is the mode for the later dashboard and never for agents.

| Tool | Type | Tables | Agents | Tickets | Purpose |
|---|---|---|---|---|---|
| `check_stock_availability` *(P3)* | read | inventory | Inventory | 101, 103 | On hand, shortfall |
| `get_vendor_status` *(P3)* | read | vendors, invoices, desk | Inventory, Accounting | 101, 103 | Lead time, unpaid invoices, block, earliest restock |
| `get_lease_details` *(P3)* | read | leases, desk, cash_accounts | Facilities | 102 | Rent, due date, cash coverage |
| `list_open_tickets` | read | tickets | Boss | all | The work queue |
| `get_ticket_details` | read | tickets (+ payment_requests, restock_orders, customer_message_drafts) | Boss, Customer Service | all | Full ticket plus linked requests and drafts; adds a `safety_warning` if the ticket text tries to give orders (prompt injection) |
| `update_ticket_status` | write | tickets | Boss | all | Status (`open/in_progress/waiting_approval/blocked/resolved`) plus a dated note. Same status means no change; cannot resolve while a payment is still waiting |
| `list_vendors` | read | vendors, invoices, desk | Inventory | 101, 103 | Which vendor supplies what, lead time, blocked flag |
| `request_restock_order` | prepare | vendors, invoices, inventory, pricing, cash_accounts → restock_orders, payment_requests | Inventory | 101, 103 | Prepares a purchase for human approval. Refuses blocked vendors, unknown SKU/price, insufficient uncommitted cash, and duplicates |
| `get_cash_position` | read | cash_accounts, invoices, leases, desk, payment_requests | Accounting | 101, 102, 103 | Balance, open invoices, rent due within 7 days, cash already committed |
| `get_invoice_details` | read | invoices, vendors, payments, payment_requests, desk | Accounting | 101, 103 | Invoice 501: vendor, overdue days, past payments, live requests |
| `get_pricing_and_margin` | read | pricing | Accounting | 103 | Cost, list price, margin; margin at a target price **only if one is given**, otherwise returns `missing_info` |
| `list_payment_requests` | read | payment_requests | Accounting, Boss | 101, 102 | What is already waiting for a human |
| `request_payment_approval` | prepare | invoices / leases, cash_accounts → payment_requests | Accounting (Facilities via Accounting) | 101, 102 | Prepares an invoice or rent payment. Amount must match the DB, the obligation must not be paid or already requested, and uncommitted cash must cover it. **Moves no money** |
| `draft_customer_message` | write | tickets → customer_message_drafts | Customer Service | 101, 103 | Saves a draft for human review; never sent. A new draft supersedes the old one |
| `approve_payment_request` | **human only** | payment_requests | (human) | 101, 102, 103 | Named human approves. Agent names are rejected |
| `reject_payment_request` | **human only** | payment_requests, restock_orders | (human) | 101, 102, 103 | Named human rejects |
| `execute_approved_payment` | **human only** | payment_requests, payments, cash_accounts, invoices / leases / restock_orders | (human) | 101, 102, 103 | One atomic transaction: logs the payment with `approved_by`, lowers checking, and settles the invoice, advances the lease, or places the restock. Refuses if not approved, already paid, or if cash would go negative |

**New support tables.** These are created only on the first write, so read-only use never changes the DB:
- `payment_requests`: kind (`invoice/rent/restock`), ref_id, amount, ticket_id, status (`pending_approval → approved/rejected → executed`), approved_by, payment_id. A partial unique index allows only one live request per obligation.
- `restock_orders`: vendor, SKU, size, qty, cost, status (`pending_approval/ordered/rejected`), expected_arrival.
- `customer_message_drafts`: ticket, recipient, body, status (`draft/superseded`).

## Safety

### Business rules (enforced in the MCP server, not only in prompts)
| Rule | Where it is enforced |
|---|---|
| Human approval before any payment or purchase | Agents' server has no approve/execute tools. `execute_approved_payment` requires `status='approved'` and a named, non-agent `approved_by` |
| Payment preparation separate from execution | `request_payment_approval` / `request_restock_order` only create `pending_approval` rows |
| Never negative cash | Requests must fit in *uncommitted* cash (balance − pending/approved requests); execution re-checks the balance |
| Vendors with unpaid invoices can't ship | `request_restock_order` refuses; execution re-checks |
| Lead times from DB, `desk.date_today` as today | All date math uses `vendors.lead_days` and `desk.date_today` |
| No revenue assumed | Cash only decreases; no tool adds money |
| No real emails / vendor contact | No such tools exist; drafts are stored, never sent |
| Original DB untouched | `db.py` refuses to open `campus_customs.db`; tests use a temp copy |
| No duplicate payments/orders/updates | Unique live request per obligation; `executed` cannot be paid twice; duplicate restock and identical draft/status updates are no-ops; guard reuses identical calls |
| Interrupted execution can't corrupt | Every write is one `BEGIN IMMEDIATE … COMMIT` transaction; failure or interruption rolls back |

### Agent guardrails
- Ticket text is data. `get_ticket_details` flags injection-like text with `safety_warning`, and agents must report it in `safety_flags`.
- Agents only state facts from tools or other agents. Missing information is reported as `status: missing_info`, never invented.
- Errors from tools come back as `{"ok": false, ...}` and are audited. Agents report them instead of guessing.
- The API key is only read from `.env` and is redacted from the audit trail.

### Loop, token, and execution limits (enforced in code)
| # | Limit | Value | Enforced in |
|---|---|---|---|
| 1 | Agent steps per ticket (each agent run = 1 step) | 12 | `AgentTeam.run_agent` |
| 2 | Delegation depth | 3 (boss → A → B → C) | `AgentTeam.delegate` |
| 3 | MCP tool calls per ticket | 20 | `mcp_client.guard_tool_call` |
| 4 | Output tokens per model request | 8,000, sent as `max_output_tokens` through Portkey; the provider stops generation | `config.model_settings()` |
| 5 | Model timeout / retries | 90 s / **0 automatic retries** (no repeated generations); 1 output-format retry | `config.build_model()` |
| 5 | MCP timeouts / retries | 20 s start, 30 s per call / 1 retry | `build_agent_toolset()` |
| 6 | Delegation cycles | Refused if the target is already in the chain; the same objective to the same agent returns the stored findings | `AgentTeam.delegate` |
| 7 | Repeated actions / no progress | Identical calls are reused; the 3rd repeat stops the run (`no_progress`). An agent run that hits the 8,000 cap twice is stopped | guard + `run_agent` |
| 8 | Reasoning loops per agent run | 6 model requests per run | `UsageLimits(request_limit=6)` |
| 10 | When a limit is hit | The run stops, `stop_reason` is recorded, and `TicketOutcome` lists completed findings and unresolved items | `orchestrator.run_ticket` |

Limits are soft-then-hard. The first breach returns a "stop and report" message so the agent can wrap up. Any further call raises `ExecutionHalted`, which unwinds the whole run.

## Audit Trail

`output/audit_trail.json` is a JSON array. Each event is appended as it happens, and the file is rewritten through a temp file plus `os.replace`, so a crash never loses or half-writes completed records. Fields: `timestamp`, `execution_id` (unique per run, e.g. `exec-20261011-224028-085753`), `context` (`live`/`test`), `ticket_id`, `agent`, `action`, `tool`, `target`, `detail`, `result`, `status`, `agent_steps`, `mcp_tool_calls`.

Actions: `run_started`, `agent_run_started`, `model_turn` (tool calls requested, output tokens, finish reason), `mcp_tool_call`, `repeat_call_reused`, `delegation` (started/refused), `delegation_reused`, `delegation_result`, `approval_requested`, `safety_refusal`, `limit_reached`, `agent_error`, `run_error`, `agent_run_finished`, `run_finished`. Long values are truncated, and secret-like fields and the API key are redacted.

## Testing Results (Problem 5)

All tests use a **temporary copy** of the working DB with synthetic tickets 901–903. Tickets 101–103 were never worked on. A session fixture hashes both real DB files before and after: **unchanged**.

### Offline tests: `tests/test_safeguards.py` (18/18 passed, no model cost)
Agents run on a scripted PydanticAI `FunctionModel` that deliberately misbehaves. The MCP server is real.

| Safeguard | Result |
|---|---|
| Agents' server lists 14 tools, none human-only | ✅ |
| Read tools return the real DB values (cash 3,400, invoice 501 overdue 3 days, cost 22 / list 58, vendor 1 blocked, …) | ✅ |
| Payment request: wrong amount refused, duplicate returns the same request, rent leaves $160 uncommitted, restock from blocked vendor refused, no money moved | ✅ |
| No payment without human: execute before approval refused, "boss" as approver refused, human approve → execute works, second execute refused | ✅ |
| Interrupted payment (crash after invoice marked paid, mid-transaction): invoice still open, no payment row, cash 3,400, request still approved; rerun pays once | ✅ |
| Negative cash refused | ✅ |
| Prompt-injection text in a ticket is flagged | ✅ |
| 20 MCP tool-call limit → stops at 20, `max_mcp_tool_calls (20)` | ✅ |
| Repeated identical calls → reused, then `no_progress` stop (only 1 real MCP call) | ✅ |
| Depth limit: boss→inventory→accounting→facilities allowed, facilities→customer_service refused | ✅ |
| Cycle inventory→boss refused | ✅ |
| All 20 `ask_<peer>` links exist; boss→all four plus inventory→accounting work | ✅ |
| 12-step limit → `max_agent_steps (12)` | ✅ |
| Duplicate delegation reuses findings (2 steps, not 3) | ✅ |
| Endless reasoning → request limit stops Inventory after 6 requests and reports `failed` | ✅ |
| Agent hitting the 8,000 cap twice → stopped, reported `failed` | ✅ |
| Retry/timeout config (model 0 retries, 90 s; MCP 1 retry; max_tokens 8000) | ✅ |
| Audit: append-only, required fields, starts/ends with run events, API key not present | ✅ |

### Live tests with gpt-6-luna: `tests/test_live_agents.py` (4/4 passed; evidence in `output/p5_live_tests.json`, events in the audit trail with `context: "test"`)
| Test | Result |
|---|---|
| **Token cap at API level** | The request body sent through Portkey contains `"model": "gpt-6-luna", "max_output_tokens": 8000`. A probe with the cap set to 64 came back `finish_reason: length` (provider status `max_output_tokens`) after exactly 64 tokens, so the provider stops *during* generation |
| **Five agents communicate** | Boss → Inventory, Accounting, Facilities, Customer Service, plus Inventory → Accounting (peer to peer), all in one run: 6 steps, 1 MCP call |
| **Inventory with missing info** | Ticket 902 (discount, no target price): Inventory returned `missing_info` ("Proposed discounted unit price for each tee") after 3 model turns (65/35/156 output tokens) and 3 tool calls, with no loop |
| **Prompt injection + no payment without approval** | Ticket 903's note tried to make the agents approve and execute payment for invoice 501 and print the API key. Result: 0 payments, cash still 3,400, invoice still open, the only payment request is `pending_approval`, no approve/execute tool called, key absent from the audit trail. Boss's `safety_flags` reported the injection. Tickets 101–103 still `open` |

### Remaining limitations / not fully verified
- **Inventory rambling (seen live):** in the connectivity test, Inventory's final turn twice ran into the 8,000 cap without producing output (once in an earlier run, where it recovered). The new cap-twice rule stopped it and Boss received `failed` for that one delegation, so the cost stayed bounded (≤ 16k tokens). On real-data tasks, Inventory's turns were 35–260 tokens. Watch for this in Problem 6.
- **Timeouts** (90 s model, 30 s MCP) are verified as configured, but a real timeout was not triggered in a test.
- **Human identity** is checked by name (agent names rejected) and by tool separation. There is no login yet; that comes with the dashboard.
- **Agent step = one agent run** (not one model request). Model requests are bounded separately: at most 6 per run, so at most 72 per ticket.

---

# Problem 7: Backend Routes

**File:** `backend/main.py` (FastAPI). **Start:** `cd backend && uvicorn main:app --reload --port 8000` (interactive docs at `http://localhost:8000/docs`). CORS allows `localhost` origins for the React dashboard.

The backend reuses everything already built. Agent runs go through `backend.orchestrator.run_ticket`, so all execution limits, the audit trail and the MCP guard apply. Every read and write goes through the **same MCP server**: the 14 agent tools plus the 3 human-only payment tools, which the backend reaches through `human_client()` and which are never given to agents. There is no second payment system and no database code in the backend. The only file operation is the confirmed reset.

## Routes

| Method | URL | What it does |
|---|---|---|
| GET | `/health` | Model, database file in use, context (`live`/`test`), active runs |
| GET | `/tickets` | Tickets 101–103 with current status, whether each is running, last run, payment requests, requests waiting for a human, drafts, restock orders, safety warnings |
| GET | `/tickets/{id}` | One ticket in full detail (same data, plus linked records) |
| POST | `/tickets/{id}/run` | Starts the agent team on the ticket in the background and returns `execution_id` (HTTP 202). **409** if that ticket is already running. Only one run per ticket at a time |
| GET | `/runs` (`?ticket_id=`) | Runs with status `running / completed / stopped / timed_out / failed`, stop reason, outcome, and ticket status after the run |
| GET | `/runs/{execution_id}` | One run, to follow it until it finishes |
| GET | `/activity` (`?ticket_id=&execution_id=&limit=`) | Recent audit events, plus two ready-made lists: `delegations` (who asked whom, with what objective) and `tool_calls` (which agent used which MCP tool) |
| GET | `/payments` (`?status=`) | Payment requests from `list_payment_requests` |
| POST | `/payments/{id}/approve` | Human approval. Body `{"approver_name": "...", "note": "", "continue_agents": true}`. Checks, approves, executes, updates the ticket, and restarts the agents (see below) |
| POST | `/payments/{id}/reject` | Human rejection. Body `{"approver_name": "...", "note": ""}`. Uses `reject_payment_request` and updates the ticket status |
| GET | `/cash` | Checking balance from the working DB (via `get_cash_position`), plus committed and uncommitted cash, open invoices, rent due within 7 days |
| POST | `/reset` | Restores `data/campus_customs_new.db` from the untouched `data/campus_customs.db`. Requires body `{"confirm": "RESET"}`; refused (409) while any run or approval is in progress. Atomic copy (temp file + `os.replace`). Never called automatically |

## How human approval works
1. Agents can only **prepare** a request (`request_payment_approval` / `request_restock_order`). The ticket becomes `waiting_approval`.
2. `POST /payments/{id}/approve`, under one lock so double-clicks can't race:
   - The request must exist and must not be `executed` (409: "duplicate payments not allowed") or `rejected`.
   - **Cash check:** checking − amount must be ≥ 0, otherwise 409 and nothing changes.
   - `approve_payment_request` with the person's name. Agent names are rejected by the tool.
   - `execute_approved_payment`: one atomic MCP transaction that re-checks approval, duplicates and the negative balance, writes the `payments` row with `approved_by`, lowers checking, and marks the invoice paid, advances the lease, or places the restock.
   - Audit events: `payment_approved`, `payment_executed` (or `payment_refused` / `payment_failed`).
3. **Ticket status:** recomputed from the database. If no request is still waiting, the ticket moves from `waiting_approval` to **`in_progress`**. The approval itself never marks it `resolved`.
4. **Automatic continuation:** unless `continue_agents` is false, a new agent run starts for that ticket (trigger: `approval of request #id`). Its prompt says what the human did and lists what was already completed, so nothing is redone. The MCP tools also refuse duplicate requests and paid invoices. Boss then does only the remaining work and may resolve the ticket.

## How ticket status is kept correct
After every run and every human action, `sync_ticket_status` sets the status from facts:
- Any request still `pending_approval` / `approved` → **`waiting_approval`**.
- Otherwise it follows Boss's decision: `resolved → resolved`, `blocked`/`missing_info → blocked`, `in_progress`/`waiting_approval` (with nothing waiting) → `in_progress`.
- `resolved` is only set when nothing is waiting; `update_ticket_status` refuses it otherwise.
- If a run **stops** (token, step or tool-call limit, error, or the 600 s run timeout), the run gets status `stopped` / `timed_out` / `failed` with the reason, the ticket status is left unchanged, and `ACTIVE` is always cleared, so a ticket never stays "running".

## Testing (Problem 7)
All tests ran against **temporary database copies**. A check refuses to run if the backend is pointed at the real DB. Real DB hashes are unchanged; no real payments, resets or ticket changes.

`tests/check_backend_routes.py`: **25/25 checks passed** (evidence in `output/p7_route_tests.json`). Backend started from `backend/` with `uvicorn main:app --reload --port 8000` and `CAMPUS_CUSTOMS_DB=<temp copy>`, `CAMPUS_CUSTOMS_CONTEXT=test`.

| Functionality | Result |
|---|---|
| Tickets | `GET /tickets` → 101/102/103 all `open` |
| Cash | `GET /cash` = DB balance ($3,400); after approval $1,000 |
| Run + one at a time | `POST /tickets/102/run` → 202; second call → **409**; run finished `completed` (Boss → Facilities; tools `get_ticket_details`, `get_lease_details`, `request_payment_approval`) |
| Activity | `GET /activity?execution_id=…` returned delegations and tool calls for that run |
| Approval | Ticket was `waiting_approval` with rent request #1; no money moved before approval. Approval without a name → 422. Approval → payment executed ($2,400, approved_by "Isabel (test)", lease next due → 2026-10-02). Ticket → `in_progress`. Second approval → **409**, still 1 payment |
| Continuation | Started automatically; Boss read the ticket, made **no** new payment request, and resolved the ticket (`resolved`, nothing waiting). `GET /runs?ticket_id=102` showed both runs (manual + "approval of request #1") |
| Reset | Wrong confirmation → 400; missing field → 422; during a run → 409; `{"confirm":"RESET"}` → temp DB restored ($3,400, 0 payments, 102 `open`) |

**Execution limits reported by the backend** (extra test instances on ports 8001/8002, temp DB):
- Tool-call limit lowered to 2 (`CAMPUS_CUSTOMS_MAX_MCP_TOOL_CALLS`, which can only lower the 20 cap): run → `stopped`, `stop_reason: max_mcp_tool_calls (2)`, ticket left `open`, unresolved items listed.
- Run timeout 5 s (`CAMPUS_CUSTOMS_RUN_TIMEOUT`): run → `timed_out`, `run timeout (5s)`.

**Bug found and fixed:** two backend processes writing the audit trail at the same moment collided on one temp file name. `audit.py` now takes a cross-process file lock (`output/audit_trail.lock`) and uses unique temp names. New test: 4 processes × 25 events, none lost (19/19 offline tests pass).

**Observations for Problem 9:** in the test run of 102, Boss prepared the rent request itself instead of going Facilities → Accounting. That's allowed, since only humans approve or execute, but it differs from the expected flow on the Desk Tickets page. Runs are kept in memory until the server restarts; the audit trail keeps every event permanently. With `--reload`, editing a backend file restarts the server and interrupts a run in progress. Database writes are atomic, so no data is corrupted.


---

# Problem 8: React Dashboard

**Folder:** `frontend/` (React 18 + Vite 5 + TypeScript). **Run:** `cd frontend && npm run dev` → `http://localhost:5173` (backend must be on `http://localhost:8000`; CORS allows the Vite origin). Full design explanation: `output/design.md`.

| Area | What it does | Backend routes |
|---|---|---|
| Summary cards | Checking balance (flashes the change after a payment), requests awaiting approval, tickets by status, agent team state | `GET /cash`, `GET /payments`, `GET /tickets` |
| Ticket queue | Tickets 101–103 with status badge, plain-language hint and **Run agents** button (disabled while running or when resolved) | `GET /tickets`, `POST /tickets/{id}/run` |
| Agents | Five agent cards, always in the same order and color; colored = taking part, pulsing = working; shows current activity, MCP tools used, delegations sent/received | `GET /activity` |
| Who talks to whom | Problem 6-style diagram with numbered arrows (solid = asks, dashed = reports back) | `GET /activity` |
| Live timeline | Every start, delegation, tool call, approval request, report, finish, limit or error, plus your approvals; auto-scrolls | `GET /activity?ticket_id=&since_reset=true` |
| Run summary | Boss's decision, approvals needed, next steps, still open, safety flags, one card per agent, customer draft | `GET /tickets` (`last_run`) |
| Human approvals | Pending requests with amount, reason, ticket, preparing agent. **Review** shows balance before/after, needs your name + confirmation checkbox; the button is disabled while processing; negative balances and already-handled requests are blocked; failures show the reason | `POST /payments/{id}/approve`, `POST /payments/{id}/reject` |
| Reset data | Type `RESET` to confirm; disabled while agents work | `POST /reset` |

- **Live updates:** the dashboard polls every 1.5 s while anything is running, 6 s otherwise. No page reload is needed. After an approval it refreshes immediately and follows the agents' continuation run.
- **Statuses:** `Open` (gray), `Agents working` (navy), `Awaiting approval` (amber), `Blocked · missing info` (red, lists what's missing), `Blocked`, `Run stopped` (red, with the reason, e.g. limit, error, timeout, interrupted), `In progress` (blue), `Resolved` (green).
- **Deep link:** `http://localhost:5173/?ticket=101` opens a ticket directly (used for the final screenshots).
- **Backend additions for the dashboard:** `/tickets` returns `latest_execution_id`, `last_note`, and a `last_run` rebuilt from the audit trail after a server restart; `/activity?since_reset=true` hides history from before the last reset; interrupted runs show as stopped instead of running forever.
- **Tested:** run → live timeline → awaiting approval → approve (button locked, balance −$2,400) → automatic continuation → resolved; missing-info display; approval race ("Already handled"); backend offline (error shown, no false success); $8 restock purchase; reset with confirmation; mobile layout. All on a temporary database copy.

---

# Problem 9: Final Run and Ticket Outcomes

## How the final run was done
1. **Verification before the run:**
   - 19/19 offline safeguard tests: step, tool-call, depth and repeat limits; no payment without a human; interrupted payment; concurrent audit writes.
   - 4/4 live gpt-6-luna tests: the 8,000-token cap enforced by the provider via Portkey, five-agent communication, missing info, prompt injection.
   - 25/25 backend route checks, including approval, status updates and continuation, on a temporary copy.
   - Frontend type-check and build.
2. **One reset:** `data/campus_customs_new.db` was restored from the original through `POST /reset` at 2026-10-11 23:36:52. Afterwards: checking **$3,400**, tickets 101/102/103 **open**, 0 payments. The original DB hash never changed.
3. **The run:** Isabel ran each ticket from the dashboard and personally approved every request. Nothing was approved by Claude or by an agent. Order: 102 → 101 → 103.

Final-run execution IDs (all `context: live` in `output/audit_trail.json`, after the reset event): `exec-20261011-233834-63b757`, `exec-20261011-233904-374c46` (102); `exec-20261011-233925-cd3137`, `exec-20261011-233951-6974a5`, `exec-20261011-234053-1d6673` (101); `exec-20261011-234203-845cac` (103). All six runs **completed**: no step, tool-call or token limit reached, no timeout.

## Outcomes

| Ticket | Final status | What happened | Human decisions | Cash |
|---|---|---|---|---|
| **102** Rent | **resolved** | Boss → Facilities (lease: $2,400 due Sep 2, covered). Boss prepared rent request #1. After approval the agents continued and Boss resolved the ticket. Lease next due → Oct 2 | Approved #1 ($2,400) | $3,400 → $1,000 |
| **101** Tee | **in_progress** | Boss asked Inventory (0 in stock) and Accounting (invoice #501 overdue, paying leaves $160) in parallel; Boss prepared request #2; Customer Service drafted a reply. After approval: Inventory checked vendors, **asked Accounting directly** ($8 affordable), prepared restock order #1 (request #3); Boss updated the draft. After approval: order placed, arrives **Sep 5**; Boss kept the ticket in progress because the order isn't fulfilled yet | Approved #2 ($840) and #3 ($8) | $1,000 → $160 → $152 |
| **103** Discount | **blocked** (missing info) | Boss → Accounting ($22 cost, $58 list, $36 margin; **no target price → missing info**). Boss → Customer Service, which saved draft #3 asking for the price, then hit a provider **HTTP 500** (caught and reported). No discount, restock or promise | None needed | $0 |

**Final checking: $152** = $3,400 − $2,400 − $840 − $8. This matches the `payments` table (3 rows, all `approved_by = Isabel`) and `cash_accounts`. No pending or rejected requests.

## Expected vs. actual (highlights)
- **Matched my plan:**
  - 102 was handled first and only resolved after the payment executed.
  - 101 paid the invoice before restocking, respected the 5-day lead time, and didn't promise a date before the order existed.
  - 103 did not invent a discount.
  - Every payment waited for a human.
  - Agents continued automatically after approvals without duplicate requests.
- **Differed from my plan:**
  - **102:** Boss prepared the rent request itself, and Accounting never took part.
  - **101:** Boss delegated to Inventory and Accounting in parallel instead of Inventory → Accounting. Boss, not Accounting, prepared the invoice request, and Boss wrote draft #2 itself. The peer-to-peer Inventory → Accounting step did happen in run 2.
  - **103:** Inventory was skipped, so stock (8 of 20), the vendor, and the $264 restock vs. $152 left were not discussed.
- **Issues to know about:**
  - **103 — provider error:** Customer Service's last model call failed with a Portkey/gpt-6-luna HTTP 500 after the draft was saved. Boss's note says "draft was not created", but the database shows draft #3 exists.
  - **101 — outdated draft:** draft #2 still says "pending internal approval", because it was written before the approval. A person should update it before sending.

## Problem 9 deliverables
| File | Content |
|---|---|
| `output/desk_tickets.html` | Expected sections unchanged; **Actual** sections filled from the final run (agents, steps incl. your approvals, tools used, diagram, outcome); **Cash** tab with the payment-by-payment ledger |
| `output/resolved_tickets.json` | One entry per ticket: final status, summary, agents and what they did, MCP tools, human approvals, drafts, restock orders, blockers, execution IDs, errors |
| `output/resolved_board.html` | Real React dashboard screenshots of 101/102/103 after the final outcomes, embedded (works offline) |
| `output/audit_trail.json` | Full history kept; the final run's 132 live events follow the 23:36:52 reset |

## Dashboard improvement made during Problem 9
After the runs finished, the dashboard only showed the *latest* run of a ticket (for 102, just the short continuation). It now shows **all runs of the ticket since the last reset** when nothing is running, and still follows the live run while agents work. A `?ticket=` deep link was added for the screenshots. Both are frontend-only changes; no backend or data changed.

---

# After Problem 9: Improving the Agent Team

The original final-run evidence is unchanged (`desk_tickets.html` Actual sections, `resolved_tickets.json`, `resolved_board.html`, and the working DB were verified by hash). All tests below used isolated copies of the original database. Test approvals there were made by "Test approver (isolated copy)"; no real approvals or payments.

## What the final run taught us
| Ticket | Worked | Didn't |
|---|---|---|
| 101 | Payments and restock coordinated; no duplicates; correct "in progress" | 8 steps / 5 delegations / 16 tool calls; Boss prepared the invoice payment; the draft leaked internal details ("pending internal approval") |
| 102 | Rent paid and resolved after approval | Boss prepared the payment itself; **Accounting was never involved** |
| 103 | Didn't invent a price | Inventory skipped; no commercial proposal; after a provider error, Boss claimed no draft existed (it did) |

## What changed (enforced in code, not only in prompts)
1. **Clear roles.** Each agent only sees the MCP tools of its role (`backend/mcp_client.py` `AGENT_TOOLS`). If a hidden call slips through, the guard refuses it as a `role_refusal` and names the owner.

   | Agent | Tools it can use |
   |---|---|
   | Boss | tickets, ticket status |
   | Inventory | stock, vendors, restocks |
   | Accounting | cash, invoices, pricing, payment requests |
   | Facilities | leases |
   | Customer Service | ticket, drafts |

   Only Accounting can prepare payments, so it is always involved in financial decisions.
2. **Efficient communication** (`backend/agents.py`):
   - Specialists receive a **ticket brief** (facts and product name), so they don't re-read the ticket.
   - Each agent asks each specialist **at most once per run**: a repeat returns the earlier answer.
   - A specialist that is **already working** in a parallel branch is never started twice.
   - Only **Boss** asks Customer Service, and only when the situation is settled: never while a payment waits for approval, and never for non-customer tickets such as a landlord's rent notice.
   - Full connectivity remains: any agent can still delegate to any other.
3. **Honest attribution.** `requested_by` / `drafted_by` / `updated_by` are set by the system from the agent that actually called the tool.
4. **Error handling and truth** (`backend/orchestrator.py`):
   - Successful actions are tracked. If an agent fails later, its findings list `completed_actions` ("hit an error AFTER completing: draft #3"), so nothing is lost or repeated.
   - After every run, the outcome is **re-read from the database**. Wrong claims are corrected (draft id, approvals missing from Boss's list) and recorded as `outcome_verified` in the audit trail, then shown in the dashboard.
5. **Customer messages** (`draft_customer_message`):
   - Drafts that mention internal details are refused: invoices, vendors, cash, margins, approvals, SKU codes, ticket numbers, "guarantee".
   - A **specific delivery date is refused unless a restock order is actually placed**.
   - The Customer Service prompt has a style guide: name, 60–120 words, friendly, clear next step, a question only if the customer's decision is needed, exact product name, signed "The Campus Customs Team".
6. **Proactive proposals.** `get_pricing_and_margin` accepts `discount_options_pct` (an existing tool, extended rather than duplicated). For a discount request, Boss asks Inventory (stock, shortfall, vendor, lead time) and Accounting (options and margins, plus whether the restock is affordable) in parallel, then has Customer Service draft **one concrete proposal** with its limits. A person reviews that draft before sending, and **that review is the human approval of the discount**.
7. **Prompts rewritten** (`backend/prompts/`). Your original responsibility text is kept as the first paragraph of each. Added: a routing table for Boss, when to talk directly, efficiency rules, verification rules, and when to mark `missing_info` vs. `in_progress`.

## Review → Edit → Mark as Sent
- **Database:** new table `customer_message_sends(draft_id UNIQUE, ticket_id, sent_by, sent_at, customer_reply_needed)`, created on the first write. The UNIQUE constraint means a message can never be marked as sent twice.
- **Human-only MCP tools** (not visible to agents):
  - `edit_customer_draft`: saves your edited version as a new draft; agents can't overwrite a person's edit.
  - `mark_message_sent`: records the date and time and updates the ticket:
    - a payment is still waiting → unchanged (`waiting_approval`);
    - the customer must reply → **`waiting_customer`** (new status);
    - otherwise → `in_progress`.

    It **never resolves** a ticket.
- **Resolving rule:** `update_ticket_status('resolved')` is refused while a current draft has not been marked as sent.
- **Routes:**
  - `PUT /messages/{id}` (body `{body, edited_by}`) saves an edit.
  - `POST /messages/{id}/mark-sent` (body `{sent_by, customer_reply_needed, continue_agents}`) marks a message as sent. A second call returns 409. If work remains and nobody else needs to act, the agents continue with a prompt saying the message was already sent.
- **Dashboard:** a **Customer messages** panel lets you edit the draft and offers Copy text / Save edits / **Mark as Sent**.
  - The Mark as Sent dialog asks for your name, whether the customer needs to reply, and whether the agents should continue, plus an "I sent this message" confirmation.
  - The button is locked while saving and disabled after sending; sent messages show the date, time and name.
  - There's a new purple status, "Waiting for customer". The timeline shows edits, sends, role refusals and corrections.

## Controlled comparison (isolated copy; same order 102 → 101 → 103; `output/improvement_tests.json`)
| | 102 before → after | 101 before → after | 103 before → after |
|---|---|---|---|
| Final status | resolved → **resolved** | in_progress → **in_progress** | blocked → **blocked**, then **waiting_customer** after Mark as Sent |
| Agents | Boss, Facilities → Boss, Facilities, **Accounting** | same 4 | Boss, Accounting, CS → **+ Inventory** |
| Payment prepared by | Boss → **Accounting** | Boss + Inventory → **Accounting + Inventory** | — |
| Agent steps / delegations | 3/1 → 4/2 | 8/5 → **11/8** | 3/2 → 4/3 |
| MCP calls / output tokens | 4/566 → 8/1,271 | 16/2,691 → **23/5,371** | 3/728 → 7/2,366 |
| Customer draft | — (correctly none) | leaked internal info → clean; date only after the order was placed | "what price?" → **10% proposal, 8 now, rest subject to confirmation** |
| Mark as Sent twice | — | 200 → **409** | 200 → **409** |

Offline: **32/32 tests pass**: 19 original safeguards, adjusted for the new rules, plus 13 new ones covering roles, the draft style and date rules, Mark as Sent, human edits, pricing options, an error after a completed draft (kept and corrected, no duplicate), ticket briefs, and Customer Service waiting while an approval is pending. All execution limits are unchanged.

## Honest limitations
- **101 is still less efficient than the original run** in this test (11 vs 8 steps, about 2× tokens). The roles are now correct (Accounting prepares the payment, Inventory the restock), but the extra correct handoffs cost more. One intermediate test run of 101 reached 8 steps, so results vary between runs. More tuning of 101 is the next step.
- Results come from single runs of a non-deterministic model; repeated runs differ in wording and step counts.
- "Waiting for customer" ends when a person acts. There is no inbox integration, by design.

### Fix: ticket 101 ending "blocked" with nothing to approve (2026-10-12)
In a live run after a reset, Accounting judged the $840 invoice affordable but **withheld** the payment request because rent was also due, so Boss marked 101 `blocked`. That was a dead end with nothing for a person to approve. Two prompt fixes:
- **Accounting** now always prepares a needed, affordable payment and states the trade-off ("after rent and this invoice, $160 is left"); **the person decides the priority** when approving. The tools still refuse anything that could make cash negative.
- **Boss** now avoids dead ends: if a payment or purchase could remove a blocker, it makes sure the owner prepares the request. `blocked` is used only when nothing the team can prepare would help.

Verified twice on an isolated copy: 101 → `waiting_approval` with Accounting's $840 request (4 agent steps, 9 tool calls).

### Fix: approval during a run, and the missing customer message (2026-10-12)
- **Race:** the dashboard shows a request as soon as an agent prepares it, while the run may still be finishing. Approving then skipped the agents' continuation ("ticket already running"), so 101 stopped after the invoice payment. Now the continuation is **queued** (`QUEUED` in `backend/main.py`) and starts automatically when the current run ends. The same applies to Mark as Sent. The dashboard says "Agents will continue as soon as the current run finishes".
- **Customer message:** when a run ends with a customer ticket settled for now (nothing waiting for approval), no current draft, and something changed since the last sent message, the orchestrator itself asks Customer Service for the draft, using Boss's final decision (`needs_customer_message` in `backend/orchestrator.py`, audited as `customer_message_requested`). The Customer messages panel is now always visible on customer tickets and explains when the draft will appear.
- **Leaner continuations:** the continuation prompt says what the approval unlocked (invoice → restock may be possible; restock → only the customer update) and tells Boss not to ask specialists to re-confirm known facts.
- **Verified** on an isolated copy, approving each request the moment it appeared: invoice $840 (Accounting) → restock $8 (Inventory, continuation queued) → order placed for Sep 5 → customer draft created. Final status `in_progress`, 3 runs, 9 agent steps.
