# Campus Customs: Multi-Agent Operations Desk (HW5)

Five AI agents (**Boss, Inventory, Accounting, Facilities, Customer Service**) manage the open tickets of the fictional Campus Customs shop. They work through an **MCP server** on a SQLite database, a **FastAPI** backend coordinates them, and a **React** dashboard lets a person follow their work, approve payments, and review customer messages.

- Model: `gpt-6-luna` through **Portkey** (PydanticAI agents).
- Agents only **prepare** payments and purchases; **a person approves them** in the dashboard before anything is paid.
- Full documentation for managers: [`output/harness.md`](output/harness.md). Dashboard design: [`output/design.md`](output/design.md).

## Project structure

```
hw5/
├── AI_prompts.md            # every prompt used, verbatim
├── requirements.txt         # Python dependencies
├── .env.example             # copy to .env and add your Portkey key
├── .gitignore
├── .mcp.json                # MCP config for Claude Code
├── README.md
├── data/
│   ├── campus_customs.db        # original database (never modified)
│   └── campus_customs_new.db    # working copy the agents use
├── mcp_server/
│   ├── server.py            # FastMCP server: 14 agent tools + human-only payment/message tools
│   ├── db.py                # SQLite helpers (working DB only, atomic writes)
│   └── README.md
├── backend/
│   ├── main.py              # FastAPI routes
│   ├── models.py            # Pydantic data models
│   ├── agents.py            # the five agents + delegation rules
│   ├── orchestrator.py      # runs one ticket, verifies the outcome against the DB
│   ├── mcp_client.py        # the agents' only route to the data (role-based tools, limits)
│   ├── config.py, audit.py, run.py
│   └── prompts/             # boss.md, inventory.md, accounting.md, facilities.md,
│                            # customer_service.md, shared_rules.md
├── frontend/                # React + Vite + TypeScript dashboard
├── tests/                   # offline safeguard tests + live/route checks
└── output/
    ├── harness.md               # tables, tools, agents, routes, safety, results
    ├── mcp_smoke.json           # Problem 4 MCP tool tests
    ├── desk_tickets.html        # Expected vs. Actual, Cash, Reflection (open directly)
    ├── design.md                # dashboard design
    ├── resolved_tickets.json    # final outcomes of tickets 101-103
    ├── resolved_board.html      # real dashboard screenshots (open directly)
    ├── audit_trail.json         # full agent/human activity history
    └── github_url.txt
```

## 1. Requirements
- **Python 3.12+** (developed with 3.14) and **Node.js 18+** (developed with 24).
- A **Portkey API key** with access to `gpt-6-luna`.
- macOS or Linux paths are shown. On Windows, use `.venv\Scripts\python` instead of `.venv/bin/python` (the backend launches the MCP server from `.venv/bin/python`, so WSL is easiest).

## 2. Install

```bash
git clone https://github.com/isabelgallastegui/campus-customs-hw5.git
cd campus-customs-hw5

# Python (the virtual environment must be named .venv in the project root)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Frontend
cd frontend && npm install && cd ..
```

## 3. Configure the environment

```bash
cp .env.example .env
# edit .env and set PORTKEY_API_KEY=<your key>
```
The real `.env` is git-ignored and is never uploaded. The key is redacted from the audit trail.

## 4. Start the MCP server (optional on its own)
The backend **starts the MCP server automatically** (over stdio) whenever it needs it, so you don't have to run it separately. To run or inspect it on its own:

```bash
.venv/bin/python mcp_server/server.py
```
- In **Claude Code**, `.mcp.json` registers it as `campus-customs-hw51` (run Claude Code from the project root).
- The human-only tools (approve/reject/execute payments, edit/mark messages) are only registered when started with `CAMPUS_CUSTOMS_HUMAN_TOOLS=1`, which the backend does for the dashboard. Agents never see them.

## 5. Run the FastAPI backend (port 8000)

```bash
cd backend
../.venv/bin/uvicorn main:app --reload --port 8000
```
Check it at <http://localhost:8000/health>; interactive API docs are at <http://localhost:8000/docs>.

| Method | Route | Purpose |
|---|---|---|
| GET | `/tickets`, `/tickets/{id}` | Tickets 101–103 with status, runs, requests, drafts |
| POST | `/tickets/{id}/run` | Run the agent team (one run per ticket at a time) |
| GET | `/runs`, `/runs/{execution_id}` | Follow a run |
| GET | `/activity` | Audit events: delegations, tool calls, decisions |
| GET | `/payments` | Payment requests |
| POST | `/payments/{id}/approve` · `/reject` | Human approval (then agents continue) |
| PUT | `/messages/{id}` · POST `/messages/{id}/mark-sent` | Edit a customer draft · confirm you sent it |
| GET | `/cash` | Checking balance |
| POST | `/reset` | Restore the working DB (needs `{"confirm": "RESET"}`) |

## 6. Run the React dashboard (port 5173)

In a second terminal:
```bash
cd frontend
npm run dev
```
Open <http://localhost:5173>. Then:
1. Pick a ticket and press **Run agents**. Follow the agents in **Agents**, **Who talks to whom** and the **Live timeline**.
2. When a ticket turns amber (**Awaiting approval**), open **Human approvals → Review**, enter your name, confirm, and **Approve & pay** (or Reject). The agents continue automatically.
3. When a customer message is ready, review or edit it in **Customer messages**, send it yourself, then press **Mark as Sent**.

## 7. Reset the working database before a new run
`data/campus_customs_new.db` currently contains the results of my latest run. To start fresh from the untouched original, use any one of these:

- **Dashboard:** **Reset data** (top right) → type `RESET` → confirm.
- **API:**
  ```bash
  curl -X POST http://localhost:8000/reset -H "Content-Type: application/json" -d '{"confirm":"RESET"}'
  ```
- **Manually** (with the backend stopped):
  ```bash
  cp data/campus_customs.db data/campus_customs_new.db
  ```

The original `data/campus_customs.db` is never modified. The audit trail keeps all history.

## 8. Tests (optional)

```bash
.venv/bin/python -m pytest tests/test_safeguards.py tests/test_improvements.py -q   # offline, no API cost
RUN_LIVE=1 .venv/bin/python -m pytest tests/test_live_agents.py -q                  # live, uses the API
```
All tests run on temporary database copies and never touch `data/`.

## Business and safety rules (enforced in code)
- Human approval before any payment or purchase; agents can only prepare requests.
- Cash can never go negative; no payment can happen twice (atomic transactions).
- Vendors with unpaid invoices can't ship; vendor lead times and `desk.date_today` are used.
- No real emails: customer messages are drafts that a person sends and marks as sent.
- Limits per ticket: 12 agent steps, delegation depth 3, 20 MCP calls; 8,000 output tokens per model request (enforced by the provider).
- Each agent only has the tools of its role: payments → Accounting, restocks → Inventory, messages → Customer Service, ticket status → Boss.

## Final results (original final run)
| Ticket | Outcome | Cash |
|---|---|---|
| 101 Classic Bulldog Tee | In progress: invoice #501 paid and 1 tee restocked (arrives Sep 5) | −$848 |
| 102 Rent | Resolved: rent paid after approval | −$2,400 |
| 103 Bulk hoodie discount | Blocked: missing customer information (target price) | $0 |

Checking: **$3,400 → $152**. Details: [`output/resolved_tickets.json`](output/resolved_tickets.json), [`output/desk_tickets.html`](output/desk_tickets.html), [`output/resolved_board.html`](output/resolved_board.html).
