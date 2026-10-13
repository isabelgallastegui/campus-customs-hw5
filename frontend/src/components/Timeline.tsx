// Live, auto-scrolling timeline of everything the agents (and you) did on this ticket.
import { useEffect, useRef, useState } from 'react'
import { time, toTimeline } from '../domain'
import { Icon } from '../icons'
import type { AuditEvent } from '../types'

export function Timeline({ events, running }: { events: AuditEvent[]; running: boolean }) {
  const [turns, setTurns] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const items = toTimeline(events, turns)
  const stick = useRef(true)

  useEffect(() => {
    const el = box.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [items.length])

  let lastExec: string | null | undefined
  return (
    <div className="timeline-wrap">
      <div className="tl-tools">
        {running && <span className="live"><i />Live</span>}
        <span className="muted">{items.length} events</span>
        <label className="toggle"><input type="checkbox" checked={turns} onChange={e => setTurns(e.target.checked)} />Show model turns</label>
      </div>
      <div className="timeline" ref={box} onScroll={e => {
        const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40 }}>
        {items.length === 0 && <div className="empty">No activity yet. Run the agents to see each step here.</div>}
        {items.map(it => {
          const header = it.executionId && it.executionId !== lastExec && it.title === 'Run started'
          if (it.executionId) lastExec = it.executionId
          const color = ['boss', 'inventory', 'accounting', 'facilities', 'customer_service'].includes(it.agent) ? `var(--${it.agent})` : undefined
          return (
            <div key={it.id}>
              {header && <div className="tl-run">Run {it.executionId}</div>}
              <div className={`tl-item ${it.tone}`}>
                <span className="tl-dot" style={color && ['agent', 'delegate', 'tool', 'result', 'good'].includes(it.tone) ? { background: color, borderColor: color } : undefined}>
                  <Icon name={it.icon} size={13} />
                </span>
                <div className="tl-body">
                  <div className="tl-title">{it.title}</div>
                  {it.text && <div className="tl-text">{it.text}</div>}
                </div>
                <span className="tl-time">{time(it.ts)}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
