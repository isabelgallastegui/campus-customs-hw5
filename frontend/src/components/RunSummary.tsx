// After a run: Boss's decision, what each agent did, and what is needed next.
import { AGENTS, findingsByAgent, money, nextStep } from '../domain'
import type { AgentLive } from '../domain'
import { Icon } from '../icons'
import type { Ticket } from '../types'

const DECISION_LABEL: Record<string, string> = {
  waiting_approval: 'Awaiting approval', blocked: 'Blocked', missing_info: 'Missing information',
  in_progress: 'In progress', resolved: 'Resolved', failed: 'Failed',
}

export function NextStep({ ticket }: { ticket: Ticket }) {
  const n = nextStep(ticket)
  const icon = { running: 'clock', approval: 'shield', customer: 'mail', missing: 'question', blocked: 'alert', resolved: 'checkCircle',
                 progress: 'clock', open: 'play', stopped: 'stop' }[n.tone]
  return (
    <div className={`next n-${n.tone}`}>
      <span className="next-ic"><Icon name={icon} size={20} /></span>
      <div>
        <div className="next-title">{n.title}</div>
        {n.items.length > 0 && <ul>{n.items.slice(0, 4).map((i, k) => <li key={k}>{i}</li>)}</ul>}
      </div>
    </div>
  )
}

export function RunSummary({ ticket, live }: { ticket: Ticket; live: Record<string, AgentLive> }) {
  const run = ticket.last_run
  if (ticket.running) return null
  const out = run?.outcome
  const d = out?.decision
  const byAgent = findingsByAgent(out?.findings)
  if (!run && !Object.values(live).some(a => a.state !== 'idle')) return null
  return (
    <div className="summary">
      {run && run.status !== 'completed' && (
        <div className="alert bad big"><Icon name="stop" />
          <div><b>Run {run.status.replace('_', ' ')}.</b> {run.stop_reason}
            <div className="muted">What was finished is kept; anything unfinished is listed below. You can run the agents again.</div></div>
        </div>
      )}
      {d && (
        <div className="decision">
          <div className="decision-head"><Icon name="flag" /><b>Boss decision</b>
            <span className={`badge d-${d.status}`}>{DECISION_LABEL[d.status] ?? d.status}</span>
            {out && <span className="muted stats">{out.agent_steps} agent steps · {out.mcp_tool_calls} tool calls</span>}
          </div>
          <p>{d.decision}</p>
          <div className="decision-grid">
            {d.approvals_needed.length > 0 && <div><h4>Needs your approval</h4><ul>{d.approvals_needed.map(a => <li key={a.payment_request_id}>#{a.payment_request_id} · {a.what}</li>)}</ul></div>}
            {d.next_steps.length > 0 && <div><h4>Next steps</h4><ul>{d.next_steps.map((s, i) => <li key={i}>{s}</li>)}</ul></div>}
            {d.unresolved.length > 0 && <div><h4>Still open</h4><ul>{d.unresolved.map((s, i) => <li key={i}>{s}</li>)}</ul></div>}
            {d.safety_flags.length > 0 && <div className="flags"><h4>Safety flags</h4><ul>{d.safety_flags.map((s, i) => <li key={i}>{s}</li>)}</ul></div>}
          </div>
        </div>
      )}
      <div className="who-did">
        {AGENTS.filter(a => a.id !== 'boss' && (byAgent[a.id] || live[a.id].state !== 'idle')).map(a => {
          const f = byAgent[a.id]?.at(-1)
          return (
            <div key={a.id} className="did" style={{ ['--c' as any]: `var(--${a.id})` }}>
              <span className="did-ic"><Icon name={a.id} size={15} /></span>
              <div>
                <div className="did-name">{a.name}{f && <span className={`mini s-${f.status}`}>{f.status.replace('_', ' ')}</span>}</div>
                <div className="did-text">{f?.summary ?? live[a.id].summary ?? live[a.id].doing}</div>
                {f?.missing_info?.length ? <div className="did-missing"><Icon name="question" size={13} />Missing: {f.missing_info.join('; ')}</div> : null}
              </div>
            </div>
          )
        })}
      </div>
      {out?.corrections && out.corrections.length > 0 && (
        <div className="alert warn"><Icon name="checkCircle" /><div><b>Checked against the database:</b>
          <ul className="corr">{out.corrections.map((c, i) => <li key={i}>{c}</li>)}</ul></div></div>
      )}
      {ticket.waiting_for_human.length > 0 && (
        <div className="muted small-note">{ticket.waiting_for_human.length} request(s) totaling {money(ticket.waiting_for_human.reduce((n, r) => n + r.amount, 0))} wait in Human approvals.</div>
      )}
    </div>
  )
}
