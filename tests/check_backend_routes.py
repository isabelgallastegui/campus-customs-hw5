"""End-to-end check of the Problem 7 backend over HTTP.

The backend must already be running against a TEMPORARY database copy:
    cd backend
    CAMPUS_CUSTOMS_DB=/tmp/p7_test.db CAMPUS_CUSTOMS_CONTEXT=test ../.venv/bin/uvicorn main:app --reload --port 8000

Usage:  .venv/bin/python tests/check_backend_routes.py /tmp/p7_test.db [base_url]

The script refuses to run if the backend is using the real working database.
It runs real agents (gpt-6-luna) on ticket 102 in the temp copy, approves the
rent request as a human, checks the ticket status and the automatic
continuation, then resets the TEMP copy. Evidence goes to output/p7_route_tests.json.
"""

import hashlib
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMP_DB = Path(sys.argv[1]).resolve()
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"
REAL_DBS = [ROOT / "data" / "campus_customs.db", ROOT / "data" / "campus_customs_new.db"]
EVIDENCE: dict = {"base_url": BASE, "temp_db": TEMP_DB.name, "checks": []}


def http(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def db(sql: str, args=()):
    conn = sqlite3.connect(TEMP_DB)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def check(name: str, ok: bool, detail=None):
    EVIDENCE["checks"].append({"check": name, "passed": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  ->  {detail}" if detail is not None else ""))
    return ok


def wait_for(execution_id: str, timeout: int = 420) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        _, run = http("GET", f"/runs/{execution_id}")
        if run.get("status") != "running":
            return run
        time.sleep(3)
    return {"status": "client_timeout"}


def hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in REAL_DBS}


def main():
    before = hashes()
    _, health = http("GET", "/health")
    if health.get("using_default_database") or health.get("database") != TEMP_DB.name:
        sys.exit(f"Refusing: backend is not using the temp database ({health}).")
    check("health uses temp database", True, health)

    # 1. Tickets
    code, t = http("GET", "/tickets")
    check("GET /tickets returns 101, 102, 103 with status",
          code == 200 and [x["id"] for x in t["tickets"]] == [101, 102, 103],
          {x["id"]: x["status"] for x in t["tickets"]})

    # 5. Cash
    code, cash = http("GET", "/cash")
    (bal,) = db("SELECT balance FROM cash_accounts WHERE name='checking'")[0]
    check("GET /cash matches the database", code == 200 and cash["checking_balance"] == bal, cash)

    # 6. Reset needs confirmation
    code, r = http("POST", "/reset", {"confirm": "yes"})
    check("POST /reset without exact confirmation is refused", code == 400, r)
    code, r = http("POST", "/reset", {})
    check("POST /reset with no body field is refused", code == 422, None)

    # 2. Run ticket 102, and a second run of the same ticket is refused
    code, run = http("POST", "/tickets/102/run")
    check("POST /tickets/102/run starts a run", code == 202 and run["status"] == "running", run["execution_id"])
    code2, dup = http("POST", "/tickets/102/run")
    check("Second run of the same ticket is refused while running", code2 == 409, dup)
    code, r = http("POST", "/reset", {"confirm": "RESET"})
    check("Reset is refused while a run is active", code == 409, r)

    first = wait_for(run["execution_id"])
    EVIDENCE["first_run"] = first
    check("Run finishes with an execution status (not stuck)", first["status"] in ("completed", "stopped"),
          {"status": first["status"], "stop_reason": first["stop_reason"],
           "decision": (first["outcome"] or {}).get("decision")})

    # 3. Activity for this run
    code, act = http("GET", f"/activity?execution_id={run['execution_id']}")
    EVIDENCE["first_run_activity"] = {"delegations": act["delegations"], "tool_calls": act["tool_calls"]}
    check("GET /activity shows delegations and tool calls for the run",
          code == 200 and act["delegations"] and act["tool_calls"],
          {"events": act["count"], "delegations": [(d["from"], d["to"]) for d in act["delegations"]],
           "tools": [(c["agent"], c["tool"]) for c in act["tool_calls"]]})

    code, t102 = http("GET", "/tickets/102")
    waiting = t102["waiting_for_human"]
    check("Ticket 102 is waiting_approval with a pending rent request",
          t102["ticket"]["status"] == "waiting_approval" and len(waiting) == 1 and waiting[0]["kind"] == "rent",
          {"status": t102["ticket"]["status"], "waiting": waiting})
    check("No money moved before approval",
          db("SELECT COUNT(*) FROM payments")[0][0] == 0 and
          db("SELECT balance FROM cash_accounts")[0][0] == 3400)
    rid = waiting[0]["id"]

    # 4. Human approval
    code, bad = http("POST", f"/payments/{rid}/approve", {"approver_name": ""})
    check("Approval without a name is refused", code == 422)
    code, appr = http("POST", f"/payments/{rid}/approve", {"approver_name": "Isabel (test)", "note": "P7 test"})
    EVIDENCE["approval"] = appr
    check("POST /payments/{id}/approve executes the payment",
          code == 200 and appr["payment"]["checking_balance_after"] == 1000, appr.get("payment"))
    check("Ticket leaves waiting_approval but is not resolved by the approval",
          appr["ticket_status"] == "in_progress", appr["ticket_status"])
    check("Agents continue automatically after approval",
          (appr.get("continuation") or {}).get("started") is True, appr.get("continuation"))
    code, again = http("POST", f"/payments/{rid}/approve", {"approver_name": "Isabel (test)"})
    check("Approving the same request again is refused (no duplicate payment)", code == 409, again)
    check("Database reflects the payment",
          db("SELECT COUNT(*) FROM payments")[0][0] == 1 and db("SELECT balance FROM cash_accounts")[0][0] == 1000
          and db("SELECT next_due FROM leases WHERE id=1")[0][0] == "2026-10-02",
          {"payments": db("SELECT kind, ref_id, amount, approved_by FROM payments"),
           "lease_next_due": db("SELECT next_due FROM leases WHERE id=1")[0][0]})
    code, cash = http("GET", "/cash")
    check("GET /cash shows the new balance", cash["checking_balance"] == 1000, cash)

    cont = wait_for(appr["continuation"]["execution_id"])
    EVIDENCE["continuation_run"] = cont
    code, act2 = http("GET", f"/activity?execution_id={cont['execution_id']}")
    EVIDENCE["continuation_activity"] = {"delegations": act2["delegations"], "tool_calls": act2["tool_calls"]}
    tools2 = [c["tool"] for c in act2["tool_calls"]]
    check("Continuation run finishes", cont["status"] in ("completed", "stopped"),
          {"status": cont["status"], "decision": (cont["outcome"] or {}).get("decision"),
           "ticket_status": cont["ticket_status"], "tools": tools2})
    check("Continuation did not repeat the payment request",
          "request_payment_approval" not in tools2 and db("SELECT COUNT(*) FROM payment_requests")[0][0] == 1,
          db("SELECT id, kind, status FROM payment_requests"))
    code, t102 = http("GET", "/tickets/102")
    check("Final ticket status is consistent (no request waiting)",
          t102["ticket"]["status"] != "waiting_approval" and not t102["waiting_for_human"],
          t102["ticket"]["status"])

    code, runs = http("GET", "/runs?ticket_id=102")
    check("GET /runs lists both runs of ticket 102", len(runs["runs"]) == 2,
          [(r["execution_id"], r["trigger"], r["status"]) for r in runs["runs"]])

    # 6. Confirmed reset of the TEMP database
    code, r = http("POST", "/reset", {"confirm": "RESET"})
    check("POST /reset with confirmation restores the (temp) database",
          code == 200 and db("SELECT balance FROM cash_accounts")[0][0] == 3400
          and db("SELECT COUNT(*) FROM payments")[0][0] == 0
          and db("SELECT status FROM tickets WHERE id=102")[0][0] == "open", r)

    after = hashes()
    check("Real databases unchanged", before == after, after)
    (ROOT / "output" / "p7_route_tests.json").write_text(json.dumps(EVIDENCE, indent=2, default=str))
    failed = [c["check"] for c in EVIDENCE["checks"] if not c["passed"]]
    print(f"\n{len(EVIDENCE['checks']) - len(failed)}/{len(EVIDENCE['checks'])} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
