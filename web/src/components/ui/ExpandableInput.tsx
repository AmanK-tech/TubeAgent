import React, { useState } from 'react'
import { Send, Loader2 } from 'lucide-react'

type Props = {
  onSend: (text: string) => void | Promise<void>
  placeholder?: string
  disabled?: boolean
  busy?: boolean
}

const ExpandableInput: React.FC<Props> = ({ onSend, placeholder, disabled, busy }) => {
  const [message, setMessage] = useState('')
  const effectiveDisabled = !!disabled || !!busy

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault()
    const text = message.trim()
    if (!text || effectiveDisabled) return
    await onSend(text)
    setMessage('')
  }

  const handleKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !effectiveDisabled) {
      e.preventDefault()
      void handleSubmit()
    }
  }

  return (
    <div className="relative">
      {/* Glow effect */}
      <div className="absolute -inset-1 bg-gradient-to-r from-blue-500/30 to-purple-500/30 rounded-2xl blur-sm opacity-75" />

      {/* Main input container */}
      <div className={`relative bg-card/90 backdrop-blur-sm border rounded-2xl p-4 shadow-glow ${effectiveDisabled ? 'border-neutral-800 opacity-80' : 'border-border'}`} aria-busy={busy ? true : undefined}>
        <div className="flex items-end space-x-3">
          {/* Auto-expanding textarea */}
          <div className="flex-1 relative">
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={placeholder || 'Ask a question...'}
              className="w-full bg-transparent text-neutral-100 placeholder-neutral-400 resize-none focus:outline-none text-lg leading-relaxed max-h-32 overflow-y-auto disabled:text-neutral-400"
              rows={1}
              style={{ height: 'auto', minHeight: '24px' }}
              disabled={effectiveDisabled}
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
            disabled={effectiveDisabled || !message.trim()}
            className={`w-10 h-10 grid place-items-center rounded-full transition-all duration-200 ${
              !effectiveDisabled && message.trim()
                ? 'bg-primary hover:bg-indigo-500 text-primary-foreground shadow-lg shadow-indigo-600/25'
                : 'bg-muted text-neutral-400 cursor-not-allowed'
            }`}
            title={busy ? 'Generating…' : 'Send message'}
            aria-live="polite"
          >
            {busy ? (
              <>
                <Loader2 size={18} className="animate-spin" />
                <span className="sr-only">Generating…</span>
              </>
            ) : (
              <Send size={18} />
            )}
          </button>
        </div>

      </div>
    </div>
  )
}

export default ExpandableInput
