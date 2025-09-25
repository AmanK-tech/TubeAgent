import React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listSessions, createSession } from '../../api/sessions'
import { Plus } from 'lucide-react'

export const SessionSidebar: React.FC<{ onSelect: (id: string) => void; activeId?: string }> = ({ onSelect, activeId }) => {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['sessions'], queryFn: listSessions })
  return (
    <aside className="panel h-full border-r border-border p-3 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-neutral-300">Conversations</div>
        <button
          className="inline-flex items-center gap-1 rounded-md bg-card hover:bg-muted text-neutral-200 px-2 py-1 text-xs border border-border"
          onClick={async () => {
            const s = await createSession()
            await qc.invalidateQueries({ queryKey: ['sessions'] })
            onSelect(s.id)
          }}
        >
          <Plus size={14} /> New
        </button>
      </div>
      <div className="flex-1 overflow-y-auto space-y-1">
        {data?.items?.map((s) => (
          <button
            key={s.id}
            onClick={() => onSelect(s.id)}
            className={`w-full text-left rounded-md px-3 py-2 text-sm hover:bg-muted ${activeId === s.id ? 'bg-muted' : ''}`}
          >
            <div className="truncate font-medium text-neutral-200">{s.title || 'New Chat'}</div>
            <div className="truncate text-xs text-neutral-500">{new Date(s.updated_at * 1000).toLocaleString()}</div>
          </button>
        ))}
      </div>
    </aside>
  )
}
