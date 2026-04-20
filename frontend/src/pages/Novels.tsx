import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AlertCircle, FileText, Loader2, Trash2, Upload, X } from 'lucide-react'
import { getNovels, novels as novelsApi, uploadFile } from '@/lib/api'
import type { Novel } from '@/types'

function formatChars(n: number, t: (key: string, opts?: Record<string, unknown>) => string): string {
  if (n >= 10000) return t('common:format.tenThousandChars', { count: (n / 10000).toFixed(1) })
  return t('common:format.chars', { count: n.toLocaleString() })
}

function formatDate(iso: string, language: string): string {
  return new Date(iso).toLocaleDateString(language, { year: 'numeric', month: 'numeric', day: 'numeric' })
}

function ImportNovelModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void
  onSuccess: (novelId: string) => void
}) {
  const { t } = useTranslation(['novels', 'common'])
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const backdropRef = useRef<HTMLDivElement>(null)

  const handleFile = (f: File) => {
    if (!f.name.match(/\.(txt|epub)$/i)) {
      setError(t('common:upload.onlyTxtEpub'))
      return
    }
    setError(null)
    setFile(f)
  }

  const handleUpload = async () => {
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const result = await uploadFile(file, (pct) => setProgress(pct))
      onSuccess(result.novel_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('common:upload.uploadFailed'))
      setUploading(false)
    }
  }

  return (
    <div
      ref={backdropRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === backdropRef.current && !uploading) onClose()
      }}
    >
      <div className="w-full max-w-md overflow-hidden rounded-2xl border border-border bg-white shadow-lg">
        <div className="flex items-center justify-between border-b border-border px-6 py-5">
          <h2 className="text-title-2 font-semibold text-primary">{t('modal.title')}</h2>
          <button
            type="button"
            onClick={onClose}
            disabled={uploading}
            className="rounded-lg p-1.5 transition hover:bg-subtle"
          >
            <X className="h-4 w-4 text-secondary" />
          </button>
        </div>

        <div className="space-y-4 p-6">
          <div
            className={`cursor-pointer rounded-xl border-2 border-dashed p-8 text-center transition ${
              dragging ? 'border-accent bg-accent/5' : 'border-border hover:border-accent/50 hover:bg-subtle'
            } ${file ? 'border-success/50 bg-success/5' : ''}`}
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              const f = e.dataTransfer.files[0]
              if (f) handleFile(f)
            }}
            onClick={() => !uploading && inputRef.current?.click()}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".txt,.epub"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) handleFile(f)
              }}
            />
            {file ? (
              <div className="space-y-2">
                <p className="text-body-bold text-primary">{file.name}</p>
                <p className="text-caption text-secondary">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
              </div>
            ) : (
              <div className="space-y-2">
                <Upload className="mx-auto h-8 w-8 text-secondary" />
                <p className="text-body-bold text-primary">{t('common:upload.dragOrClickToSelect')}</p>
                <p className="text-caption text-secondary">{t('common:upload.supportedFormatsTxtEpub')}</p>
              </div>
            )}
          </div>

          {uploading && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-caption text-secondary">{t('common:upload.uploading')}</span>
                <span className="text-caption font-medium text-primary">{Math.round(progress)}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-subtle">
                <div className="h-full rounded-full bg-accent transition-all duration-200" style={{ width: `${progress}%` }} />
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-center gap-2 rounded-lg bg-error/10 px-3 py-2">
              <AlertCircle className="h-4 w-4 flex-shrink-0 text-error" />
              <p className="text-callout text-error">{error}</p>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 border-t border-border px-6 py-4">
          <button type="button" onClick={onClose} disabled={uploading} className="button-secondary">
            {t('common:action.cancel')}
          </button>
          <button
            type="button"
            onClick={handleUpload}
            disabled={!file || uploading}
            className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            {uploading ? t('common:upload.uploadingShort') : t('common:upload.startImport')}
          </button>
        </div>
      </div>
    </div>
  )
}

function NovelItem({
  novel,
  deleting,
  onOpen,
  onDelete,
}: {
  novel: Novel
  deleting: boolean
  onOpen: () => void
  onDelete: () => void
}) {
  const { t, i18n } = useTranslation(['novels', 'common'])
  return (
    <div className="rounded-2xl border border-border bg-white p-5 shadow-xs">
      <div className="flex items-start justify-between gap-4">
        <button type="button" onClick={onOpen} className="min-w-0 text-left">
          <p className="truncate text-title-2 font-semibold text-primary">《{novel.title}》</p>
          <p className="mt-1 text-callout text-secondary">
            {formatChars(novel.total_chars, t)} · {novel.chapter_count != null ? t('chapterCount', { count: novel.chapter_count }) : '—'} · {novel.file_format.toUpperCase()}
          </p>
          <p className="mt-1 text-caption text-tertiary">{formatDate(novel.imported_at, i18n.language)}</p>
        </button>

        <div className="flex items-center gap-2">
          <button type="button" onClick={onOpen} className="button-secondary">
            {t('view')}
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={deleting}
            className="button-secondary flex items-center gap-2 text-error hover:text-error disabled:cursor-not-allowed disabled:opacity-50"
          >
            {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
            {deleting ? t('deleting') : t('common:action.delete')}
          </button>
        </div>
      </div>
    </div>
  )
}

export function Novels() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { t } = useTranslation(['novels', 'common'])
  const [showImport, setShowImport] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const deleteLockRef = useRef(false)

  const { data: novels = [], isLoading } = useQuery({
    queryKey: ['novels'],
    queryFn: getNovels,
    refetchInterval: deletingId ? false : 10000,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => novelsApi.delete(id),
    onMutate: async (id: string) => {
      await queryClient.cancelQueries({ queryKey: ['novels'] })
      const previous = queryClient.getQueryData<Novel[]>(['novels']) ?? []
      const deletedNovel = previous.find((item) => item.id === id) ?? null
      queryClient.setQueryData<Novel[]>(
        ['novels'],
        previous.filter((item) => item.id !== id),
      )
      return { previous, deletedNovel }
    },
    onSuccess: (_data, _id, context) => {
      const title = context?.deletedNovel?.title
      setMessage(title ? t('deleted', { title }) : t('deletedNovel'))
    },
    onError: (error, _id, context) => {
      if (context?.previous) {
        queryClient.setQueryData<Novel[]>(['novels'], context.previous)
      }
      setMessage(error instanceof Error ? error.message : t('deleteFailed'))
    },
    onSettled: () => {
      deleteLockRef.current = false
      setDeletingId(null)
      queryClient.invalidateQueries({ queryKey: ['novels'] })
    },
  })

  const handleDelete = async (id: string) => {
    if (deleteLockRef.current) return
    const current = novels.find((item) => item.id === id)
    if (!current) return
    const confirmed = window.confirm(t('confirmDelete', { title: current.title }))
    if (!confirmed) return

    deleteLockRef.current = true
    setDeletingId(id)
    setMessage(null)
    try {
      await deleteMutation.mutateAsync(id)
    } catch {
      // handled by mutation callbacks
    }
  }

  const handleImportSuccess = (novelId: string) => {
    setShowImport(false)
    queryClient.invalidateQueries({ queryKey: ['novels'] })
    navigate(`/novels/${novelId}`)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-display font-bold text-primary">{t('title')}</h1>
          <p className="mt-1 text-callout text-secondary">{t('subtitle')}</p>
        </div>
        <button type="button" onClick={() => setShowImport(true)} className="button-primary flex items-center gap-2">
          <Upload className="h-4 w-4" />
          {t('importButton')}
        </button>
      </div>

      {message && (
        <div className="rounded-xl border border-border bg-subtle px-4 py-3 text-callout text-secondary">
          {message}
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="rounded-xl border border-border bg-white p-5 shadow-xs">
              <div className="animate-pulse space-y-2">
                <div className="h-5 w-1/3 rounded-lg bg-subtle" />
                <div className="h-3.5 w-1/2 rounded bg-subtle" />
              </div>
            </div>
          ))}
        </div>
      ) : novels.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border bg-subtle px-6 py-20 text-center">
          <FileText className="mx-auto h-10 w-10 text-tertiary" />
          <p className="mt-3 text-title-3 font-semibold text-primary">{t('empty')}</p>
          <p className="mt-1 text-callout text-secondary">{t('emptyHint')}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {novels.map((novel) => (
            <NovelItem
              key={novel.id}
              novel={novel}
              deleting={deletingId === novel.id}
              onOpen={() => navigate(`/novels/${novel.id}`)}
              onDelete={() => handleDelete(novel.id)}
            />
          ))}
        </div>
      )}

      {showImport && (
        <ImportNovelModal
          onClose={() => setShowImport(false)}
          onSuccess={handleImportSuccess}
        />
      )}
    </div>
  )
}
