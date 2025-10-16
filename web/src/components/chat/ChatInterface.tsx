import React, { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listMessages } from '../../api/messages'
import { API_BASE } from '../../api/client'
import { MessageItem } from './MessageItem'
import { TaskBar } from './TaskBar'

interface Props {
  sessionId: string
  isSending: boolean
  onError?: (message: string) => void
  onMessageComplete?: () => void
}

type WSMessage = {
  type: 'connected' | 'token' | 'message_complete' | 'error' | string;
  text?: string;
  message?: string
}

export const ChatInterface: React.FC<Props> = ({ sessionId, isSending, onError, onMessageComplete }) => {
  const bottomRef = useRef<HTMLDivElement>(null)
  const [streamText, setStreamText] = useState('')
  const [wsError, setWsError] = useState('')
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'connected' | 'disconnected'>('disconnected')

  // Fetch messages
  const { data: messagesData, refetch: refetchMessages } = useQuery({
    queryKey: ['messages', sessionId],
    queryFn: () => listMessages(sessionId),
    enabled: !!sessionId,
    refetchInterval: 2000, // Refresh every 2 seconds
  })

  const messages = messagesData?.items || []

  // Auto scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamText])

  // WebSocket connection
  useEffect(() => {
    if (!sessionId) return

    const apiBase = API_BASE as string
    let wsUrl: string

    if (/^https?:\/\//i.test(apiBase)) {
      const u = new URL(apiBase)
      const proto = u.protocol === 'https:' ? 'wss:' : 'ws:'
      wsUrl = `${proto}//${u.host}/ws/chat/${sessionId}`
    } else {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      wsUrl = `${proto}//${window.location.host}/ws/chat/${sessionId}`
    }

    const ws = new WebSocket(wsUrl)
    setConnectionStatus('connecting')

    // Connection timeout
    const timeoutId = setTimeout(() => {
      if (ws.readyState === WebSocket.CONNECTING) {
        ws.close()
        setConnectionStatus('disconnected')
        setWsError('Connection timeout')
      }
    }, 8000)

    ws.onopen = () => {
      clearTimeout(timeoutId)
      setConnectionStatus('connected')
      setWsError('')
      // Send ping to keep connection alive
      try {
        ws.send(JSON.stringify({ type: 'ping' }))
      } catch {}
    }

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data)

        if (msg.type === 'connected') {
          setConnectionStatus('connected')
          setWsError('')
        } else if (msg.type === 'token' && msg.text) {
          setStreamText(prev => prev + msg.text)
        } else if (msg.type === 'message_complete') {
          setStreamText('')
          refetchMessages()
          setWsError('')
          onMessageComplete?.()
        } else if (msg.type === 'error') {
          setStreamText('')
          const errorMsg = msg.message || 'An error occurred while generating the response.'
          setWsError(errorMsg)
          onError?.(errorMsg)
          refetchMessages()
        }
      } catch (e) {
        // Ignore non-JSON messages
      }
    }

    ws.onerror = () => {
      setConnectionStatus('disconnected')
      setWsError('Connection error')
    }

    ws.onclose = () => {
      setConnectionStatus('disconnected')
      setWsError('Connection lost')
    }

    // Keep alive ping
    const pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type: 'ping' }))
        } catch {}
      }
    }, 15000)

    return () => {
      clearTimeout(timeoutId)
      clearInterval(pingInterval)
      ws.close()
    }
  }, [sessionId, onError, refetchMessages])

  return (
    <div className="flex flex-col flex-1 relative z-10">
      {/* Connection status indicator */}
      {connectionStatus === 'connecting' && (
        <div className="absolute top-4 left-1/2 transform -translate-x-1/2 z-20">
          <div className="bg-blue-500/10 border border-blue-500/40 text-blue-300 rounded-md px-3 py-2 text-sm flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
            Connecting...
          </div>
        </div>
      )}

      {wsError && (
        <div className="absolute top-4 left-1/2 transform -translate-x-1/2 z-20">
          <div className="bg-red-500/10 border border-red-500/40 text-red-300 rounded-md px-3 py-2 text-sm max-w-md">
            {wsError}
          </div>
        </div>
      )}

      {/* Messages container */}
      <div className="flex-1 overflow-y-auto px-6 py-6 min-h-[200px]">
        <div className="relative z-10 mx-auto max-w-3xl text-[16px] sm:text-[17px] leading-[1.75]">
          {/* Messages */}
          {messages.map((message: any, index: number) => (
            <div key={message.id}>
              <MessageItem
                role={message.role as any}
                content={message.content}
              />
              {/* Loading animation below the last user message when sending */}
              {message.role === 'user' &&
               index === messages.length - 1 &&
               isSending &&
               !streamText && (
                <div className="my-4 pl-1">
                  {/* Enhanced loading indicator */}
                  <div className="flex items-center gap-2 mb-3">
                    <div className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                      <span className="w-2 h-2 rounded-full bg-purple-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                      <span className="w-2 h-2 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '300ms' }} />
                    </div>
                    <span className="text-sm text-neutral-400 animate-pulse">Processing your request...</span>
                  </div>
                </div>
              )}
            </div>
          ))}

          {/* If no user messages yet, show loading at the bottom */}
          {isSending && !streamText && messages.length === 0 && (
            <div className="my-4 pl-1">
              {/* Enhanced loading indicator */}
              <div className="flex items-center gap-2 mb-3">
                <div className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                  <span className="w-2 h-2 rounded-full bg-purple-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                  <span className="w-2 h-2 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
                <span className="text-sm text-neutral-400 animate-pulse">Processing your request...</span>
              </div>
            </div>
          )}

          {/* Streaming text */}
          {streamText && (
            <MessageItem role="assistant" content={streamText} />
          )}

          {/* Simple typing indicator when only streaming (no task bar) */}
          {isSending && streamText && (
            <div className="my-2 pl-1">
              <div className="flex items-center gap-1 text-neutral-400">
                <span className="w-2 h-2 rounded-full bg-neutral-400 animate-pulse" />
                <span className="w-2 h-2 rounded-full bg-neutral-400 animate-pulse" style={{ animationDelay: '150ms' }} />
                <span className="w-2 h-2 rounded-full bg-neutral-400 animate-pulse" style={{ animationDelay: '300ms' }} />
              </div>
            </div>
          )}

          {/* Scroll anchor */}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  )
}