'use client'

import { useState, useRef, useEffect, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

// Production: set NEXT_PUBLIC_API_BASE on Vercel to the Railway public API (no trailing slash).
const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || 'http://127.0.0.1:8000').replace(
  /\/$/,
  '',
)

function explainFetchError(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err)
  const isLocal =
    API_BASE.includes('127.0.0.1') ||
    API_BASE.includes('localhost')
  if (msg === 'Failed to fetch' || msg.includes('NetworkError') || msg.includes('Load failed')) {
    if (isLocal) {
      return (
        `无法连接 API（当前地址是本地 ${API_BASE}）。` +
        `请在 Vercel → Settings → Environment Variables 设置 ` +
        `NEXT_PUBLIC_API_BASE=https://你的.up.railway.app（无末尾/），然后 Redeploy。`
      )
    }
    return (
      `无法连接 API：${API_BASE}。请检查：` +
      `1) 浏览器打开 ${API_BASE}/health 是否返回 ok；` +
      `2) Railway Variables：CORS_ORIGINS=你的 vercel 域名（无末尾/）或 CORS_ORIGIN_REGEX=1；` +
      `3) 改变量后两边都 Redeploy。原始错误：${msg}`
    )
  }
  return msg
}

/** Numbered marker inserted at caret; backend replaces with binding twist block. */
function twistMarker(n: number) {
  return `⟦TWIST:${n}⟧`
}

/** Product flow: input → generate → result (no card-edit mid-step) */
type Stage = 'seed' | 'loading' | 'result'

interface CharacterDraft {
  id: number
  prompt: string
}

interface TwistItem {
  id: number
  content: string
}

const STYLE_LABELS: Record<string, string> = {
  literary_realism: 'Literary realism',
  minimalism: 'Minimalism',
  psychological: 'Psychological',
  poetic: 'Poetic / lyrical',
}

const POV_LABELS: Record<string, string> = {
  third_limited: 'Third-person limited',
  first_person: 'First person',
}

function formatApiError(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === 'object' && d && 'msg' in d ? String((d as any).msg) : String(d)))
      .join('; ')
  }
  return fallback
}

/** Renumber markers in premise after twist list changes (keeps relative order of markers). */
function renumberMarkersInPremise(text: string, count: number): string {
  if (!text) return text
  // Collect marker positions in order of appearance
  const re = /⟦TWIST(?::\d+)?⟧/g
  const matches = [...text.matchAll(re)]
  if (matches.length === 0) return text
  let out = ''
  let last = 0
  let i = 0
  for (const m of matches) {
    const start = m.index ?? 0
    out += text.slice(last, start)
    i += 1
    if (i <= count) {
      out += twistMarker(i)
    }
    // if more markers than twists, drop extras
    last = start + m[0].length
  }
  out += text.slice(last)
  return out
}

function stripAllTwistMarkers(text: string): string {
  return text.replace(/⟦TWIST(?::\d+)?⟧/g, '').replace(/[ \t]{2,}/g, ' ')
}

export default function LensWrite() {
  const [stage, setStage] = useState<Stage>('seed')

  // Inputs
  const [seed, setSeed] = useState('')
  const [drafts, setDrafts] = useState<CharacterDraft[]>([{ id: 1, prompt: '' }])
  // Optional plot twists (user-written; place numbered markers in premise)
  const [twists, setTwists] = useState<TwistItem[]>([])
  const [resultTwists, setResultTwists] = useState<string[]>([])
  const seedTextareaRef = useRef<HTMLTextAreaElement | null>(null)
  const nextTwistId = useRef(1)

  // Render prefs
  const [style, setStyle] = useState('literary_realism')
  const [perspective, setPerspective] = useState('third_limited')
  const [focalIndex, setFocalIndex] = useState(0)

  // Generation
  const [threadId, setThreadId] = useState('')
  const [finalStory, setFinalStory] = useState<any>(null)
  const [acceptance, setAcceptance] = useState<{
    acceptance_ok?: boolean
    in_word_band?: boolean
    enrichment_in_prose_ok?: boolean
    acceptance_summary?: string
    failure_reasons?: string[]
    pass_reasons?: string[]
    reasons?: { code: string; ok: boolean; message: string }[]
    actual_word_count?: number
  } | null>(null)
  const [iterationCount, setIterationCount] = useState(0)
  const [maxIterations, setMaxIterations] = useState(6)
  const [errorMsg, setErrorMsg] = useState('')
  const [apiHealth, setApiHealth] = useState<
    'checking' | 'ok' | 'fail' | 'local-default'
  >('checking')
  const [apiHealthDetail, setApiHealthDetail] = useState('')
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    const isLocal =
      API_BASE.includes('127.0.0.1') || API_BASE.includes('localhost')
    // On Vercel (browser host is not localhost), localhost API is almost always wrong.
    const onRemoteHost =
      typeof window !== 'undefined' &&
      !['localhost', '127.0.0.1'].includes(window.location.hostname)
    if (isLocal && onRemoteHost) {
      setApiHealth('local-default')
      setApiHealthDetail(
        `当前 API 仍是 ${API_BASE}。请在 Vercel 设置 NEXT_PUBLIC_API_BASE=你的 Railway https 地址，然后 Redeploy。`,
      )
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const r = await fetch(`${API_BASE}/health`, { method: 'GET' })
        if (cancelled) return
        if (!r.ok) {
          setApiHealth('fail')
          setApiHealthDetail(`HTTP ${r.status} from ${API_BASE}/health`)
          return
        }
        const j = await r.json().catch(() => ({}))
        setApiHealth('ok')
        setApiHealthDetail(
          j?.has_xai_key === false
            ? `${API_BASE} 可达，但 Railway 未配置 XAI_API_KEY`
            : `${API_BASE} 正常`,
        )
      } catch (e) {
        if (cancelled) return
        setApiHealth('fail')
        setApiHealthDetail(
          `无法访问 ${API_BASE}/health（CORS 或地址错误）。请在 Railway 设 CORS_ORIGINS / 确认域名。`,
        )
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const filledDrafts = drafts.filter(d => d.prompt.trim())
  const focalOptions =
    filledDrafts.length > 0
      ? filledDrafts.map((d, i) => ({
          value: i,
          label: d.prompt.trim().slice(0, 40) || `Character ${i + 1}`,
        }))
      : [{ value: 0, label: 'Character 1' }]

  const addDraft = () => {
    if (drafts.length >= 10) return
    const newId = Math.max(0, ...drafts.map(d => d.id)) + 1
    setDrafts([...drafts, { id: newId, prompt: '' }])
  }

  const removeDraft = (id: number) => {
    if (drafts.length <= 1) {
      setDrafts([{ id: 1, prompt: '' }])
      return
    }
    setDrafts(drafts.filter(d => d.id !== id))
  }

  const updateDraft = (id: number, prompt: string) => {
    setDrafts(drafts.map(d => (d.id === id ? { ...d, prompt } : d)))
  }

  const addTwist = () => {
    if (twists.length >= 8) return
    const id = nextTwistId.current++
    const next = [...twists, { id, content: '' }]
    setTwists(next)
  }

  const removeTwist = (id: number) => {
    const next = twists.filter(t => t.id !== id)
    setTwists(next)
    setSeed(prev => renumberMarkersInPremise(stripAllTwistMarkers(prev), next.length))
  }

  const updateTwist = (id: number, content: string) => {
    setTwists(twists.map(t => (t.id === id ? { ...t, content } : t)))
  }

  /** Insert marker for twist at 1-based list index (display order). */
  const insertTwistMarkerAt = (listIndex: number) => {
    const n = listIndex + 1
    const marker = twistMarker(n)
    const el = seedTextareaRef.current
    // Remove any existing marker for this index first
    const withoutThis = seed.replaceAll(marker, '')
    if (!el) {
      setSeed(`${withoutThis}${withoutThis ? ' ' : ''}${marker}`)
      return
    }
    const start = el.selectionStart ?? withoutThis.length
    // Map caret from original seed to withoutThis (approx: strip only this marker)
    const before = seed.slice(0, start).replaceAll(marker, '')
    const insertAt = Math.min(before.length, withoutThis.length)
    const next = withoutThis.slice(0, insertAt) + marker + withoutThis.slice(insertAt)
    setSeed(next)
    requestAnimationFrame(() => {
      el.focus()
      const pos = insertAt + marker.length
      el.setSelectionRange(pos, pos)
    })
  }

  const clearAllTwists = () => {
    setTwists([])
    setSeed(prev => stripAllTwistMarkers(prev))
  }

  const handleGenerateStory = async () => {
    if (!seed.trim()) {
      alert('Please enter a story premise.')
      return
    }
    const emptyTwist = twists.some(t => !t.content.trim())
    if (twists.length > 0 && emptyTwist) {
      alert('Every twist needs content, or remove the empty twist rows.')
      return
    }

    setStage('loading')
    setIterationCount(0)
    setMaxIterations(6)
    setFinalStory(null)
    setAcceptance(null)
    setResultTwists([])
    setErrorMsg('')
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)

    // Ensure markers are 1..n matching list order
    const premise = renumberMarkersInPremise(seed.trim(), twists.length)
    const twistPayload = twists.map(t => t.content.trim()).filter(Boolean)
    const character_drafts = filledDrafts.map(d => ({
      prompt: d.prompt.trim(),
      use_slider: false,
      sliders: null,
    }))

    try {
      const res = await fetch(`${API_BASE}/stories`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          story_prompt: premise,
          story_seed: seed.trim() || null,
          character_drafts,
          // Backend defaults invent cast size when drafts empty
          story_length: 'medium',
          expand_prompt: true,
          style,
          preferred_perspective: perspective,
          focal_character: String(focalIndex + 1),
          twists: twistPayload,
          twist_enabled: twistPayload.length > 0,
        }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(formatApiError(err.detail, 'Failed to start story generation'))
      }
      const data = await res.json()
      const newThreadId = data.thread_id
      setThreadId(newThreadId)

      pollIntervalRef.current = setInterval(async () => {
        try {
          const statusRes = await fetch(`${API_BASE}/stories/${newThreadId}/status`)
          const statusData = await statusRes.json()
          if (statusData.iteration_count !== undefined) {
            setIterationCount(statusData.iteration_count)
          }
          if (statusData.max_iterations !== undefined) {
            setMaxIterations(statusData.max_iterations)
          }
          if (statusData.error) {
            if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
            setErrorMsg(statusData.error)
            setStage('seed')
            alert('Generation error: ' + statusData.error)
            return
          }
          if (statusData.final_story && statusData.is_interrupted === false) {
            setFinalStory(statusData.final_story)
            const rt = Array.isArray(statusData.twists)
              ? statusData.twists
                  .map((t: any) => (typeof t === 'string' ? t : t?.content || ''))
                  .filter(Boolean)
              : statusData.twist_content
                ? [String(statusData.twist_content)]
                : []
            setResultTwists(rt)
            setAcceptance({
              acceptance_ok: statusData.acceptance_ok,
              in_word_band: statusData.in_word_band,
              enrichment_in_prose_ok: statusData.enrichment_in_prose_ok,
              acceptance_summary: statusData.acceptance_summary,
              failure_reasons: statusData.failure_reasons || [],
              pass_reasons: statusData.pass_reasons || [],
              reasons: statusData.reasons || [],
              actual_word_count:
                statusData.actual_word_count ?? statusData.final_story?.word_count,
            })
            setStage('result')
            if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
          }
        } catch {
          // ignore poll blips
        }
      }, 1500)
    } catch (e: unknown) {
      console.error(e)
      const friendly = explainFetchError(e)
      setErrorMsg(friendly)
      alert(friendly)
      setStage('seed')
    }
  }

  const resetAll = () => {
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
    setStage('seed')
    setSeed('')
    setTwists([])
    setResultTwists([])
    setDrafts([{ id: 1, prompt: '' }])
    setFinalStory(null)
    setAcceptance(null)
    setIterationCount(0)
    setErrorMsg('')
    setThreadId('')
  }

  /** Preview: replace markers with twist chips */
  const renderPremisePreview = () => {
    if (twists.length === 0) return null
    const parts: ReactNode[] = []
    const re = /⟦TWIST:(\d+)⟧/g
    let last = 0
    let m: RegExpExecArray | null
    const text = seed || ''
    let key = 0
    while ((m = re.exec(text)) !== null) {
      const idx = Number(m[1])
      parts.push(<span key={key++}>{text.slice(last, m.index)}</span>)
      const content = twists[idx - 1]?.content?.trim() || '…'
      parts.push(
        <span
          key={key++}
          className="mx-0.5 rounded bg-violet-200 px-1.5 py-0.5 font-medium text-violet-900"
        >
          TWIST {idx}: {content}
        </span>,
      )
      last = (m.index ?? 0) + m[0].length
    }
    parts.push(<span key={key++}>{text.slice(last)}</span>)
    const unplaced = twists
      .map((t, i) => ({ i: i + 1, c: t.content.trim() }))
      .filter(t => t.c && !text.includes(twistMarker(t.i)))
    return (
      <div className="rounded-md border border-violet-100 bg-white p-3 text-xs text-gray-700 whitespace-pre-wrap">
        <div className="font-medium text-violet-800 mb-1">Premise with twist placement</div>
        {parts}
        {unplaced.length > 0 && (
          <div className="mt-2 pt-2 border-t border-violet-100 space-y-1 text-violet-800">
            {unplaced.map(u => (
              <div key={u.i}>+ late binding twist {u.i}: {u.c}</div>
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto p-8">
      <h1 className="text-4xl font-bold mb-2 text-center">LensWrite</h1>
      <p className="text-center text-sm text-gray-500 mb-8">
        Premise + character notes → internal cast & plot → finished story
      </p>

      {/* Progress crumbs */}
      <div className="flex flex-wrap justify-center gap-2 text-xs mb-8">
        {[
          { k: 'seed', label: '1 Input' },
          { k: 'loading', label: '2 Generate' },
          { k: 'result', label: '3 Story' },
        ].map((s, i) => {
          const active = stage === s.k
          const done =
            (['loading', 'result'].includes(stage) && i === 0) ||
            (stage === 'result' && i === 1)
          return (
            <span
              key={s.k}
              className={`px-2 py-1 rounded-full border ${
                active
                  ? 'bg-blue-600 text-white border-blue-600'
                  : done
                    ? 'bg-blue-50 text-blue-700 border-blue-200'
                    : 'bg-gray-50 text-gray-400 border-gray-200'
              }`}
            >
              {s.label}
            </span>
          )
        })}
      </div>

      {/* ===== Input ===== */}
      {stage === 'seed' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 max-w-5xl mx-auto">
          <div className="space-y-4">
            {/* API connectivity banner — diagnoses Failed to fetch before generate */}
            <div
              className={`rounded-lg border px-3 py-2 text-xs ${
                apiHealth === 'ok'
                  ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
                  : apiHealth === 'checking'
                    ? 'border-gray-200 bg-gray-50 text-gray-600'
                    : 'border-red-200 bg-red-50 text-red-800'
              }`}
            >
              <div className="font-medium">
                API:{' '}
                <span className="font-mono break-all">{API_BASE || '(empty)'}</span>
                {apiHealth === 'checking' && ' · checking…'}
                {apiHealth === 'ok' && ' · connected'}
                {apiHealth === 'fail' && ' · unreachable'}
                {apiHealth === 'local-default' && ' · wrong for Vercel'}
              </div>
              {apiHealthDetail && <p className="mt-1 opacity-90">{apiHealthDetail}</p>}
            </div>

            <div>
              <label className="block text-sm font-medium mb-2">Story premise *</label>
              <textarea
                ref={seedTextareaRef}
                className="w-full h-36 p-4 border rounded-lg font-sans"
                placeholder="e.g. Two estranged siblings sort their mother’s belongings in the kitchen and must decide what to do with the house…"
                value={seed}
                onChange={e => setSeed(e.target.value)}
              />
              {/⟦TWIST(?::\d+)?⟧/.test(seed) && (
                <p className="mt-1.5 text-xs text-violet-700">
                  Twist markers in the premise mark where each reveal lands in the story.
                </p>
              )}
            </div>

            {/* Multi plot twists */}
            <div
              className={`rounded-lg border p-4 space-y-3 ${
                twists.length > 0
                  ? 'border-violet-300 bg-violet-50/60'
                  : 'border-gray-200 bg-gray-50/50'
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-gray-800">Plot twists</div>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Optional. Add one or more twists, write each reveal, and insert a marker
                    at the cursor in the premise so it lands at that narrative moment.
                  </p>
                </div>
                <div className="flex gap-2 shrink-0">
                  {twists.length > 0 && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={clearAllTwists}
                      className="text-gray-500"
                    >
                      Clear all
                    </Button>
                  )}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={addTwist}
                    disabled={twists.length >= 8}
                    className="border-violet-300 text-violet-800 hover:bg-violet-100"
                  >
                    + Add twist
                  </Button>
                </div>
              </div>

              {twists.length === 0 && (
                <p className="text-xs text-gray-500">No twists. Story follows the premise as written.</p>
              )}

              {twists.map((t, index) => (
                <div
                  key={t.id}
                  className="rounded-md border border-violet-200 bg-white p-3 space-y-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-violet-900">
                      Twist {index + 1}{' '}
                      <span className="font-normal text-gray-400">
                        {twistMarker(index + 1)}
                      </span>
                    </span>
                    <div className="flex gap-1">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => insertTwistMarkerAt(index)}
                        className="h-7 text-xs border-violet-200"
                      >
                        Insert here
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => removeTwist(t.id)}
                        className="h-7 text-xs text-red-500"
                      >
                        Remove
                      </Button>
                    </div>
                  </div>
                  <textarea
                    className="w-full h-20 p-2 border rounded-lg text-sm border-violet-100"
                    placeholder="e.g. The letter is in their own handwriting / The stranger is their child…"
                    value={t.content}
                    onChange={e => updateTwist(t.id, e.target.value)}
                  />
                  {seed.includes(twistMarker(index + 1)) ? (
                    <p className="text-[11px] text-violet-700">Marker placed in premise.</p>
                  ) : (
                    <p className="text-[11px] text-gray-500">
                      No marker yet — will bind as a late reveal if you leave it unplaced.
                    </p>
                  )}
                </div>
              ))}

              {twists.length > 0 && renderPremisePreview()}
            </div>

            <div className="rounded-lg border p-4 space-y-4 bg-gray-50/50">
              <div className="text-sm font-medium text-gray-800">Narrative preferences</div>
              <p className="text-xs text-gray-500">
                Thin premises are expanded internally. Description grain follows style.
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Style
                  </label>
                  <select
                    className="w-full p-2 border rounded-lg text-sm bg-white"
                    value={style}
                    onChange={e => setStyle(e.target.value)}
                  >
                    <option value="literary_realism">Literary realism</option>
                    <option value="minimalism">Minimalism</option>
                    <option value="psychological">Psychological realism</option>
                    <option value="poetic">Poetic / lyrical</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Point of view
                  </label>
                  <select
                    className="w-full p-2 border rounded-lg text-sm bg-white"
                    value={perspective}
                    onChange={e => setPerspective(e.target.value)}
                  >
                    <option value="third_limited">Third-person limited</option>
                    <option value="first_person">First person</option>
                  </select>
                </div>
                <div className="sm:col-span-2">
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    {perspective === 'first_person' ? 'Narrator' : 'Focal character'}
                  </label>
                  <select
                    className="w-full p-2 border rounded-lg text-sm bg-white"
                    value={Math.min(focalIndex, Math.max(0, focalOptions.length - 1))}
                    onChange={e => setFocalIndex(Number(e.target.value))}
                  >
                    {focalOptions.map(o => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">Character notes (optional)</h2>
              <Button onClick={addDraft} variant="outline" size="sm" type="button">
                + Add
              </Button>
            </div>
            <p className="text-xs text-gray-500">
              Leave blank to invent characters from the premise, or write a short sketch.
              Profiles are built internally — no separate card editor.
            </p>
            {drafts.map((d, index) => (
              <Card key={d.id}>
                <CardHeader className="flex flex-row items-center justify-between py-3">
                  <CardTitle className="text-base">Character {index + 1}</CardTitle>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-red-500"
                    type="button"
                    onClick={() => removeDraft(d.id)}
                  >
                    Clear
                  </Button>
                </CardHeader>
                <CardContent>
                  <textarea
                    className="w-full h-20 p-3 border rounded-lg text-sm"
                    placeholder="e.g. Elena, practical older sister, keeps lists, avoids scenes…"
                    value={d.prompt}
                    onChange={e => updateDraft(d.id, e.target.value)}
                  />
                </CardContent>
              </Card>
            ))}
            <Button
              className="w-full"
              onClick={handleGenerateStory}
              disabled={!seed.trim()}
            >
              Generate story
            </Button>
            {errorMsg && (
              <p className="text-sm text-red-600 text-center">{errorMsg}</p>
            )}
          </div>
        </div>
      )}

      {/* ===== loading ===== */}
      {stage === 'loading' && (
        <div className="text-center py-20">
          <p className="text-xl mb-4">Generating…</p>
          <p className="text-sm text-gray-500 mb-8">
            Internally: cast → outline → plot core → literary render (usually 1–3 min)
          </p>
          <div className="w-80 mx-auto">
            <div className="h-3 bg-gray-200 rounded-full overflow-hidden">
              <div
                className="h-3 bg-blue-600 rounded-full transition-all duration-500"
                style={{
                  width: `${Math.min(((iterationCount + 1) / maxIterations) * 100, 100)}%`,
                }}
              />
            </div>
            <div className="text-sm text-gray-500 mt-2">
              Render pass {iterationCount}/{maxIterations}
              {threadId ? ` · ${threadId.slice(0, 8)}` : ''}
            </div>
          </div>
        </div>
      )}

      {/* ===== result ===== */}
      {stage === 'result' && finalStory && (
        <div className="max-w-3xl mx-auto">
          <h2 className="text-3xl font-bold mb-6 text-center">{finalStory.title}</h2>
          <div className="prose prose-lg max-w-none whitespace-pre-wrap leading-relaxed">
            {finalStory.content}
          </div>
          <div className="mt-8 text-sm text-gray-500 text-center space-y-1">
            <div>
              Style: {STYLE_LABELS[style] || style}
            </div>
            <div>
              POV:{' '}
              {POV_LABELS[finalStory.chosen_perspective?.perspective_type] ||
                finalStory.chosen_perspective?.perspective_type}
              {finalStory.chosen_perspective?.focal_character
                ? ` · ${finalStory.chosen_perspective.focal_character}`
                : ''}
              {finalStory.word_count != null
                ? ` | ~${finalStory.word_count} words`
                : ''}
            </div>
            {resultTwists.length > 0 && (
              <div className="mt-2 text-violet-800 text-left max-w-xl mx-auto rounded-md border border-violet-200 bg-violet-50 px-3 py-2 space-y-1">
                <div className="font-medium">Your twists</div>
                <ul className="list-disc pl-5">
                  {resultTwists.map((t, i) => (
                    <li key={i}>{t}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {acceptance && (
            <div
              className={`mt-6 rounded-lg border p-4 text-sm ${
                acceptance.acceptance_ok
                  ? 'border-green-200 bg-green-50 text-green-900'
                  : 'border-amber-200 bg-amber-50 text-amber-950'
              }`}
            >
              <div className="font-medium mb-1">
                {acceptance.acceptance_ok ? 'Accepted' : 'Needs attention'} —{' '}
                {acceptance.acceptance_summary || 'No summary'}
              </div>
              <ul className="list-disc pl-5 space-y-1 text-left">
                {(acceptance.reasons && acceptance.reasons.length > 0
                  ? acceptance.reasons
                  : [
                      ...(acceptance.failure_reasons || []).map(m => ({
                        code: 'FAIL',
                        ok: false,
                        message: m,
                      })),
                      ...(acceptance.pass_reasons || []).map(m => ({
                        code: 'PASS',
                        ok: true,
                        message: m,
                      })),
                    ]
                ).map((r, i) => (
                  <li key={i} className={r.ok ? 'text-green-800' : 'text-amber-900'}>
                    {r.ok ? '✓' : '✗'} {r.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <Button onClick={resetAll} className="mt-8 w-full">
            Write another
          </Button>
        </div>
      )}
    </div>
  )
}
