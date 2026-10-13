// Shared logic: agent identities, ticket status labels, and turning audit events into a timeline.
import type { AgentId, AuditEvent, Decision, Finding, PaymentRequest, Ticket } from './types'

export const AGENTS: { id: AgentId; name: string; role: string }[] = [
  { id: 'boss', name: 'Boss', role: 'Delegates & decides' },
  { id: 'inventory', name: 'Inventory', role: 'Stock & vendors' },
  { id: 'accounting', name: 'Accounting', role: 'Cash & payments' },
  { id: 'facilities', name: 'Facilities', role: 'Rent & leases' },
  { id: 'customer_service', name: 'Customer Service', role: 'Drafts replies' },
]
export const AGENT_IDS = AGENTS.map(a => a.id) as string[]
export const agentName = (id: string | null | undefined) =>
  AGENTS.find(a => a.id === id)?.name ?? (id === 'system' ? 'System' : id === 'human' ? 'You' : id ?? '')

export const money = (n: number) => n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: n % 1 ? 2 : 0 })
export const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })

// ---------------------------------------------------------------- statuses

export type StatusKey = 'running' | 'approval' | 'customer' | 'missing' | 'blocked' | 'resolved' | 'progress' | 'open' | 'stopped'
export interface StatusView { key: StatusKey; label: string; icon: string; hint: string }

export function lastDecision(t: Ticket): Decision | null {
  return t.last_run?.outcome?.decision ?? null
}

function missingInfo(t: Ticket): string[] {
  const out = t.last_run?.outcome
  if (!out) return []
  const fromFindings = out.findings.flatMap(f => f.missing_info ?? [])
  return [...new Set([...fromFindings, ...(out.decision?.status === 'missing_info' ? out.decision.unresolved : [])])]
}

export function ticketStatus(t: Ticket): StatusView {
  if (t.running) return { key: 'running', label: 'Agents working', icon: 'clock', hint: 'Follow the live timeline.' }
  const run = t.last_run
  if (t.status === 'waiting_approval' || t.waiting_for_human.length)
    return { key: 'approval', label: 'Awaiting approval', icon: 'shield',
             hint: `${t.waiting_for_human.length} request${t.waiting_for_human.length === 1 ? ' needs' : 's need'} your decision.` }
  if (t.status === 'waiting_customer') {
    const last = [...t.message_drafts].reverse().find(d => d.sent)
    return { key: 'customer', label: 'Waiting for customer', icon: 'mail',
             hint: last?.sent_at ? `Message sent ${new Date(last.sent_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}; waiting for their reply.` : 'Waiting for the customer to reply.' }
  }
  if (t.status === 'resolved') return { key: 'resolved', label: 'Resolved', icon: 'checkCircle', hint: 'Completed. Nothing left to do.' }
  if (t.status === 'blocked') {
    const missing = lastDecision(t)?.status === 'missing_info' || missingInfo(t).length > 0 || /missing_info/.test(t.last_note ?? '')
    return missing
      ? { key: 'missing', label: 'Blocked · missing info', icon: 'question', hint: 'Waiting for information from someone.' }
      : { key: 'blocked', label: 'Blocked', icon: 'alert', hint: 'Something prevents progress.' }
  }
  if (run && ['stopped', 'timed_out', 'failed'].includes(run.status))
    return { key: 'stopped', label: 'Run stopped', icon: 'stop', hint: run.stop_reason ?? 'The last run ended early.' }
  if (t.status === 'in_progress') return { key: 'progress', label: 'In progress', icon: 'clock', hint: 'Work started, not finished.' }
  return { key: 'open', label: 'Open', icon: 'ticket', hint: 'Not handled yet. Run the agents.' }
}

/** What a person should do next for this ticket, in plain words. */
export const unsentDraft = (t: Ticket) => [...t.message_drafts].reverse().find(d => d.status === 'draft' && !d.sent) ?? null

export function nextStep(t: Ticket): { tone: StatusKey; title: string; items: string[] } {
  const n = baseNextStep(t)
  const draft = unsentDraft(t)
  if (draft && !t.running) n.items = [...n.items, `Review the customer message to ${draft.recipient}, send it yourself, then press "Mark as Sent".`]
  return n
}

function baseNextStep(t: Ticket): { tone: StatusKey; title: string; items: string[] } {
  const s = ticketStatus(t)
  const d = lastDecision(t)
  const run = t.last_run
  switch (s.key) {
    case 'running': return { tone: s.key, title: 'The agents are working on this ticket.', items: ['Activity appears in the timeline as it happens.'] }
    case 'approval': return { tone: s.key, title: 'Your approval is needed.', items: t.waiting_for_human.map(r => `${requestLabel(r)} · ${money(r.amount)}: review it in Human approvals.`) }
    case 'missing': return { tone: s.key, title: 'Blocked: information is missing.', items: missingInfo(t).length ? missingInfo(t) : (d?.unresolved ?? []) }
    case 'blocked': return { tone: s.key, title: 'Blocked.', items: d?.unresolved?.length ? d.unresolved : d?.next_steps ?? [] }
    case 'stopped': return { tone: s.key, title: `The last run stopped: ${run?.stop_reason ?? 'unknown reason'}.`,
                             items: [...(run?.outcome?.unresolved ?? []), 'Check the timeline, then run the agents again if needed.'] }
    case 'customer': return { tone: s.key, title: 'Waiting for the customer to reply.', items: [s.hint] }
    case 'resolved': return { tone: s.key, title: 'Resolved. Nothing left to do.', items: d?.decision ? [d.decision] : [] }
    case 'progress': return { tone: s.key, title: 'In progress.', items: d?.next_steps?.length ? d.next_steps : ['Run the agents to continue.'] }
    default: return { tone: s.key, title: 'Not started.', items: ['Press "Run agents" to let the team handle this ticket.'] }
  }
}

export function requestLabel(r: PaymentRequest): string {
  if (r.kind === 'invoice') return `Invoice #${r.ref_id}`
  if (r.kind === 'rent') return `Rent · lease #${r.ref_id}`
  return `Restock order #${r.ref_id}`
}
export const requestIcon = (r: PaymentRequest) => (r.kind === 'invoice' ? 'receipt' : r.kind === 'rent' ? 'key' : 'cart')

// ---------------------------------------------------------------- timeline

export type Tone = 'agent' | 'delegate' | 'tool' | 'result' | 'human' | 'warn' | 'bad' | 'good' | 'system'
export interface TimelineItem {
  id: string
  ts: string
  executionId: string | null
  agent: string
  target?: string | null
  tone: Tone
  icon: string
  title: string
  text?: string
  tool?: string | null
}

const short = (v: any, n = 180): string => {
  if (v == null) return ''
  const s = typeof v === 'string' ? v : (v.summary ?? v.decision ?? v.error ?? v.message ?? JSON.stringify(v))
  return String(s).length > n ? String(s).slice(0, n) + '…' : String(s)
}

function objective(prompt: any): string {
  const m = /Objective:\s*([\s\S]*?)(\n|$)/.exec(String(prompt ?? ''))
  return m ? m[1] : short(prompt, 140)
}

export function toTimeline(events: AuditEvent[], showModelTurns = false): TimelineItem[] {
  const items: TimelineItem[] = []
  events.forEach((e, i) => {
    const id = `${e.timestamp}-${i}`
    const base = { id, ts: e.timestamp, executionId: e.execution_id, agent: e.agent, target: e.target, tool: e.tool }
    const A = agentName(e.agent), T = agentName(e.target)
    switch (e.action) {
      case 'run_started': items.push({ ...base, tone: 'system', icon: 'play', title: 'Run started' }); break
      case 'agent_run_started': items.push({ ...base, tone: 'agent', icon: 'play', title: `${A} started`, text: e.agent === 'boss' ? short(e.detail, 160) : objective(e.detail) }); break
      case 'delegation':
        if (e.status === 'started') items.push({ ...base, tone: 'delegate', icon: 'arrow', title: `${A} → ${T}`, text: short(e.detail) })
        else items.push({ ...base, tone: 'warn', icon: 'alert', title: `${A} → ${T} refused`, text: short(e.result) })
        break
      case 'delegation_reused': items.push({ ...base, tone: 'system', icon: 'arrow', title: `${A} reused ${T}'s earlier answer`, text: short(e.detail) }); break
      case 'delegation_result': items.push({ ...base, agent: e.target ?? e.agent, target: e.agent, tone: e.status === 'failed' ? 'bad' : 'result', icon: 'back',
                                             title: `${T} reported to ${A}`, text: short(e.result) }); break
      case 'mcp_tool_call': items.push({ ...base, tone: e.status === 'ok' ? 'tool' : e.status === 'tool_refused' ? 'warn' : 'bad', icon: 'tool',
                                         title: `${A} used ${e.tool}`, text: e.status === 'ok' ? '' : `${e.status}: ${short(e.result, 140)}` }); break
      case 'repeat_call_reused': items.push({ ...base, tone: 'system', icon: 'tool', title: `${A} repeated ${e.tool} (reused result)` }); break
      case 'approval_requested': items.push({ ...base, tone: 'human', icon: 'shield', title: `${A} prepared a request for human approval`, text: short(e.result?.message ?? e.result, 160) }); break
      case 'agent_run_finished': items.push({ ...base, tone: 'good', icon: 'check', title: `${A} finished`, text: short(e.result) }); break
      case 'model_turn': if (showModelTurns) items.push({ ...base, tone: 'system', icon: 'system', title: `${A} thought`, text: `${e.detail?.output_tokens ?? '?'} tokens · ${(e.detail?.tool_calls_requested ?? []).join(', ') || 'answer'}` }); break
      case 'limit_reached': items.push({ ...base, tone: 'bad', icon: 'stop', title: `Limit reached (${A})`, text: short(e.result) }); break
      case 'safety_refusal': items.push({ ...base, tone: 'bad', icon: 'shield', title: `Safety refusal (${A})`, text: short(e.result) }); break
      case 'agent_error': case 'run_error': case 'run_timeout': items.push({ ...base, tone: 'bad', icon: 'alert', title: e.action === 'run_timeout' ? 'Run timed out' : `Error (${A})`, text: short(e.result) }); break
      case 'run_finished': items.push({ ...base, tone: e.status === 'completed' ? 'good' : 'bad', icon: e.status === 'completed' ? 'checkCircle' : 'stop',
                                        title: e.status === 'completed' ? 'Run finished' : 'Run stopped early', text: e.result?.stop_reason ? `Reason: ${e.result.stop_reason}` : '' }); break
      case 'payment_approved': items.push({ ...base, agent: 'human', tone: 'human', icon: 'human', title: `You approved request #${e.detail?.request_id}`, text: `Approver: ${e.detail?.approver}` }); break
      case 'payment_executed': items.push({ ...base, agent: 'human', tone: 'good', icon: 'wallet', title: `Payment executed: ${money(e.result?.amount ?? 0)}`, text: `Checking now ${money(e.result?.checking_balance_after ?? 0)}` }); break
      case 'payment_refused': case 'payment_failed': items.push({ ...base, agent: 'human', tone: 'bad', icon: 'alert', title: 'Payment refused', text: short(e.result) }); break
      case 'payment_rejected': items.push({ ...base, agent: 'human', tone: 'warn', icon: 'x', title: `You rejected request #${e.detail?.request_id}` }); break
      case 'message_edited': items.push({ ...base, agent: 'human', tone: 'human', icon: 'mail', title: `You edited the customer message (now #${e.result?.draft_id})` }); break
      case 'message_marked_sent': items.push({ ...base, agent: 'human', tone: 'good', icon: 'mail', title: `You marked message #${e.detail?.draft_id} as sent`, text: `${new Date(e.result?.sent_at).toLocaleString()} · ticket now ${e.result?.ticket_status}` }); break
      case 'message_mark_sent_refused': items.push({ ...base, agent: 'human', tone: 'bad', icon: 'alert', title: 'Mark as sent refused', text: short(e.result) }); break
      case 'role_refusal': items.push({ ...base, tone: 'warn', icon: 'shield', title: `${A} tried ${e.tool} (not its role)`, text: short(e.result) }); break
      case 'outcome_verified': if (e.status === 'corrected') items.push({ ...base, tone: 'warn', icon: 'checkCircle', title: 'System corrected the report from the database', text: (e.result?.corrections ?? []).join(' ') }); break
      case 'agents_continue': items.push({ ...base, agent: 'system', tone: 'system', icon: 'play', title: e.status === 'started' ? (e.result?.queued ? 'Agents continue (queued start)' : 'Agents continue after approval') : e.status === 'queued' ? 'Agents will continue when the current run ends' : 'Continuation skipped', text: short(e.result?.reason ?? '') }); break
      default: break
    }
  })
  return items
}

// ------------------------------------------------------------ live agents

export interface AgentLive {
  state: 'idle' | 'working' | 'done' | 'failed'
  doing: string
  tools: string[]
  sent: number
  received: number
  summary?: string
}

export function agentStates(events: AuditEvent[]): Record<string, AgentLive> {
  const s: Record<string, AgentLive> = {}
  AGENT_IDS.forEach(id => (s[id] = { state: 'idle', doing: '', tools: [], sent: 0, received: 0 }))
  for (const e of events) {
    const a = s[e.agent]
    if (e.action === 'agent_run_started' && a) { a.state = 'working'; a.doing = e.agent === 'boss' ? 'Reading the ticket and planning' : objective(e.detail) }
    if (e.action === 'mcp_tool_call' && a && e.tool) { if (!a.tools.includes(e.tool)) a.tools.push(e.tool); if (a.state === 'working') a.doing = `Using ${e.tool}` }
    if (e.action === 'approval_requested' && a && e.tool && !a.tools.includes(e.tool)) a.tools.push(e.tool)
    if (e.action === 'delegation' && e.status === 'started' && a) { a.sent++; a.doing = `Waiting on ${agentName(e.target)}`; if (e.target && s[e.target]) s[e.target].received++ }
    if (e.action === 'delegation_result' && a) a.doing = `Got ${agentName(e.target)}'s answer`
    if (e.action === 'agent_run_finished' && a) { a.state = 'done'; a.summary = short(e.result, 220); a.doing = 'Finished' }
    if ((e.action === 'agent_error' || e.action === 'limit_reached') && a) { a.state = 'failed'; a.doing = short(e.result, 100) }
    if (e.action === 'run_finished' || e.action === 'run_timeout' || e.action === 'run_error') {
      // The run is over: nobody is still working.
      Object.values(s).forEach(x => { if (x.state === 'working') { x.state = 'failed'; x.doing = `Stopped: ${short(e.result?.stop_reason ?? e.result, 90)}` } })
    }
  }
  return s
}

export function findingsByAgent(findings: Finding[] = []): Record<string, Finding[]> {
  const out: Record<string, Finding[]> = {}
  findings.forEach(f => (out[f.agent] ??= []).push(f))
  return out
}
