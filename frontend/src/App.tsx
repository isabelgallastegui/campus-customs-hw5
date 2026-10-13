import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API_URL, ApiError, api } from './api'
import { agentStates, money, ticketStatus } from './domain'
import { Icon } from './icons'
import type { AuditEvent, Cash, Health, PaymentRequest, Ticket } from './types'
import { AgentBoard } from './components/AgentBoard'
import { Approvals } from './components/Approvals'
import { CashCard } from './components/CashCard'
import { FlowGraph } from './components/FlowGraph'
import { Modal } from './components/Modal'
import { NextStep, RunSummary } from './components/RunSummary'
import { TicketQueue } from './components/TicketQueue'
import { CustomerMessages } from './components/CustomerMessages'
import { Timeline } from './components/Timeline'

const FAST_MS = 1500   // while agents are working or a payment is processing
const SLOW_MS = 6000   // idle

interface Toast { id: number; kind: 'ok' | 'err'; text: string }

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [tickets, setTickets] = useState<Ticket[] | null>(null)
  const [today, setToday] = useState('')
  const [requests, setRequests] = useState<PaymentRequest[]>([])
  const [cash, setCash] = useState<Cash | null>(null)
  const [events, setEvents] = useState<AuditEvent[]>([])
  // ?ticket=101 opens a ticket directly (handy for links and screenshots).
  const [selected, setSelected] = useState<number | null>(() => Number(new URLSearchParams(location.search).get('ticket')) || null)
  const [offline, setOffline] = useState<string | null>(null)
  const [starting, setStarting] = useState<number | null>(null)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [resetOpen, setResetOpen] = useState(false)
  const selectedRef = useRef<number | null>(null)
  selectedRef.current = selected

  const notify = useCallback((kind: 'ok' | 'err', text: string) => {
    const id = Date.now() + Math.random()
    setToasts(t => [...t, { id, kind, text }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), kind === 'ok' ? 7000 : 11000)
  }, [])

  const refresh = useCallback(async () => {
    try {
      const [h, t, p, c] = await Promise.all([api.health(), api.tickets(), api.payments(), api.cash()])
      setHealth(h); setTickets(t.tickets); setToday(t.today); setRequests(p.payment_requests); setCash(c)
      const sel = selectedRef.current ?? t.tickets[0]?.id ?? null
      if (selectedRef.current === null && sel !== null) setSelected(sel)
      if (sel !== null) {
        const a = await api.activity(sel)
        // Ignore a late answer for a ticket that is no longer selected.
        if (selectedRef.current === sel || selectedRef.current === null) setEvents(a.events.filter(e => e.context === h.context))
      }
      setOffline(null)
    } catch (e) {
      setOffline(e instanceof ApiError ? e.message : String(e))
    }
  }, [])

  const anyRunning = !!tickets?.some(t => t.running) || starting !== null
  useEffect(() => { setEvents([]); refresh() }, [refresh, selected])
  useEffect(() => {
    const id = setInterval(refresh, anyRunning ? FAST_MS : SLOW_MS)
    return () => clearInterval(id)
  }, [refresh, anyRunning])

  async function runTicket(id: number) {
    setSelected(id); setStarting(id)
    try {
      await api.run(id)
      notify('ok', `Agents started on ticket ${id}.`)
    } catch (e) {
      notify('err', `Could not start ticket ${id}: ${e instanceof ApiError ? e.message : e}`)
    } finally {
      setStarting(null); refresh()
    }
  }

  const ticket = tickets?.find(t => t.id === selected) ?? null
  // Follow the newest run of the selected ticket (after an approval, that's the continuation).
  const currentExec = ticket?.active_execution_id ?? ticket?.latest_execution_id ?? null
  // While running: follow that run live. Afterwards: the whole ticket (all runs since the last reset).
  const runEvents = useMemo(() => ticket?.running ? events.filter(e => e.execution_id === currentExec) : events,
                            [events, currentExec, ticket?.running])
  const runCount = new Set(events.filter(e => e.action === 'run_started').map(e => e.execution_id)).size
  const live = useMemo(() => {
    const s = agentStates(runEvents)
    // If the ticket is no longer running, nobody can still be working (e.g. the server restarted).
    if (ticket && !ticket.running) Object.values(s).forEach(a => { if (a.state === 'working') { a.state = 'failed'; a.doing = 'Run interrupted before finishing' } })
    return s
  }, [runEvents, ticket?.running])
  // Timeline: every run of this ticket since the last reset, with your approvals in between.
  const timelineEvents = events

  const pending = requests.filter(r => r.status === 'pending_approval' || r.status === 'approved')
  const counts = (tickets ?? []).reduce<Record<string, number>>((m, t) => { const k = ticketStatus(t).key; m[k] = (m[k] ?? 0) + 1; return m }, {})

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="logo"><Icon name="logo" size={22} /></span>
          <div><h1>Campus Customs · Operations Desk</h1>
            <p>Five AI agents handle the tickets. You approve the money.</p></div>
        </div>
        <div className="top-right">
          <span className="pill"><i className="dot" />Shop date · {today ? new Date(today + 'T12:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '…'}</span>
          {health && <span className={`pill ${health.context !== 'live' ? 'warn' : ''}`} title="Database the backend is using">{health.database}{health.context !== 'live' && ' · test'}</span>}
          <span className={`pill ${offline ? 'bad' : 'ok'}`}><i className="dot" />{offline ? 'Backend offline' : anyRunning ? 'Live · updating' : 'Connected'}</span>
          <button className="btn ghost light" onClick={() => setResetOpen(true)} disabled={anyRunning} title={anyRunning ? 'Wait for runs to finish' : 'Restore the working database'}>
            <Icon name="reset" size={14} />Reset data</button>
        </div>
      </header>

      {offline && <div className="banner bad"><Icon name="alert" /><div><b>{offline}</b> Start it with <code>cd backend && uvicorn main:app --reload --port 8000</code>. Retrying automatically…</div></div>}

      <section className="kpis">
        <CashCard cash={cash} />
        <div className={`kpi ${pending.length ? 'attn' : ''}`}>
          <div className="kpi-label"><Icon name="shield" size={15} />Awaiting your approval</div>
          <div className="kpi-value">{pending.length}<small>{pending.length ? money(pending.reduce((n, r) => n + r.amount, 0)) : 'all clear'}</small></div>
        </div>
        <div className="kpi">
          <div className="kpi-label"><Icon name="ticket" size={15} />Tickets</div>
          <div className="status-strip">
            {(['running', 'approval', 'customer', 'missing', 'blocked', 'stopped', 'progress', 'open', 'resolved'] as const).filter(k => counts[k]).map(k =>
              <span key={k} className={`badge b-${k}`}>{counts[k]} {{ running: 'running', approval: 'awaiting approval', customer: 'waiting for customer', missing: 'missing info', blocked: 'blocked', stopped: 'stopped', progress: 'in progress', open: 'open', resolved: 'resolved' }[k]}</span>)}
            {!tickets && <span className="muted">Loading…</span>}
          </div>
        </div>
        <div className="kpi">
          <div className="kpi-label"><Icon name="system" size={15} />Agent team</div>
          <div className="team-dots">
            {Object.entries(live).map(([id, s]) => <span key={id} className={`tdot ${s.state}`} style={{ ['--c' as any]: `var(--${id})` }} title={`${id}: ${s.state}`}><Icon name={id} size={13} /></span>)}
          </div>
          <div className="kpi-sub"><span>{anyRunning ? 'Working…' : 'Idle'}</span><span>{health?.model ?? ''}</span></div>
        </div>
      </section>

      <main className="grid">
        <aside className="col-left">
          <div className="col-title"><Icon name="list" size={15} />Ticket queue</div>
          {tickets ? <TicketQueue tickets={tickets} selected={selected} onSelect={setSelected} onRun={runTicket} starting={starting} />
            : <div className="queue">{[1, 2, 3].map(i => <div key={i} className="tcard skeleton" />)}</div>}
        </aside>

        <section className="col-main">
          {ticket ? <>
            <div className="card ws-head">
              <div className="ws-num">#{ticket.id}</div>
              <div className="ws-info">
                <div className="eyebrow">{ticket.type} · {ticket.requester}</div>
                <h2>{ticket.subject}</h2>
                <p className="quote">“{(ticket.notes ?? '').split('\n')[0]}”</p>
              </div>
              <span className={`badge big b-${ticketStatus(ticket).key}`}><Icon name={ticketStatus(ticket).icon} size={14} />{ticketStatus(ticket).label}</span>
            </div>
            <NextStep ticket={ticket} />
            {ticket.safety_warning && <div className="alert warn"><Icon name="shield" />{ticket.safety_warning}</div>}

            <div className="card">
              <div className="card-head"><Icon name="system" /><h2>Agents</h2><span className="muted">{!currentExec ? 'No run yet' : ticket.running ? `Live · run ${currentExec}` : `All ${runCount} run${runCount === 1 ? '' : 's'} of this ticket`}</span></div>
              <AgentBoard live={live} />
            </div>

            <div className="flow-row">
              <div className="card">
                <div className="card-head"><Icon name="arrow" /><h2>Who talks to whom</h2><span className="muted">solid asks · dashed reports</span></div>
                <FlowGraph events={runEvents} live={live} />
              </div>
              <div className="card">
                <div className="card-head"><Icon name="clock" /><h2>Live timeline</h2></div>
                <Timeline events={timelineEvents} running={ticket.running} />
              </div>
            </div>

            <RunSummary ticket={ticket} live={live} />
            <CustomerMessages ticket={ticket} onDone={refresh} notify={notify} />
          </> : <div className="card skeleton tall" />}
        </section>

        <aside className="col-right">
          <Approvals requests={requests} cash={cash} onDone={refresh} notify={notify} />
          <div className="card rules">
            <div className="card-head"><Icon name="shield" /><h2>House rules</h2></div>
            <ul>
              <li>Agents prepare; <b>you approve</b>. Every payment needs a named person.</li>
              <li>Cash can never go below <b>$0</b>, and no payment can happen twice.</li>
              <li>Vendors with unpaid invoices can't ship.</li>
              <li>Customer messages are drafts. Nothing is sent.</li>
            </ul>
          </div>
        </aside>
      </main>

      <div className="toasts" aria-live="polite">
        {toasts.map(t => <div key={t.id} className={`toast ${t.kind}`}><Icon name={t.kind === 'ok' ? 'checkCircle' : 'alert'} />{t.text}</div>)}
      </div>

      {resetOpen && <ResetModal onClose={() => setResetOpen(false)} onDone={() => { setResetOpen(false); setSelected(null); notify('ok', 'Working database restored from the original. All tickets are open again.'); refresh() }} notify={notify} />}
      <footer className="foot">Backend: {API_URL} · data from <code>{health?.database ?? '…'}</code> through the MCP server</footer>
    </div>
  )
}

function ResetModal({ onClose, onDone, notify }: { onClose: () => void; onDone: () => void; notify: (k: 'ok' | 'err', t: string) => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function go() {
    setBusy(true); setError(null)
    try { await api.reset(); onDone() }
    catch (e) { const m = e instanceof ApiError ? e.message : String(e); setError(m); notify('err', `Reset failed: ${m}`) }
    finally { setBusy(false) }
  }
  return (
    <Modal title="Reset the working database?" icon="reset" onClose={onClose} busy={busy}>
      <div className="review">
        <p>This replaces <code>campus_customs_new.db</code> with the untouched original. All payments, requests, drafts and ticket changes are undone. The audit trail is kept.</p>
        <label className="field">Type <b>RESET</b> to confirm<input value={text} onChange={e => setText(e.target.value)} autoFocus disabled={busy} /></label>
        {error && <div className="alert bad"><Icon name="alert" />{error}</div>}
        <div className="modal-actions">
          <button className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn danger" onClick={go} disabled={text !== 'RESET' || busy}>{busy ? <><span className="spinner" />Resetting…</> : 'Reset database'}</button>
        </div>
      </div>
    </Modal>
  )
}
