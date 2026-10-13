"""Offline tests for the post-final-run improvements (no model cost, temp database).

Run:  .venv/bin/python -m pytest tests/test_improvements.py -v
"""

import asyncio
import json

from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from conftest import query
from test_safeguards import call, events, finish, run, turns, who

from backend.mcp_client import AGENT_TOOLS


def ask(peer, objective):
    return ModelResponse(parts=[ToolCallPart(f"ask_{peer}", {"objective": objective})])


# ------------------------------------------------------------ roles

def test_each_agent_sees_only_its_role_tools(db):
    seen = {}

    def script(messages, info):
        name = who(info)
        seen[name] = {t.name for t in info.function_tools if not t.name.startswith("ask_")}
        if name == "boss" and turns(messages) == 0:
            return ModelResponse(parts=[ToolCallPart(f"ask_{p}", {"objective": f"role check for {p}"})
                                        for p in ("inventory", "accounting", "facilities", "customer_service")])
        return finish(info, name)

    run(script)
    for agent, tools in AGENT_TOOLS.items():
        assert seen[agent] == set(tools), agent
    assert "request_payment_approval" not in seen["boss"] and "request_payment_approval" in seen["accounting"]


def test_boss_cannot_prepare_a_payment_itself(db):
    def script(messages, info):
        if who(info) == "boss" and turns(messages) == 0:
            return ModelResponse(parts=[ToolCallPart("request_payment_approval", {
                "kind": "rent", "ref_id": 1, "amount": 2400, "requested_by": "boss", "reason": "rent"})])
        return finish(info, who(info))

    out = run(script)
    ev = events(out.execution_id)
    # Boss never sees the tool, so the call is rejected before it reaches MCP at all.
    assert not any(e["tool"] == "request_payment_approval" and e["action"] in ("mcp_tool_call", "approval_requested") for e in ev)
    assert query("SELECT name FROM sqlite_master WHERE name='payment_requests'") == [] or \
        query("SELECT COUNT(*) n FROM payment_requests")[0]["n"] == 0


def test_guard_refuses_a_tool_outside_the_role(db):
    """Second layer: even if a hidden tool call reached the guard, it is refused and audited."""
    from types import SimpleNamespace
    from backend.mcp_client import current_path, guard_tool_call
    from backend.models import TicketRun
    r = TicketRun(execution_id="exec-role-test", ticket_id=102, context="test")
    current_path.set(("boss",))

    async def never(*a):
        raise AssertionError("must not reach MCP")

    res = asyncio.run(guard_tool_call(SimpleNamespace(deps=r), never, "request_payment_approval",
                                      {"kind": "rent", "ref_id": 1, "amount": 2400}))
    assert res["ok"] is False and "accounting" in res["error"]
    assert any(e["action"] == "role_refusal" for e in events("exec-role-test"))


# ------------------------------------------------------- customer messages

def test_draft_with_internal_details_is_refused(db):
    bad = call("draft_customer_message", {"ticket_id": 101, "drafted_by": "customer_service",
               "body": "Hi Tauhid, your tee is pending internal approval because our vendor invoice is unpaid."})
    assert bad["ok"] is False and {"invoice", "vendor", "unpaid"} <= set(bad["found"])
    good = call("draft_customer_message", {"ticket_id": 101, "drafted_by": "customer_service",
                "body": "Hi Tauhid, thanks for your order. Size S is out of stock right now; we'll confirm "
                        "a date as soon as it's back. Best regards, The Campus Customs Team"})
    assert good["ok"] and good["sent"] is False


def test_mark_as_sent_once_status_rules_and_never_resolves(db):
    d = call("draft_customer_message", {"ticket_id": 103, "drafted_by": "customer_service",
             "body": "Hi Yale AI Club, thanks for your request. We can offer 8 hoodies now at 10% off. "
                     "Would that work for you? Best regards, The Campus Customs Team"})
    # A ticket with an unsent draft cannot be resolved.
    r = call("update_ticket_status", {"ticket_id": 103, "status": "resolved", "note": "t", "updated_by": "boss"})
    assert r["ok"] is False and "not been sent" in r["error"]
    sent = call("mark_message_sent", {"draft_id": d["draft_id"], "sent_by": "Isabel (test)",
                                      "customer_reply_needed": True}, human=True)
    assert sent["ok"] and sent["ticket_status"] == "waiting_customer" and sent["sent_at"]
    again = call("mark_message_sent", {"draft_id": d["draft_id"], "sent_by": "Isabel (test)"}, human=True)
    assert again["ok"] is False and again["already_sent"]
    assert call("mark_message_sent", {"draft_id": d["draft_id"], "sent_by": "boss"}, human=True)["ok"] is False
    assert query("SELECT COUNT(*) n FROM customer_message_sends")[0]["n"] == 1
    assert query("SELECT status FROM tickets WHERE id=103")[0]["status"] == "waiting_customer"
    details = call("get_ticket_details", {"ticket_id": 103})
    assert details["message_drafts"][-1]["sent"] and details["message_drafts"][-1]["customer_reply_needed"]


def test_mark_as_sent_keeps_waiting_approval_and_agents_cannot_mark(db):
    call("request_payment_approval", {"kind": "invoice", "ref_id": 501, "amount": 840,
                                      "requested_by": "accounting", "reason": "t", "ticket_id": 101})
    call("update_ticket_status", {"ticket_id": 101, "status": "waiting_approval", "note": "t", "updated_by": "boss"})
    d = call("draft_customer_message", {"ticket_id": 101, "drafted_by": "customer_service",
             "body": "Hi Tauhid, size S is out of stock; we'll confirm a date soon. Best regards, The Campus Customs Team"})
    sent = call("mark_message_sent", {"draft_id": d["draft_id"], "sent_by": "Isabel (test)"}, human=True)
    assert sent["ticket_status"] == "waiting_approval"
    assert "mark_message_sent" not in call(None)          # agents' server has no such tool


def test_human_edit_is_a_new_version_and_agents_cannot_overwrite_it(db):
    d = call("draft_customer_message", {"ticket_id": 101, "drafted_by": "customer_service",
             "body": "Hi Tauhid, size S is out of stock; we'll confirm a date soon. Best regards, The Campus Customs Team"})
    e = call("edit_customer_draft", {"draft_id": d["draft_id"], "edited_by": "Isabel (test)",
             "body": "Hi Tauhid, thank you for your patience. We'll write again as soon as size S is back."}, human=True)
    assert e["ok"] and e["draft_id"] != d["draft_id"]
    over = call("draft_customer_message", {"ticket_id": 101, "drafted_by": "customer_service",
                "body": "Hi Tauhid, here is a new version that would replace yours. Best regards, The Campus Customs Team"})
    assert over["ok"] is False and "edited by a person" in over["error"]
    rows = query("SELECT id, status, drafted_by FROM customer_message_drafts ORDER BY id")
    assert [r["status"] for r in rows] == ["superseded", "draft"] and "(edited)" in rows[1]["drafted_by"]


def test_pricing_options_for_a_proposal(db):
    p = call("get_pricing_and_margin", {"sku": "CC-HOOD-NAVY", "qty": 20, "discount_options_pct": [5, 10, 15]})
    assert [o["unit_price"] for o in p["proposal_options"]] == [55.1, 52.2, 49.3]
    assert all(not o["below_cost"] for o in p["proposal_options"]) and p["discount_evaluated"] is False


# ------------------------------------------------------- errors & truth

def test_agent_error_keeps_completed_draft_and_outcome_is_corrected(db):
    def script(messages, info):
        name = who(info)
        if name == "boss":
            if turns(messages) == 0:
                return ask("customer_service", "Draft a reply asking the club for its target price")
            tool = info.output_tools[0].name     # Boss wrongly claims nothing was drafted
            return ModelResponse(parts=[ToolCallPart(tool, {"ticket_id": 103, "status": "missing_info",
                "decision": "Need a price.", "unresolved": ["Customer reply draft was not created because Customer Service failed."]})])
        if name == "customer_service":
            if turns(messages) == 0:
                return ModelResponse(parts=[ToolCallPart("draft_customer_message", {
                    "ticket_id": 103, "drafted_by": "Customer Service",
                    "body": "Hi Yale AI Club, thanks for reaching out. What price per hoodie did you have in mind? "
                            "Best regards, The Campus Customs Team"})])
            raise ModelHTTPError(status_code=500, model_name="gpt-6-luna", body={"message": "Internal Server Error"})
        return finish(info, name)

    out = asyncio.run(__import__("backend.orchestrator", fromlist=["run_ticket"]).run_ticket(
        103, context="test", model=FunctionModel(script)))
    cs = next(f for f in out.findings if f["agent"] == "customer_service")
    assert cs["status"] == "failed" and cs["completed_actions"][0]["tool"] == "draft_customer_message"
    assert "AFTER completing" in cs["summary"]
    draft_id = query("SELECT id FROM customer_message_drafts")[0]["id"]
    assert out.decision.customer_draft_id == draft_id                # corrected from the database
    assert not any("not created" in u for u in out.decision.unresolved)
    assert any(f"Draft #{draft_id} exists" in c for c in out.corrections)
    assert any(e["action"] == "outcome_verified" and e["status"] == "corrected" for e in events(out.execution_id))
    assert query("SELECT COUNT(*) n FROM customer_message_drafts")[0]["n"] == 1   # no duplicate


def test_specialists_receive_ticket_facts_and_dont_reread(db):
    prompts = {}

    def script(messages, info):
        name = who(info)
        if name == "boss" and turns(messages) == 0:
            return ModelResponse(parts=[ToolCallPart("get_ticket_details", {"ticket_id": 101})])
        if name == "boss" and turns(messages) == 1:
            return ask("inventory", "Check stock and vendor for the order")
        if name == "inventory":
            prompts["inventory"] = "".join(str(getattr(p, "content", "")) for m in messages for p in m.parts)
        return finish(info, name)

    run(script, ticket=101)
    assert "Ticket facts" in prompts["inventory"] and "CC-TEE-WHITE" in prompts["inventory"]


def test_no_dates_without_an_order_and_no_landlord_messages(db):
    dated = call("draft_customer_message", {"ticket_id": 103, "drafted_by": "customer_service",
                 "body": "Hi Yale AI Club, the remaining hoodies should arrive September 5. Best regards, The Campus Customs Team"})
    assert dated["ok"] is False and "no restock order" in dated["error"]
    landlord = call("draft_customer_message", {"ticket_id": 102, "drafted_by": "customer_service",
                    "body": "Hello, we confirm the rent will be handled. Best regards, The Campus Customs Team"})
    assert landlord["ok"] is False and "not a customer" in landlord["error"]
    assert call("get_ticket_details", {"ticket_id": 103})["item_name"] == "Basic Hoodie Big Yale"


def test_customer_service_waits_while_approval_is_pending(db):
    def script(messages, info):
        name = who(info)
        if name == "boss" and turns(messages) == 0:
            return ModelResponse(parts=[ToolCallPart("get_ticket_details", {"ticket_id": 101})])
        if name == "boss" and turns(messages) == 1:
            return ask("accounting", "Prepare the invoice 501 payment if affordable")
        if name == "accounting" and turns(messages) == 0:
            return ModelResponse(parts=[ToolCallPart("request_payment_approval", {
                "kind": "invoice", "ref_id": 501, "amount": 840, "requested_by": "Accounting", "reason": "t", "ticket_id": 101})])
        if name == "boss" and turns(messages) == 2:
            return ask("customer_service", "Draft a message about the delay")
        return finish(info, name)

    out = run(script, ticket=101)
    ev = events(out.execution_id)
    assert any(e["action"] == "delegation" and e["status"] == "refused" and "waiting for human approval" in e["result"] for e in ev)
    assert not any(e["action"] == "agent_run_started" and e["agent"] == "customer_service" for e in ev)
