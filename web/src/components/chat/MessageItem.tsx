import React from 'react'
import ReactMarkdown from 'react-markdown'

export const MessageItem: React.FC<{ role: 'user' | 'assistant' | 'system' | 'tool'; content: string }> = ({ role, content }) => {
  const isUser = role === 'user'
  return (
    <div className={`w-full flex ${isUser ? 'justify-end' : 'justify-start'} my-2`}>
      <div className={`max-w-[75%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm ${isUser ? 'bg-primary text-primary-foreground shadow-glow' : 'bg-muted text-neutral-100'}`}>
        {isUser ? (
          <span className="whitespace-pre-wrap">{content}</span>
        ) : (
          <div className="prose prose-invert prose-pre:bg-neutral-900 prose-pre:text-neutral-100">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
}
