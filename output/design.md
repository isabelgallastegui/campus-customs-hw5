# Campus Customs Operations Desk: Dashboard Design

The dashboard (`frontend/`, React + Vite + TypeScript) is the screen a shop manager keeps open all day. It answers three questions at a glance: **What's happening? What needs me? What can I do?** Everything on it comes from the FastAPI backend (`http://localhost:8000`), which reads and writes the real working database through the MCP server. There is no mock data.

Run it: `cd backend && uvicorn main:app --reload --port 8000`, then `cd frontend && npm run dev` and open `http://localhost:5173`.

## Layout

```
┌──────────────────────────────── Header: name · shop date · database · Live/Connected · Reset ─┐
│ [ Checking balance ]  [ Awaiting your approval ]  [ Tickets by status ]  [ Agent team ]        │  ← summary cards
├──────────────┬───────────────────────────────────────────────────────┬────────────────────────┤
│ Ticket queue │  Selected ticket: header + big status badge           │ Human approvals        │
│  101 ▸ Run   │  "What's needed next" banner                          │  pending requests      │
│  102 ▸ Run   │  Agents (5 cards, live)                               │  → Review & approve    │
│  103 ▸ Run   │  Who talks to whom (diagram) │ Live timeline          │  recent decisions      │
│              │  Run summary: Boss decision · what each agent did ·   │ House rules            │
│              │  customer draft                                       │                        │
└──────────────┴───────────────────────────────────────────────────────┴────────────────────────┘
```

- **Left: the queue.** One card per ticket with its status, a one-line hint ("1 request needs your decision · $2,400") and a **Run agents** button.
- **Center: the workspace** for the selected ticket, read top to bottom in the order things happen: status → what to do next → who is working → how they talk → what they decided.
- **Right: things only a human can do.** Approvals sit apart from the agents' work, so it's always clear who is responsible for the money.
- **Top: four summary cards.** Cash, the approval count (it turns amber when something waits), ticket counts by status, and the agent team's live state.

On smaller screens the columns stack: queue, then workspace, then approvals. The ticket cards become a grid, and the agent cards wrap.

## How the agents are visually different

The identity comes from the Problem 6 Desk Tickets page. Each agent always has the **same icon, color and position** everywhere:

| Agent | Icon | Color |
|---|---|---|
| Boss | briefcase | navy |
| Inventory | box | amber |
| Accounting | calculator | green |
| Facilities | building | brown |
| Customer Service | speech bubble | wine |

- Agents taking part in the current run are **in color**; the others stay **light gray**, so you see participation instantly.
- A working agent **pulses** (its card glows and its node ripples in the diagram). Finished agents say **Done** with a one-line summary; stopped ones say **Stopped** in red.
- Each agent card lists the **MCP tools** it actually used (e.g. `check_stock_availability`) and how many delegations it sent and received (↗ / ↙).

## How you follow the activity

The dashboard refreshes itself: every **1.5 s while agents are working or a payment is processing**, every 6 s otherwise. You never reload the page.

1. **Live timeline.** Every step appears as it happens, with the time:
   - an agent **starts** (with its objective);
   - **delegates** ("Boss → Facilities: review lease #1…");
   - **uses an MCP tool**;
   - **prepares a request for approval** (amber);
   - **reports back**;
   - **finishes** (green);
   - hits a **limit or error** (red).

   Your own approvals and payments appear in the same line, and when agents continue after an approval, a new run section starts automatically. It auto-scrolls unless you scroll up to read. "Show model turns" reveals the low-level model calls if you want them.
2. **Who talks to whom.** The same fixed five-node diagram as Problem 6. Numbered arrows appear in order: **solid** = an agent asks another, **dashed** = it reports back. It shows that agents talk to each other (Facilities → Accounting), not only to Boss.
3. **Run summary.** When a run ends:
   - Boss's decision with a status badge;
   - what needs approval, next steps, what's still open, and any safety flags;
   - a short card per agent saying what it did, including what information is missing;
   - the customer message draft (never sent).

## How ticket statuses are shown

Each status has its own color, icon and words, shown the same way on the queue card, the workspace header and the summary strip:

| Status | Look | Meaning |
|---|---|---|
| **Open** | gray, ticket icon | Not handled yet |
| **Agents working** | navy, blinking | A run is in progress |
| **Awaiting approval** | **amber**, shield | A payment or purchase waits for you |
| **Blocked · missing info** | **red**, question mark | Someone must provide information (e.g. the club's target price); the missing items are listed |
| **Blocked** | red, warning | Something else prevents progress |
| **Run stopped** | red, stop sign | A limit, error, timeout or server restart ended the run; the reason is shown and you can run again |
| **In progress** | light blue | Work started, not finished (e.g. right after a payment) |
| **Resolved** | **green**, check | Truly finished. The button turns into "Done" |

Under the header, a **"What's needed next"** banner, colored like the status, says it in plain words: "Your approval is needed: Rent · lease #1 · $2,400", or "Blocked: information is missing: proposed discounted unit price". A ticket is never shown as running after its run ended: interrupted runs are marked **Run stopped**.

## How cash and approvals are shown

- **Checking balance** is the first and largest card. It also shows how much is free and how much is already promised to waiting requests (amber bar). When a payment goes through, the number updates and a **−$2,400** chip flashes.
- **Human approvals** lists each pending request with its amount, reason, ticket, and which agent prepared it. **Review** opens a confirmation dialog:
  - checking **now → after** this payment;
  - your name, recorded as the approver;
  - a "I confirm" checkbox.
  - **Approve & pay** stays disabled until everything is filled in. It is **disabled while processing** ("Paying…"), so you can't double-click.
  - If the payment would make cash negative, the dialog says so and won't allow it. If someone else already paid it, it says "Already handled".
  - If the backend refuses, the **reason is shown** in the dialog and in a red message. It is never shown as a success.
- After an approval, a green confirmation says what was paid and the new balance. The ticket leaves "Awaiting approval", and if the agents continue, their activity appears in the timeline on its own.
- **Reset data** (top right) asks you to type `RESET` and is disabled while agents are working.

## Why it's practical

- **Attention first.** Amber means "you're needed" (approvals, the approval card, the ticket hint). Red means "stuck". Green means "done". You can scan the screen in seconds.
- **One place per responsibility.** Agents' work in the middle, human decisions on the right. Money only moves from the right column.
- **Safe by design.** The dashboard can't do anything the backend wouldn't allow. Confirmation steps, disabled buttons and shown errors protect against double payments and mistakes.
- **Honest.** Statuses come from the database and the audit trail, not from guesses. A stopped run says why; missing information says exactly what is missing.

## Why it's creative

- The agents feel like a **team on a board**: colored avatars that light up, pulse while working and show the tools they hold.
- The **communication diagram** turns delegation into a picture you can read, with numbered, animated arrows and dashed "report back" lines.
- The **live timeline** reads like a story of the run, with your own approvals woven into it.
- The visual language (navy, warm paper background, serif headings, amber accents) is the same Campus Customs identity as the HW 4 website and the Problem 6 page, so it all feels like one product.
