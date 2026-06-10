<template>
  <div class="right-panel">
    <!-- Tab bar -->
    <div class="rp-tabs">
      <button
        class="rp-tab"
        :class="{ active: activeTab === 'analysis' }"
        @click="$emit('update:activeTab', 'analysis')"
      >
        <span class="rp-tab-icon">&#x1F4CB;</span> Analysis
      </button>
      <button
        class="rp-tab"
        :class="{ active: activeTab === 'chat' }"
        @click="$emit('update:activeTab', 'chat')"
      >
        <span class="rp-tab-icon">&#x1F4AC;</span> Chat
      </button>
    </div>

    <!-- Analysis Tab：滚动式手术进程叙事（替代以前的"当前窗口详情"）-->
    <div v-show="activeTab === 'analysis'" class="tab-analysis">
      <div v-if="chapters.length > 0" class="an-content">
        <div class="narrative-header">
          <span class="narrative-title">手术进程</span>
          <span class="narrative-meta">
            {{ totalWindows }} 个窗口 · {{ chapters.length }} 个阶段段
          </span>
        </div>

        <!-- 相邻同阶段合并后的章节 -->
        <div
          v-for="ch in chapters"
          :key="ch.key"
          class="chapter"
          :class="{ active: ch.containsCurrent }"
        >
          <!-- phase 标签已去掉：文字里本来就会提阶段，避免重复 -->
          <div class="chapter-head">
            <span class="chapter-time">
              {{ formatTime(ch.startTime) }} – {{ formatTime(ch.endTime) }}
            </span>
            <span class="chapter-count">{{ ch.windows.length }} 个窗口</span>
            <span
              v-if="ch.hasQuickOnly"
              class="chapter-stage-dot quick"
              title="此段仍在精修中"
            ></span>
          </div>
          <div class="chapter-text">{{ ch.mergedSummary }}</div>
        </div>

        <!-- 可折叠：当前播放窗口的专家/CoT 详细信息 -->
        <details v-if="displaySummary" class="window-detail">
          <summary>
            <span>当前窗口 #{{ displaySummary.window_id + 1 }} 的模型判断细节</span>
            <span v-if="displaySummary.stage === 1" class="an-stage-badge quick">⚡ Quick</span>
            <span v-else-if="displaySummary.stage === 2" class="an-stage-badge refined">✓ Refined</span>
          </summary>

          <div v-if="hasExperts" class="an-experts">
            <div class="an-section-title">专家判断</div>
            <div v-if="experts.phase && experts.phase.label" class="an-expert-row">
              <span class="an-expert-tag phase">Phase</span>
              <span class="an-expert-val">{{ experts.phase.label }}</span>
              <span class="an-expert-conf">{{ (experts.phase.confidence * 100).toFixed(0) }}%</span>
            </div>
            <div v-if="experts.triplet && experts.triplet.triplet && experts.triplet.triplet.length" class="an-expert-row">
              <span class="an-expert-tag triplet">Triplet</span>
              <span class="an-expert-val">{{ topTriplet }}</span>
            </div>
            <div v-if="yoloTools.length" class="an-expert-row">
              <span class="an-expert-tag yolo">YOLO</span>
              <span class="an-expert-val">{{ yoloTools.join(', ') }}</span>
            </div>
          </div>

          <div v-if="displaySummary.surgr1_reasoning" class="an-cot">
            <button class="an-cot-toggle" @click.prevent="cotOpen = !cotOpen">
              <span>{{ cotOpen ? '▾' : '▸' }}</span>
              <span>SurgR1 思维链</span>
            </button>
            <pre v-if="cotOpen" class="an-cot-body">{{ displaySummary.surgr1_reasoning }}</pre>
          </div>

          <div v-if="displaySummary.stage === 2 && displaySummary.stage1_summary" class="an-stage1-block">
            <div class="an-stage1-label">⚡ Quick 初稿</div>
            <div class="an-stage1-body">{{ displaySummary.stage1_summary }}</div>
          </div>
        </details>
      </div>

      <!-- Empty state -->
      <div v-else class="an-empty">
        <div class="an-empty-icon">&#x1F4CA;</div>
        <div class="an-empty-text">暂无分析</div>
        <div class="an-empty-hint">分析开始后，手术进程将在此滚动展开</div>
      </div>

      <!-- Actions：基于当前显示的章节（通常是正在播放的那段）-->
      <div v-if="currentChapter" class="an-actions">
        <button class="an-btn primary" @click="$emit('tts', currentChapterAsSummary)">
          &#128264; 朗读本段
        </button>
        <button class="an-btn" @click="copyChapter">
          &#128203; 复制
        </button>
        <button v-if="displaySummary" class="an-btn" @click="$emit('sam3', displaySummary)">
          &#127917; SAM3
        </button>
      </div>
    </div>

    <!-- Chat Tab -->
    <div v-show="activeTab === 'chat'" class="tab-chat">
      <ChatPanel
        :sessionId="sessionId"
        :summaries="summaries"
        @seekToWindow="(wid) => $emit('seekToWindow', wid)"
        @message="(msg) => $emit('chatMessage', msg)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import ChatPanel from './ChatPanel.vue'

const props = defineProps({
  activeTab: { type: String, default: 'analysis' },
  summaries: { type: Array, default: () => [] },
  currentSummary: { type: Object, default: null },
  selectedWindowId: { type: Number, default: -1 },
  sessionId: { type: String, default: '' },
  isProcessing: { type: Boolean, default: false },
})

const emit = defineEmits([
  'update:activeTab', 'tts', 'sam3', 'seekToWindow', 'chatMessage'
])

const cotOpen = ref(false)

const displaySummary = computed(() => {
  if (props.selectedWindowId >= 0) {
    return props.summaries.find(s => s.window_id === props.selectedWindowId) || props.currentSummary
  }
  return props.currentSummary
})

const experts = computed(() => displaySummary.value?.experts || {})
const hasExperts = computed(() => {
  const e = experts.value
  if (!e) return false
  const hasPhase = e.phase && e.phase.label
  const hasTriplet = e.triplet && e.triplet.triplet && e.triplet.triplet.length > 0
  const hasYolo = e.yolo && e.yolo.tools && e.yolo.tools.length > 0
  return hasPhase || hasTriplet || hasYolo
})
const topTriplet = computed(() => {
  const t = experts.value?.triplet?.triplet?.[0]
  if (!t) return ''
  return `${t.label} (${(t.confidence * 100).toFixed(0)}%)`
})
const yoloTools = computed(() => {
  const tools = experts.value?.yolo?.tools || []
  return tools.slice(0, 6).map(t => `${t.label} ×${t.frames_seen}`)
})

const formatTime = (seconds) => {
  if (seconds == null) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

// ---- 章节合并：相邻同 phase 的窗口合并为一段叙事，句子级去重 ----
const PHASE_CN = {
  Preparation: '准备阶段',
  CalotTriangleDissection: '肝胆三角解剖',
  ClippingCutting: '夹闭切断',
  GallbladderDissection: '胆囊分离',
  GallbladderPackaging: '胆囊装袋',
  GallbladderRetraction: '胆囊牵拉',
  CleaningCoagulation: '清洁凝血',
}

// 字符 bigram Jaccard 相似度，用来判断新内容是否跟已有句子近似重复
function bigrams(s) {
  const t = String(s || '').replace(/\s+/g, '')
  const out = new Set()
  for (let i = 0; i < t.length - 1; i++) out.add(t.slice(i, i + 2))
  return out
}
function jaccard(a, b) {
  if (!a.size || !b.size) return 0
  let inter = 0
  for (const x of a) if (b.has(x)) inter++
  return inter / (a.size + b.size - inter)
}

function stripPhaseHeader(t) {
  return String(t || '').replace(/^【[^】]*】\s*/, '').trim()
}

// 章节文本只保留"关键节点"——窗口级 + 句子级双层去重，并对总长度设上限。
// 目标：用户看到的叙事像手术记录，而不是同一句话的几十次复述。
const CHAPTER_MAX_CHARS = 600
const SIM_SKIP_THRESHOLD = 0.45   // 新窗口与已有合并文本相似度≥45% 就整窗口跳过
const SENT_FINGERPRINT_LEN = 12    // 句子指纹长度：越短去重越狠
const BORING_TOKENS = ['器械', '操作', '动作', '手术', '进行中', '继续', '正在']

function sentenceFingerprint(s) {
  // 去空白 + 去语气词后前 N 字
  return s.replace(/\s+/g, '')
          .replace(/[，,]/g, '')
          .slice(0, SENT_FINGERPRINT_LEN)
}

function isRedundantSentence(s, existingKeys) {
  const key = sentenceFingerprint(s)
  if (!key) return true
  if (existingKeys.has(key)) return true
  // 近似变体：只改了尾部语气词/形容词 → 前 8 字一致也算重复
  const shortKey = key.slice(0, 8)
  for (const ek of existingKeys) {
    if (ek.slice(0, 8) === shortKey) return true
  }
  return false
}

function mergeIncrementally(windows) {
  let text = ''
  let bigramsAcc = new Set()
  const existingSentKeys = new Set()

  for (const w of windows) {
    const cleaned = stripPhaseHeader(w.summary)
    if (!cleaned) continue

    // 首条直接进
    if (!text) {
      // 首条也做句子级控长
      const sents = cleaned.split(/(?<=[。；！？!?;])/).map(s => s.trim()).filter(Boolean)
      for (const s of sents) {
        const key = sentenceFingerprint(s)
        if (key && !existingSentKeys.has(key)) {
          existingSentKeys.add(key)
          text += s
          if (text.length >= CHAPTER_MAX_CHARS) break
        }
      }
      bigramsAcc = bigrams(text)
      continue
    }

    // 到达长度上限后只允许明确新增信息（window_id 边界事件，如阶段转变），否则不再追加
    if (text.length >= CHAPTER_MAX_CHARS) continue

    const newBg = bigrams(cleaned)
    const sim = jaccard(bigramsAcc, newBg)
    if (sim >= SIM_SKIP_THRESHOLD) continue  // 整窗口相似 → 跳过

    const addSents = cleaned.split(/(?<=[。；！？!?;])/).map(s => s.trim())
      .filter(s => s && !isRedundantSentence(s, existingSentKeys))

    // 只取确实带了新信息量的句子
    for (const s of addSents) {
      existingSentKeys.add(sentenceFingerprint(s))
      text += s
      if (text.length >= CHAPTER_MAX_CHARS) break
    }
    if (addSents.length) bigramsAcc = bigrams(text)
  }
  return text
}

// 章节 mergedSummary 缓存：避免每次 SSE 新增窗口时都重跑全量 Jaccard。
// key = "phaseX-windowId"（章节起始 window 稳定不变），value = { signature, mergedSummary }
const _chapterCache = new Map()

const chapters = computed(() => {
  const sorted = [...(props.summaries || [])].sort((a, b) => a.window_id - b.window_id)
  const out = []
  let cur = null
  for (const s of sorted) {
    const phase = s.phase || 'Unknown'
    if (cur && cur.phase === phase) {
      cur.windows.push(s)
      cur.endTime = s.end_time
    } else {
      cur = {
        key: `${phase}-${s.window_id}`,
        phase,
        phaseLabel: PHASE_CN[phase] || phase || '未知阶段',
        startTime: s.start_time,
        endTime: s.end_time,
        windows: [s],
      }
      out.push(cur)
    }
  }
  const currentId = props.currentSummary?.window_id ?? -1
  for (const ch of out) {
    const refined = ch.windows.filter(w => w.stage === 2)
    const src = refined.length > 0 ? refined : ch.windows
    // 指纹：窗口 id+stage 序列；只要没新加/升级窗口就直接用缓存
    const sig = src.map(w => `${w.window_id}:${w.stage || 0}`).join(',')
    const cached = _chapterCache.get(ch.key)
    if (cached && cached.sig === sig) {
      ch.mergedSummary = cached.merged
    } else {
      ch.mergedSummary = mergeIncrementally(src)
      _chapterCache.set(ch.key, { sig, merged: ch.mergedSummary })
    }
    ch.containsCurrent = ch.windows.some(w => w.window_id === currentId)
    ch.hasQuickOnly = refined.length === 0
  }
  return out
})

const totalWindows = computed(() => (props.summaries || []).length)

const currentChapter = computed(() =>
  chapters.value.find(c => c.containsCurrent) || chapters.value[chapters.value.length - 1]
)

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
}

.rp-tabs {
  display: flex;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.rp-tab {
  flex: 1;
  padding: 15px 0;
  text-align: center;
  font-size: 20px;
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
  overflow-y: auto;
  padding: 24px;
}

.an-content::-webkit-scrollbar { width: 3px; }
.an-content::-webkit-scrollbar-track { background: transparent; }
.an-content::-webkit-scrollbar-thumb { background: var(--bg-elevated); border-radius: 2px; }

.an-window-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.an-window-num {
  font-size: 30px;
  font-weight: 700;
  font-family: var(--font-mono);
  color: var(--accent-primary);
}

.an-window-tag {
  font-size: 17px;
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
  font-size: 19px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  margin-bottom: 14px;
}

.an-phase {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  background: var(--bg-tertiary);
  border-radius: 100px;
  font-size: 19px;
  color: var(--accent-primary);
  margin-bottom: 16px;
  border: 1px solid var(--border-subtle);
}

.an-phase-dot {
  width: 5px;
  height: 5px;
  border-radius: 2px;
  background: var(--accent-primary);
}

.an-full-text {
  font-size: 24px;
  line-height: 1.9;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* ===== 手术进程叙事 ===== */
.narrative-header {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-subtle);
}
.narrative-title {
  font-size: 30px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0.3px;
}
.narrative-meta {
  font-size: 21px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

.chapter {
  padding: 16px 0 18px;
  border-bottom: 1px dashed var(--border-subtle);
}
.chapter:last-of-type { border-bottom: none; }
.chapter.active .chapter-phase {
  color: var(--accent-primary);
}
.chapter.active {
  background: linear-gradient(90deg, var(--accent-glow), transparent);
  margin: 0 -22px;
  padding: 18px 24px 20px;
  border-left: 3px solid var(--accent-primary);
}
.chapter-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.chapter-phase {
  font-size: 26px;
  font-weight: 700;
  color: var(--text-primary);
}
.chapter-time {
  font-size: 21px;
  font-family: var(--font-mono);
  color: var(--text-tertiary);
}
.chapter-count {
  font-size: 19px;
  padding: 3px 9px;
  border-radius: 3px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-family: var(--font-mono);
}
.chapter-stage-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.chapter-stage-dot.quick {
  background: #fdcb6e;
  box-shadow: 0 0 5px rgba(253, 203, 110, 0.7);
}
.chapter-text {
  font-size: 24px;
  line-height: 1.9;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* ===== 当前窗口详情（可折叠） ===== */
.window-detail {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--border-subtle);
}
.window-detail summary {
  cursor: pointer;
  padding: 6px 0;
  font-size: 20px;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 8px;
  list-style: none;
  user-select: none;
}
.window-detail summary::-webkit-details-marker { display: none; }
.window-detail summary::before {
  content: '▸';
  color: var(--text-tertiary);
  font-size: 16px;
  transition: transform 0.15s;
}
.window-detail[open] summary::before {
  content: '▾';
}
.window-detail summary:hover {
  color: var(--accent-primary);
}
.an-stage1-block {
  margin-top: 10px;
  padding: 8px 10px;
  background: rgba(253, 203, 110, 0.05);
  border-left: 2px solid rgba(253, 203, 110, 0.4);
  font-size: 20px;
  line-height: 1.6;
  color: var(--text-secondary);
}
.an-stage1-label {
  font-size: 18px;
  color: #fdcb6e;
  margin-bottom: 4px;
  font-weight: 600;
}
.an-stage1-body {
  color: var(--text-secondary);
}

/* Stage badge */
.an-stage-badge {
  font-size: 17px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  margin-left: auto;
  letter-spacing: 0.3px;
}
.an-stage-badge.quick {
  background: rgba(253, 203, 110, 0.18);
  color: #fdcb6e;
  border: 1px solid rgba(253, 203, 110, 0.4);
}
.an-stage-badge.refined {
  background: rgba(0, 212, 170, 0.15);
  color: var(--accent-primary);
  border: 1px solid rgba(0, 212, 170, 0.4);
}

/* Experts */
.an-experts {
  margin-top: 16px;
  padding: 12px;
  background: var(--bg-tertiary);
  border-radius: 8px;
  border: 1px solid var(--border-subtle);
}
.an-section-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.6px;
  margin-bottom: 8px;
}
.an-expert-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 20px;
  margin-bottom: 6px;
  line-height: 1.4;
}
.an-expert-row:last-child { margin-bottom: 0; }
.an-expert-tag {
  flex-shrink: 0;
  font-size: 17px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 3px;
  font-family: var(--font-mono);
  width: 72px;
  text-align: center;
}
.an-expert-tag.phase   { background: rgba(0, 212, 170, 0.15); color: var(--accent-primary); }
.an-expert-tag.triplet { background: rgba(138, 43, 226, 0.18); color: #b388ff; }
.an-expert-tag.yolo    { background: rgba(255, 128, 0, 0.18); color: #ffab6b; }
.an-expert-val {
  flex: 1;
  color: var(--text-primary);
  word-break: break-all;
}
.an-expert-conf {
  font-family: var(--font-mono);
  color: var(--text-tertiary);
  font-size: 18px;
}

/* CoT */
.an-cot {
  margin-top: 14px;
  border-top: 1px dashed var(--border-subtle);
  padding-top: 12px;
}
.an-cot-toggle {
  background: transparent;
  border: none;
  color: var(--text-secondary);
  font-size: 19px;
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
  gap: 5px;
  font-family: var(--font-display);
}
.an-cot-toggle:hover { color: var(--accent-primary); }
.an-cot-body {
  margin-top: 8px;
  padding: 10px 12px;
  background: var(--bg-primary);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 19px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
  max-height: 240px;
  overflow-y: auto;
}

/* Stage 1 diff */
.an-stage1 {
  margin-top: 12px;
  font-size: 19px;
  color: var(--text-tertiary);
}
.an-stage1 summary {
  cursor: pointer;
  padding: 4px 0;
}
.an-stage1 summary:hover { color: var(--accent-primary); }
.an-stage1-body {
  margin-top: 6px;
  padding: 8px 10px;
  background: rgba(253, 203, 110, 0.05);
  border-left: 2px solid rgba(253, 203, 110, 0.4);
  color: var(--text-secondary);
  line-height: 1.6;
}

.an-divider {
  height: 1px;
  background: var(--border-subtle);
  margin: 16px 0;
}

.an-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.an-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 19px;
}

.an-meta-label { color: var(--text-tertiary); }
.an-meta-value { color: var(--text-secondary); font-family: var(--font-mono); }

/* Actions */
.an-actions {
  padding: 16px 22px;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.an-btn {
  flex: 1;
  padding: 11px 0;
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  font-size: 19px;
  cursor: pointer;
  text-align: center;
  transition: all 0.15s;
  font-family: var(--font-display);
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
.an-empty-text { font-size: 23px; color: var(--text-secondary); }
.an-empty-hint { font-size: 19px; color: var(--text-tertiary); }

/* Chat tab */
.tab-chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
