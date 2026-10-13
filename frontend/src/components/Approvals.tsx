// Right column: everything that needs a human decision, plus recent payments.
import { useState } from 'react'
import { ApiError, api } from '../api'
import { money, requestIcon, requestLabel, time } from '../domain'
import { Icon } from '../icons'
import type { Cash, PaymentRequest } from '../types'
import { Modal } from './Modal'

export function Approvals({ requests, cash, onDone, notify }: {
  requests: PaymentRequest[]; cash: Cash | null
  onDone: () => void; notify: (kind: 'ok' | 'err', text: string) => void
}) {
  const [open, setOpen] = useState<PaymentRequest | null>(null)
  const pending = requests.filter(r => r.status === 'pending_approval' || r.status === 'approved')
  const history = requests.filter(r => r.status === 'executed' || r.status === 'rejected').slice(-4).reverse()

  return (
    <section className="card approvals">
      <div className="card-head">
        <Icon name="shield" /><h2>Human approvals</h2>
        <span className={`count ${pending.length ? 'hot' : ''}`}>{pending.length}</span>
      </div>
      <p className="explain">Agents only <b>prepare</b> payments and purchases. Nothing is paid until you approve it here.</p>
      {pending.length === 0 && <div className="empty small"><Icon name="checkCircle" />Nothing waiting for you.</div>}
      {pending.map(r => (
        <div key={r.id} className="req">
          <span className="req-ic"><Icon name={requestIcon(r)} /></span>
          <div className="req-body">
            <div className="req-top"><b>{requestLabel(r)}</b><span className="amt">{money(r.amount)}</span></div>
            <div className="req-reason">{r.reason}</div>
            <div className="req-meta">Ticket {r.ticket_id ?? '—'} · prepared by {r.requested_by} · {time(r.requested_at)}</div>
          </div>
          <button className="btn primary" onClick={() => setOpen(r)}>Review</button>
        </div>
      ))}
      {history.length > 0 && <>
        <div className="sub">Recent decisions</div>
        {history.map(r => (
          <div key={r.id} className={`hist ${r.status}`}>
            <Icon name={r.status === 'executed' ? 'check' : 'x'} size={14} />
            <span>{requestLabel(r)} · {money(r.amount)}</span>
            <span className="muted">{r.status === 'executed' ? `paid · ${r.approved_by}` : `rejected · ${r.approved_by}`}</span>
          </div>
        ))}
      </>}
      {open && <ReviewModal req={open} current={requests.find(r => r.id === open.id) ?? open} cash={cash} onClose={() => setOpen(null)}
                            onDone={(msg) => { setOpen(null); notify('ok', msg); onDone() }} notifyErr={t => notify('err', t)} />}
    </section>
  )
}

function ReviewModal({ req, current, cash, onClose, onDone, notifyErr }: {
  req: PaymentRequest; current: PaymentRequest; cash: Cash | null; onClose: () => void; onDone: (msg: string) => void; notifyErr: (t: string) => void
}) {
  const [name, setName] = useState(() => localStorage.getItem('approver') ?? '')
  const [note, setNote] = useState('')
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const balance = cash?.checking_balance ?? 0
  const after = balance - req.amount
  // The request may change while the dialog is open (e.g. someone else approved it).
  const handled = current.status === 'executed' || current.status === 'rejected'
  const negative = !handled && after < 0
  const canApprove = name.trim().length >= 2 && checked && !negative && !handled && !busy

  async function act(kind: 'approve' | 'reject') {
    setBusy(kind); setError(null)
    localStorage.setItem('approver', name.trim())
    try {
      if (kind === 'approve') {
        const res = await api.approve(req.id, name.trim(), note)
        const cont = res.continuation?.started ? ` Agents are continuing on ticket ${res.ticket_id}.`
          : (res.continuation as any)?.queued ? ` Agents will continue on ticket ${res.ticket_id} as soon as the current run finishes.` : ''
        onDone(`Paid ${money(res.payment.amount)} for ${requestLabel(req)}. Checking is now ${money(res.payment.checking_balance_after)}.${cont}`)
      } else {
        await api.reject(req.id, name.trim(), note)
        onDone(`Rejected ${requestLabel(req)}. No money moved.`)
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e)
      setError(msg); notifyErr(`${kind === 'approve' ? 'Approval' : 'Rejection'} failed: ${msg}`)
    } finally { setBusy(null) }
  }

  return (
    <Modal title="Review payment request" icon={requestIcon(req)} onClose={onClose} busy={!!busy}>
      <div className="review">
        <div className="review-amt"><span>{requestLabel(req)}</span><b>{money(req.amount)}</b></div>
        <dl>
          <dt>Reason</dt><dd>{req.reason}</dd>
          <dt>Ticket</dt><dd>{req.ticket_id ?? 'not linked'}</dd>
          <dt>Prepared by</dt><dd>{req.requested_by} (agent) · {time(req.requested_at)}</dd>
        </dl>
        <div className={`impact ${negative ? 'neg' : ''}`}>
          <div><span>Checking now</span><b>{money(balance)}</b></div>
          <Icon name="arrow" />
          <div><span>After payment</span><b>{money(after)}</b></div>
        </div>
        {handled && <div className="alert warn"><Icon name="alert" />Already handled: {current.status === 'executed' ? `paid (approved by ${current.approved_by})` : `rejected by ${current.approved_by}`}. Nothing more to do.</div>}
        {negative && <div className="alert bad"><Icon name="alert" />This would make checking negative, so it can't be approved.</div>}
        <label className="field">Your name (recorded as approver)
          <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Isabel" disabled={!!busy} />
        </label>
        <label className="field">Note (optional)
          <input value={note} onChange={e => setNote(e.target.value)} disabled={!!busy} />
        </label>
        <label className="confirm"><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} disabled={!!busy} />
          I confirm this payment of {money(req.amount)} should be made now.</label>
        {error && <div className="alert bad"><Icon name="alert" />{error}</div>}
        <div className="modal-actions">
          <button className="btn ghost" onClick={() => act('reject')} disabled={!!busy || handled || name.trim().length < 2}>
            {busy === 'reject' ? <><span className="spinner dark" />Rejecting…</> : 'Reject'}</button>
          <button className="btn primary" onClick={() => act('approve')} disabled={!canApprove}>
            {busy === 'approve' ? <><span className="spinner" />Paying…</> : <><Icon name="check" size={14} />Approve & pay</>}</button>
        </div>
      </div>
    </Modal>
  )
}
