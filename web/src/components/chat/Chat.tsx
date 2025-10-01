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
  const bottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [data, streamText])

  useEffect(() => {
    if (!sessionId) return
    const base = import.meta.env.VITE_API_URL || window.location.origin
    const ws = new WebSocket(`${base.replace('http', 'ws')}/ws/chat/${sessionId}`)

    ws.onopen = () => setWsError('')
    ws.onerror = () => {
      setWsError('WebSocket connection error')
      onError?.('WebSocket connection error')
    }
    ws.onclose = () => {
      // only show a soft hint; auto-reconnect handled by remount
      // keep last known streamText as-is
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
    return () => ws.close()
  }, [sessionId])

  return (
    <div className="flex-1 overflow-y-auto px-6 py-6 relative">
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
