// Customer messages: review and edit the agent's draft, send it yourself, then "Mark as Sent".
// Nothing is emailed by the system; marking only records that a person sent it.
import { useEffect, useState } from 'react'
import { ApiError, api } from '../api'
import { lastDecision, unsentDraft } from '../domain'
import { Icon } from '../icons'
import type { Draft, Ticket } from '../types'
import { Modal } from './Modal'

const when = (iso: string) => new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })

export function CustomerMessages({ ticket, onDone, notify }: {
  ticket: Ticket; onDone: () => void; notify: (k: 'ok' | 'err', t: string) => void
}) {
  const current = unsentDraft(ticket)
  const sent = ticket.message_drafts.filter(d => d.sent)
  const [text, setText] = useState(current?.body ?? '')
  const [saving, setSaving] = useState(false)
  const [marking, setMarking] = useState<Draft | null>(null)
  const [editor, setEditor] = useState(() => localStorage.getItem('approver') ?? '')
  useEffect(() => { setText(current?.body ?? '') }, [current?.id])

  if (!current && sent.length === 0) {
    if (!['customer_order', 'price_override'].includes(ticket.type)) return null
    return (
      <div className="card messages">
        <div className="card-head"><Icon name="mail" /><h2>Customer messages</h2></div>
        <div className="empty small-left"><Icon name="clock" size={15} />
          {ticket.running ? 'The agents are working. A customer message is drafted once the situation is settled.'
            : ticket.waiting_for_human.length ? 'Customer Service drafts the message after you decide the pending approval, so it reflects the final situation.'
            : 'No customer message yet. It is drafted when the agents settle this ticket.'}</div>
      </div>)
  }
  const dirty = !!current && text.trim() !== current.body.trim()

  async function save() {
    if (!current) return
    setSaving(true); localStorage.setItem('approver', editor.trim())
    try {
      await api.editMessage(current.id, text.trim(), editor.trim())
      notify('ok', 'Your edits were saved as the new draft. Nothing was sent.'); onDone()
    } catch (e) { notify('err', `Could not save edits: ${e instanceof ApiError ? e.message : e}`) }
    finally { setSaving(false) }
  }

  return (
    <div className="card messages">
      <div className="card-head"><Icon name="mail" /><h2>Customer messages</h2>
        <span className="muted">Drafts are never sent by the system</span></div>
      {current && (
        <div className="msg current">
          <div className="msg-head">
            <span className="badge b-approval"><Icon name="mail" size={12} />Draft · not sent</span>
            <span className="muted">To <b>{current.recipient}</b> · by {current.drafted_by} · {when(current.drafted_at)}</span>
          </div>
          <textarea value={text} onChange={e => setText(e.target.value)} rows={6} disabled={saving || ticket.running} aria-label="Customer message draft" />
          <div className="msg-actions">
            <button className="btn ghost" onClick={() => { navigator.clipboard?.writeText(text); notify('ok', 'Message copied. Paste it into your email.') }}>Copy text</button>
            {dirty && <input className="editor" value={editor} onChange={e => setEditor(e.target.value)} placeholder="Your name" aria-label="Edited by" />}
            <button className="btn ghost" onClick={save} disabled={!dirty || saving || editor.trim().length < 2}>{saving ? <><span className="spinner dark" />Saving…</> : 'Save edits'}</button>
            <button className="btn primary" onClick={() => setMarking(current)} disabled={dirty || saving || ticket.running}
                    title={dirty ? 'Save your edits first, so the sent version is recorded' : 'After you have sent this email yourself'}>
              <Icon name="check" size={14} />Mark as Sent</button>
          </div>
          {dirty && <div className="muted">Save your edits before marking the message as sent.</div>}
        </div>
      )}
      {sent.map(d => (
        <div key={d.id} className="msg sent">
          <div className="msg-head">
            <span className="badge b-resolved"><Icon name="check" size={12} />Sent</span>
            <span className="muted">To <b>{d.recipient}</b> · marked by {d.sent_by} · {d.sent_at && when(d.sent_at)}</span>
            {d.customer_reply_needed && <span className="badge b-customer">Waiting for reply</span>}
          </div>
          <p>{d.body}</p>
        </div>
      ))}
      {marking && <MarkSentModal draft={marking} ticket={ticket} onClose={() => setMarking(null)}
        onDone={(m) => { setMarking(null); notify('ok', m); onDone() }} notifyErr={t => notify('err', t)} />}
    </div>
  )
}

function MarkSentModal({ draft, ticket, onClose, onDone, notifyErr }: {
  draft: Draft; ticket: Ticket; onClose: () => void; onDone: (m: string) => void; notifyErr: (t: string) => void
}) {
  const d = lastDecision(ticket)
  const suggestReply = ticket.status === 'blocked' || d?.status === 'missing_info' || !!d?.customer_reply_needed
  const [name, setName] = useState(() => localStorage.getItem('approver') ?? '')
  const [reply, setReply] = useState(suggestReply)
  const [cont, setCont] = useState(true)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function go() {
    setBusy(true); setError(null); localStorage.setItem('approver', name.trim())
    try {
      const r = await api.markSent(draft.id, name.trim(), reply, cont)
      onDone(`Message #${draft.id} marked as sent at ${new Date(r.sent_at).toLocaleTimeString()}. Ticket is now "${r.ticket_status.replace('_', ' ')}".`
             + (r.continuation?.started ? ' Agents are continuing with the remaining work.' : ''))
    } catch (e) {
      const m = e instanceof ApiError ? e.message : String(e); setError(m); notifyErr(`Mark as sent failed: ${m}`)
    } finally { setBusy(false) }
  }
  return (
    <Modal title="Mark message as sent" icon="mail" onClose={onClose} busy={busy}>
      <div className="review">
        <p>Only confirm this <b>after you have sent the email to {draft.recipient} yourself</b>. The system records the date and time once; it does not send anything.</p>
        <div className="msg-preview">{draft.body}</div>
        <label className="field">Your name<input value={name} onChange={e => setName(e.target.value)} disabled={busy} /></label>
        <label className="confirm"><input type="checkbox" checked={reply} onChange={e => setReply(e.target.checked)} disabled={busy} />
          The customer needs to reply (keep the ticket waiting for them)</label>
        <label className="confirm"><input type="checkbox" checked={cont} onChange={e => setCont(e.target.checked)} disabled={busy} />
          Let the agents continue if other work remains</label>
        <label className="confirm"><input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)} disabled={busy} />
          I sent this message to the customer.</label>
        <p className="muted">Marking as sent never resolves the ticket by itself.</p>
        {error && <div className="alert bad"><Icon name="alert" />{error}</div>}
        <div className="modal-actions">
          <button className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary" onClick={go} disabled={busy || !confirm || name.trim().length < 2}>
            {busy ? <><span className="spinner" />Saving…</> : <><Icon name="check" size={14} />Mark as Sent</>}</button>
        </div>
      </div>
    </Modal>
  )
}
