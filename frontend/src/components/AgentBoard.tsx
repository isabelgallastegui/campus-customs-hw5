// The five agents, always in the same order. Color = taking part in this run.
import { AGENTS } from '../domain'
import type { AgentLive } from '../domain'
import { Icon } from '../icons'

const STATE_LABEL = { idle: 'Not involved', working: 'Working', done: 'Done', failed: 'Stopped' }

export function AgentBoard({ live }: { live: Record<string, AgentLive> }) {
  return (
    <div className="agents">
      {AGENTS.map(a => {
        const s = live[a.id]
        const on = s.state !== 'idle'
        return (
          <div key={a.id} className={`agent ${on ? 'on' : ''} ${s.state}`} style={{ ['--c' as any]: `var(--${a.id})` }}>
            <div className="agent-top">
              <span className="av"><Icon name={a.id} size={19} /></span>
              <div>
                <div className="nm">{a.name}</div>
                <div className="rl">{a.role}</div>
              </div>
              <span className={`st ${s.state}`}>{s.state === 'working' && <i className="spin-dot" />}{STATE_LABEL[s.state]}</span>
            </div>
            <div className="doing">{on ? (s.summary && s.state === 'done' ? s.summary : s.doing) : 'Not part of this run.'}</div>
            <div className="agent-foot">
              {s.tools.length > 0 ? s.tools.map(t => <code key={t}>{t}</code>) : <span className="muted">{on ? 'No MCP tools yet' : ''}</span>}
              {on && (s.sent > 0 || s.received > 0) && <span className="msgs" title="Delegations sent / received">↗ {s.sent} · ↙ {s.received}</span>}
            </div>
          </div>
        )
      })}
    </div>
  )
}
