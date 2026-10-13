"""Offline safeguard tests (no model calls, no cost).

Agents run on a scripted PydanticAI FunctionModel instead of gpt-6-luna, so
each test can force a misbehaviour (looping, deep delegation, cycles, endless
tool calls) and check that the code, not the prompt, stops it. The MCP server
is real and runs against a throwaway database copy.

Run:  .venv/bin/python -m pytest tests/test_safeguards.py -v
"""

import asyncio
import json
import os

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from conftest import query

from backend import audit, config
from backend.mcp_client import build_agent_toolset, human_client
from backend.orchestrator import run_ticket

HEADERS = {"# Boss Agent": "boss", "# Inventory Agent": "inventory",
           "# Accounting Agent": "accounting", "# Facilities Agent": "facilities",
           "# Customer Service Agent": "customer_service"}


def who(info: AgentInfo) -> str:
    return next(name for header, name in HEADERS.items() if (info.instructions or "").startswith(header))


def finish(info: AgentInfo, name: str, summary: str = "done") -> ModelResponse:
    tool = info.output_tools[0].name
    args = ({"ticket_id": 901, "status": "in_progress", "decision": summary} if name == "boss"
            else {"agent": name, "status": "complete", "summary": summary})
    return ModelResponse(parts=[ToolCallPart(tool, args)])


def turns(messages) -> int:
    return sum(isinstance(m, ModelResponse) for m in messages)


def last_returns(messages) -> list:
    return [p.content for p in messages[-1].parts if isinstance(p, ToolReturnPart)]


def events(execution_id: str) -> list[dict]:
    return [e for e in json.loads(audit.AUDIT_PATH.read_text()) if e["execution_id"] == execution_id]


def run(script, ticket=901):
    return asyncio.run(run_ticket(ticket, context="test", model=FunctionModel(script)))


# ------------------------------------------------------------- MCP tools

async def _call(tool, args, human=False):
    if human:
        async with human_client() as c:
            return (await c.call_tool(tool, args, raise_on_error=False)).data
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from backend.mcp_client import _launch
    cmd, a, env = _launch(human=False)
    async with Client(StdioTransport(command=cmd, args=a, env=env)) as c:
        if tool is None:
            return [t.name for t in await c.list_tools()]
        return (await c.call_tool(tool, args, raise_on_error=False)).data


def call(tool, args=None, human=False):
    return asyncio.run(_call(tool, args or {}, human))


def test_agent_server_has_no_human_only_tools(db):
    names = call(None)
    assert len(names) == 14
    assert not {"approve_payment_request", "execute_approved_payment",
                "reject_payment_request"} & set(names)


def test_read_tools_return_real_database_values(db):
    cash = call("get_cash_position")
    assert cash["checking_balance"] == query("SELECT balance FROM cash_accounts")[0]["balance"] == 3400
    assert cash["open_invoice_total"] == 840 and cash["today"] == "2026-08-31"
    inv = call("get_invoice_details", {"invoice_id": 501})
    assert inv["vendor_name"] == "Bulldog Print Co" and inv["overdue"] and inv["days_overdue"] == 3
    price = call("get_pricing_and_margin", {"sku": "CC-HOOD-NAVY", "qty": 20})
    assert (price["unit_cost"], price["list_price"], price["discount_evaluated"]) == (22, 58, False)
    vendors = call("list_vendors")["vendors"]
    assert [v["blocked_by_unpaid_invoice"] for v in vendors] == [True, False, False]
    assert call("get_ticket_details", {"ticket_id": 102})["ticket"]["lease_id"] == 1
    assert {t["id"] for t in call("list_open_tickets")["tickets"]} >= {101, 102, 103}


def test_ticket_injection_text_is_flagged(db):
    assert "safety_warning" in call("get_ticket_details", {"ticket_id": 903})
    assert "safety_warning" not in call("get_ticket_details", {"ticket_id": 101})


def test_payment_request_validations(db):
    wrong = call("request_payment_approval", {"kind": "invoice", "ref_id": 501, "amount": 1.0,
                                              "requested_by": "accounting", "reason": "t"})
    assert wrong["ok"] is False and "match" in wrong["error"]
    ok = call("request_payment_approval", {"kind": "invoice", "ref_id": 501, "amount": 840,
                                           "requested_by": "accounting", "reason": "t"})
    assert ok["status"] == "pending_approval"
    dup = call("request_payment_approval", {"kind": "invoice", "ref_id": 501, "amount": 840,
                                            "requested_by": "accounting", "reason": "t"})
    assert dup["duplicate"] and dup["payment_request_id"] == ok["payment_request_id"]
    rent = call("request_payment_approval", {"kind": "rent", "ref_id": 1, "amount": 2400,
                                             "requested_by": "facilities", "reason": "t"})
    assert rent["uncommitted_cash_after"] == 160
    # Only $160 uncommitted now: a 12-hoodie restock ($264) must be refused for cash...
    # ...but vendor 1 is blocked by invoice 501 first.
    blocked = call("request_restock_order", {"vendor_id": 1, "sku": "CC-HOOD-NAVY", "size": "M",
                                             "qty": 12, "requested_by": "inventory"})
    assert blocked["ok"] is False and "unpaid" in blocked["error"]
    # No money moved.
    assert query("SELECT COUNT(*) n FROM payments")[0]["n"] == 0
    assert query("SELECT balance FROM cash_accounts")[0]["balance"] == 3400


def test_no_payment_without_human_approval(db):
    req = call("request_payment_approval", {"kind": "invoice", "ref_id": 501, "amount": 840,
                                            "requested_by": "accounting", "reason": "t"})
    rid = req["payment_request_id"]
    assert call("execute_approved_payment", {"request_id": rid}, human=True)["ok"] is False
    assert call("approve_payment_request", {"request_id": rid, "approver_name": "boss"},
                human=True)["ok"] is False
    assert call("approve_payment_request", {"request_id": rid, "approver_name": "Isabel"},
                human=True)["status"] == "approved"
    paid = call("execute_approved_payment", {"request_id": rid}, human=True)
    assert paid["checking_balance_after"] == 2560 and paid["invoice_status"] == "paid"
    again = call("execute_approved_payment", {"request_id": rid}, human=True)
    assert again["ok"] is False and "twice" in again["error"]
    assert query("SELECT COUNT(*) n FROM payments")[0]["n"] == 1


def test_interrupted_payment_leaves_database_unchanged(db, monkeypatch):
    import server  # the MCP server module, in-process on the test DB
    rid = server.request_payment_approval("invoice", 501, 840, "accounting", "t")["payment_request_id"]
    server.approve_payment_request(rid, "Isabel")
    real = server._apply_payment_effects

    def crash(conn, req, today):
        real(conn, req, today)          # invoice already marked paid inside the transaction...
        raise KeyboardInterrupt         # ...then the process is interrupted

    monkeypatch.setattr(server, "_apply_payment_effects", crash)
    with pytest.raises(KeyboardInterrupt):
        server.execute_approved_payment(rid)
    assert query("SELECT status FROM invoices WHERE id=501")[0]["status"] == "open"
    assert query("SELECT COUNT(*) n FROM payments")[0]["n"] == 0
    assert query("SELECT balance FROM cash_accounts")[0]["balance"] == 3400
    assert query("SELECT status FROM payment_requests WHERE id=?", (rid,))[0]["status"] == "approved"
    monkeypatch.setattr(server, "_apply_payment_effects", real)
    assert server.execute_approved_payment(rid)["ok"]
    assert server.execute_approved_payment(rid)["ok"] is False
    assert query("SELECT COUNT(*) n FROM payments")[0]["n"] == 1


def test_negative_cash_is_refused(db):
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("UPDATE cash_accounts SET balance = 500")
    conn.commit(); conn.close()
    r = call("request_payment_approval", {"kind": "rent", "ref_id": 1, "amount": 2400,
                                          "requested_by": "facilities", "reason": "t"})
    assert r["ok"] is False and "negative" in r["error"]


# ------------------------------------------------------ execution limits

def test_mcp_tool_call_limit(db):
    counter = iter(range(1, 1000))

    def script(messages, info):
        if who(info) != "boss":
            return finish(info, who(info))
        # Five distinct ticket lookups per turn, forever (Boss's own tool).
        return ModelResponse(parts=[ToolCallPart("get_ticket_details", {"ticket_id": next(counter)})
                                    for _ in range(5)])

    out = run(script)
    assert out.mcp_tool_calls == 20 and not out.completed
    assert out.stop_reason == "max_mcp_tool_calls (20)"
    assert any(e["action"] == "limit_reached" for e in events(out.execution_id))


def test_repeated_identical_calls_stop_for_no_progress(db):
    def script(messages, info):
        return ModelResponse(parts=[ToolCallPart("list_open_tickets", {})])

    out = run(script)
    assert out.mcp_tool_calls == 1            # only the first call reached MCP
    assert out.stop_reason.startswith("no_progress")
    actions = [e["action"] for e in events(out.execution_id)]
    assert actions.count("repeat_call_reused") == config.MAX_REPEATED_ACTIONS - 1 and "limit_reached" in actions


def test_delegation_depth_limit_and_full_chain(db):
    nxt = {"boss": "inventory", "inventory": "accounting",
           "accounting": "facilities", "facilities": "customer_service"}
    replies = {}

    def script(messages, info):
        name = who(info)
        if turns(messages) == 0 and name in nxt:
            return ModelResponse(parts=[ToolCallPart(f"ask_{nxt[name]}",
                                                     {"objective": f"depth test from {name}"})])
        replies[name] = last_returns(messages)
        return finish(info, name)

    out = run(script)
    assert out.completed
    ev = events(out.execution_id)
    started = [(e["agent"], e["target"]) for e in ev if e["action"] == "delegation" and e["status"] == "started"]
    assert started == [("boss", "inventory"), ("inventory", "accounting"), ("accounting", "facilities")]
    assert any(e["action"] == "limit_reached" and "depth" in e["result"] for e in ev)
    assert "depth limit" in json.dumps(replies["facilities"])


def test_delegation_cycle_is_refused(db):
    def script(messages, info):
        name = who(info)
        if turns(messages) == 0 and name == "boss":
            return ModelResponse(parts=[ToolCallPart("ask_inventory", {"objective": "check something"})])
        if turns(messages) == 0 and name == "inventory":
            return ModelResponse(parts=[ToolCallPart("ask_boss", {"objective": "what should I do?"})])
        return finish(info, name)

    out = run(script)
    ev = events(out.execution_id)
    assert any(e["action"] == "delegation" and e["status"] == "refused" and "cycle" in e["result"]
               for e in ev)


def test_all_twenty_delegation_links_exist_and_work(db):
    from backend.agents import ALL_AGENTS, AgentTeam
    team = AgentTeam(build_agent_toolset(), model=FunctionModel(lambda m, i: finish(i, who(i))))
    for caller in ALL_AGENTS:
        tools = set(team.agents[caller]._function_toolset.tools)
        assert tools == {f"ask_{p}" for p in ALL_AGENTS if p != caller}

    def script(messages, info):
        name = who(info)
        if name == "boss" and turns(messages) == 0:
            return ModelResponse(parts=[ToolCallPart(f"ask_{p}", {"objective": f"say hello, {p}"})
                                        for p in ("inventory", "accounting", "facilities", "customer_service")])
        return finish(info, name, f"hello from {name}")

    out = run(script)
    pairs = {(e["agent"], e["target"]) for e in events(out.execution_id)
             if e["action"] == "delegation_result"}
    assert pairs == {("boss", "inventory"), ("boss", "accounting"), ("boss", "facilities"),
                     ("boss", "customer_service")}

    # Peer-to-peer: Inventory asks Accounting directly (Boss did not ask Accounting).
    def peer(messages, info):
        name = who(info)
        if name == "boss" and turns(messages) == 0:
            return ModelResponse(parts=[ToolCallPart("ask_inventory", {"objective": "check stock and restock cost"})])
        if name == "inventory" and turns(messages) == 0:
            return ModelResponse(parts=[ToolCallPart("ask_accounting", {"objective": "peer question from inventory"})])
        return finish(info, name)

    out = run(peer)
    pairs = {(e["agent"], e["target"]) for e in events(out.execution_id) if e["action"] == "delegation_result"}
    assert pairs == {("boss", "inventory"), ("inventory", "accounting")}


def test_agent_step_limit(db):
    """Each specialist now runs at most once per run (repeats are reused), so the 12-step cap is
    checked directly: the 12th step is allowed, the 13th stops the whole run."""
    from backend.agents import AgentTeam
    from backend.models import ExecutionHalted, TicketRun
    team = AgentTeam(build_agent_toolset(), model=FunctionModel(lambda m, i: finish(i, who(i))))
    r = TicketRun(execution_id="exec-step-test", ticket_id=901, context="test", agent_steps=11)

    async def go():
        async with team.agents["boss"].toolsets[0]:
            await team.run_agent("inventory", r, "step 12")        # allowed
            with pytest.raises(ExecutionHalted):
                await team.run_agent("accounting", r, "step 13")   # refused
    asyncio.run(go())
    assert r.agent_steps == 12 and r.stop_reason == "max_agent_steps (12)"


def test_repeated_delegation_to_same_specialist_is_reused_not_rerun(db):
    def script(messages, info):
        name = who(info)
        if name == "boss":
            n = turns(messages)
            return ModelResponse(parts=[ToolCallPart("ask_inventory", {"objective": f"distinct task {n}-{k}"})
                                        for k in range(3)]) if n < 2 else finish(info, name)
        return finish(info, name)

    out = run(script)
    assert out.agent_steps == 2                 # Boss + one Inventory run
    ev = events(out.execution_id)
    assert sum(e["action"] == "delegation_reused" for e in ev) + sum(
        e["action"] == "delegation" and e["status"] == "refused" for e in ev) == 5   # 2 in flight + 3 reused


def test_duplicate_delegation_reuses_findings(db):
    def script(messages, info):
        name = who(info)
        if name == "boss" and turns(messages) < 2:
            return ModelResponse(parts=[ToolCallPart("ask_inventory", {"objective": "Check tee stock size S"})])
        return finish(info, name)

    out = run(script)
    assert out.agent_steps == 2           # Boss + one Inventory run, not two
    assert any(e["action"] == "delegation_reused" for e in events(out.execution_id))


def test_request_limit_stops_endless_reasoning(db):
    counter = iter(range(1, 1000))

    def script(messages, info):
        name = who(info)
        if name == "boss" and turns(messages) == 0:
            return ModelResponse(parts=[ToolCallPart("ask_inventory", {"objective": "keep thinking forever"})])
        if name == "inventory":   # never finishes
            return ModelResponse(parts=[ToolCallPart("check_stock_availability",
                                                     {"sku": "CC-TEE-WHITE", "size": "M",
                                                      "qty_needed": next(counter)})])
        return finish(info, name)

    out = run(script)
    assert out.completed and out.findings[0]["status"] == "failed"
    assert out.mcp_tool_calls == config.REQUEST_LIMIT_PER_RUN
    assert any(e["agent"] == "inventory" and e["action"] == "limit_reached"
               for e in events(out.execution_id))


def test_retry_and_timeout_configuration():
    model = config.build_model()
    assert model.client.max_retries == 0
    assert model.client.timeout == config.MODEL_TIMEOUT_SECONDS
    assert config.model_settings()["max_tokens"] == 8000
    toolset = build_agent_toolset()
    assert toolset.max_retries == 1


def test_audit_trail_is_valid_append_only_and_secret_free(db):
    before = json.loads(audit.AUDIT_PATH.read_text()) if audit.AUDIT_PATH.exists() else []
    out = run(lambda m, i: finish(i, who(i)))
    after = json.loads(audit.AUDIT_PATH.read_text())
    assert after[:len(before)] == before                   # nothing deleted or changed
    ev = events(out.execution_id)
    assert [ev[0]["action"], ev[-1]["action"]] == ["run_started", "run_finished"]
    for e in ev:
        assert {"timestamp", "execution_id", "ticket_id", "agent", "action", "tool",
                "result", "status"} <= e.keys()
    key = os.getenv("PORTKEY_API_KEY")
    assert key and key not in audit.AUDIT_PATH.read_text()
    assert audit._clean({"api_key": key, "note": f"x{key}x"}) == {"api_key": "[redacted]",
                                                                 "note": "x[redacted]x"}


def test_agent_hitting_token_cap_twice_is_stopped(db):
    from pydantic_ai.messages import TextPart

    def script(messages, info):
        name = who(info)
        if name == "boss" and turns(messages) == 0:
            return ModelResponse(parts=[ToolCallPart("ask_inventory", {"objective": "ramble until cut off"})])
        if name == "inventory":   # every response runs into the output cap
            return ModelResponse(parts=[TextPart("thinking " * 50)], finish_reason="length")
        return finish(info, name)

    out = run(script)
    assert out.completed and out.findings[0]["status"] == "failed"
    caps = [e for e in events(out.execution_id) if e["status"] == "token_cap"]
    assert len(caps) == 2



def test_audit_trail_concurrent_processes_lose_nothing():
    import subprocess
    import sys
    from conftest import ROOT
    code = ("from backend import audit\n"
            "for i in range(25): audit.record(None, 'test', 'concurrency_check', 'ok', detail=i)")
    before = len(json.loads(audit.AUDIT_PATH.read_text())) if audit.AUDIT_PATH.exists() else 0
    procs = [subprocess.Popen([sys.executable, "-c", code], cwd=ROOT) for _ in range(4)]
    assert all(p.wait(60) == 0 for p in procs)
    after = json.loads(audit.AUDIT_PATH.read_text())
    assert len(after) == before + 100
