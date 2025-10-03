import React, { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listMessages } from '../../api/messages'
import { MessageItem } from './MessageItem'

type WSMessage = { type: 'connected' | 'token' | 'message_complete' | 'error' | string; text?: string; message?: string }

export const Chat: React.FC<{ sessionId?: string; onMessageComplete?: () => void; onError?: (msg?: string) => void; loading?: boolean }> = ({ sessionId, onMessageComplete, onError, loading }) => {
  const { data, refetch } = useQuery({
    queryKey: ['messages', sessionId],
    queryFn: () => listMessages(sessionId!),
    enabled: !!sessionId,
  })
  const [streamText, setStreamText] = useState('')
  const [wsError, setWsError] = useState<string>('')
  const [stickyError, setStickyError] = useState<string>('')
  const bottomRef = useRef<HTMLDivElement | null>(null)
  // Soft auto-reconnect nonce; bumping this re-runs the WS effect
  const [wsNonce, setWsNonce] = useState(0)
  const mountedRef = useRef<boolean>(true)
  const [reconnectAttempts, setReconnectAttempts] = useState(0)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [data, streamText])

  // Clear any previous error when a new request starts
  useEffect(() => {
    if (loading) setWsError('')
  }, [loading])

  useEffect(() => {
    if (!sessionId) return
    const base = import.meta.env.VITE_API_URL || window.location.origin
    const ws = new WebSocket(`${base.replace('http', 'ws')}/ws/chat/${sessionId}`)

    // Keepalive ping every ~15s to avoid idle timeouts behind proxies (Render free/edge)
    let pingTimer: number | undefined
    const startKeepAlive = () => {
      // window.setInterval returns number in browsers
      pingTimer = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          try { ws.send(JSON.stringify({ type: 'ping' })) } catch {}
        }
      }, 15000) as unknown as number
    }

    // Optional gentle reconnect timer
    let reconnectTimer: number | undefined

    ws.onopen = () => {
      setWsError('')
      setReconnectAttempts(0)
      startKeepAlive()
      // Send an immediate ping to keep upstream proxies from idling us out
      try { ws.send(JSON.stringify({ type: 'ping' })) } catch {}
    }
    ws.onerror = () => {
      const msg = 'WebSocket connection error'
      // Show a transient inline message and a sticky, dismissible banner
      setWsError(msg)
      setStickyError((prev) => prev || msg)
      setStreamText('')
      onError?.(msg)
      // Try to refresh the REST history in case server finished the message
      refetch()
    }
    ws.onclose = (ev: CloseEvent) => {
      if (pingTimer) window.clearInterval(pingTimer)
      setStreamText('')
      // Only surface a user-visible notice after a couple failures; otherwise silently reconnect
      setReconnectAttempts((prev) => {
        const next = prev + 1
        if (next >= 2) {
          const detail = `Connection closed (code ${ev.code}${ev.reason ? `, reason: ${ev.reason}` : ''}). Reconnecting…`
          setWsError(detail)
          setStickyError((p) => p || detail)
        }
        return next
      })
      // Pull latest messages so a completed assistant reply shows up
      refetch()
      // Soft auto-reconnect with small backoff if still mounted and session unchanged
      if (mountedRef.current && sessionId) {
        const attempt = reconnectAttempts + 1
        const delay = Math.min(15000, 1500 * Math.pow(2, Math.max(0, attempt - 1)))
        reconnectTimer = window.setTimeout(() => setWsNonce((n) => n + 1), delay) as unknown as number
      }
    }

    ws.onmessage = (evt) => {
      try {
        const msg: WSMessage = JSON.parse(evt.data)
        if (msg.type === 'token' && msg.text) {
          setStreamText((s) => s + msg.text)
        } else if (msg.type === 'message_complete') {
          setStreamText('')
          refetch()
          setWsError('')
          onMessageComplete?.()
        } else if (msg.type === 'error') {
          setStreamText('')
          setWsError(msg.message || 'An error occurred while generating the response.')
          onError?.(msg.message)
          // refresh to at least show the user message that was posted
          refetch()
        }
      } catch (e) {
        // ignore non-JSON WS frames
      }
    }
    return () => {
      try { ws.close() } catch {}
      if (pingTimer) window.clearInterval(pingTimer)
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
    }
  }, [sessionId, wsNonce])

  return (
    <div className="flex-1 overflow-y-auto px-6 py-6 relative">
      {/* Sticky error banner (dismissible) */}
      {stickyError && (
        <div className="absolute left-0 right-0 top-2 z-20 flex justify-center px-4">
          <div className="max-w-3xl w-full bg-red-500/10 border border-red-500/40 text-red-300 rounded-md px-3 py-2 text-sm flex items-start gap-3">
            <span className="mt-[2px]">{stickyError}</span>
            <button
              onClick={() => setStickyError('')}
              className="ml-auto text-red-300/80 hover:text-red-200"
              aria-label="Dismiss error"
            >
              ×
            </button>
          </div>
        </div>
      )}
      <div className="relative z-10 mx-auto max-w-3xl text-[16px] sm:text-[17px] leading-[1.75]">
        {data?.items?.map((m) => (
          <MessageItem key={m.id} role={m.role as any} content={m.content} />
        ))}
        {/* Typing / generating indicator: shown while waiting for assistant */}
        {(loading && !streamText) && (
          <div className="my-2 pl-1">
            <div className="flex items-center gap-1 text-neutral-400">
              <span className="w-2 h-2 rounded-full bg-neutral-400 animate-pulse" />
              <span className="w-2 h-2 rounded-full bg-neutral-400 animate-pulse [animation-delay:150ms]" />
              <span className="w-2 h-2 rounded-full bg-neutral-400 animate-pulse [animation-delay:300ms]" />
            </div>
          </div>
        )}
        {wsError && <MessageItem role="system" content={`Error: ${wsError}`} />}
        {streamText && <MessageItem role="assistant" content={streamText} />}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
