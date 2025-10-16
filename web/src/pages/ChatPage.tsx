import React, { useState, useEffect, useCallback } from 'react'
import { getStorage } from '../utils/storage'
import { listSessions, createSession, closeSession } from '../api/sessions'
import { listMessages, sendMessage } from '../api/messages'
import { LandingView } from '../components/chat/LandingView'
import { ChatInterface } from '../components/chat/ChatInterface'
import ExpandableInput from '../components/ui/ExpandableInput'
import { TaskBar } from '../components/chat/TaskBar'

type ViewState = 'landing' | 'chat'

export const ChatPage: React.FC = () => {
  // Simple, clean state management
  const [viewState, setViewState] = useState<ViewState>('landing')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const ACTIVE_KEY = 'tubeagent.activeSessionId'
  const storage = getStorage()

  // Initialize session on mount
  useEffect(() => {
    initializeSession()
  }, [])

  const initializeSession = async () => {
    setIsLoading(true)
    setError(null)

    try {
      // Get existing sessions
      const sessions = await listSessions()
      const storedSessionId = storage.getItem(ACTIVE_KEY)

      // Determine which session to use
      let selectedSessionId: string
      if (storedSessionId && sessions.items.find(s => s.id === storedSessionId)) {
        selectedSessionId = storedSessionId
      } else if (sessions.items.length > 0) {
        selectedSessionId = sessions.items[0].id
      } else {
        const newSession = await createSession()
        selectedSessionId = newSession.id
      }

      setSessionId(selectedSessionId)

      // Check if session has messages to determine view
      const messages = await listMessages(selectedSessionId)
      const hasMessages = messages.items && messages.items.length > 0

      if (hasMessages) {
        setViewState('chat')
      } else {
        setViewState('landing')
      }

    } catch (err) {
      console.error('Failed to initialize session:', err)
      setError('Failed to connect to backend. Please refresh the page.')

      // Try to create a new session as fallback
      try {
        const newSession = await createSession()
        setSessionId(newSession.id)
        setViewState('landing')
      } catch (fallbackErr) {
        console.error('Fallback session creation failed:', fallbackErr)
        setError('Unable to connect to the service. Please try again later.')
      }
    } finally {
      setIsLoading(false)
    }
  }

  const handleSendMessage = async (message: string) => {
    if (!sessionId || isSending) return

    setIsSending(true)
    setError(null)

    try {
      // Switch to chat view immediately
      setViewState('chat')

      // Send the message
      await sendMessage(sessionId, {
        role: 'user',
        content: message,
        user_req: message
      })

      // Store session ID
      storage.setItem(ACTIVE_KEY, sessionId)

    } catch (err) {
      console.error('Failed to send message:', err)
      setError('Failed to send message. Please try again.')
      // Return to landing on error
      setViewState('landing')
      setIsSending(false) // Only reset on error
    }
    // Don't reset isSending here - let the WebSocket callback handle it
  }

  const handleRetry = () => {
    initializeSession()
  }

  // Handle page close
  useEffect(() => {
    if (!sessionId) return

    const handlePageClose = () => {
      try {
        const base = (import.meta.env.VITE_API_URL as string) || window.location.origin
        const url = `${base}/sessions/${sessionId}/close`
        const payload = JSON.stringify({ reason: 'pagehide' })

        if (navigator.sendBeacon) {
          const blob = new Blob([payload], { type: 'application/json' })
          navigator.sendBeacon(url, blob)
        } else {
          fetch(url, {
            method: 'POST',
            body: payload,
            headers: { 'Content-Type': 'application/json' },
            keepalive: true
          }).catch(() => {})
        }
      } catch {}
    }

    window.addEventListener('pagehide', handlePageClose)
    window.addEventListener('beforeunload', handlePageClose)

    return () => {
      window.removeEventListener('pagehide', handlePageClose)
      window.removeEventListener('beforeunload', handlePageClose)
    }
  }, [sessionId])

  // Show loading state
  if (isLoading) {
    return (
      <div className="min-h-screen relative flex items-center justify-center bg-[#0b0f19]">
        <div className="chat-bg" />
        <div className="relative z-10 flex flex-col items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0ms' }} />
            <div className="w-3 h-3 rounded-full bg-purple-500 animate-bounce" style={{ animationDelay: '150ms' }} />
            <div className="w-3 h-3 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '300ms' }} />
          </div>
          <p className="text-neutral-400 text-sm animate-pulse">Connecting...</p>
        </div>
      </div>
    )
  }

  // Show error state
  if (error) {
    return (
      <div className="min-h-screen relative flex items-center justify-center bg-[#0b0f19]">
        <div className="chat-bg" />
        <div className="relative z-10 max-w-md w-full bg-red-500/10 border border-red-500/40 rounded-lg p-6 text-center mx-4">
          <div className="text-red-300 mb-4">
            <svg className="w-12 h-12 mx-auto mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <h3 className="text-lg font-semibold mb-2">Connection Error</h3>
            <p className="text-sm opacity-80 mb-4">{error}</p>
          </div>
          <div className="flex gap-2 justify-center">
            <button
              onClick={handleRetry}
              className="bg-blue-500 hover:bg-blue-600 text-white px-4 py-2 rounded-md transition-colors text-sm"
            >
              Retry
            </button>
            <button
              onClick={() => window.location.reload()}
              className="bg-neutral-600 hover:bg-neutral-500 text-white px-4 py-2 rounded-md transition-colors text-sm"
            >
              Refresh Page
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen relative flex flex-col bg-[#0b0f19]">
      {/* Background gradient */}
      <div className="chat-bg" />

      {/* Landing view */}
      {viewState === 'landing' && sessionId && (
        <LandingView />
      )}

      {/* Chat interface */}
      {viewState === 'chat' && sessionId && (
        <ChatInterface
          sessionId={sessionId}
          isSending={isSending}
          onError={(errorMsg) => {
            setError(errorMsg)
            setViewState('landing')
            setIsSending(false)
          }}
          onMessageComplete={() => {
            setIsSending(false)
          }}
        />
      )}

      {/* Floating input - only shown when we have a session */}
      {sessionId && (
        <div
          className={`fixed left-1/2 bottom-6 -translate-x-1/2 transform-gpu transition-transform duration-500 z-40 w-full max-w-3xl px-6 ${
            viewState === 'landing' ? '-translate-y-[26vh] sm:-translate-y-[28vh] md:-translate-y-[30vh]' : 'translate-y-0'
          }`}
        >
          <div className="pointer-events-auto">
            {/* Task bar when sending */}
            {isSending && <TaskBar sessionId={sessionId} active={true} />}

            {/* Input */}
            <ExpandableInput
              placeholder="Ask about any YouTube video — paste a link…"
              onSend={handleSendMessage}
              disabled={isLoading || isSending}
              busy={isSending}
            />
          </div>
        </div>
      )}
    </div>
  )
}