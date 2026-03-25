import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import * as api from '@/lib/api'
import type {
  ChapterAnalysis,
  ChapterMarkRequest,
  CharacterTrajectoryResponse,
  RewriteReviewRequest,
  UpdateProviderForm,
  StageName
} from '@/types'

// ========== Novels ==========

export const useNovels = () =>
  useQuery({
    queryKey: ['novels'],
    queryFn: api.novels.list,
    staleTime: 30000, // 30 seconds
  })

export const useNovel = (id: string) =>
  useQuery({
    queryKey: ['novel', id],
    queryFn: () => api.novels.get(id),
    enabled: !!id,
    staleTime: 10000, // 10 seconds
  })

export const useImportNovel = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.novels.import,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['novels'] })
    },
  })
}

export const useDeleteNovel = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.novels.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['novels'] })
    },
  })
}

// ========== Stages ==========

export const useRunStage = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ novelId, stage }: { novelId: string; stage: StageName }) =>
      api.stages.run(novelId, stage),
    onSuccess: (_, { novelId }) => {
      queryClient.invalidateQueries({ queryKey: ['novel', novelId] })
    },
  })
}

export const usePauseStage = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ novelId, stage }: { novelId: string; stage: StageName }) =>
      api.stages.pause(novelId, stage),
    onSuccess: (_, { novelId }) => {
      queryClient.invalidateQueries({ queryKey: ['novel', novelId] })
    },
  })
}

export const useResumeStage = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ novelId, stage }: { novelId: string; stage: StageName }) =>
      api.stages.resume(novelId, stage),
    onSuccess: (_, { novelId }) => {
      queryClient.invalidateQueries({ queryKey: ['novel', novelId] })
    },
  })
}

export const useRetryStage = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ novelId, stage }: { novelId: string; stage: StageName }) =>
      api.stages.retry(novelId, stage),
    onSuccess: (_, { novelId }) => {
      queryClient.invalidateQueries({ queryKey: ['novel', novelId] })
    },
  })
}

export const useConfirmSplit = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.stages.confirmSplit,
    onSuccess: (_, novelId) => {
      queryClient.invalidateQueries({ queryKey: ['novel', novelId] })
    },
  })
}

// ========== Chapters ==========

export const useChapters = (novelId: string) =>
  useQuery({
    queryKey: ['chapters', novelId],
    queryFn: () => api.chapters.list(novelId),
    enabled: !!novelId,
    staleTime: 30000,
  })

export const useChapter = (novelId: string, chapterIdx: number) =>
  useQuery({
    queryKey: ['chapter', novelId, chapterIdx],
    queryFn: () => api.chapters.get(novelId, chapterIdx),
    enabled: !!novelId && chapterIdx >= 0,
    staleTime: 30000,
  })

export const useChapterAnalysis = (novelId: string, chapterIdx: number) =>
  useQuery({
    queryKey: ['chapter-analysis', novelId, chapterIdx],
    queryFn: () => api.chapters.getAnalysis(novelId, chapterIdx),
    enabled: !!novelId && chapterIdx >= 0,
    staleTime: 60000, // 1 minute
  })

export const useUpdateChapterAnalysis = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ novelId, chapterIdx, analysis }: {
      novelId: string
      chapterIdx: number
      analysis: ChapterAnalysis
    }) => api.chapters.updateAnalysis(novelId, chapterIdx, analysis),
    onSuccess: (_, { novelId, chapterIdx }) => {
      queryClient.invalidateQueries({ queryKey: ['chapter-analysis', novelId, chapterIdx] })
      queryClient.invalidateQueries({ queryKey: ['chapter', novelId, chapterIdx] })
      queryClient.invalidateQueries({ queryKey: ['chapter-rewrites', novelId, chapterIdx] })
    },
  })
}

export const useChapterRewrites = (novelId: string, chapterIdx: number) =>
  useQuery({
    queryKey: ['chapter-rewrites', novelId, chapterIdx],
    queryFn: () => api.chapters.getRewrites(novelId, chapterIdx),
    enabled: !!novelId && chapterIdx >= 0,
    staleTime: 30000,
  })

export const useReviewRewrite = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ novelId, chapterIdx, segmentId, action, rewritten_text }: {
      novelId: string
      chapterIdx: number
      segmentId: string
      action: RewriteReviewRequest['action']
      rewritten_text?: string | null
    }) => api.chapters.reviewRewrite(novelId, chapterIdx, segmentId, {
      action,
      rewritten_text: rewritten_text ?? null,
    }),
    onSuccess: (_, { novelId, chapterIdx }) => {
      queryClient.invalidateQueries({ queryKey: ['chapter-rewrites', novelId, chapterIdx] })
      queryClient.invalidateQueries({ queryKey: ['chapter', novelId, chapterIdx] })
    },
  })
}

export const useUpdateChapterMarks = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ novelId, chapterIdx, marks }: {
      novelId: string
      chapterIdx: number
      marks: ChapterMarkRequest
    }) => api.chapters.updateMarks(novelId, chapterIdx, marks),
    onSuccess: (_, { novelId, chapterIdx }) => {
      queryClient.invalidateQueries({ queryKey: ['chapter', novelId, chapterIdx] })
      queryClient.invalidateQueries({ queryKey: ['chapter-rewrites', novelId, chapterIdx] })
    },
  })
}

export const useCharacterTrajectory = (novelId: string, characterName: string) =>
  useQuery<CharacterTrajectoryResponse>({
    queryKey: ['chapter-character-trajectory', novelId, characterName],
    queryFn: () => api.chapters.getCharacterTrajectory(novelId, characterName),
    enabled: !!novelId && !!characterName,
    staleTime: 30000,
  })

export const useRetryChapter = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ novelId, stage, chapterIdx }: {
      novelId: string
      stage: StageName
      chapterIdx: number
    }) => api.chapters.retryChapter(novelId, stage, chapterIdx),
    onSuccess: (_, { novelId, chapterIdx }) => {
      queryClient.invalidateQueries({ queryKey: ['chapter', novelId, chapterIdx] })
    },
  })
}

// ========== Config ==========

export const useSceneRules = () =>
  useQuery({
    queryKey: ['config', 'scene-rules'],
    queryFn: api.config.getSceneRules,
    staleTime: 300000, // 5 minutes
  })

export const useUpdateSceneRules = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.config.updateSceneRules,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config', 'scene-rules'] })
    },
  })
}

export const useRewriteStrategies = () =>
  useQuery({
    queryKey: ['config', 'rewrite-strategies'],
    queryFn: api.config.getStrategies,
    staleTime: 300000,
  })

export const useUpdateRewriteStrategies = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.config.updateStrategies,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config', 'rewrite-strategies'] })
    },
  })
}

export const usePrompts = () =>
  useQuery({
    queryKey: ['config', 'prompts'],
    queryFn: api.config.getPrompts,
    staleTime: 300000,
  })

export const useUpdatePrompts = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.config.updatePrompts,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config', 'prompts'] })
    },
  })
}

export const useParams = () =>
  useQuery({
    queryKey: ['config', 'params'],
    queryFn: api.config.getParams,
    staleTime: 300000,
  })

export const useUpdateParams = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.config.updateParams,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config', 'params'] })
    },
  })
}

export const useAiParseConfig = () =>
  useMutation({
    mutationFn: api.config.aiParse,
  })

export const useAiApplyConfig = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.config.aiApply,
    onSuccess: () => {
      // Invalidate all config queries
      queryClient.invalidateQueries({ queryKey: ['config'] })
    },
  })
}

export const useConfigPresets = () =>
  useQuery({
    queryKey: ['config', 'presets'],
    queryFn: api.config.getPresets,
    staleTime: 300000,
  })

export const useCreateConfigPreset = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.config.createPreset,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config', 'presets'] })
    },
  })
}

// ========== Providers ==========

export const useProviders = () =>
  useQuery({
    queryKey: ['providers'],
    queryFn: api.providers.list,
    staleTime: 60000, // 1 minute
  })

export const useCreateProvider = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.providers.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] })
    },
  })
}

export const useUpdateProvider = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateProviderForm }) =>
      api.providers.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] })
    },
  })
}

export const useDeleteProvider = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.providers.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] })
    },
  })
}

export const useTestProvider = () =>
  useMutation({
    mutationFn: api.providers.test,
  })

// ========== Workers ==========

export const useWorkerStatus = () =>
  useQuery({
    queryKey: ['workers', 'status'],
    queryFn: api.workers.status,
    refetchInterval: 5000, // Refresh every 5 seconds
    staleTime: 0, // Always consider stale to enable refetch
  })

export const useSetWorkerCount = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.workers.setCount,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workers', 'status'] })
    },
  })
}
