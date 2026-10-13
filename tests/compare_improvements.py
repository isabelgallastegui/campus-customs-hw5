"""Controlled re-run of tickets 102, 101, 103 with the improved agents, on an ISOLATED database copy.

The backend must run against a temp copy of the ORIGINAL database, e.g.
    cd backend && CAMPUS_CUSTOMS_DB=/tmp/improve.db CAMPUS_CUSTOMS_CONTEXT=test ../.venv/bin/uvicorn main:app --port 8001

Usage:  .venv/bin/python tests/compare_improvements.py /tmp/improve.db http://127.0.0.1:8001

Approvals here are test approvals on the isolated copy only ("Test approver (isolated copy)").
Writes output/improvement_tests.json with metrics compared to the original final run.
"""

import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMP_DB = Path(sys.argv[1]).resolve()
BASE = sys.argv[2]
APPROVER = "Test approver (isolated copy)"
INTERNAL = re.compile(r"invoice|vendor|supplier|unpaid|overdue|cash|margin|approval|CC-[A-Z]+-[A-Z]+|ticket\s*#?\d+|guarantee", re.I)

# Baseline from the original final run (output/audit_trail.json, execution IDs in output/resolved_tickets.json).
BASELINE = {
    101: {"runs": 3, "agent_steps": 8, "delegations": 5, "mcp_calls": 16, "repeats": 1, "output_tokens": 2691,
          "agents": ["boss", "inventory", "accounting", "customer_service"], "payment_prepared_by": ["boss", "inventory"],
          "final_status": "in_progress"},
    102: {"runs": 2, "agent_steps": 3, "delegations": 1, "mcp_calls": 4, "repeats": 0, "output_tokens": 566,
          "agents": ["boss", "facilities"], "payment_prepared_by": ["boss"], "final_status": "resolved"},
    103: {"runs": 1, "agent_steps": 3, "delegations": 2, "mcp_calls": 3, "repeats": 1, "output_tokens": 728,
          "agents": ["boss", "accounting", "customer_service"], "payment_prepared_by": [], "final_status": "blocked"},
}


def http(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait(execution_id, timeout=480):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, r = http("GET", f"/runs/{execution_id}")
        if r.get("status") != "running":
            return r
        time.sleep(3)
    return {"status": "client_timeout"}


def ticket(t):
    return http("GET", f"/tickets/{t}")[1]


def process(t, log):
    """Run a ticket; approve whatever it prepares (test approver); follow continuations."""
    code, run = http("POST", f"/tickets/{t}/run")
    assert code == 202, run
    execs = [run["execution_id"]]
    r = wait(run["execution_id"])
    log.append({"ticket": t, "execution_id": run["execution_id"], "status": r["status"], "trigger": "manual"})
    for _ in range(4):
        waiting = ticket(t)["waiting_for_human"]
        if not waiting:
            break
        for req in waiting:
            code, res = http("POST", f"/payments/{req['id']}/approve", {"approver_name": APPROVER, "note": "isolated test"})
            log.append({"ticket": t, "approved_request": req["id"], "kind": req["kind"], "amount": req["amount"],
                        "prepared_by": req["requested_by"], "http": code,
                        "checking_after": (res.get("payment") or {}).get("checking_balance_after")})
            cont = (res.get("continuation") or {})
            if cont.get("started"):
                execs.append(cont["execution_id"])
                rr = wait(cont["execution_id"])
                log.append({"ticket": t, "execution_id": cont["execution_id"], "status": rr["status"],
                            "trigger": f"approval #{req['id']}"})
    return execs


def metrics(execs):
    events = [e for e in json.loads((ROOT / "output/audit_trail.json").read_text()) if e.get("execution_id") in set(execs)]
    turns = [e["detail"]["output_tokens"] for e in events if e["action"] == "model_turn"]
    agents = sorted({e["agent"] for e in events if e["action"] == "agent_run_started"})
    return {
        "runs": len(execs), "agent_steps": sum(e["action"] == "agent_run_started" for e in events),
        "delegations": sum(e["action"] == "delegation" and e["status"] == "started" for e in events),
        "delegation_pairs": [(e["agent"], e["target"]) for e in events if e["action"] == "delegation" and e["status"] == "started"],
        "mcp_calls": sum(e["action"] in ("mcp_tool_call", "approval_requested") for e in events),
        "tools": sorted({e["tool"] for e in events if e["action"] in ("mcp_tool_call", "approval_requested") and e["tool"]}),
        "repeats": sum(e["action"] == "repeat_call_reused" for e in events),
        "role_refusals": sum(e["action"] == "role_refusal" for e in events),
        "errors": [str(e["result"])[:120] for e in events if e["action"] in ("agent_error", "run_error")],
        "corrections": [c for e in events if e["action"] == "outcome_verified" for c in (e["result"] or {}).get("corrections", [])],
        "output_tokens": sum(turns), "agents": agents,
    }


def db(sql, a=()):
    c = sqlite3.connect(f"file:{TEMP_DB}?mode=ro", uri=True)
    rows = c.execute(sql, a).fetchall()
    c.close()
    return rows


def main():
    _, h = http("GET", "/health")
    if h.get("using_default_database") or h.get("database") != TEMP_DB.name:
        sys.exit(f"Refusing: backend is not on the isolated copy ({h}).")
    log, result = [], {"database": TEMP_DB.name, "approver": APPROVER, "tickets": {}}
    for t in (102, 101, 103):
        execs = process(t, log)
        tk = ticket(t)
        drafts = [d for d in tk["message_drafts"] if d["status"] == "draft"]
        reqs = db("SELECT id, kind, amount, requested_by, status FROM payment_requests WHERE ticket_id = ?", (t,)) \
            if db("SELECT name FROM sqlite_master WHERE name='payment_requests'") else []
        m = metrics(execs)
        result["tickets"][t] = {
            "after": m, "before": BASELINE[t], "final_status": tk["ticket"]["status"],
            "boss_decision": ((tk.get("last_run") or {}).get("outcome") or {}).get("decision"),
            "payment_requests": [{"id": r[0], "kind": r[1], "amount": r[2], "prepared_by": r[3], "status": r[4]} for r in reqs],
            "customer_draft": ({"id": drafts[-1]["id"], "body": drafts[-1]["body"], "words": len(drafts[-1]["body"].split()),
                                "internal_terms": sorted({x.lower() for x in INTERNAL.findall(drafts[-1]["body"])})}
                               if drafts else None),
            "execution_ids": execs,
        }

    # Mark as Sent on the isolated copy: 103 needs the customer's answer, 101 doesn't.
    sent = {}
    for t, reply in ((103, True), (101, False)):
        d = result["tickets"][t]["customer_draft"]
        if not d:
            continue
        code, res = http("POST", f"/messages/{d['id']}/mark-sent",
                         {"sent_by": APPROVER, "customer_reply_needed": reply, "continue_agents": False})
        code2, res2 = http("POST", f"/messages/{d['id']}/mark-sent", {"sent_by": APPROVER})
        sent[t] = {"first": {"http": code, "ticket_status": res.get("ticket_status"), "sent_at": res.get("sent_at")},
                   "second": {"http": code2, "detail": res2.get("detail")},
                   "status_in_db": db("SELECT status FROM tickets WHERE id = ?", (t,))[0][0]}
    result["mark_as_sent"] = sent
    result["log"] = log
    result["cash"] = {"final_checking": db("SELECT balance FROM cash_accounts")[0][0],
                      "payments": db("SELECT id, kind, amount, approved_by FROM payments ORDER BY id")}
    (ROOT / "output/improvement_tests.json").write_text(json.dumps(result, indent=2, default=str))
    for t, r in result["tickets"].items():
        b, a = r["before"], r["after"]
        print(f"{t}: status {b['final_status']} -> {r['final_status']} | steps {b['agent_steps']}->{a['agent_steps']} "
              f"| delegations {b['delegations']}->{a['delegations']} | mcp {b['mcp_calls']}->{a['mcp_calls']} "
              f"| repeats {b['repeats']}->{a['repeats']} | tokens {b['output_tokens']}->{a['output_tokens']} | agents {a['agents']} "
              f"| prepared_by {[p['prepared_by'] for p in r['payment_requests']]} | refusals {a['role_refusals']} | errors {len(a['errors'])}")
        if r["customer_draft"]:
            print(f"   draft ({r['customer_draft']['words']} words, internal terms {r['customer_draft']['internal_terms']}): {r['customer_draft']['body']}")
    print("mark as sent:", json.dumps(sent))
    print("cash:", result["cash"])


if __name__ == "__main__":
    main()
