// Checking balance, always visible. Flashes when it changes (e.g. after an approved payment).
import { useEffect, useRef, useState } from 'react'
import { money } from '../domain'
import { Icon } from '../icons'
import type { Cash } from '../types'

export function CashCard({ cash }: { cash: Cash | null }) {
  const prev = useRef<number | null>(null)
  const [delta, setDelta] = useState<number | null>(null)
  useEffect(() => {
    if (!cash) return
    if (prev.current !== null && prev.current !== cash.checking_balance) {
      setDelta(cash.checking_balance - prev.current)
      const t = setTimeout(() => setDelta(null), 6000)
      prev.current = cash.checking_balance
      return () => clearTimeout(t)
    }
    prev.current = cash.checking_balance
  }, [cash?.checking_balance])

  if (!cash) return <div className="kpi cash skeleton" />
  const base = Math.max(cash.checking_balance, 1)
  const committed = Math.min(cash.committed_to_requests, base)
  return (
    <div className={`kpi cash ${delta ? 'flash' : ''}`}>
      <div className="kpi-label"><Icon name="wallet" size={15} />Checking balance</div>
      <div className="kpi-value">{money(cash.checking_balance)}
        {delta !== null && <span className={`delta ${delta < 0 ? 'down' : 'up'}`}>{delta < 0 ? '−' : '+'}{money(Math.abs(delta))}</span>}
      </div>
      <div className="meter" title="Share of checking already promised to requests">
        <span style={{ width: `${(committed / base) * 100}%` }} />
      </div>
      <div className="kpi-sub">
        <span>{money(cash.uncommitted_cash)} free</span>
        <span>{money(cash.committed_to_requests)} waiting approval</span>
      </div>
    </div>
  )
}
