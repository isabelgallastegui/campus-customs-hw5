"""The agents' only route to the shop's data: the campus-customs MCP server.

There is no sqlite3 in backend/. Every read and write goes through MCP, and
every call passes through `guard_tool_call`, which enforces in code:

- the per-ticket MCP tool-call budget,
- reuse of earlier identical results (no repeated work, no duplicate writes),
- a stop after too many repeated actions (lack of progress),
- refusal of human-only payment tools (which the agents' server does not even register),
- an audit event for every call, success or failure.
"""

import json
import os
from contextvars import ContextVar
from typing import Any

from fastmcp import Client as FastMCPClient
from fastmcp.client.transports import StdioTransport as FastMCPStdio
from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset, StdioTransport
from pydantic_ai.toolsets import AbstractToolset

from . import audit
from .config import (
    MAX_MCP_TOOL_CALLS,
    MAX_REPEATED_ACTIONS,
    MCP_CALL_TIMEOUT_SECONDS,
    MCP_INIT_TIMEOUT_SECONDS,
    MCP_TOOL_RETRIES,
    PROJECT_ROOT,
)
from .models import ExecutionHalted, TicketRun

HUMAN_ONLY_TOOLS = frozenset({"approve_payment_request", "reject_payment_request",
                              "execute_approved_payment", "edit_customer_draft", "mark_message_sent"})

# Which MCP tools each agent may use. Enforced twice: agents only *see* their own
# tools (filtered toolset) and the guard refuses anything else. Actions that change
# data belong to exactly one role, so the right specialist is always involved.
AGENT_TOOLS: dict[str, frozenset[str]] = {
    "boss": frozenset({"list_open_tickets", "get_ticket_details", "list_payment_requests",
                       "update_ticket_status"}),
    "inventory": frozenset({"check_stock_availability", "list_vendors", "get_vendor_status",
                            "request_restock_order"}),
    "accounting": frozenset({"get_cash_position", "get_invoice_details", "get_pricing_and_margin",
                             "list_payment_requests", "request_payment_approval", "get_vendor_status",
                             "get_lease_details"}),
    "facilities": frozenset({"get_lease_details"}),
    "customer_service": frozenset({"get_ticket_details", "draft_customer_message"}),
}
TOOL_OWNER = {"request_payment_approval": "accounting", "request_restock_order": "inventory",
              "draft_customer_message": "customer_service", "update_ticket_status": "boss",
              "check_stock_availability": "inventory", "list_vendors": "inventory",
              "get_cash_position": "accounting", "get_invoice_details": "accounting",
              "get_pricing_and_margin": "accounting", "get_lease_details": "facilities"}
AGENT_LABEL = {"boss": "Boss", "inventory": "Inventory", "accounting": "Accounting",
               "facilities": "Facilities", "customer_service": "Customer Service"}
WRITE_TOOLS = frozenset({"request_payment_approval", "request_restock_order", "draft_customer_message",
                         "update_ticket_status"})

# The delegation chain of the agent currently running, e.g. ("boss", "inventory").
# A ContextVar, so parallel delegations each see their own chain.
current_path: ContextVar[tuple[str, ...]] = ContextVar("current_path", default=("boss",))


def _launch(human: bool) -> tuple[str, list[str], dict[str, str]]:
    """Same command as .mcp.json. Only the human process gets the payment tools."""
    env = {"CAMPUS_CUSTOMS_HUMAN_TOOLS": "1" if human else "0"}
    if os.getenv("CAMPUS_CUSTOMS_DB"):
        env["CAMPUS_CUSTOMS_DB"] = os.environ["CAMPUS_CUSTOMS_DB"]
    python = str(PROJECT_ROOT / ".venv" / "bin" / "python")
    return python, [str(PROJECT_ROOT / "mcp_server" / "server.py")], env


def toolset_for(toolset: MCPToolset, agent: str) -> AbstractToolset:
    """The shared MCP toolset, showing an agent only the tools of its role."""
    allowed = AGENT_TOOLS[agent]
    return toolset.filtered(lambda ctx, tool_def: tool_def.name in allowed)


def build_agent_toolset() -> MCPToolset:
    """The toolset all five agents share. Human-only tools are not registered on this server."""
    command, args, env = _launch(human=False)
    return MCPToolset(
        StdioTransport(command=command, args=args, env=env, cwd=str(PROJECT_ROOT)),
        process_tool_call=guard_tool_call,
        max_retries=MCP_TOOL_RETRIES,
        init_timeout=MCP_INIT_TIMEOUT_SECONDS,
        read_timeout=MCP_CALL_TIMEOUT_SECONDS,
    )


def human_client() -> FastMCPClient:
    """For actions a person takes (approve/execute). Not given to any agent."""
    command, args, env = _launch(human=True)
    return FastMCPClient(FastMCPStdio(command=command, args=args, env=env, cwd=str(PROJECT_ROOT)),
                         timeout=MCP_CALL_TIMEOUT_SECONDS)


async def guard_tool_call(ctx: RunContext[TicketRun], call_tool: Any, name: str,
                          args: dict[str, Any]) -> Any:
    run = ctx.deps
    agent = current_path.get()[-1]

    if run.halted:
        audit.record(run, agent, "mcp_tool_call", "halted", tool=name, detail=args,
                     result=f"Execution already stopped: {run.stop_reason}")
        raise ExecutionHalted(run.stop_reason)

    if name in HUMAN_ONLY_TOOLS:
        audit.record(run, agent, "safety_refusal", "refused", tool=name, detail=args,
                     result="Human-only tool; agents cannot approve or execute payments.")
        return {"ok": False, "error": "Human approval is required. Prepare a request instead."}

    if agent in AGENT_TOOLS and name not in AGENT_TOOLS[agent]:
        owner = TOOL_OWNER.get(name, "the right specialist")
        audit.record(run, agent, "role_refusal", "refused", tool=name, detail=args,
                     result=f"{name} belongs to {owner}.")
        return {"ok": False, "error": f"{name} is not your responsibility. Delegate this to {owner} "
                                      "with a clear objective."}

    signature = f"{name}:{json.dumps(args, sort_keys=True)}"
    if signature in run.tool_results:
        run.repeated_actions += 1
        if run.repeated_actions >= MAX_REPEATED_ACTIONS:
            run.stop(f"no_progress: {run.repeated_actions} repeated actions")
            audit.record(run, agent, "limit_reached", "stopped", tool=name, detail=args,
                         result=run.stop_reason)
            return {"ok": False, "error": "Stopped: you keep repeating the same call. "
                    "Return your findings now with what you already have."}
        audit.record(run, agent, "repeat_call_reused", "ok", tool=name, detail=args,
                     result="Identical call already made this run; returned earlier result.")
        return {"repeat": True, "note": "Same call already made; reuse this result.",
                "result": run.tool_results[signature]}

    if run.mcp_tool_calls >= MAX_MCP_TOOL_CALLS:
        run.stop(f"max_mcp_tool_calls ({MAX_MCP_TOOL_CALLS})")
        audit.record(run, agent, "limit_reached", "stopped", tool=name, detail=args,
                     result=run.stop_reason)
        return {"ok": False, "error": f"Tool budget of {MAX_MCP_TOOL_CALLS} calls used. "
                "Stop and report what is done and what is unresolved."}

    # The record says who really acted, whatever name the agent typed.
    for field_name in ("requested_by", "drafted_by", "updated_by"):
        if field_name in args:
            args = {**args, field_name: AGENT_LABEL.get(agent, agent)}

    run.mcp_tool_calls += 1
    try:
        result = await call_tool(name, args)
    except Exception as exc:  # report, never invent a result
        audit.record(run, agent, "mcp_tool_call", "error", tool=name, detail=args,
                     result=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "error": f"Tool failed: {type(exc).__name__}. Report this; do not guess."}

    run.tool_results[signature] = result
    if name == "get_ticket_details" and isinstance(result, dict) and result.get("ok"):
        run.ticket_snapshot = result
    if name in WRITE_TOOLS and isinstance(result, dict) and result.get("ok") is not False:
        # Completed actions survive a later failure of the same agent and are reported.
        ref = result.get("draft_id") or result.get("payment_request_id") or result.get("status")
        run.actions.append({"agent": agent, "tool": name, "ref": ref,
                            "summary": f"{name} → {ref}" if ref else name})
    if isinstance(result, dict) and result.get("payment_request_id"):
        run.payment_request_ids.append(result["payment_request_id"])
        audit.record(run, agent, "approval_requested", "pending_approval", tool=name,
                     detail=args, result=result)
    else:
        failed = isinstance(result, dict) and (result.get("ok") is False or result.get("found") is False)
        audit.record(run, agent, "mcp_tool_call", "tool_refused" if failed else "ok",
                     tool=name, detail=args, result=result)
    return result
