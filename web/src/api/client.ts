export const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    headers: {
      'Content-Type': 'application/json',
      // Hint for ngrok to suppress the browser warning interstitial
      'ngrok-skip-browser-warning': 'true',
      ...(init?.headers || {}),
    },
    ...init,
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as T
}

export const api = { request }
