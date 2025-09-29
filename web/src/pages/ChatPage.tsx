import React, { useEffect, useState } from 'react'
import { Chat } from '../components/chat/Chat'
import { Landing } from '../components/chat/Landing'
import { listSessions, createSession, closeSession } from '../api/sessions'
import { listMessages, sendMessage } from '../api/messages'
import ExpandableInput from '../components/ui/ExpandableInput'

export const ChatPage: React.FC = () => {
  const [active, setActive] = useState<string | undefined>()
  const [showLanding, setShowLanding] = useState<boolean>(true)
  const [inFlight, setInFlight] = useState<boolean>(false)

  useEffect(() => {
    // Ensure at least one session exists for first-time UX
    listSessions().then(async (res) => {
      if (!res.items.length) {
        const s = await createSession()
        setActive(s.id)
        setShowLanding(true)
      } else {
        const sid = res.items[0].id
        setActive(sid)
        try {
          const msgs = await listMessages(sid)
          setShowLanding((msgs.items || []).length === 0)
        } catch {
          setShowLanding(true)
        }
      }
    })
  }, [])

  // Attempt to close the active session when the page is closed/navigated away.
  useEffect(() => {
    if (!active) return
    const handler = () => {
      try {
        const base = (import.meta.env.VITE_API_URL as string) || window.location.origin
        const url = `${base}/sessions/${active}/close`
        const payload = JSON.stringify({ reason: 'pagehide' })
        if (navigator.sendBeacon) {
          const blob = new Blob([payload], { type: 'application/json' })
          navigator.sendBeacon(url, blob)
        } else {
          // Fallback using keepalive fetch
          fetch(url, { method: 'POST', body: payload, headers: { 'Content-Type': 'application/json' }, keepalive: true }).catch(() => {})
        }
      } catch {}
    }
    window.addEventListener('pagehide', handler)
    window.addEventListener('beforeunload', handler)
    return () => {
      window.removeEventListener('pagehide', handler)
      window.removeEventListener('beforeunload', handler)
    }
  }, [active])

  return (
    <div className="min-h-screen relative flex flex-col">
      {/* Global chat background, fixed to viewport to avoid seams on scroll */}
      <div className="chat-bg" />
      {/* Main chat column (full width), faded in after landing */}
      <div
        className={`flex flex-col flex-1 transform-gpu transition-all duration-500 ${
          showLanding ? 'opacity-0 translate-y-2 pointer-events-none' : 'opacity-100 translate-y-0'
        }`}
      >
        {/* Add bottom padding to prevent overlap with floating input */}
        <div className="flex-1 pb-40">
          <Chat
            sessionId={active}
            onMessageComplete={() => setInFlight(false)}
            onError={() => setInFlight(false)}
          />
        </div>
      </div>

      {/* Floating unified input: center on landing, docks to bottom in chat */}
      <div
        className={`fixed left-1/2 bottom-6 -translate-x-1/2 transform-gpu transition-transform duration-500 z-40 w-full max-w-3xl px-6 ${
          showLanding ? '-translate-y-[26vh] sm:-translate-y-[28vh] md:-translate-y-[30vh]' : 'translate-y-0'
        }`}
      >
        <div className="pointer-events-auto">
          <ExpandableInput
            placeholder="Ask about any YouTube video, channel, or topic..."
            onSend={async (t) => {
              if (!active) return
              // include user_req for backend routing parity with Composer
              try {
                setInFlight(true)
                await sendMessage(active, { role: 'user', content: t, user_req: t })
              } catch (e) {
                setInFlight(false)
                throw e
              }
              setShowLanding(false)
            }}
            disabled={!active}
            busy={inFlight}
          />
        </div>
      </div>

      {/* Landing overlay on top when there are no messages */}
      {showLanding && (
        <div className="absolute inset-0">
          <Landing />
        </div>
      )}
    </div>
  )
}
