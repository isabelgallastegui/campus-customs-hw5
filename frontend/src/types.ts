// Shapes returned by the FastAPI backend (backend/main.py).

export type AgentId = 'boss' | 'inventory' | 'accounting' | 'facilities' | 'customer_service'

export interface PaymentRequest {
  id: number
  kind: 'invoice' | 'rent' | 'restock'
  ref_id: number
  amount: number
  account: string
  ticket_id: number | null
  reason: string | null
  requested_by: string
  requested_at: string
  status: 'pending_approval' | 'approved' | 'rejected' | 'executed'
  approved_by: string | null
  decided_at: string | null
  payment_id: number | null
}

export interface Finding {
  agent: AgentId
  objective?: string
  status: string
  summary: string
  facts?: string[]
  recommendation?: string
  missing_info?: string[]
  payment_request_ids?: number[]
  safety_flags?: string[]
}

export interface Decision {
  ticket_id: number
  status: 'waiting_approval' | 'blocked' | 'missing_info' | 'in_progress' | 'resolved' | 'failed'
  decision: string
  next_steps: string[]
  approvals_needed: { payment_request_id: number; what: string }[]
  customer_draft_id: number | null
  unresolved: string[]
  safety_flags: string[]
  customer_reply_needed?: boolean
}

export interface Outcome {
  execution_id: string
  completed: boolean
  stop_reason: string | null
  agent_steps: number
  mcp_tool_calls: number
  decision: Decision | null
  findings: Finding[]
  unresolved: string[]
  verified?: Record<string, any>
  corrections?: string[]
}

export type RunStatus = 'running' | 'completed' | 'stopped' | 'timed_out' | 'failed'

export interface Run {
  execution_id: string
  ticket_id: number
  trigger: string
  status: RunStatus
  started_at: string
  finished_at: string | null
  stop_reason: string | null
  ticket_status: string | null
  outcome: Outcome | null
}

export interface Draft {
  id: number; ticket_id: number; recipient: string; body: string; status: 'draft' | 'superseded'
  drafted_by: string; drafted_at: string
  sent: boolean; sent_at: string | null; sent_by: string | null; customer_reply_needed: boolean | null
}

export interface Ticket {
  id: number
  type: string
  requester: string
  subject: string
  sku: string | null
  size: string | null
  qty: number | null
  lease_id: number | null
  invoice_id: number | null
  status: 'open' | 'in_progress' | 'waiting_approval' | 'waiting_customer' | 'blocked' | 'resolved'
  notes: string | null
  created_at: string
  running: boolean
  active_execution_id: string | null
  latest_execution_id: string | null
  last_note: string | null
  last_run: Run | null
  payment_requests: PaymentRequest[]
  waiting_for_human: PaymentRequest[]
  message_drafts: Draft[]
  safety_warning: string | null
}

export interface AuditEvent {
  timestamp: string
  execution_id: string | null
  context: string | null
  ticket_id: number | null
  agent: string
  action: string
  tool: string | null
  target: string | null
  detail: any
  result: any
  status: string
}

export interface Cash {
  today: string
  checking_balance: number
  committed_to_requests: number
  uncommitted_cash: number
  open_invoice_total: number
  rent_due_within_7_days: number
}

export interface Health { ok: boolean; model: string; database: string; context: string; active_runs: Record<string, string> }
