// The only way the dashboard talks to the shop: HTTP calls to the FastAPI backend.
import type { AuditEvent, Cash, Health, PaymentRequest, Run, Ticket } from './types'

export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(API_URL + path, { headers: { 'Content-Type': 'application/json' }, ...init })
  } catch {
    throw new ApiError(0, `Can't reach the backend at ${API_URL}.`)
  }
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = typeof body.detail === 'string' ? body.detail
      : Array.isArray(body.detail) ? body.detail.map((d: any) => d.msg).join('; ') : res.statusText
    throw new ApiError(res.status, detail)
  }
  return body as T
}

export const api = {
  health: () => request<Health>('/health'),
  tickets: () => request<{ today: string; tickets: Ticket[] }>('/tickets'),
  run: (id: number) => request<Run>(`/tickets/${id}/run`, { method: 'POST' }),
  activity: (ticketId: number) => request<{ events: AuditEvent[] }>(`/activity?ticket_id=${ticketId}&limit=500&since_reset=true`),
  payments: () => request<{ payment_requests: PaymentRequest[] }>('/payments'),
  approve: (id: number, approver_name: string, note: string) =>
    request<{ ok: boolean; payment: any; ticket_id: number | null; ticket_status: string | null;
              continuation: { started: boolean; execution_id?: string; reason?: string } | null }>(
      `/payments/${id}/approve`, { method: 'POST', body: JSON.stringify({ approver_name, note }) }),
  reject: (id: number, approver_name: string, note: string) =>
    request<{ ok: boolean }>(`/payments/${id}/reject`, { method: 'POST', body: JSON.stringify({ approver_name, note }) }),
  editMessage: (id: number, body: string, edited_by: string) =>
    request<{ ok: boolean; draft_id: number }>(`/messages/${id}`, { method: 'PUT', body: JSON.stringify({ body, edited_by }) }),
  markSent: (id: number, sent_by: string, customer_reply_needed: boolean, continue_agents: boolean) =>
    request<{ ok: boolean; sent_at: string; ticket_status: string; continuation: { started: boolean } | null }>(
      `/messages/${id}/mark-sent`, { method: 'POST', body: JSON.stringify({ sent_by, customer_reply_needed, continue_agents }) }),
  cash: () => request<Cash>('/cash'),
  reset: () => request<{ ok: boolean }>('/reset', { method: 'POST', body: JSON.stringify({ confirm: 'RESET' }) }),
}
