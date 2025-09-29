import { api } from './client'

export type Session = {
  id: string
  title: string
  created_at: number
  updated_at: number
}

export async function listSessions(): Promise<{ items: Session[] }> {
  return api.request('/sessions')
}

export async function createSession(title?: string): Promise<{ id: string; title: string; created_at: number }> {
  return api.request('/sessions', { method: 'POST', body: JSON.stringify({ title }) })
}

export async function deleteSession(id: string): Promise<{ ok: boolean }> {
  return api.request(`/sessions/${id}`, { method: 'DELETE' })
}

export async function closeSession(id: string): Promise<{ ok: boolean; skipped?: string }> {
  return api.request(`/sessions/${id}/close`, { method: 'POST', body: JSON.stringify({ reason: 'pagehide' }) })
}
