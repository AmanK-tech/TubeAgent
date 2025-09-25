import React, { useState } from 'react'
import { Send } from 'lucide-react'

type Props = {
  onSend: (text: string) => void | Promise<void>
  placeholder?: string
}

const ExpandableInput: React.FC<Props> = ({ onSend, placeholder }) => {
  const [message, setMessage] = useState('')

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault()
    const text = message.trim()
    if (!text) return
    await onSend(text)
    setMessage('')
  }

  const handleKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void handleSubmit()
    }
  }

  return (
    <div className="relative">
      {/* Glow effect */}
      <div className="absolute -inset-1 bg-gradient-to-r from-blue-500/30 to-purple-500/30 rounded-2xl blur-sm opacity-75" />

      {/* Main input container */}
      <div className="relative bg-card/90 backdrop-blur-sm border border-border rounded-2xl p-4 shadow-glow">
        <div className="flex items-end space-x-3">
          {/* Auto-expanding textarea */}
          <div className="flex-1 relative">
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={placeholder || 'Ask a question...'}
              className="w-full bg-transparent text-neutral-100 placeholder-neutral-400 resize-none focus:outline-none text-lg leading-relaxed max-h-32 overflow-y-auto"
              rows={1}
              style={{ height: 'auto', minHeight: '24px' }}
              onInput={(e) => {
                const el = e.currentTarget
                el.style.height = 'auto'
                el.style.height = Math.min(el.scrollHeight, 128) + 'px'
              }}
            />
          </div>

          {/* Send button only */}
          <button
            onClick={handleSubmit}
            disabled={!message.trim()}
            className={`w-10 h-10 grid place-items-center rounded-full transition-all duration-200 ${
              message.trim()
                ? 'bg-primary hover:bg-indigo-500 text-primary-foreground shadow-lg shadow-indigo-600/25'
                : 'bg-muted text-neutral-400 cursor-not-allowed'
            }`}
            title="Send message"
          >
            <Send size={18} />
          </button>
        </div>

      </div>
    </div>
  )
}

export default ExpandableInput
