import React, { useEffect, useState } from 'react'
import { Chat } from '../components/chat/Chat'
import { Composer } from '../components/chat/Composer'
import { Landing } from '../components/chat/Landing'
import { listSessions, createSession } from '../api/sessions'
import { listMessages } from '../api/messages'

export const ChatPage: React.FC = () => {
  const [active, setActive] = useState<string | undefined>()
  const [showLanding, setShowLanding] = useState<boolean>(true)

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

  return (
    <div className="min-h-screen relative flex flex-col">
      {/* Main chat column (full width), faded in after landing */}
      <div
        className={`flex flex-col flex-1 transform-gpu transition-all duration-500 ${
          showLanding ? 'opacity-0 translate-y-2 pointer-events-none' : 'opacity-100 translate-y-0'
        }`}
      >
        <Chat sessionId={active} />
        <Composer sessionId={active} />
      </div>

      {/* Landing overlay on top when there are no messages */}
      {showLanding && (
        <div className="absolute inset-0">
          <Landing sessionId={active} onSubmitted={() => setShowLanding(false)} />
        </div>
      )}
    </div>
  )
}
