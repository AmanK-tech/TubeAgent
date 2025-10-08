import React, { useEffect, useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getProgress, type ProgressStep } from '../../api/progress'
import { listMessages } from '../../api/messages'
import { CheckCircle2, Loader2, XCircle, ChevronDown, Circle } from 'lucide-react'

type Props = {
  sessionId?: string
  active?: boolean
}

const LABELS: Record<string, string> = {
  fetch_task: 'Fetch video details',
  extract_audio: 'Understand video',
  transcribe_asr: 'Respond using the video content',
  summarise_url_direct: 'Respond using the video content',
  emit_output: 'Save deliverables',
}

// Define expected task flows
const SHORT_VIDEO_FLOW = ['fetch_task', 'summarise_url_direct']
const LONG_VIDEO_FLOW = ['fetch_task', 'extract_audio', 'transcribe_asr']

function labelFor(step: ProgressStep | { name: string; status: string }): string {
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

  // Check if there's an assistant response
  const hasAssistantResponse = messagesData?.items?.some((msg: any) => msg.role === 'assistant') ?? false

  // Detect which flow is being used based on actual steps
  const detectedFlow = useMemo(() => {
    if (steps.length === 0) return null
    // If we see summarise_url_direct, it's a short video flow
    const hasUrlDirect = steps.some(s => s.name === 'summarise_url_direct')
    // If we see extract_audio or transcribe_asr (without summarise_url_direct first), it's long video
    const hasExtractOrTranscribe = steps.some(s => s.name === 'extract_audio' || s.name === 'transcribe_asr')
    
    if (hasUrlDirect) return 'short'
    if (hasExtractOrTranscribe) return 'long'
    return null
  }, [steps])

  // Create expected task list based on detected flow
  const expectedTasks = useMemo(() => {
    if (!detectedFlow) return []
    return detectedFlow === 'short' ? SHORT_VIDEO_FLOW : LONG_VIDEO_FLOW
  }, [detectedFlow])

  // Merge actual progress with expected tasks
  const adjustedSteps = useMemo(() => {
    if (expectedTasks.length === 0) {
      // No flow detected yet, show actual steps
      return steps.map((step) => {
        if (step.name === 'summarise_url_direct' && step.status === 'running' && hasAssistantResponse) {
          return { ...step, status: 'done' as const }
        }
        return step
      })
    }

    // Create full task list with statuses
    const taskMap = new Map<string, ProgressStep>()
    steps.forEach(step => taskMap.set(step.name, step))

    const result = expectedTasks.map(taskName => {
      const actualStep = taskMap.get(taskName)
      if (actualStep) {
        // Override summarise_url_direct status if assistant response exists
        if (taskName === 'summarise_url_direct' && actualStep.status === 'running' && hasAssistantResponse) {
          return { ...actualStep, status: 'done' as const }
        }
        return actualStep
      }
      // Task not started yet, show as pending
      return {
        name: taskName,
        status: 'pending' as const,
        started_at: null,
        ended_at: null,
        note: null,
      }
    })

    // Add any unexpected tasks that appeared in progress but aren't in expected flow
    steps.forEach(step => {
      if (!expectedTasks.includes(step.name)) {
        result.push(step)
      }
    })

    return result
  }, [steps, expectedTasks, hasAssistantResponse])

  const total = adjustedSteps.length
  const completed = adjustedSteps.filter((s) => s.status === 'done').length
  const hasRunningTasks = adjustedSteps.some((s) => s.status === 'running')

  return (
    <div className="mb-3">
      <div className="bg-card/90 backdrop-blur-sm border border-border rounded-xl px-4 py-3 shadow-glow">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2 text-sm font-medium text-neutral-200">
            {hasRunningTasks && <Loader2 className="w-3.5 h-3.5 animate-spin text-sky-400" />}
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
            const isPending = s.status === 'pending'
            return (
              <div key={idx} className="flex items-start gap-2 text-sm">
                {isPending && <span className="mt-0.5"><Circle className="w-4 h-4 text-neutral-500"/></span>}
                {isActive && <span className="mt-0.5"><Loader2 className="w-4 h-4 animate-spin text-sky-400"/></span>}
                {isDone && <span className="mt-0.5"><CheckCircle2 className="w-4 h-4 text-emerald-400"/></span>}
                {isErr && <span className="mt-0.5"><XCircle className="w-4 h-4 text-red-400"/></span>}
                <div className="flex-1">
                  <div className={isActive ? 'text-neutral-200' : isPending ? 'text-neutral-400' : 'text-neutral-300'}>{labelFor(s)}</div>
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
