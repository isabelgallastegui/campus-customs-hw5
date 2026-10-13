import type { ReactNode } from 'react'
import { Icon } from '../icons'

export function Modal({ title, icon, children, onClose, busy }: {
  title: string; icon: string; children: ReactNode; onClose: () => void; busy?: boolean
}) {
  return (
    <div className="overlay" onClick={() => !busy && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} onClick={e => e.stopPropagation()}>
        <div className="modal-head">
          <span className="mi"><Icon name={icon} size={18} /></span>
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} disabled={busy} aria-label="Close"><Icon name="x" size={16} /></button>
        </div>
        {children}
      </div>
    </div>
  )
}
