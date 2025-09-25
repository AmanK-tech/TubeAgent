import React, { useState } from 'react'
import { sendMessage } from '../../api/messages'

export const Composer: React.FC<{ sessionId?: string }> = ({ sessionId }) => {
  const [text, setText] = useState('')
  const disabled = !sessionId || text.trim().length === 0

  const onSend = async () => {
    if (!sessionId) return
    const payload = { role: 'user', content: text }
    setText('')
    await sendMessage(sessionId, payload)
  }

  return (
    <div className="border-t border-border p-3 bg-panel/60">
      <div className="bg-card rounded-xl p-3 focus-within:ring-1 focus-within:ring-primary">
        <textarea
          className="w-full bg-transparent outline-none resize-none text-sm placeholder:text-neutral-400 max-h-40 text-neutral-100"
          placeholder="Ask anything"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              onSend()
            }
          }}
        />
        <div className="mt-2 flex items-center justify-end">
          <button
            disabled={disabled}
            onClick={onSend}
            className={`rounded-full h-8 px-4 text-sm font-medium transition-colors border border-transparent ${disabled ? 'bg-neutral-800 text-neutral-400' : 'bg-primary text-primary-foreground hover:bg-indigo-500'}`}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
