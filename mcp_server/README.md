# Campus Customs MCP Server

## Purpose
This FastMCP server is the **only** way the five Campus Customs agents (Boss, Inventory, Accounting, Facilities, Customer Service) read and change the shop's database. It provides read tools, tools that *prepare* requests for human approval, and a few safe record updates. Payments are approved and executed only by a human.

## Database
- Uses the working copy **`data/campus_customs_new.db`** (tests can point it at a temp copy with `CAMPUS_CUSTOMS_DB`).
- Refuses to open the original `data/campus_customs.db`.
- The shop's current date always comes from `desk.date_today`.
- Every write is a single `BEGIN IMMEDIATE … COMMIT` transaction, so an interrupted call changes nothing.
- Three support tables (`payment_requests`, `restock_orders`, `customer_message_drafts`) are created on the first write only.

## Files
- `server.py`: FastMCP server and tools, grouped by Tickets / Inventory / Accounting / Facilities / Customer Service / Human-only. Add new tools with `@mcp.tool`.
- `db.py`: database path, connection and transaction helpers, support-table schema, `get_today()`, cash helpers.

## Tools for agents (14)

| Tool | Type | Reads / writes | Tickets |
|---|---|---|---|
| `list_open_tickets()` | read | tickets | all |
| `get_ticket_details(ticket_id)` | read | tickets + related requests/drafts; flags injection-like text | all |
| `update_ticket_status(ticket_id, status, note, updated_by)` | write | tickets | all |
| `check_stock_availability(sku, size, qty_needed)` | read | inventory | 101, 103 |
| `list_vendors()` | read | vendors, invoices | 101, 103 |
| `get_vendor_status(vendor_id)` | read | vendors, invoices, desk | 101, 103 |
| `request_restock_order(vendor_id, sku, size, qty, requested_by, ticket_id?)` | prepare | → restock_orders, payment_requests | 101, 103 |
| `get_cash_position()` | read | cash_accounts, invoices, leases, payment_requests | all |
| `get_invoice_details(invoice_id)` | read | invoices, vendors, payments, payment_requests | 101, 103 |
| `get_pricing_and_margin(sku, qty, target_unit_price?)` | read | pricing | 103 |
| `list_payment_requests(status?)` | read | payment_requests | 101, 102 |
| `request_payment_approval(kind, ref_id, amount, requested_by, reason, ticket_id?)` | prepare | → payment_requests | 101, 102 |
| `get_lease_details(lease_id)` | read | leases, desk, cash_accounts | 102 |
| `draft_customer_message(ticket_id, body, drafted_by)` | write | → customer_message_drafts (never sent) | 101, 103 |

## Human-only tools (3)
Registered only when started with `CAMPUS_CUSTOMS_HUMAN_TOOLS=1` (the future dashboard). The agents' server process never has them.

| Tool | What it does |
|---|---|
| `approve_payment_request(request_id, approver_name, note?)` | A named human approves (agent names are rejected) |
| `reject_payment_request(request_id, approver_name, note?)` | A named human rejects |
| `execute_approved_payment(request_id)` | Atomic: payment row with `approved_by`, lower checking, settle invoice / advance lease / place restock. Refuses unapproved, already-executed, or negative-cash payments |

## Running
```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python mcp_server/server.py
```
Claude Code connects through `.mcp.json` (agent mode, no human-only tools).
