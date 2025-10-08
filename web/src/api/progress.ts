import { api } from './client'

export type ProgressStep = {
  name: string
  status: 'running' | 'done' | 'error' | 'pending'
  started_at?: number | null
  ended_at?: number | null
  note?: string | null
}

export type Progress = {
  steps: ProgressStep[]
  updated_at?: number
}

export async function getProgress(sessionId: string): Promise<Progress> {
  return api.request(`/sessions/${sessionId}/progress`)
}

