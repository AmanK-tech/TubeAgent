import React, { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getProgress, type ProgressStep } from '../../api/progress'
import { listMessages } from '../../api/messages'
import { CheckCircle2, Loader2, XCircle, ChevronDown } from 'lucide-react'

type Props = {
  sessionId?: string
  active?: boolean
}

const LABELS: Record<string, string> = {
  fetch_task: 'Fetch video details',
  extract_audio: 'Download video',
  transcribe_asr: 'Understand video',


  summarise_url_direct: 'Respond using the video content',


  summarise_global: 'Respond using the video content',
  emit_output: 'Save deliverables',
}

function labelFor(step: ProgressStep): string {
  return LABELS[step.name] || step.name.replace(/_/g, ' ')
}

export const TaskBar: React.FC<Props> = ({ sessionId, active }) => {
  const [collapsed, setCollapsed] = useState(false)
  const COLLAPSE_KEY = 'tubeagent.taskbar.collapsed'

  // Restore collapse state (per session if available)
  useEffect(() => {
    const key = sessionId ? `${COLLAPSE_KEY}.${sessionId}` : COLLAPSE_KEY
    try {
      const v = localStorage.getItem(key)
      if (v !== null) setCollapsed(v === '1')
    } catch {}
  }, [sessionId])

  // Persist collapse state
  useEffect(() => {
    const key = sessionId ? `${COLLAPSE_KEY}.${sessionId}` : COLLAPSE_KEY
    try { localStorage.setItem(key, collapsed ? '1' : '0') } catch {}
  }, [collapsed, sessionId])
  const { data: progressData } = useQuery({
    queryKey: ['progress', sessionId],
    queryFn: () => getProgress(sessionId!),
    enabled: !!sessionId && !!active,
    refetchInterval: active ? 1000 : false,
  })

  const { data: messagesData } = useQuery({
    queryKey: ['messages', sessionId],
    queryFn: () => listMessages(sessionId!),
    enabled: !!sessionId && !!active,
    refetchInterval: active ? 1000 : false,
  })

  const steps = progressData?.steps ?? []
  if (!active && steps.length === 0) return null

  // Check if there's a system response
  const hasSystemResponse = messagesData?.items?.some((msg: any) => msg.role === 'system') ?? false

  // Override summarise_url_direct status if system response exists
  const adjustedSteps = steps.map((step) => {
    if (step.name === 'summarise_url_direct' && step.status === 'running' && hasSystemResponse) {
      return { ...step, status: 'done' as const }
    }
    return step
  })

  const total = Math.max(adjustedSteps.length, 1)
  const completed = adjustedSteps.filter((s) => s.status === 'done').length

  return (
    <div className="mb-3">
      <div className="bg-card/90 backdrop-blur-sm border border-border rounded-xl px-4 py-3 shadow-glow">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2 text-sm font-medium text-neutral-200">
            {active && <Loader2 className="w-3.5 h-3.5 animate-spin text-sky-400" />}
            <span>Task progress</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="text-xs text-neutral-400">{completed} / {total}</div>
            <button
              type="button"
              aria-label={collapsed ? 'Expand task progress' : 'Collapse task progress'}
              aria-expanded={!collapsed}
              onClick={() => setCollapsed((v) => !v)}
              className="text-neutral-400 hover:text-neutral-200 transition-colors"
            >
              <ChevronDown className={`w-4 h-4 transition-transform ${collapsed ? '-rotate-90' : 'rotate-0'}`} />
            </button>
          </div>
        </div>
        <div id="taskbar-panel" className={collapsed ? 'hidden' : 'space-y-2'}>
          {adjustedSteps.length === 0 && (
            <div className="flex items-center gap-2 text-neutral-400 text-sm">
              <Loader2 className="w-4 h-4 animate-spin"/>
              <span>Working…</span>
            </div>
          )}
          {adjustedSteps.map((s, idx) => {
            const isActive = s.status === 'running'
            const isDone = s.status === 'done'
            const isErr = s.status === 'error'
            return (
              <div key={idx} className="flex items-start gap-2 text-sm">
                {isActive && <span className="mt-0.5"><Loader2 className="w-4 h-4 animate-spin text-sky-400"/></span>}
                {isDone && <span className="mt-0.5"><CheckCircle2 className="w-4 h-4 text-emerald-400"/></span>}
                {isErr && <span className="mt-0.5"><XCircle className="w-4 h-4 text-red-400"/></span>}
                <div className="flex-1">
                  <div className={isActive ? 'text-neutral-200' : 'text-neutral-300'}>{labelFor(s)}</div>
                  {!!s.note && (
                    <div className="text-xs text-neutral-400">{s.note}</div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default TaskBar
