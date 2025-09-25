import React, { useState } from 'react'
import { sendMessage } from '../../api/messages'
import ExpandableInput from '../ui/ExpandableInput'

export const Landing: React.FC<{ sessionId?: string; onSubmitted?: () => void }> = ({ sessionId, onSubmitted }) => {
  const [text, setText] = useState('')
  const disabled = !sessionId || text.trim().length === 0

  const onSend = async () => {
    if (!sessionId || disabled) return
    await sendMessage(sessionId, { role: 'user', content: text })
    onSubmitted?.()
  }

  return (
    <div className="absolute inset-0 flex items-center justify-center">
      <div className="landing-bg" />
      <div className="w-full max-w-3xl px-6 text-center">
        <h1 className="text-6xl font-semibold tracking-tight text-white/95 mb-8 animate-fade-in-up [animation-delay:80ms] drop-shadow-[0_2px_20px_rgba(99,102,241,.35)]">TubeAgent</h1>
        <div className="pointer-events-auto animate-fade-in [animation-delay:140ms]">
          <ExpandableInput
            onSend={async (t) => {
              if (!sessionId) return
              await sendMessage(sessionId, { role: 'user', content: t })
              onSubmitted?.()
            }}
            placeholder="Ask about any YouTube video, channel, or topic..."
          />
        </div>
      </div>
    </div>
  )
}
