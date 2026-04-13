import { create } from 'zustand'
import { devtools } from 'zustand/middleware'
import { wsManager } from '@/lib/ws'
import type { WSMessage, StageStatus, StageName } from '@/types'

// ========== Novel List Store ==========

export interface NovelListState {
  novels: any[]
  loading: boolean
  error?: string
}

interface NovelListStore extends NovelListState {
  setNovels: (novels: any[]) => void
  setLoading: (loading: boolean) => void
  setError: (error?: string) => void
  addNovel: (novel: any) => void
  removeNovel: (novelId: string) => void
}

export const useNovelListStore = create<NovelListStore>()(
  devtools(
    (set) => ({
      // Initial state
      novels: [],
      loading: false,
      error: undefined,

      // Actions
      setNovels: (novels) => set({ novels, error: undefined }, false, 'setNovels'),
      setLoading: (loading) => set({ loading }, false, 'setLoading'),
      setError: (error) => set({ error, loading: false }, false, 'setError'),

      addNovel: (novel) =>
        set(
          (state) => ({
            novels: [novel, ...state.novels],
          }),
          false,
          'addNovel'
        ),

      removeNovel: (novelId) =>
        set(
          (state) => ({
            novels: state.novels.filter((n) => n.id !== novelId),
          }),
          false,
          'removeNovel'
        ),
    }),
    { name: 'novel-list-store' }
  )
)

// ========== Progress Store ==========

export interface StageProgress {
  status: StageStatus
  chapters_done: number
  chapters_total: number
  percentage: number
  error?: string
  started_at?: number
  completed_at?: number
  duration_ms?: number
}

interface ProgressState {
  stageProgress: Record<string, Record<StageName, StageProgress>>
}

interface ProgressStore extends ProgressState {
  updateProgress: (novelId: string, stage: StageName, data: Partial<StageProgress>) => void
  setStageRunning: (novelId: string, stage: StageName, total?: number) => void
  setStageCompleted: (novelId: string, stage: StageName, duration?: number) => void
  setStageFailed: (novelId: string, stage: StageName, error: string) => void
  setStageStale: (novelId: string, stage: StageName) => void
  resetProgress: (novelId: string) => void
  getNovelProgress: (novelId: string) => Record<StageName, StageProgress>
}

const defaultStageProgress = (): StageProgress => ({
  status: 'pending',
  chapters_done: 0,
  chapters_total: 0,
  percentage: 0,
})

const defaultStages: StageName[] = ['import', 'split', 'analyze', 'mark', 'rewrite', 'assemble']

export const useProgressStore = create<ProgressStore>()(
  devtools(
    (set, get) => ({
      // Initial state
      stageProgress: {},

      // Update progress for specific novel and stage
      updateProgress: (novelId, stage, data) =>
        set(
          (state) => ({
            stageProgress: {
              ...state.stageProgress,
              [novelId]: {
                ...state.stageProgress[novelId],
                [stage]: {
                  ...state.stageProgress[novelId]?.[stage],
                  ...data,
                },
              },
            },
          }),
          false,
          'updateProgress'
        ),

      setStageRunning: (novelId, stage, total = 0) => {
        const now = Date.now()
        get().updateProgress(novelId, stage, {
          status: 'running',
          chapters_done: 0,
          chapters_total: total,
          percentage: 0,
          started_at: now,
          error: undefined,
        })
      },

      setStageCompleted: (novelId, stage, duration) => {
        const now = Date.now()
        const current = get().stageProgress[novelId]?.[stage]
        get().updateProgress(novelId, stage, {
          status: 'completed',
          chapters_done: current?.chapters_total || 1,
          percentage: 100,
          completed_at: now,
          duration_ms: duration,
        })
      },

      setStageFailed: (novelId, stage, error) => {
        const now = Date.now()
        get().updateProgress(novelId, stage, {
          status: 'failed',
          error,
          completed_at: now,
        })
      },

      setStageStale: (novelId, stage) => {
        get().updateProgress(novelId, stage, {
          status: 'paused',
        })
      },

      resetProgress: (novelId) =>
        set(
          (state) => ({
            stageProgress: {
              ...state.stageProgress,
              [novelId]: defaultStages.reduce((acc, stage) => {
                acc[stage] = defaultStageProgress()
                return acc
              }, {} as Record<StageName, StageProgress>),
            },
          }),
          false,
          'resetProgress'
        ),

      getNovelProgress: (novelId) => {
        const progress = get().stageProgress[novelId]
        if (!progress) {
          return defaultStages.reduce((acc, stage) => {
            acc[stage] = defaultStageProgress()
            return acc
          }, {} as Record<StageName, StageProgress>)
        }
        return progress as Record<StageName, StageProgress>
      },
    }),
    { name: 'progress-store' }
  )
)

// ========== Worker Store ==========

interface WorkerState {
  active: number
  idle: number
  queue_size: number
  lastUpdated?: number
}

interface WorkerStore extends WorkerState {
  update: (data: Partial<WorkerState>) => void
  isHealthy: () => boolean
}

export const useWorkerStore = create<WorkerStore>()(
  devtools(
    (set, get) => ({
      // Initial state
      active: 0,
      idle: 0,
      queue_size: 0,
      lastUpdated: undefined,

      // Update worker status
      update: (data) =>
        set(
          (state) => ({
            ...state,
            ...data,
            lastUpdated: Date.now(),
          }),
          false,
          'update'
        ),

      // Check if worker pool is healthy
      isHealthy: () => {
        const { active, idle, lastUpdated } = get()
        const isRecent = lastUpdated && (Date.now() - lastUpdated) < 30000 // 30 seconds
        return isRecent && (active > 0 || idle > 0)
      },
    }),
    { name: 'worker-store' }
  )
)

// ========== WebSocket Sync Hook ==========

export function useWSSync() {
  return {
    connect: () => {
      wsManager.connect()
      wsManager.subscribe('*') // Subscribe to all novels

      const unsubscribe = wsManager.onMessage((msg: WSMessage) => {
        const progressStore = useProgressStore.getState()
        const workerStore = useWorkerStore.getState()

        switch (msg.type) {
          case 'stage_progress':
            progressStore.updateProgress(msg.novel_id, msg.stage as StageName, {
              chapters_done: msg.chapters_done,
              chapters_total: msg.chapters_total,
              percentage: msg.percentage,
            })
            break

          case 'chapter_completed':
            // Increment chapter progress
            const currentProgress = progressStore.stageProgress[msg.novel_id]?.[msg.stage as StageName]
            if (currentProgress) {
              progressStore.updateProgress(msg.novel_id, msg.stage as StageName, {
                chapters_done: currentProgress.chapters_done + 1,
                percentage: ((currentProgress.chapters_done + 1) / currentProgress.chapters_total) * 100,
              })
            }
            break

          case 'stage_completed':
            progressStore.setStageCompleted(msg.novel_id, msg.stage as StageName, msg.duration_ms)
            break

          case 'stage_failed':
            progressStore.setStageFailed(msg.novel_id, msg.stage as StageName, msg.error)
            break

          case 'chapter_failed':
            console.error(`Chapter ${msg.chapter_index} failed in ${msg.stage}:`, msg.error)
            break

          case 'task_paused':
            break

          case 'task_resumed':
            break

          case 'stage_stale':
            progressStore.setStageStale(msg.novel_id, msg.stage as StageName)
            break

          case 'worker_pool_status':
            workerStore.update({
              active: msg.active,
              idle: msg.idle,
              queue_size: msg.queue_size,
            })
            break

          case 'ping':
            // Server ping - client should respond with pong (handled by WSManager)
            break

          case 'pong':
            // Server pong response - no action needed
            break
        }
      })

      return unsubscribe
    },

    disconnect: () => {
      wsManager.disconnect()
    },
  }
}
