// Prefer same-origin proxy in dev; override via VITE_API_URL when needed
// Using '/api' plays nicely with Vite's dev proxy and production reverse proxies
export const API_BASE = import.meta.env.VITE_API_URL || '/api'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const base = API_BASE.endsWith('/') ? API_BASE.slice(0, -1) : API_BASE
  const path = url.startsWith('/') ? url : `/${url}`
  const res = await fetch(`${base}${path}` as string, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
    ...init,
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as T
}

export const api = { request }
