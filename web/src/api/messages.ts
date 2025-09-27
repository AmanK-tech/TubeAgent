import { api } from './client'

export type Message = { id: string; role: 'user' | 'assistant' | 'system' | 'tool'; content: string; created_at: number }

export async function listMessages(sessionId: string): Promise<{ items: Message[]; next_cursor?: number | null }> {
  return api.request(`/sessions/${sessionId}/messages`)
}

export async function sendMessage(
  sessionId: string,
  payload: { role: 'user' | 'system'; content: string; user_req?: string }
): Promise<{ message_id: string }> {
  return api.request(`/sessions/${sessionId}/messages`, { method: 'POST', body: JSON.stringify(payload) })
}
