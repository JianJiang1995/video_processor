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

        <div
          v-if="bleedingStatus"
          class="bleeding-status"
          :class="bleedingStatus.status"
        >
          <div class="bleeding-status-head">
            <span class="bleeding-status-title">{{ bleedingStatus.title }}</span>
            <span class="bleeding-status-time">
              窗口 {{ bleedingStatus.windowId + 1 }} · {{ formatTime(bleedingStatus.startTime) }} – {{ formatTime(bleedingStatus.endTime) }}
            </span>
          </div>
          <div class="bleeding-status-text">{{ bleedingStatus.text }}</div>
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
          </div>
          <div class="chapter-text">{{ ch.mergedSummary }}</div>
        </div>
      </div>

      <!-- Empty state -->
      <div v-else class="an-empty">
        <div class="an-empty-icon">&#x1F4CA;</div>
        <div class="an-empty-text">暂无分析</div>
        <div class="an-empty-hint">分析开始后，手术进程将在此滚动展开</div>
      </div>

      <!-- Actions：基于当前显示的章节（通常是正在播放的那段）-->
      <div class="an-actions" :class="{ disabled: !currentChapter }">
        <button
          class="an-btn primary"
          :disabled="!currentChapter"
          @click="$emit('tts', currentChapterAsSummary)"
        >
          &#128264; 朗读本段
        </button>
        <button class="an-btn" :disabled="!currentChapter" @click="copyChapter">
          &#128203; 复制
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
import { computed } from 'vue'
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
  'update:activeTab', 'tts', 'seekToWindow', 'chatMessage'
])

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
  return String(t || '')
    .replace(/^【[^】]*】\s*/, '')
    .replace(/【专家实时快照[^】]*】/g, '')
    .replace(/该段为实时快照，?\s*R1\/Gemini\s*精修结果稍后覆盖。?/g, '')
    .replace(/已基于\s*\d+\s*帧快速更新手术进程，?\s*R1\/Gemini\s*精修结果稍后覆盖。?/g, '')
    .replace(/R1\/Gemini\s*精修结果稍后覆盖。?/g, '')
    .replace(/精修(?:后|结果)?(?:将|会|稍后)?覆盖。?/g, '')
    .replace(/YOLO\s*(?:暂定)?(?:检出|检测出)/gi, '检出')
    .replace(/暂未稳定检出器械/g, '未见明确器械')
    .replace(/当前判断为/g, '当前处于')
    .replace(/(?:动作三元组提示|主要动作)[:：]\s*\[[^\n。]*。?/g, '')
    .replace(/(?:动作三元组提示|主要动作)[:：]\s*(?:\[[^\]]+\](?:-[^；。,\s]+)*[；,，、\s]*)+。?/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function hasSevereBleedingText(text) {
  const t = String(text || '').toLowerCase()
  if (/(无(?:明显)?出血|未见(?:明显)?出血|没有(?:明显)?出血|无活动性出血|未见活动性出血|no bleeding|without bleeding)/i.test(t)) return false
  return /(大量(?:活动性)?出血|活动性出血|明显出血|持续出血|喷涌出血|喷射性出血|涌血|出血点|active bleeding|heavy bleeding|massive bleeding|profuse bleeding)/i.test(t)
}

function hasBleedingResolvedText(text) {
  const t = String(text || '').toLowerCase()
  return /(出血(?:已经|已)?(?:停止|控制|解决)|已(?:完成)?止血|止血(?:完成|成功|有效)|凝血后(?:未见|无)活动性出血|未见活动性出血|无活动性出血|bleeding (?:stopped|controlled|resolved)|hemostasis achieved)/i.test(t)
}

function bleedingText(text, fallback) {
  const cleaned = stripPhaseHeader(text)
  return cleaned || fallback
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
    const updatedWindows = ch.windows.filter(w => w.stage === 2)
    const src = updatedWindows.length > 0 ? updatedWindows : ch.windows
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
  }
  return out
})

const totalWindows = computed(() => (props.summaries || []).length)

const bleedingStatus = computed(() => {
  const sorted = [...(props.summaries || [])].sort((a, b) => (a.window_id ?? 0) - (b.window_id ?? 0))
  let lastActive = null
  let lastResolved = null

  for (const w of sorted) {
    if (hasSevereBleedingText(w.summary)) {
      lastActive = w
      lastResolved = null
    } else if (lastActive && hasBleedingResolvedText(w.summary)) {
      lastResolved = w
    }
  }

  if (lastResolved) {
    return {
      status: 'resolved',
      title: '出血已控制',
      windowId: lastResolved.window_id,
      startTime: lastResolved.start_time,
      endTime: lastResolved.end_time,
      text: bleedingText(lastResolved.summary, '活动性出血已控制，继续观察术野。')
    }
  }

  if (lastActive) {
    return {
      status: 'active',
      title: '活动性出血',
      windowId: lastActive.window_id,
      startTime: lastActive.start_time,
      endTime: lastActive.end_time,
      text: bleedingText(lastActive.summary, '当前窗口出现大量活动性出血，需要重点关注。')
    }
  }

  return null
})

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

.bleeding-status {
  margin: 0 0 18px;
  padding: 18px 20px;
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
  margin-bottom: 10px;
}

.bleeding-status-title {
  font-size: 30px;
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
  font-size: 21px;
  white-space: nowrap;
}

.bleeding-status-text {
  color: var(--text-primary);
  font-size: 26px;
  line-height: 1.65;
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
.chapter-text {
  font-size: 24px;
  line-height: 1.9;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* Actions */
.an-actions {
  padding: 16px 22px;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  gap: 6px;
  flex-shrink: 0;
  min-height: 74px;
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
.an-empty-text { font-size: 23px; color: var(--text-secondary); }
.an-empty-hint { font-size: 19px; color: var(--text-tertiary); }

/* Chat tab */
.tab-chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.rp-tab,
.narrative-meta,
.chapter-count,
.chapter-time {
  font-size: 24px;
}

.narrative-title {
  font-size: 34px;
}

.chapter-text,
.an-full-text {
  font-size: 30px;
  line-height: 1.75;
}

.an-btn {
  font-size: 26px;
}

.chapter {
  padding: 22px 0 24px;
}

.chapter.active {
  margin: 0 -24px;
  padding: 24px 28px 28px;
  border-left-width: 6px;
}

.an-actions {
  min-height: 92px;
  padding: 20px 24px;
}

.an-btn {
  padding: 16px 0;
}
</style>
