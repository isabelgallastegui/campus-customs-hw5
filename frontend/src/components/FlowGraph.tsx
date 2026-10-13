// Who talks to whom, drawn like the Problem 6 page: fixed agent positions, numbered arrows in order.
import { AGENTS } from '../domain'
import type { AgentLive } from '../domain'
import { ICON_PATHS } from '../icons'
import type { AuditEvent } from '../types'

const POS: Record<string, [number, number]> = {
  boss: [260, 54], inventory: [112, 146], customer_service: [408, 146], accounting: [150, 282], facilities: [370, 282],
}

interface Edge { from: string; to: string; back: boolean }

function edges(events: AuditEvent[]): Edge[] {
  const out: Edge[] = []
  for (const e of events) {
    if (e.action === 'delegation' && e.status === 'started' && e.target && POS[e.agent] && POS[e.target])
      out.push({ from: e.agent, to: e.target, back: false })
    if (e.action === 'delegation_result' && e.target && POS[e.agent] && POS[e.target])
      out.push({ from: e.target, to: e.agent, back: true })
  }
  return out
}

function geometry(a: string, b: string, k: number) {
  const [x1, y1] = POS[a], [x2, y2] = POS[b]
  const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy)
  const ux = dx / len, uy = dy / len, nx = -uy, ny = ux, r = 33, bend = 24 + k * 10
  const sx = x1 + ux * r + nx * 8, sy = y1 + uy * r + ny * 8
  const ex = x2 - ux * (r + 4) + nx * 8, ey = y2 - uy * (r + 4) + ny * 8
  const cx = (x1 + x2) / 2 + nx * bend, cy = (y1 + y2) / 2 + ny * bend
  return { d: `M${sx},${sy} Q${cx},${cy} ${ex},${ey}`, mx: 0.25 * sx + 0.5 * cx + 0.25 * ex, my: 0.25 * sy + 0.5 * cy + 0.25 * ey }
}

export function FlowGraph({ events, live }: { events: AuditEvent[]; live: Record<string, AgentLive> }) {
  const list = edges(events)
  const seen: Record<string, number> = {}
  const involved = new Set(list.flatMap(e => [e.from, e.to]))
  events.forEach(e => { if (e.action === 'agent_run_started') involved.add(e.agent) })
  return (
    <svg className="flow" viewBox="0 0 520 336" role="img" aria-label="Agent communication flow">
      <defs>
        {AGENTS.map(a => (
          <marker key={a.id} id={`fa-${a.id}`} viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill={`var(--${a.id})`} />
          </marker>
        ))}
      </defs>
      {list.map((e, i) => {
        const key = `${e.from}>${e.to}`
        const k = (seen[key] = (seen[key] ?? -1) + 1)
        const g = geometry(e.from, e.to, k)
        return (
          <g key={i} className="edge-g">
            <path className={`edge ${e.back ? 'back' : ''}`} d={g.d} stroke={`var(--${e.from})`} markerEnd={`url(#fa-${e.from})`} />
            <g transform={`translate(${g.mx},${g.my})`}>
              <circle r="10" fill={`var(--${e.from})`} stroke="#fff" strokeWidth="2" />
              <text textAnchor="middle" dy="3.8" className="edge-n">{i + 1}</text>
            </g>
          </g>
        )
      })}
      {AGENTS.map(a => {
        const [x, y] = POS[a.id]
        const on = involved.has(a.id)
        const working = live[a.id]?.state === 'working'
        const label = a.id === 'boss' ? { y: -38, anchor: 'middle' as const, x: 0 }
          : a.id === 'inventory' ? { x: -36, y: 4, anchor: 'end' as const } : { x: 0, y: 47, anchor: 'middle' as const }
        return (
          <g key={a.id} className={`node ${on ? 'on' : ''} ${working ? 'working' : ''}`} transform={`translate(${x},${y})`}>
            {working && <circle className="pulse" r="27" fill={`var(--${a.id})`} />}
            <circle className="ring" r="27" style={on ? { fill: `var(--${a.id})` } : undefined} />
            <g transform="translate(-11,-11) scale(.92)" className="nicon" dangerouslySetInnerHTML={{ __html: ICON_PATHS[a.id] }} />
            <text x={label.x} y={label.y} textAnchor={label.anchor}>{a.name}</text>
          </g>
        )
      })}
      {list.length === 0 && <text className="flow-empty" x="260" y="226" textAnchor="middle">Arrows appear as agents delegate</text>}
    </svg>
  )
}
