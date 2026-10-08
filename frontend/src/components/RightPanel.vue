<template>
  <div class="right-panel" :style="{ '--panel-text-size': textSize + 'px' }">
    <!-- Tab bar -->
    <div class="rp-tabs">
      <button
        class="rp-tab"
        :class="{ active: activeTab === 'analysis' }"
        @click="$emit('update:activeTab', 'analysis')"
      >
        <span class="rp-tab-icon">&#x1F4CB;</span> {{ t('right.analysis') }}
      </button>
      <button
        class="rp-tab"
        :class="{ active: activeTab === 'chat' }"
        @click="$emit('update:activeTab', 'chat')"
      >
        <span class="rp-tab-icon">&#x1F4AC;</span> {{ t('right.chat') }}
      </button>
    </div>

    <div class="reading-controls" role="group" :aria-label="t('right.textSize')">
      <Type :size="17" aria-hidden="true" />
      <label for="right-panel-text-size">{{ t('right.textSize') }}</label>
      <button class="reading-button" :disabled="textSize <= MIN_PANEL_TEXT_SIZE"
        :title="t('right.smallerText')" :aria-label="t('right.smallerText')" @click="textSize--">
        <Minus :size="16" aria-hidden="true" />
      </button>
      <input id="right-panel-text-size" v-model.number="textSize" type="range"
        :min="MIN_PANEL_TEXT_SIZE" :max="MAX_PANEL_TEXT_SIZE" step="1"
        :aria-valuetext="`${textSize} px`" data-testid="right-panel-text-size" />
      <button class="reading-button" :disabled="textSize >= MAX_PANEL_TEXT_SIZE"
        :title="t('right.largerText')" :aria-label="t('right.largerText')" @click="textSize++">
        <Plus :size="16" aria-hidden="true" />
      </button>
      <output for="right-panel-text-size" class="reading-size">{{ textSize }}px</output>
      <button class="reading-button" :title="t('right.resetTextSize')" :aria-label="t('right.resetTextSize')"
        @click="textSize = DEFAULT_PANEL_TEXT_SIZE">
        <RotateCcw :size="15" aria-hidden="true" />
      </button>
    </div>

    <!-- Analysis Tab：滚动式手术进程叙事（替代以前的"当前窗口详情"）-->
    <div v-show="activeTab === 'analysis'" class="tab-analysis">
      <div v-if="chapters.length > 0" ref="analysisLayoutRef" class="an-content">
        <div
          class="an-latest-pane"
          :class="{ 'without-history': historyChapters.length === 0 }"
          :style="historyChapters.length > 0 ? { flexBasis: latestPaneHeight + 'px' } : undefined"
        >
          <div class="narrative-header">
            <span class="narrative-title">{{ t('right.surgicalProgress') }}</span>
            <span class="narrative-meta">
              {{ t('app.windowCount', { count: totalWindows }) }} · {{ t('right.updatedByWindow') }}
            </span>
          </div>

          <div
            v-if="bleedingStatus"
            class="bleeding-status"
            :class="bleedingStatus.status"
          >
            <div class="bleeding-status-head">
              <span class="bleeding-status-title">{{ bleedingStatus.title }}</span>
              <span class="bleeding-status-time">
                {{ windowLabel(bleedingStatus.windowId + 1) }} · {{ formatTime(bleedingStatus.startTime) }} – {{ formatTime(bleedingStatus.endTime) }}
              </span>
            </div>
            <div class="bleeding-status-text">{{ bleedingStatus.text }}</div>
          </div>

          <div v-if="topChapters.length > 0" class="latest-chapters">
            <div
              v-for="(ch, index) in topChapters"
              :key="`top-${ch.key}`"
              class="latest-chapter"
              :class="{ secondary: index === 1 }"
              role="button"
              tabindex="0"
              @click="seekChapter(ch)"
              @keydown.enter.prevent="seekChapter(ch)"
              @keydown.space.prevent="seekChapter(ch)"
            >
              <div class="latest-label">{{ index === 0 ? primaryChapterLabel : t('right.previousWindow') }}</div>
              <div class="latest-head">
                <span class="chapter-time">
                  {{ formatTime(ch.startTime) }} – {{ formatTime(ch.endTime) }}
                </span>
                <span class="chapter-count">{{ windowLabel(ch.windows[0].window_id + 1) }}</span>
              </div>
              <div class="chapter-phase">{{ ch.phaseLabel }}</div>
              <div class="latest-text">{{ ch.mergedSummary }}</div>
            </div>
          </div>
        </div>

        <div
          v-if="historyChapters.length > 0"
          class="analysis-history-resize-handle"
          :title="t('right.resizeHistoryTitle')"
          @pointerdown="startLatestPaneResize"
          @dblclick="resetLatestPaneHeight"
        ></div>

        <div v-if="historyChapters.length > 0" class="an-history-pane">
          <div class="history-title">
            {{ t('right.historyWindow') }}
          </div>

          <!-- 旧窗口倒序显示：最新窗口固定在上方，避免实时结果滚到列表底部 -->
          <div
            v-for="ch in historyChapters"
            :key="ch.key"
            class="chapter"
            :class="{ active: ch.containsCurrent }"
            role="button"
            tabindex="0"
            @click="seekChapter(ch)"
            @keydown.enter.prevent="seekChapter(ch)"
            @keydown.space.prevent="seekChapter(ch)"
          >
            <div class="chapter-head">
              <span class="chapter-time">
                {{ formatTime(ch.startTime) }} – {{ formatTime(ch.endTime) }}
              </span>
              <span class="chapter-count">{{ ch.windowLabel }}</span>
            </div>
            <div class="chapter-phase">{{ ch.phaseLabel }}</div>
            <div class="chapter-text">{{ ch.mergedSummary }}</div>
          </div>
        </div>
      </div>

      <!-- Empty state -->
      <div v-else class="an-empty">
        <div class="an-empty-icon">&#x1F4CA;</div>
        <div class="an-empty-text">{{ t('right.noAnalysis') }}</div>
        <div class="an-empty-hint">{{ t('right.noAnalysisHint') }}</div>
      </div>

      <!-- Actions：基于当前显示的章节（通常是正在播放的那段）-->
      <div class="an-actions" :class="{ disabled: !currentChapter }">
        <button
          class="an-btn primary"
          :disabled="!currentChapter"
          @click="$emit('tts', currentChapterAsSummary)"
        >
          &#128264; {{ t('right.readSegment') }}
        </button>
        <button class="an-btn" :disabled="!currentChapter" @click="copyChapter">
          &#128203; {{ t('right.copy') }}
        </button>
      </div>
    </div>

    <!-- Chat Tab -->
    <div v-show="activeTab === 'chat'" class="tab-chat">
      <ChatPanel
        :sessionId="sessionId"
        :summaries="summaries"
        :visible="activeTab === 'chat'"
        @seekToWindow="(wid) => $emit('seekToWindow', wid)"
        @message="(msg) => $emit('chatMessage', msg)"
        @review="$emit('review', $event)"
      />
    </div>
  </div>
</template>

<script setup>
import { formatWindowNarrative, phaseLabel as formatPhaseLabel, mergeWindowNarratives, isReviewedWindow } from '../utils/windowNarrative.js'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Type, Minus, Plus, RotateCcw } from '@lucide/vue'
import { DEFAULT_PANEL_TEXT_SIZE, MIN_PANEL_TEXT_SIZE, MAX_PANEL_TEXT_SIZE,
  loadPanelTextSize, savePanelTextSize } from '../utils/panelTextSize.js'
import ChatPanel from './ChatPanel.vue'
import { useI18n } from '@/i18n'

const { language, t, windowLabel } = useI18n()
const textSize = ref(loadPanelTextSize())
watch(textSize, value => savePanelTextSize(value))

const props = defineProps({
  activeTab: { type: String, default: 'analysis' },
  summaries: { type: Array, default: () => [] },
  currentSummary: { type: Object, default: null },
  selectedWindowId: { type: Number, default: -1 },
  sessionId: { type: String, default: '' },
  isProcessing: { type: Boolean, default: false },
})

const emit = defineEmits([
  'update:activeTab', 'tts', 'seekToWindow', 'chatMessage', 'review'
])

const LATEST_PANE_STORAGE_KEY = 'surg_right_latest_pane_height_v1'
const analysisLayoutRef = ref(null)
const defaultLatestPaneHeight = () => Math.min(620, Math.max(320, Math.round(window.innerHeight * 0.48)))
const latestPaneHeight = ref(Number(localStorage.getItem(LATEST_PANE_STORAGE_KEY)) || defaultLatestPaneHeight())
const clampLatestPaneHeight = (value) => {
  const available = analysisLayoutRef.value?.clientHeight || Math.max(560, window.innerHeight - 180)
  return Math.min(Math.max(220, available - 210), Math.max(220, value))
}

const startLatestPaneResize = (event) => {
  event.preventDefault()
  event.currentTarget.setPointerCapture?.(event.pointerId)
  const startY = event.clientY
  const startHeight = latestPaneHeight.value
  document.body.classList.add('is-resizing-analysis-history')

  const onMove = (e) => {
    latestPaneHeight.value = clampLatestPaneHeight(startHeight + (e.clientY - startY))
  }
  const onUp = () => {
    latestPaneHeight.value = clampLatestPaneHeight(latestPaneHeight.value)
    localStorage.setItem(LATEST_PANE_STORAGE_KEY, String(latestPaneHeight.value))
    document.body.classList.remove('is-resizing-analysis-history')
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }

  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp, { once: true })
}

const resetLatestPaneHeight = () => {
  latestPaneHeight.value = clampLatestPaneHeight(defaultLatestPaneHeight())
  localStorage.setItem(LATEST_PANE_STORAGE_KEY, String(latestPaneHeight.value))
}

const handlePanelViewportResize = () => {
  latestPaneHeight.value = clampLatestPaneHeight(latestPaneHeight.value)
}

onMounted(() => window.addEventListener('resize', handlePanelViewportResize))
onUnmounted(() => {
  window.removeEventListener('resize', handlePanelViewportResize)
  document.body.classList.remove('is-resizing-analysis-history')
})

const formatTime = (seconds) => {
  if (seconds == null) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

const phaseLabel = phase => formatPhaseLabel(phase, language.value)

function bigrams(text) {
  const value = String(text || '').replace(/\s+/g, '')
  return new Set(Array.from({ length: Math.max(0, value.length - 1) }, (_, i) => value.slice(i, i + 2)))
}
function jaccard(a, b) {
  if (!a.size || !b.size) return 0
  const intersection = [...a].filter(value => b.has(value)).length
  return intersection / (a.size + b.size - intersection)
}

function hasSevereBleedingText(text) {
  const t = String(text || '').toLowerCase()
  if (/(无(?:明显)?出血|未见(?:明显)?出血|没有(?:明显)?出血|无活动性出血|未见活动性出血|no bleeding|without bleeding)/i.test(t)) return false
  return /(大量(?:活动性)?出血|明显(?:活动性)?出血|持续(?:活动性)?出血|喷涌出血|喷射性出血|涌血|明确出血源|影响视野的持续渗血|heavy bleeding|massive bleeding|profuse bleeding|significant bleeding)/i.test(t)
}

function hasBleedingResolvedText(text) {
  const t = String(text || '').toLowerCase()
  return /(出血(?:已经|已)?(?:停止|控制|解决)|已(?:完成)?止血|止血(?:完成|成功|有效)|凝血后(?:未见|无)活动性出血|未见活动性出血|无活动性出血|bleeding (?:stopped|controlled|resolved)|hemostasis achieved)/i.test(t)
}

function bleedingText(text, fallback) {
  const cleaned = formatWindowNarrative(text)
  return cleaned || fallback
}

// Only finalized, adjacent windows are eligible for conservative merging.

const latestWindowId = computed(() => {
  return (props.summaries || []).reduce((latest, s) => {
    const id = Number(s?.window_id)
    return Number.isFinite(id) && id > latest ? id : latest
  }, -1)
})

const playbackWindowId = computed(() => {
  const id = Number(props.currentSummary?.window_id)
  return Number.isFinite(id) && id >= 0 ? id : -1
})

const activeWindowId = computed(() => {
  const selectedId = Number(props.selectedWindowId)
  if (Number.isFinite(selectedId) && selectedId >= 0) return selectedId
  if (playbackWindowId.value >= 0) return playbackWindowId.value
  return latestWindowId.value
})

const chapters = computed(() => {
  const sorted = [...(props.summaries || [])].sort((a, b) => a.window_id - b.window_id)
  const currentId = activeWindowId.value
  return sorted.map((s) => {
    const phase = s.phase || 'Unknown'
    const cleaned = formatWindowNarrative(s.summary)
    return {
      key: `window-${s.window_id}-${s.stage || 0}`,
      phase,
      phaseLabel: phaseLabel(phase),
      startTime: s.start_time,
      endTime: s.end_time,
      windows: [s],
      windowLabel: windowLabel(s.window_id + 1),
      mergedSummary: cleaned || t('right.emptyWindowResult'),
      containsCurrent: s.window_id === currentId,
    }
  })
})

const totalWindows = computed(() => (props.summaries || []).length)

const latestChapter = computed(() => {
  if (!chapters.value.length) return null
  return topChapters.value[0] || chapters.value.reduce((latest, ch) => {
    const latestId = latest?.windows?.[0]?.window_id ?? -1
    const currentId = ch?.windows?.[0]?.window_id ?? -1
    return currentId > latestId ? ch : latest
  }, null)
})

const topChapters = computed(() => {
  if (!chapters.value.length) return []
  const currentId = activeWindowId.value
  const current = chapters.value.find(ch => (ch.windows?.[0]?.window_id ?? -1) === currentId)
    || chapters.value.reduce((latest, ch) => {
      const latestId = latest?.windows?.[0]?.window_id ?? -1
      const chId = ch?.windows?.[0]?.window_id ?? -1
      return chId > latestId ? ch : latest
    }, null)
  if (!current) return []

  const currentWindowId = current.windows?.[0]?.window_id ?? -1
  const previous = [...chapters.value]
    .filter(ch => (ch.windows?.[0]?.window_id ?? -1) < currentWindowId)
    .sort((a, b) => (b.windows?.[0]?.window_id ?? -1) - (a.windows?.[0]?.window_id ?? -1))[0]
  return [current, previous].filter(Boolean)
})

const primaryChapterLabel = computed(() => {
  if (Number(props.selectedWindowId) >= 0) return t('right.selectedWindow')
  if (playbackWindowId.value >= 0 && playbackWindowId.value < latestWindowId.value) {
    return t('right.currentPlaybackWindow')
  }
  return t('right.latestWindow')
})

const historyChapters = computed(() => {
  const topIds = new Set(topChapters.value.map(ch => ch.windows?.[0]?.window_id))
  const history = [...chapters.value]
    .filter(ch => !topIds.has(ch.windows?.[0]?.window_id))
    .sort((a, b) => (b.windows?.[0]?.window_id ?? 0) - (a.windows?.[0]?.window_id ?? 0))
  const groups = []
  for (const ch of history) {
    const last = groups[groups.length - 1]
    const lastMinId = last ? Math.min(...last.windows.map(w => w.window_id ?? -1)) : -1
    const curId = ch.windows?.[0]?.window_id ?? -1
    const contiguous = last && curId === lastMinId - 1
    const sim = last ? jaccard(bigrams(last.mergedSummary), bigrams(ch.mergedSummary)) : 0
    const samePhase = last && last.phase === ch.phase

    const chronological = last ? [...last.windows, ...ch.windows].sort((a, b) => a.window_id - b.window_id) : []
    const merged = last && contiguous && samePhase && sim >= 0.65
      && chronological.length <= 6 && chronological.every(w => isReviewedWindow(w)
        && w.summary !== t('translation.pending') && w.summary !== t('translation.failed'))
      ? mergeWindowNarratives(chronological) : null
    if (merged) {
      last.windows = [...last.windows, ...ch.windows]
      last.startTime = Math.min(last.startTime, ch.startTime)
      last.endTime = Math.max(last.endTime, ch.endTime)
      last.mergedSummary = merged
      const ids = last.windows.map(w => (w.window_id ?? 0) + 1)
      last.windowLabel = ids.length > 1
        ? windowLabel(Math.min(...ids), Math.max(...ids))
        : windowLabel(ids[0])
      last.key = `history-${Math.min(...ids)}-${Math.max(...ids)}-${last.windows.length}`
      last.containsCurrent = last.windows.some(w => w.window_id === activeWindowId.value)
    } else {
      groups.push({
        ...ch,
        windowLabel: windowLabel((ch.windows?.[0]?.window_id ?? 0) + 1),
        key: `history-${ch.windows?.[0]?.window_id ?? 0}-${ch.stage || 0}`,
      })
    }
  }
  return groups
})

const bleedingStatus = computed(() => {
  const cutoffWindowId = activeWindowId.value
  const sorted = [...(props.summaries || [])]
    .filter(w => cutoffWindowId < 0 || (w.window_id ?? 0) <= cutoffWindowId)
    .sort((a, b) => (a.window_id ?? 0) - (b.window_id ?? 0))
  let lastActive = null
  let lastResolved = null

  for (const w of sorted) {
    if (hasSevereBleedingText(w.original_summary || w.summary)) {
      lastActive = w
      lastResolved = null
    } else if (lastActive && hasBleedingResolvedText(w.original_summary || w.summary)) {
      lastResolved = w
    }
  }

  if (lastResolved) {
    return {
      status: 'resolved',
      title: t('right.bleedingControlled'),
      windowId: lastResolved.window_id,
      startTime: lastResolved.start_time,
      endTime: lastResolved.end_time,
      text: bleedingText(lastResolved.summary, t('right.bleedingControlledFallback'))
    }
  }

  if (lastActive) {
    return {
      status: 'active',
      title: t('right.activeBleeding'),
      windowId: lastActive.window_id,
      startTime: lastActive.start_time,
      endTime: lastActive.end_time,
      text: bleedingText(lastActive.summary, t('right.activeBleedingFallback'))
    }
  }

  return null
})

const currentChapter = computed(() => topChapters.value[0] || latestChapter.value || chapters.value[chapters.value.length - 1])

const currentChapterAsSummary = computed(() => {
  const c = currentChapter.value
  if (!c) return null
  return {
    summary: c.mergedSummary,
    window_id: c.windows[0].window_id,
    start_time: c.startTime,
    end_time: c.endTime,
  }
})

const copyChapter = () => {
  const c = currentChapter.value
  if (c?.mergedSummary) {
    navigator.clipboard.writeText(c.mergedSummary)
  }
}

const seekChapter = (chapter) => {
  const windows = chapter?.windows || []
  if (!windows.length) return
  const target = [...windows].sort((a, b) => (b.window_id ?? 0) - (a.window_id ?? 0))[0]
  emit('seekToWindow', target.window_id)
}
</script>

<style scoped>
.right-panel {
  background: var(--bg-secondary);
  border-left: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  width: 420px;
  flex-shrink: 0;
  min-width: 0;
  --panel-chat-size: calc(var(--panel-text-size) + 4px);
  --panel-detail-size: calc(var(--panel-text-size) - 2px);
  --panel-meta-size: calc(var(--panel-text-size) - 4px);
}

.reading-controls {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 12px;
  min-height: 38px;
  flex: 0 0 auto;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  font-size: 13px;
}
.reading-controls label { white-space: nowrap; }
.reading-controls input { flex: 1; width: 60px; min-width: 40px; accent-color: var(--accent-primary); cursor: pointer; }
.reading-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}
.reading-button:hover:not(:disabled) { background: var(--bg-elevated); color: var(--text-primary); }
.reading-button:disabled { opacity: .35; cursor: default; }
.reading-button:focus-visible, .reading-controls input:focus-visible { outline: 2px solid var(--accent-primary); outline-offset: 2px; }
.reading-size { flex: 0 0 38px; text-align: center; font-variant-numeric: tabular-nums; }

.rp-tabs {
  display: flex;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.rp-tab {
  flex: 1;
  padding: 10px 0;
  text-align: center;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-tertiary);
  cursor: pointer;
  border: none;
  background: transparent;
  border-bottom: 2px solid transparent;
  transition: all 0.15s;
  font-family: var(--font-display);
}

.rp-tab:hover { color: var(--text-secondary); }

.rp-tab.active {
  color: var(--accent-primary);
  border-bottom-color: var(--accent-primary);
}

.rp-tab-icon { margin-right: 4px; }

/* Analysis tab */
.tab-analysis {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.an-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.an-latest-pane,
.an-history-pane {
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.an-latest-pane {
  flex: 0 0 auto;
  padding: 14px 16px 9px;
}

.an-latest-pane.without-history {
  flex: 1 1 auto;
}

.an-history-pane {
  flex: 1 1 auto;
  padding: 7px 16px 14px;
}

.an-latest-pane::-webkit-scrollbar,
.an-history-pane::-webkit-scrollbar { width: 5px; }
.an-latest-pane::-webkit-scrollbar-track,
.an-history-pane::-webkit-scrollbar-track { background: transparent; }
.an-latest-pane::-webkit-scrollbar-thumb,
.an-history-pane::-webkit-scrollbar-thumb { background: var(--bg-elevated); border-radius: 3px; }

.analysis-history-resize-handle {
  position: relative;
  flex: 0 0 14px;
  height: 14px;
  cursor: row-resize;
  touch-action: none;
  border-top: 1px solid var(--border-subtle);
  border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-secondary);
  transition: background 0.12s ease;
}

.analysis-history-resize-handle::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  width: 68px;
  height: 10px;
  border: 1px solid var(--border-medium);
  border-radius: 5px;
  background-color: var(--bg-elevated);
  background-image: radial-gradient(circle, var(--text-tertiary) 1.3px, transparent 1.5px);
  background-size: 8px 8px;
  transform: translate(-50%, -50%);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.28);
}

.analysis-history-resize-handle:hover {
  background: rgba(240, 160, 48, 0.08);
}

.analysis-history-resize-handle:hover::before {
  border-color: var(--accent-primary);
  background-color: rgba(240, 160, 48, 0.18);
  background-image: radial-gradient(circle, var(--accent-primary) 1.3px, transparent 1.5px);
}

:global(body.is-resizing-analysis-history) {
  cursor: row-resize !important;
  user-select: none;
}

:global(body.is-resizing-analysis-history *) {
  cursor: row-resize !important;
}

.an-window-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.an-window-num {
  font-size: 22px;
  font-weight: 700;
  font-family: var(--font-mono);
  color: var(--accent-primary);
}

.an-window-tag {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--bg-primary);
  background: var(--accent-primary);
  padding: 2px 7px;
  border-radius: 4px;
}

.an-window-tag.selected {
  background: var(--text-tertiary);
}

.an-time {
  font-size: 14px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  margin-bottom: 8px;
}

.an-phase {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  background: var(--bg-tertiary);
  border-radius: 100px;
  font-size: 14px;
  color: var(--accent-primary);
  margin-bottom: 10px;
  border: 1px solid var(--border-subtle);
}

.an-phase-dot {
  width: 5px;
  height: 5px;
  border-radius: 2px;
  background: var(--accent-primary);
}

.an-full-text {
  font-size: 17px;
  line-height: 1.55;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* ===== 手术进程叙事 ===== */
.narrative-header {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-subtle);
}
.narrative-title {
  font-size: var(--panel-text-size);
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0;
}
.narrative-meta {
  font-size: var(--panel-meta-size);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

.bleeding-status {
  margin: 0 0 18px;
  padding: 12px 14px;
  border-radius: 8px;
  border: 2px solid transparent;
}

.bleeding-status.active {
  background: rgba(255, 72, 72, 0.16);
  border-color: rgba(255, 72, 72, 0.8);
  box-shadow: inset 5px 0 0 #ff4d4d;
}

.bleeding-status.resolved {
  background: rgba(0, 190, 120, 0.15);
  border-color: rgba(0, 190, 120, 0.75);
  box-shadow: inset 5px 0 0 #20c878;
}

.bleeding-status-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.bleeding-status-title {
  font-size: calc(var(--panel-text-size) + 2px);
  font-weight: 900;
}

.bleeding-status.active .bleeding-status-title {
  color: #ff6868;
}

.bleeding-status.resolved .bleeding-status-title {
  color: #37d98b;
}

.bleeding-status-time {
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  font-size: var(--panel-meta-size);
  white-space: nowrap;
}

.bleeding-status-text {
  color: var(--text-primary);
  font-size: var(--panel-text-size);
  line-height: 1.5;
}

.latest-chapters {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin: 0 0 22px;
}

.latest-chapter {
  margin: 0 0 22px;
  padding: 14px 16px 16px;
  background: rgba(245, 158, 11, 0.12);
  border: 2px solid rgba(245, 158, 11, 0.65);
  border-left: 7px solid var(--accent-primary);
  border-radius: 8px;
  cursor: pointer;
}

.latest-chapters .latest-chapter {
  margin: 0;
}

.latest-chapter.secondary {
  background: rgba(245, 158, 11, 0.08);
  border-color: rgba(245, 158, 11, 0.42);
  border-left-width: 5px;
}

.latest-label {
  display: inline-flex;
  align-items: center;
  margin-bottom: 12px;
  padding: 4px 10px;
  border-radius: 4px;
  background: var(--accent-primary);
  color: var(--bg-primary);
  font-size: var(--panel-meta-size);
  font-weight: 800;
}

.latest-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.latest-text {
  font-size: calc(var(--panel-text-size) + 2px);
  line-height: 1.5;
  font-weight: 650;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.latest-chapter.secondary .latest-text {
  font-size: var(--panel-text-size);
}

.history-title {
  margin: 8px 0 6px;
  color: var(--text-tertiary);
  font-size: var(--panel-detail-size);
  font-weight: 700;
}

.chapter {
  padding: 16px 0 18px;
  border-bottom: 1px dashed var(--border-subtle);
  cursor: pointer;
}
.chapter:last-of-type { border-bottom: none; }
.chapter.active .chapter-phase {
  color: var(--accent-primary);
}
.chapter.active {
  background: transparent;
  margin: 0;
  padding: 16px 0 18px;
  border-left: none;
}
.chapter-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.chapter-phase {
  font-size: var(--panel-meta-size);
  font-weight: 600;
  color: var(--text-secondary);
  margin: 4px 0 7px;
}
.chapter-time {
  font-size: var(--panel-meta-size);
  font-family: var(--font-mono);
  color: var(--text-tertiary);
}
.chapter-count {
  font-size: var(--panel-meta-size);
  padding: 3px 9px;
  border-radius: 3px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-family: var(--font-mono);
}
.chapter-text {
  font-size: var(--panel-text-size);
  line-height: 1.55;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* Actions */
.an-actions {
  padding: 8px 14px;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  gap: 6px;
  flex-shrink: 0;
  min-height: 52px;
}

.an-btn {
  flex: 1;
  padding: 8px 0;
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  font-size: var(--panel-meta-size);
  cursor: pointer;
  text-align: center;
  transition: border-color 0.15s, color 0.15s, background-color 0.15s;
  font-family: var(--font-display);
}

.an-btn:disabled {
  opacity: 0.45;
  cursor: default;
}

.an-btn:disabled:hover {
  border-color: var(--border-subtle);
  color: var(--text-secondary);
  background: var(--bg-tertiary);
}

.an-btn:hover {
  border-color: var(--accent-primary);
  color: var(--accent-primary);
  background: var(--accent-glow);
}

.an-btn.primary {
  background: var(--accent-primary);
  border-color: var(--accent-primary);
  color: var(--bg-primary);
  font-weight: 500;
}

.an-btn.primary:hover { filter: brightness(1.1); }

/* Empty state */
.an-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.an-empty-icon { font-size: 40px; opacity: 0.3; }
.an-empty-text { font-size: calc(var(--panel-text-size) + 2px); color: var(--text-secondary); }
.an-empty-hint { font-size: var(--panel-detail-size); color: var(--text-tertiary); text-align: center; padding: 0 12px; }

/* Chat tab */
.tab-chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

</style>
