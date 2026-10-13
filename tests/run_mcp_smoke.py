"""MCP smoke test: Claude Code calls each tool through the real MCP connection.

Runs Claude Code headless with the project's .mcp.json, captures the actual
tool call + tool result from the stream, verifies each result against direct
SQLite queries on data/campus_customs_new.db, and writes output/mcp_smoke.json.

Usage:  .venv/bin/python tests/run_mcp_smoke.py
Set CLAUDE_BIN if the `claude` CLI is not on PATH.
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "campus_customs_new.db"
SERVER = "campus-customs-hw51"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")

TESTS = [
    {
        "tool_name": "check_stock_availability",
        "prompt": "Ticket 101: Tauhid Zaman wants 1 Classic Bulldog Tee (SKU CC-TEE-WHITE) in size S. "
                  "Use the check_stock_availability tool to see if we can fill this order from stock.",
    },
    {
        "tool_name": "get_vendor_status",
        "prompt": "Ticket 103 needs 12 more CC-HOOD-NAVY hoodies from Bulldog Print Co (vendor 1). "
                  "Use the get_vendor_status tool to check vendor 1's lead time and whether unpaid invoices block a new order.",
    },
    {
        "tool_name": "get_lease_details",
        "prompt": "Ticket 102 is a rent notice from Elm City Properties for lease 1. "
                  "Use the get_lease_details tool to check how much rent is due, when, and whether cash covers it.",
    },
]


def run_claude(prompt: str, tool_name: str) -> dict:
    """Run one headless Claude Code session and return the captured MCP call."""
    full_tool = f"mcp__{SERVER}__{tool_name}"
    proc = subprocess.run(
        [CLAUDE_BIN, "-p", prompt,
         "--mcp-config", str(ROOT / ".mcp.json"), "--strict-mcp-config",
         "--allowedTools", full_tool,
         "--output-format", "stream-json", "--verbose"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    tools_seen, servers, call, result, error = [], [], None, None, None
    for line in proc.stdout.splitlines():
        event = json.loads(line)
        if event.get("type") == "result" and call is None:
            error = event.get("result")
        if event.get("type") == "system" and event.get("subtype") == "init":
            tools_seen = [t for t in event.get("tools", []) if t.startswith(f"mcp__{SERVER}__")]
            servers = event.get("mcp_servers", [])
        for block in (event.get("message") or {}).get("content", []) if isinstance(
                (event.get("message") or {}).get("content"), list) else []:
            if block.get("type") == "tool_use" and block.get("name") == full_tool:
                call = block
            if block.get("type") == "tool_result" and call and block.get("tool_use_id") == call["id"]:
                content = block["content"]
                text = content if isinstance(content, str) else "".join(
                    c.get("text", "") for c in content if c.get("type") == "text")
                result = json.loads(text)
    return {"servers": servers, "tools_seen": tools_seen, "call": call, "result": result, "error": error}


def expected(tool_name: str, args: dict) -> dict:
    """Recompute key fields straight from SQLite to verify the tool output."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    if tool_name == "check_stock_availability":
        (qty,) = conn.execute("SELECT qty FROM inventory WHERE sku=? AND size=?",
                              (args["sku"], args["size"])).fetchone()
        return {"qty_on_hand": qty, "shortfall": max(args["qty_needed"] - qty, 0)}
    if tool_name == "get_vendor_status":
        (lead,) = conn.execute("SELECT lead_days FROM vendors WHERE id=?", (args["vendor_id"],)).fetchone()
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM invoices WHERE vendor_id=? AND status!='paid'", (args["vendor_id"],))]
        return {"lead_days": lead, "unpaid_invoice_ids": ids}
    if tool_name == "get_lease_details":
        rent, due = conn.execute("SELECT monthly_rent, next_due FROM leases WHERE id=?",
                                 (args["lease_id"],)).fetchone()
        (bal,) = conn.execute("SELECT balance FROM cash_accounts WHERE name='checking'").fetchone()
        return {"monthly_rent": rent, "next_due": due, "checking_balance": bal}


def actual(tool_name: str, out: dict) -> dict:
    if tool_name == "get_vendor_status":
        return {"lead_days": out["lead_days"],
                "unpaid_invoice_ids": [i["invoice_id"] for i in out["unpaid_invoices"]]}
    return {k: out[k] for k in expected_keys[tool_name]}


expected_keys = {
    "check_stock_availability": ["qty_on_hand", "shortfall"],
    "get_lease_details": ["monthly_rent", "next_due", "checking_balance"],
}


def main():
    results = []
    for test in TESTS:
        run = run_claude(test["prompt"], test["tool_name"])
        called = run["call"] is not None and run["result"] is not None
        exp = expected(test["tool_name"], run["call"]["input"]) if called else None
        act = actual(test["tool_name"], run["result"]) if called else None
        passed = called and exp == act
        results.append({
            "prompt": test["prompt"],
            "tool_name": test["tool_name"],
            "mcp_tool_id": f"mcp__{SERVER}__{test['tool_name']}",
            "tool_input": run["call"]["input"] if called else None,
            "tool_output": run["result"],
            "verified_against_db": {"expected": exp, "actual": act},
            "passed": passed,
            "error": None if called else run["error"],
        })
        print(f"{test['tool_name']}: {'PASS' if passed else 'FAIL'}")

    smoke = {
        "server": SERVER,
        "config": ".mcp.json",
        "database": "data/campus_customs_new.db",
        "connection": {"mcp_servers": run["servers"], "tools_discovered": run["tools_seen"]},
        "all_passed": all(r["passed"] for r in results),
        "tests": results,
    }
    (ROOT / "output" / "mcp_smoke.json").write_text(json.dumps(smoke, indent=2) + "\n")


if __name__ == "__main__":
    main()
