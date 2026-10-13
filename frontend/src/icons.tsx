// Line icons (same set as the Problem 6 Desk Tickets page). No network or icon library needed.
const PATHS: Record<string, string> = {
  boss: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18M11 13v2h2v-2"/>',
  inventory: '<path d="M21 8 12 3 3 8l9 5 9-5z"/><path d="M3 8v8l9 5 9-5V8M12 13v8"/>',
  accounting: '<rect x="5" y="2.5" width="14" height="19" rx="2"/><path d="M8 6.5h8M8 11h.01M12 11h.01M16 11h.01M8 14.5h.01M12 14.5h.01M16 14.5h.01M8 18h8"/>',
  facilities: '<path d="M3 21h18M5 21V8l7-4.5L19 8v13M9.5 21v-5h5v5M9 10.5h.01M15 10.5h.01"/>',
  customer_service: '<path d="M21 14.5a2 2 0 0 1-2 2H8l-5 4.5V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M8 8.5h8M8 12h5"/>',
  system: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
  human: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/>',
  tool: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  back: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  play: '<path d="M7 4.5v15l12-7.5z"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  checkCircle: '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.7 2.7L16 9.5"/>',
  alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
  stop: '<circle cx="12" cy="12" r="9"/><path d="M9 9h6v6H9z"/>',
  shield: '<path d="M12 3 20 6v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="m9 12 2.2 2.2L15.5 10"/>',
  wallet: '<path d="M4 7a2 2 0 0 1 2-2h12v3.5"/><path d="M4 7v11a2 2 0 0 0 2 2h14V8.5H6A2 2 0 0 1 4 7z"/><circle cx="16" cy="14.3" r="1.2"/>',
  reset: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  ticket: '<path d="M3 8a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-2a2 2 0 0 0 0-4z"/><path d="M14 6v2M14 11v2M14 16v2"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  question: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17h.01"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  receipt: '<path d="M5.5 3h13v18l-2.6-1.7-2.2 1.7-2.2-1.7-2.2 1.7-2.2-1.7L5.5 21z"/><path d="M9 8h6M9 11.5h6M9 15h3.5"/>',
  key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3 20.5 2.5M17 6l3 3M14.3 8.7l2 2"/>',
  cart: '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.6 12.2a2 2 0 0 0 2 1.6h8.6a2 2 0 0 0 2-1.6L22 7H6"/>',
  logo: '<path d="M20.4 6.6 12 2 3.6 6.6v10.8L12 22l8.4-4.6z"/><path d="M8 9.5c1-1.5 2.4-2 4-2 2.5 0 4 1.6 4 1.6M8 14.5s1.5 2 4 2c1.6 0 3-.5 4-2"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  flag: '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
  list: '<path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
}

export function Icon({ name, size = 18, className = '' }: { name: string; size?: number; className?: string }) {
  return (
    <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none"
         strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
         dangerouslySetInnerHTML={{ __html: PATHS[name] ?? PATHS.tool }} />
  )
}

export const ICON_PATHS = PATHS
