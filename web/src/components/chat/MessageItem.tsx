import React from 'react'
import ReactMarkdown from 'react-markdown'

export const MessageItem: React.FC<{ role: 'user' | 'assistant' | 'system' | 'tool'; content: string }> = ({ role, content }) => {
  const isUser = role === 'user'
  const isPlain = role === 'assistant' || role === 'system'

  return (
    <div className={`w-full flex ${isUser ? 'justify-end' : 'justify-start'} my-2`}>
      {isUser ? (
        <div className="max-w-[75%] rounded-2xl px-4 py-3 text-[17px] sm:text-[18px] leading-7 shadow-sm bg-primary text-primary-foreground shadow-glow">
          <span className="whitespace-pre-wrap">{content}</span>
        </div>
      ) : isPlain ? (
        // Plain text style (no bubble) for assistant/system messages
        <div className="w-full max-w-full text-[17px] sm:text-[18px] leading-7">
          <div className="prose prose-invert prose-base sm:prose-lg max-w-none prose-headings:font-semibold prose-h1:text-3xl sm:prose-h1:text-4xl prose-h2:text-2xl sm:prose-h2:text-3xl prose-h3:text-xl sm:prose-h3:text-2xl prose-pre:bg-neutral-900 prose-pre:text-neutral-100">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        </div>
      ) : (
        // Default muted bubble for other roles (e.g., tool)
        <div className="max-w-[75%] rounded-2xl px-4 py-3 text-[17px] sm:text-[18px] leading-7 shadow-sm bg-muted text-neutral-100">
          <div className="prose prose-invert prose-base sm:prose-lg max-w-none prose-headings:font-semibold prose-h1:text-3xl sm:prose-h1:text-4xl prose-h2:text-2xl sm:prose-h2:text-3xl prose-h3:text-xl sm:prose-h3:text-2xl prose-pre:bg-neutral-900 prose-pre:text-neutral-100">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  )
}
