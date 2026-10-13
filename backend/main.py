"""FastAPI backend for the Campus Customs dashboard.

Start from the backend/ folder:  uvicorn main:app --reload --port 8000

Every read and write goes through the existing MCP server (the same tools the
agents use, plus the human-only payment tools). The only file operation here
is the explicit, confirmed reset, which copies the untouched original database
over the working copy.

Routes
  GET  /health                          server, model, and database in use
  GET  /tickets                         tickets 101-103 with status, run state, payment requests
  GET  /tickets/{id}                    one ticket in detail
  POST /tickets/{id}/run                start the agent team on a ticket (one run per ticket at a time)
  GET  /runs, /runs/{execution_id}      execution status and outcome
  GET  /activity                        recent audit events (filter by ticket or execution)
  GET  /payments                        payment requests (optionally by status)
  POST /payments/{id}/approve           human approves + executes, then agents continue
  POST /payments/{id}/reject            human rejects a pending request
  PUT  /messages/{id}                   human edits the current customer draft (new version)
  POST /messages/{id}/mark-sent         human confirms they sent the draft (date/time saved once)
  GET  /cash                            checking balance from the working database
  POST /reset                           restore the working database (needs {"confirm": "RESET"})
"""

import asyncio
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Started from backend/, so make the project root importable for `backend.*` and `mcp_server`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend import audit  # noqa: E402
from backend.config import MODEL_NAME  # noqa: E402
from backend.mcp_client import human_client  # noqa: E402
from backend.orchestrator import new_execution_id, run_ticket  # noqa: E402

TICKET_IDS = (101, 102, 103)
CONTEXT = os.getenv("CAMPUS_CUSTOMS_CONTEXT", "live")        # "test" when running tests
RUN_TIMEOUT_SECONDS = int(os.getenv("CAMPUS_CUSTOMS_RUN_TIMEOUT", 600))
ORIGINAL_DB = PROJECT_ROOT / "data" / "campus_customs.db"
WORKING_DB = Path(os.getenv("CAMPUS_CUSTOMS_DB", PROJECT_ROOT / "data" / "campus_customs_new.db")).resolve()

# Decision status from Boss -> ticket status in the database (when no payment is waiting).
DECISION_TO_TICKET = {"resolved": "resolved", "in_progress": "in_progress", "blocked": "blocked",
                      "missing_info": "blocked", "waiting_approval": "in_progress"}
# Statuses that only a person's action changes (an agent run can't move a ticket out of them by guessing).
WAITING_ON_PEOPLE = {"waiting_customer"}

app = FastAPI(title="Campus Customs Desk API", version="1.0")
# The Vite dashboard (Problem 8) runs on http://localhost:5173.
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
                   allow_methods=["*"], allow_headers=["*"])

# In-memory execution registry. The audit trail is the permanent record.
RUNS: dict[str, dict[str, Any]] = {}
ACTIVE: dict[int, str] = {}           # ticket_id -> execution_id currently running
# Continuations requested while the ticket was still running; started as soon as that run ends.
QUEUED: dict[int, tuple[str, str]] = {}
APPROVAL_LOCK = asyncio.Lock()        # one human payment action at a time


# ------------------------------------------------------------------ helpers

async def mcp(*calls: tuple[str, dict]) -> list[Any]:
    """Run one or more MCP tool calls in a single session and return their data."""
    try:
        async with human_client() as client:
            results = []
            for name, args in calls:
                res = await client.call_tool(name, args, raise_on_error=False)
                if res.is_error:
                    raise HTTPException(502, f"MCP tool {name} failed: {res.content[0].text if res.content else ''}")
                results.append(res.data)
            return results
    except HTTPException:
        raise
    except Exception as exc:  # server could not start, timed out, etc.
        raise HTTPException(502, f"MCP server error: {type(exc).__name__}: {exc}")


def human_event(ticket_id: int | None, action: str, status: str, **kw) -> None:
    """Audit an action taken by a person (or the backend on their behalf)."""
    run = SimpleNamespace(execution_id=kw.pop("execution_id", None), ticket_id=ticket_id, context=CONTEXT)
    audit.record(run, kw.pop("agent", "human"), action, status, **kw)


def run_summary(execution_id: str) -> dict:
    r = RUNS[execution_id]
    return {k: v for k, v in r.items() if k != "task"}


def run_from_audit(execution_id: str, events: list[dict]) -> dict | None:
    """Rebuild a finished run from the audit trail (runs in memory are lost on restart)."""
    ev = [e for e in events if e.get("execution_id") == execution_id]
    start = next((e for e in ev if e["action"] == "run_started"), None)
    end = next((e for e in reversed(ev) if e["action"] in ("run_finished", "run_timeout", "run_error")), None)
    if not start:
        return None
    if not end:   # started but never finished, and not running now: the server restarted mid-run
        return {"execution_id": execution_id, "ticket_id": start["ticket_id"], "trigger": "from audit trail",
                "status": "failed", "started_at": start["timestamp"], "finished_at": None,
                "stop_reason": "interrupted: the server restarted before this run finished",
                "ticket_status": None, "outcome": None}
    finished = [e for e in ev if e["action"] == "agent_run_finished" and isinstance(e.get("result"), dict)]
    boss = [e["result"] for e in finished if e["agent"] == "boss"]
    findings = [{"agent": e["agent"], **e["result"]} for e in finished if e["agent"] != "boss"]
    stats = end.get("result") if isinstance(end.get("result"), dict) else {}
    stop = stats.get("stop_reason") or (end.get("result") if end["action"] != "run_finished" else None)
    status = {"run_timeout": "timed_out", "run_error": "failed"}.get(end["action"],
                                                                      "completed" if end["status"] == "completed" else "stopped")
    decision = boss[-1] if boss else None
    return {"execution_id": execution_id, "ticket_id": start["ticket_id"], "trigger": "from audit trail",
            "status": status, "started_at": start["timestamp"], "finished_at": end["timestamp"],
            "stop_reason": stop, "ticket_status": None,
            "outcome": {"execution_id": execution_id, "ticket_id": start["ticket_id"], "completed": decision is not None,
                        "stop_reason": stop, "agent_steps": stats.get("agent_steps", 0),
                        "mcp_tool_calls": stats.get("mcp_tool_calls", 0), "decision": decision,
                        "findings": findings, "unresolved": (decision or {}).get("unresolved", [])}}


def last_run(ticket_id: int, events: list[dict] | None = None) -> dict | None:
    runs = [r for r in RUNS.values() if r["ticket_id"] == ticket_id]
    if runs:
        return run_summary(runs[-1]["execution_id"])
    if events is not None:
        exec_id = latest_execution(ticket_id, events)
        return run_from_audit(exec_id, events) if exec_id and ticket_id not in ACTIVE else None
    return None


def read_audit() -> list[dict]:
    return json.loads(audit.AUDIT_PATH.read_text()) if audit.AUDIT_PATH.exists() else []


def latest_execution(ticket_id: int, events: list[dict]) -> str | None:
    """Newest run for a ticket: from memory, or from the audit trail after a restart."""
    if ticket_id in ACTIVE:
        return ACTIVE[ticket_id]
    runs = [r for r in RUNS.values() if r["ticket_id"] == ticket_id]
    if runs:
        return runs[-1]["execution_id"]
    # Runs from before the most recent reset belong to an older copy of the data.
    resets = [e["timestamp"] for e in events if e["action"] == "database_reset" and e.get("context") == CONTEXT]
    since = resets[-1] if resets else ""
    starts = [e for e in events if e.get("ticket_id") == ticket_id and e["action"] == "run_started"
              and e.get("context") == CONTEXT and e["timestamp"] > since]
    return starts[-1]["execution_id"] if starts else None


def last_note(notes: str | None) -> str | None:
    lines = [l for l in (notes or "").splitlines() if l.strip()]
    return lines[-1] if lines else None


def live_requests(ticket: dict) -> list[dict]:
    return [r for r in ticket["payment_requests"] if r["status"] in ("pending_approval", "approved")]


async def find_ticket_for_request(req: dict) -> int | None:
    """A request's ticket, from its ticket_id or from what it pays (invoice/lease/restock)."""
    if req.get("ticket_id"):
        return req["ticket_id"]
    details = await mcp(*[("get_ticket_details", {"ticket_id": t}) for t in TICKET_IDS])
    for d in details:
        t = d["ticket"]
        if (req["kind"] == "invoice" and t["invoice_id"] == req["ref_id"]) or \
           (req["kind"] == "rent" and t["lease_id"] == req["ref_id"]) or \
           any(o["id"] == req["ref_id"] for o in d["restock_orders"] if req["kind"] == "restock"):
            return t["id"]
    return None


async def sync_ticket_status(ticket_id: int, reason: str, decision_status: str | None = None) -> str:
    """Set the ticket status from facts, not hopes.

    - Any payment request still pending or approved-but-unpaid -> waiting_approval.
    - Otherwise follow Boss's decision; 'resolved' is refused by the MCP tool
      if anything is still waiting, so a ticket is never resolved too early.
    - If the run stopped without a decision, the status is left as it was.
    """
    (details,) = await mcp(("get_ticket_details", {"ticket_id": ticket_id}))
    current = details["ticket"]["status"]
    sent_waiting = [d for d in details["message_drafts"] if d.get("sent") and d.get("customer_reply_needed")]
    if live_requests(details):
        target = "waiting_approval"
    elif decision_status in ("missing_info", "blocked") and sent_waiting:
        target = "waiting_customer"     # we asked the customer and are waiting for the answer
    elif current in WAITING_ON_PEOPLE and decision_status != "resolved":
        return current
    elif decision_status in DECISION_TO_TICKET:
        target = DECISION_TO_TICKET[decision_status]
    elif current == "waiting_approval":
        target = "in_progress"          # nothing is waiting any more
    else:
        return current
    if target != current:
        (res,) = await mcp(("update_ticket_status", {"ticket_id": ticket_id, "status": target,
                                                     "note": reason, "updated_by": "system"}))
        if not res.get("ok"):
            return current
    return target


# ------------------------------------------------------------- agent runs

async def _execute_run(execution_id: str, ticket_id: int, prompt: str | None) -> None:
    run = RUNS[execution_id]
    try:
        outcome = await asyncio.wait_for(
            run_ticket(ticket_id, context=CONTEXT, prompt=prompt, execution_id=execution_id),
            timeout=RUN_TIMEOUT_SECONDS)
        run["outcome"] = outcome.model_dump()
        run["stop_reason"] = outcome.stop_reason
        if outcome.completed:
            run["status"] = "completed"
        else:
            run["status"] = "stopped"   # an execution limit or error ended it early
        decision = outcome.decision.status if outcome.decision else None
        run["ticket_status"] = await sync_ticket_status(
            ticket_id, f"After run {execution_id}: {decision or 'stopped - ' + str(outcome.stop_reason)}",
            decision)
    except asyncio.TimeoutError:
        run.update(status="timed_out", stop_reason=f"run timeout ({RUN_TIMEOUT_SECONDS}s)")
        human_event(ticket_id, "run_timeout", "stopped", agent="system", execution_id=execution_id,
                    result=run["stop_reason"])
    except Exception as exc:  # never leave a ticket "running"
        run.update(status="failed", stop_reason=f"{type(exc).__name__}: {exc}")
        human_event(ticket_id, "run_error", "error", agent="system", execution_id=execution_id,
                    result=run["stop_reason"])
    finally:
        run["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        ACTIVE.pop(ticket_id, None)
        if ticket_id in QUEUED:   # a person approved/sent something while this run was finishing
            prompt, trigger = QUEUED.pop(ticket_id)
            nxt = start_run(ticket_id, prompt, trigger=trigger)
            human_event(ticket_id, "agents_continue", "started", agent="system", execution_id=None,
                        result={"started": True, "queued": True, **nxt})


def start_run(ticket_id: int, prompt: str | None = None, trigger: str = "manual") -> dict:
    """Start a background run. Check-and-set happens without awaiting, so it is atomic."""
    if ticket_id in ACTIVE:
        raise HTTPException(409, f"Ticket {ticket_id} is already running ({ACTIVE[ticket_id]}).")
    execution_id = new_execution_id()
    ACTIVE[ticket_id] = execution_id
    RUNS[execution_id] = {
        "execution_id": execution_id, "ticket_id": ticket_id, "trigger": trigger,
        "status": "running", "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "finished_at": None, "stop_reason": None, "ticket_status": None, "outcome": None,
    }
    RUNS[execution_id]["task"] = asyncio.create_task(_execute_run(execution_id, ticket_id, prompt))
    return run_summary(execution_id)


def continuation_prompt(ticket_id: int, req: dict, payment: dict, approver: str) -> str:
    """Tell Boss what the human did and what is already done, so nothing is repeated."""
    prev = last_run(ticket_id)
    done = []
    if prev and prev.get("outcome"):
        out = prev["outcome"]
        if out.get("decision"):
            done.append(f"Previous decision: {out['decision']['decision']}")
        done += [f"{f['agent']}: {f.get('summary')}" for f in out.get("findings", [])][:6]
    effects = {k: v for k, v in payment.items() if k not in ("ok", "payment_request_id")}
    return (
        f"Continue ticket {ticket_id} after a human decision.\n"
        f"Payment request #{req['id']} ({req['kind']} {req['ref_id']}, ${req['amount']:,.2f}) was approved by "
        f"{approver} and executed: {json.dumps(effects)}.\n"
        "Already completed earlier (do not repeat these steps or recreate requests/drafts):\n- "
        + ("\n- ".join(done) if done else "see get_ticket_details") +
        "\nWhat this approval unlocked: " + {
            "invoice": "the vendor is no longer blocked by this invoice, so a restock may now be possible (Inventory).",
            "rent": "rent is paid; nothing else may be needed.",
            "restock": "the restock order is placed; only the customer update remains (the system asks Customer Service).",
        }.get(req["kind"], "see the ticket.") +
        "\nRead the ticket once with get_ticket_details. Delegate only if a NEW action is needed; don't ask "
        "specialists to re-confirm payments or facts listed above. Use status 'resolved' only if nothing at all remains."
    )


# ------------------------------------------------------------------ models

class ApproveBody(BaseModel):
    approver_name: str = Field(min_length=2, description="The person approving.")
    note: str = ""
    continue_agents: bool = True


class RejectBody(BaseModel):
    approver_name: str = Field(min_length=2)
    note: str = ""


class EditBody(BaseModel):
    body: str = Field(min_length=10)
    edited_by: str = Field(min_length=2)


class MarkSentBody(BaseModel):
    sent_by: str = Field(min_length=2, description="The person who sent the email.")
    customer_reply_needed: bool = False
    continue_agents: bool = True


class ResetBody(BaseModel):
    confirm: str = Field(description='Must be exactly "RESET".')


# ------------------------------------------------------------------ routes

@app.get("/health")
async def health():
    return {"ok": True, "model": MODEL_NAME, "database": WORKING_DB.name,
            "using_default_database": WORKING_DB == (PROJECT_ROOT / "data" / "campus_customs_new.db").resolve(),
            "context": CONTEXT, "active_runs": ACTIVE}


@app.get("/tickets")
async def list_tickets():
    details = await mcp(*[("get_ticket_details", {"ticket_id": t}) for t in TICKET_IDS])
    events = read_audit()
    out = []
    for d in details:
        t = d["ticket"]
        out.append({**t, "running": t["id"] in ACTIVE, "active_execution_id": ACTIVE.get(t["id"]),
                    "latest_execution_id": latest_execution(t["id"], events), "last_note": last_note(t["notes"]),
                    "last_run": last_run(t["id"], events), "payment_requests": d["payment_requests"],
                    "waiting_for_human": live_requests(d), "message_drafts": d["message_drafts"],
                    "restock_orders": d["restock_orders"], "safety_warning": d.get("safety_warning")})
    return {"today": details[0]["today"], "tickets": out}


@app.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: int):
    if ticket_id not in TICKET_IDS:
        raise HTTPException(404, "Unknown ticket.")
    (d,) = await mcp(("get_ticket_details", {"ticket_id": ticket_id}))
    return {**d, "running": ticket_id in ACTIVE, "active_execution_id": ACTIVE.get(ticket_id),
            "last_run": last_run(ticket_id, read_audit()), "waiting_for_human": live_requests(d)}


@app.post("/tickets/{ticket_id}/run", status_code=202)
async def run_ticket_route(ticket_id: int):
    if ticket_id not in TICKET_IDS:
        raise HTTPException(404, "Unknown ticket.")
    return start_run(ticket_id)


@app.get("/runs")
async def list_runs(ticket_id: int | None = None):
    runs = [run_summary(e) for e, r in RUNS.items() if ticket_id is None or r["ticket_id"] == ticket_id]
    return {"runs": runs[::-1]}


@app.get("/runs/{execution_id}")
async def get_run(execution_id: str):
    if execution_id not in RUNS:
        raise HTTPException(404, "Unknown execution id (runs are kept until the server restarts; "
                                 "the audit trail keeps everything).")
    return run_summary(execution_id)


@app.get("/activity")
async def activity(ticket_id: int | None = None, execution_id: str | None = None,
                   limit: int = Query(150, ge=1, le=2000), since_reset: bool = False):
    """Recent audit events: what each agent did, which tools it used, and who delegated to whom.

    since_reset=true hides events from before the most recent database reset.
    """
    events = read_audit()
    if since_reset:
        resets = [i for i, e in enumerate(events) if e["action"] == "database_reset" and e.get("context") == CONTEXT]
        events = events[resets[-1] + 1:] if resets else events
    if ticket_id is not None:
        events = [e for e in events if e.get("ticket_id") == ticket_id]
    if execution_id:
        events = [e for e in events if e.get("execution_id") == execution_id]
    events = events[-limit:]
    flow = [{"from": e["agent"], "to": e["target"], "objective": e["detail"], "status": e["status"],
             "execution_id": e["execution_id"], "timestamp": e["timestamp"]}
            for e in events if e["action"] == "delegation" and e["status"] == "started"]
    tools = [{"agent": e["agent"], "tool": e["tool"], "status": e["status"],
              "execution_id": e["execution_id"], "timestamp": e["timestamp"]}
             for e in events if e["tool"]]
    return {"count": len(events), "events": events, "delegations": flow, "tool_calls": tools}


@app.get("/payments")
async def list_payments(status: str | None = None):
    (res,) = await mcp(("list_payment_requests", {"status": status} if status else {}))
    return res


@app.post("/payments/{request_id}/approve")
async def approve_payment(request_id: int, body: ApproveBody):
    """Human approval: check, approve, execute (atomic, in MCP), update the ticket, continue the agents."""
    async with APPROVAL_LOCK:
        (reqs, cash) = await mcp(("list_payment_requests", {}), ("get_cash_position", {}))
        req = next((r for r in reqs["payment_requests"] if r["id"] == request_id), None)
        if req is None:
            raise HTTPException(404, "Payment request not found.")
        ticket_id = await find_ticket_for_request(req)
        if req["status"] == "executed":
            human_event(ticket_id, "payment_refused", "refused", tool="execute_approved_payment",
                        detail={"request_id": request_id}, result="Already executed; no duplicate payment.")
            raise HTTPException(409, "Already paid. Duplicate payments are not allowed.")
        if req["status"] == "rejected":
            raise HTTPException(409, "This request was rejected.")
        if cash["checking_balance"] - req["amount"] < 0:
            human_event(ticket_id, "payment_refused", "refused", detail={"request_id": request_id},
                        result=f"Balance ${cash['checking_balance']} cannot cover ${req['amount']}.")
            raise HTTPException(409, "Not enough cash: paying would make checking negative.")

        if req["status"] == "pending_approval":
            (approved,) = await mcp(("approve_payment_request", {"request_id": request_id,
                                     "approver_name": body.approver_name, "note": body.note}))
            if not approved.get("ok"):
                raise HTTPException(400, approved.get("error"))
            human_event(ticket_id, "payment_approved", "approved", tool="approve_payment_request",
                        detail={"request_id": request_id, "approver": body.approver_name}, result=approved)

        (paid,) = await mcp(("execute_approved_payment", {"request_id": request_id}))
        if not paid.get("ok"):
            human_event(ticket_id, "payment_failed", "error", tool="execute_approved_payment",
                        detail={"request_id": request_id}, result=paid)
            raise HTTPException(409, paid.get("error"))
        human_event(ticket_id, "payment_executed", "executed", tool="execute_approved_payment",
                    detail={"request_id": request_id, "approver": body.approver_name}, result=paid)

    status, continuation = None, None
    if ticket_id is not None:
        status = await sync_ticket_status(
            ticket_id, f"Payment request #{request_id} approved by {body.approver_name} and executed.")
        if body.continue_agents and status != "resolved":
            prompt = continuation_prompt(ticket_id, req, paid, body.approver_name)
            trigger = f"approval of request #{request_id}"
            if ticket_id in ACTIVE:
                # Don't drop it: start right after the current run finishes.
                QUEUED[ticket_id] = (prompt, trigger)
                continuation = {"started": False, "queued": True,
                                "reason": f"starts when the current run ({ACTIVE[ticket_id]}) finishes"}
            else:
                continuation = {"started": True, **start_run(ticket_id, prompt, trigger=trigger)}
            human_event(ticket_id, "agents_continue", "started" if continuation["started"] else "queued",
                        agent="system", result=continuation)
    return {"ok": True, "payment": paid, "ticket_id": ticket_id, "ticket_status": status,
            "continuation": continuation}


@app.post("/payments/{request_id}/reject")
async def reject_payment(request_id: int, body: RejectBody):
    async with APPROVAL_LOCK:
        (reqs,) = await mcp(("list_payment_requests", {}))
        req = next((r for r in reqs["payment_requests"] if r["id"] == request_id), None)
        if req is None:
            raise HTTPException(404, "Payment request not found.")
        (res,) = await mcp(("reject_payment_request", {"request_id": request_id,
                            "approver_name": body.approver_name, "note": body.note}))
        if not res.get("ok"):
            raise HTTPException(409, res.get("error"))
    ticket_id = await find_ticket_for_request(req)
    human_event(ticket_id, "payment_rejected", "rejected", tool="reject_payment_request",
                detail={"request_id": request_id, "approver": body.approver_name}, result=res)
    status = await sync_ticket_status(ticket_id, f"Payment request #{request_id} rejected by "
                                                 f"{body.approver_name}.") if ticket_id else None
    return {"ok": True, "ticket_id": ticket_id, "ticket_status": status}


async def find_draft(draft_id: int) -> dict:
    details = await mcp(*[("get_ticket_details", {"ticket_id": t}) for t in TICKET_IDS])
    for d in details:
        for m in d["message_drafts"]:
            if m["id"] == draft_id:
                return {**m, "ticket": d["ticket"], "waiting": live_requests(d)}
    raise HTTPException(404, "Message draft not found.")


@app.put("/messages/{draft_id}")
async def edit_message(draft_id: int, body: EditBody):
    """A person edits the current draft before sending. Saved as a new version; nothing is sent."""
    await find_draft(draft_id)
    (res,) = await mcp(("edit_customer_draft", {"draft_id": draft_id, "body": body.body, "edited_by": body.edited_by}))
    if not res.get("ok"):
        raise HTTPException(409, res.get("error"))
    draft = await find_draft(res["draft_id"])
    human_event(draft["ticket_id"], "message_edited", "ok", tool="edit_customer_draft",
                detail={"from_draft": draft_id, "edited_by": body.edited_by}, result={"draft_id": res["draft_id"]})
    return {"ok": True, "draft_id": res["draft_id"], "changed": res.get("changed", True)}


@app.post("/messages/{draft_id}/mark-sent")
async def mark_sent(draft_id: int, body: MarkSentBody):
    """A person confirms they sent the email themselves. Saved once; never resolves a ticket."""
    draft = await find_draft(draft_id)
    ticket_id = draft["ticket_id"]
    (res,) = await mcp(("mark_message_sent", {"draft_id": draft_id, "sent_by": body.sent_by,
                                              "customer_reply_needed": body.customer_reply_needed}))
    if not res.get("ok"):
        human_event(ticket_id, "message_mark_sent_refused", "refused", tool="mark_message_sent",
                    detail={"draft_id": draft_id}, result=res.get("error"))
        raise HTTPException(409, res.get("error"))
    human_event(ticket_id, "message_marked_sent", "sent", tool="mark_message_sent",
                detail={"draft_id": draft_id, "sent_by": body.sent_by,
                        "customer_reply_needed": body.customer_reply_needed}, result=res)

    # Continue only if there is agent work left: not waiting on a person, not resolved.
    continuation = None
    status = res["ticket_status"]
    if body.continue_agents and status not in ("waiting_customer", "waiting_approval", "resolved"):
        if ticket_id in ACTIVE:
            QUEUED[ticket_id] = (f"Continue ticket {ticket_id}: customer message #{draft_id} was sent by "
                                 f"{body.sent_by} at {res['sent_at']}. Do not draft it again; do only open work.",
                                 f"message #{draft_id} sent")
            continuation = {"started": False, "queued": True, "reason": "starts when the current run finishes"}
        else:
            prompt = (f"Continue ticket {ticket_id}: customer message #{draft_id} was sent by {body.sent_by} "
                      f"at {res['sent_at']}. Do not draft it again. Read the ticket once, do only work that is "
                      "still open, and resolve the ticket only if nothing remains.")
            continuation = {"started": True, **start_run(ticket_id, prompt, trigger=f"message #{draft_id} sent")}
        human_event(ticket_id, "agents_continue", "started" if continuation["started"] else "skipped",
                    agent="system", result=continuation)
    return {"ok": True, **res, "continuation": continuation}


@app.get("/cash")
async def cash():
    (c,) = await mcp(("get_cash_position", {}))
    return {"today": c["today"], "checking_balance": c["checking_balance"],
            "committed_to_requests": c["committed_to_requests"], "uncommitted_cash": c["uncommitted_cash"],
            "open_invoice_total": c["open_invoice_total"], "rent_due_within_7_days": c["rent_due_within_7_days"]}


@app.post("/reset")
async def reset(body: ResetBody):
    """Restore the working database from the untouched original. Only on explicit request."""
    if body.confirm != "RESET":
        raise HTTPException(400, 'Reset needs {"confirm": "RESET"}.')
    if ACTIVE or APPROVAL_LOCK.locked():
        raise HTTPException(409, f"Cannot reset while work is in progress: {ACTIVE or 'approval'}.")
    if WORKING_DB == ORIGINAL_DB.resolve():
        raise HTTPException(500, "Refusing: working database path points at the original.")
    tmp = WORKING_DB.with_suffix(".db.resetting")
    shutil.copyfile(ORIGINAL_DB, tmp)
    os.replace(tmp, WORKING_DB)          # atomic: never a half-copied database
    RUNS.clear()
    human_event(None, "database_reset", "ok", agent="human",
                detail={"from": ORIGINAL_DB.name, "to": WORKING_DB.name})
    return {"ok": True, "restored": WORKING_DB.name, "from": ORIGINAL_DB.name}
