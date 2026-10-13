// Left column: the three tickets, their status, and the Run button.
import { money, ticketStatus } from '../domain'
import { Icon } from '../icons'
import type { Ticket } from '../types'

const ITEM = (t: Ticket) => t.type === 'rent_notice' ? `Lease #${t.lease_id}` : `${t.qty} × ${t.sku} · ${t.size}`
const TYPE_ICON: Record<string, string> = { customer_order: 'cart', rent_notice: 'key', price_override: 'receipt' }

export function TicketQueue({ tickets, selected, onSelect, onRun, starting }: {
  tickets: Ticket[]; selected: number | null; onSelect: (id: number) => void; onRun: (id: number) => void; starting: number | null
}) {
  return (
    <div className="queue">
      {tickets.map(t => {
        const s = ticketStatus(t)
        const waiting = t.waiting_for_human.reduce((n, r) => n + r.amount, 0)
        const busy = t.running || starting === t.id
        return (
          <article key={t.id} className={`tcard ${selected === t.id ? 'sel' : ''} s-${s.key}`} onClick={() => onSelect(t.id)}
                   tabIndex={0} onKeyDown={e => e.key === 'Enter' && onSelect(t.id)}>
            <div className="tcard-head">
              <span className="tnum"><Icon name={TYPE_ICON[t.type] ?? 'ticket'} size={15} />{t.id}</span>
              <span className={`badge b-${s.key}`}><Icon name={s.icon} size={12} />{s.label}</span>
            </div>
            <div className="tcard-title">{t.subject}</div>
            <div className="tcard-meta">{t.requester} · {ITEM(t)}</div>
            <div className="tcard-hint">{s.hint}{waiting > 0 && <b> {money(waiting)}</b>}</div>
            {t.message_drafts.some(d => d.status === 'draft' && !d.sent) && <div className="tcard-msg"><Icon name="mail" size={12} />Message to review & send</div>}
            <button className="btn run" disabled={busy || t.status === 'resolved'}
                    onClick={e => { e.stopPropagation(); onRun(t.id) }}
                    title={t.status === 'resolved' ? 'Already resolved' : t.running ? 'Already running' : 'Run the agent team'}>
              {busy ? <><span className="spinner" />Running…</> : t.status === 'resolved' ? <><Icon name="check" size={14} />Done</>
                : <><Icon name="play" size={13} />{t.latest_execution_id ? 'Run again' : 'Run agents'}</>}
            </button>
          </article>
        )
      })}
    </div>
  )
}
