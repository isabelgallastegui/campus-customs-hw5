"""Run one ticket from start to finish and always return a TicketOutcome.

If any limit is hit or something fails, the run stops safely, the reason is
recorded, and the outcome lists what was completed and what is unresolved.
"""

import uuid
from datetime import datetime

from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models import Model

from . import audit
from .agents import AgentTeam
import re

from fastmcp import Client as FastMCPClient
from fastmcp.client.transports import StdioTransport as FastMCPStdio

from .config import PROJECT_ROOT
from .mcp_client import _launch, build_agent_toolset, current_path
from .models import ExecutionHalted, TicketOutcome, TicketRun


def new_execution_id() -> str:
    return f"exec-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


async def read_ticket(ticket_id: int) -> dict | None:
    """Read the ticket straight from the database (via MCP) to check what really happened."""
    command, args, env = _launch(human=False)
    try:
        async with FastMCPClient(FastMCPStdio(command=command, args=args, env=env, cwd=str(PROJECT_ROOT))) as c:
            res = await c.call_tool("get_ticket_details", {"ticket_id": ticket_id}, raise_on_error=False)
            return res.data
    except Exception:
        return None


def needs_customer_message(details: dict | None) -> bool:
    """A customer ticket that is settled for now should have a current message for a person to send."""
    if not details or not details.get("ok"):
        return False
    t = details["ticket"]
    if t["type"] not in ("customer_order", "price_override") or t["status"] == "resolved":
        return False
    if any(r["status"] in ("pending_approval", "approved") for r in details["payment_requests"]):
        return False                      # the situation will still change
    drafts = [d for d in details["message_drafts"] if d["status"] == "draft"]
    if any(not d["sent"] for d in drafts):
        return False                      # a message is already waiting for review
    sent = [d["sent_at"] for d in drafts if d["sent"]]
    if not sent:
        return True
    changes = [r["decided_at"] for r in details["payment_requests"] if r["status"] == "executed" and r.get("decided_at")]
    changes += [o["requested_at"] for o in details["restock_orders"]]
    return any(c > max(sent) for c in changes)   # something changed since the customer last heard from us


def reconcile(run: TicketRun, outcome: TicketOutcome, details: dict | None) -> None:
    """Make the reported outcome match the database. Corrections are listed, never hidden."""
    if not details or not details.get("ok"):
        outcome.corrections.append("Could not re-read the ticket to verify the outcome.")
        return
    drafts = [d for d in details["message_drafts"] if d["status"] == "draft"]
    live = [r for r in details["payment_requests"] if r["status"] in ("pending_approval", "approved")]
    outcome.verified = {
        "ticket_status": details["ticket"]["status"],
        "current_drafts": [{"id": d["id"], "sent": d["sent"], "drafted_by": d["drafted_by"]} for d in drafts],
        "requests_waiting": [{"id": r["id"], "kind": r["kind"], "amount": r["amount"]} for r in live],
        "actions_this_run": run.actions,
    }
    d = outcome.decision
    if d is None:
        return
    unsent = [x for x in drafts if not x["sent"]]
    if unsent and d.customer_draft_id != unsent[-1]["id"]:
        outcome.corrections.append(f"Draft #{unsent[-1]['id']} exists in the database "
                                   f"(Boss reported {d.customer_draft_id or 'none'}).")
        d.customer_draft_id = unsent[-1]["id"]
    if drafts:
        wrong = [u for u in d.unresolved if re.search(r"draft.*(not|wasn't|was not) (created|saved)|no (customer )?(reply )?draft", u, re.I)]
        for u in wrong:
            d.unresolved.remove(u)
            outcome.corrections.append(f"Removed incorrect claim '{u}': draft #{drafts[-1]['id']} was saved.")
    listed = {a.payment_request_id for a in d.approvals_needed}
    for r in live:
        if r["id"] not in listed:
            outcome.corrections.append(f"Request #{r['id']} ({r['kind']}, ${r['amount']:,.0f}) is waiting for approval "
                                       "but was missing from Boss's list.")
    outcome.unresolved = d.unresolved


async def run_ticket(ticket_id: int, context: str = "live", prompt: str | None = None,
                     model: Model | None = None, execution_id: str | None = None) -> TicketOutcome:
    run = TicketRun(execution_id=execution_id or new_execution_id(), ticket_id=ticket_id, context=context)
    audit.record(run, "system", "run_started", "running")
    prompt = prompt or (
        f"Handle ticket {ticket_id}. Read it with get_ticket_details, delegate only what is "
        "needed, then return your decision."
    )
    decision, error = None, None
    current_path.set(("boss",))
    try:
        toolset = build_agent_toolset()
        async with toolset:
            team = AgentTeam(toolset, model=model)
            decision = await team.run_agent("boss", run, prompt)
            # Make sure the customer hears about a settled situation, even if Boss forgot to ask.
            if decision is not None and not run.halted and needs_customer_message(await read_ticket(ticket_id)):
                audit.record(run, "system", "customer_message_requested", "started", target="customer_service",
                             result="ticket settled for now and no current customer message")
                current_path.set(("boss",))
                cs = await team.delegate(
                    run, "customer_service",
                    "Draft the customer message for the current situation of this ticket.",
                    context=f"Boss's decision: {decision.decision}\nNext steps: {'; '.join(decision.next_steps)}\n"
                            f"Customer needs to reply: {decision.customer_reply_needed}")
                if isinstance(cs, dict) and cs.get("status") not in (None, "failed"):
                    for a in run.actions:
                        if a["tool"] == "draft_customer_message":
                            decision.customer_draft_id = a["ref"]
    except ExecutionHalted as exc:
        error = run.stop_reason or str(exc)
    except UsageLimitExceeded as exc:
        run.stop(f"request_limit: {exc}")
        error = run.stop_reason
    except Exception as exc:  # stop safely and say why
        run.stop(f"error: {type(exc).__name__}: {exc}")
        error = run.stop_reason
        audit.record(run, "system", "run_error", "error", result=error)

    findings = [{"agent": a, "objective": o, **f} for (a, o), f in run.findings.items()]
    unresolved = []
    if decision is None:
        unresolved = [f"Run stopped before Boss decided: {error}"]
        unresolved += [f"{f['agent']}: {f.get('summary')}" for f in findings
                       if f.get("status") != "complete"]
    else:
        unresolved = decision.unresolved

    outcome = TicketOutcome(
        execution_id=run.execution_id, ticket_id=ticket_id, completed=decision is not None,
        stop_reason=run.stop_reason, agent_steps=run.agent_steps,
        mcp_tool_calls=run.mcp_tool_calls, decision=decision, findings=findings,
        unresolved=unresolved,
    )
    reconcile(run, outcome, await read_ticket(ticket_id))
    if run.actions and decision is None:
        outcome.unresolved.append("Completed before stopping (kept, not repeated): "
                                  + "; ".join(a["summary"] for a in run.actions))
    if outcome.corrections or outcome.verified:
        audit.record(run, "system", "outcome_verified", "corrected" if outcome.corrections else "ok",
                     result={"corrections": outcome.corrections, "verified": outcome.verified})
    audit.record(run, "system", "run_finished", "completed" if decision else "stopped",
                 result={"stop_reason": run.stop_reason, "agent_steps": run.agent_steps,
                         "mcp_tool_calls": run.mcp_tool_calls,
                         "payment_requests": run.payment_request_ids,
                         "corrections": outcome.corrections})
    return outcome
