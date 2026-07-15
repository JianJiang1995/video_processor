<template>
  <div class="right-panel">
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

    <!-- Analysis Tab：滚动式手术进程叙事（替代以前的"当前窗口详情"）-->
    <div v-show="activeTab === 'analysis'" class="tab-analysis">
      <div v-if="chapters.length > 0" class="an-content">
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
            <div class="latest-text">{{ ch.mergedSummary }}</div>
          </div>
        </div>

        <div v-if="historyChapters.length > 0" class="history-title">
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
          <!-- phase 标签已去掉：文字里本来就会提阶段，避免重复 -->
          <div class="chapter-head">
            <span class="chapter-time">
              {{ formatTime(ch.startTime) }} – {{ formatTime(ch.endTime) }}
            </span>
            <span class="chapter-count">{{ ch.windowLabel }}</span>
          </div>
          <div class="chapter-text">{{ ch.mergedSummary }}</div>
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
        @seekToWindow="(wid) => $emit('seekToWindow', wid)"
        @message="(msg) => $emit('chatMessage', msg)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ChatPanel from './ChatPanel.vue'
import { useI18n } from '@/i18n'

const { language, t, windowLabel } = useI18n()

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
  GallbladderRetraction: '标本袋牵拉取出',
  CleaningCoagulation: '清洁凝血',
}

const PHASE_EN = {
  Preparation: 'Preparation',
  CalotTriangleDissection: 'Calot Triangle Dissection',
  ClippingCutting: 'Clipping and Cutting',
  GallbladderDissection: 'Gallbladder Dissection',
  GallbladderPackaging: 'Gallbladder Packaging',
  GallbladderRetraction: 'Specimen Bag Retraction',
  CleaningCoagulation: 'Cleaning and Coagulation',
}

const phaseLabel = (phase) => {
  const table = language.value === 'zh' ? PHASE_CN : PHASE_EN
  return table[phase] || phase || t('right.unknownPhase')
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
    .replace(/^\s*\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?s?\s*[：:]\s*/i, '')
    .replace(/【专家实时快照[^】]*】/g, '')
    .replace(/该段为实时快照，?\s*R1\/Gemini\s*精修结果稍后覆盖。?/g, '')
    .replace(/已基于\s*\d+\s*帧快速更新手术进程，?\s*R1\/Gemini\s*精修结果稍后覆盖。?/g, '')
    .replace(/R1\/Gemini\s*精修结果稍后覆盖。?/g, '')
    .replace(/精修(?:后|结果)?(?:将|会|稍后)?覆盖。?/g, '')
    .replace(/YOLO\s*(?:暂定)?(?:检出|检测出)/gi, '检出')
    .replace(/暂未稳定检出器械/g, '未见明确器械')
    .replace(/当前判断为/g, '当前处于')
    .replace(/[，,。；;\s]*暂无明确关键操作变化[。；;\s]*/g, '。')
    .replace(/当前处于当前阶段[，,]/g, '当前画面')
    .replace(/(?:动作三元组提示|主要动作)[:：]\s*\[[^\n。]*。?/g, '')
    .replace(/(?:动作三元组提示|主要动作)[:：]\s*(?:\[[^\]]+\](?:-[^；。,\s]+)*[；,，、\s]*)+。?/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

const INSTRUMENT_LIST_RE = '(?:抓钳|钛夹钳|施夹钳|施夹器|剪刀|电剪|电钩|冲洗器|吸引器|冲吸器|双极电凝|双极|器械|钛夹)'
const NON_CLIP_INSTRUMENT_RE = '(?:抓钳|剪刀|电剪|电钩|冲洗器|吸引器|冲吸器|双极电凝|双极|器械)'
const PROGRESS_KEYWORDS = /(牵拉|暴露|分离|剥离|剪切|剪断|切断|夹闭|闭合|施夹|钛夹|胆囊|胆囊管|胆囊动脉|血管|管状结构|肝床|肝胆三角|三角区|CVS|清理|冲洗|吸引|装袋|取出|穿刺|穿入|穿孔|残端|粘连|组织|出血|止血|渗血|凝血|视野|起雾|雾气|烟雾|模糊|镜头|移出体外|退出体外|离开腹腔|腹腔外|套管口|腹壁外)/
const BLEEDING_KEYWORDS = /(大量(?:活动性)?出血|活动性出血|明显出血|持续出血|喷涌出血|喷射性出血|涌血|出血点|出血|止血|渗血|凝血|无活动性出血|未见活动性出血|bleeding|hemostasis)/i
const VISIBILITY_KEYWORDS = /(镜头移出体外|移出体外|退出体外|离开腹腔|腹腔外|套管口|腹壁外|镜头起雾|起雾|雾气|烟雾|模糊|视野受遮挡|视野不清|scope moved outside|outside the body|trocar|extra-abdominal|fog|smoke|blur)/i
const SAFETY_KEYWORDS = /(CVS|安全视野|关键安全视野|两条结构|胆囊管|胆囊动脉|管状结构|残端|夹闭|切断|critical view|cystic duct|cystic artery)/i
const CVS_STATUS_RE = /(CVS|安全关键视野|关键安全视野|critical view of safety|critical view)/i
const LOW_VALUE_VISUAL_RE = new RegExp(`^(?:当前)?(?:可见|见|视野中可见)${INSTRUMENT_LIST_RE}(?:、${INSTRUMENT_LIST_RE})*[。；;，,\\s]*$`)

function splitSentences(text) {
  return String(text || '')
    .split(/(?<=[。；！？!?;])/)
    .map(s => s.trim())
    .filter(Boolean)
}

function softenInstrumentLanguage(text) {
  let out = String(text || '')
  const instrumentList = new RegExp(`${INSTRUMENT_LIST_RE}(?:、${INSTRUMENT_LIST_RE})*`, 'g')
  const visualOnly = new RegExp(`(?:当前)?(?:可见|见|视野中可见)${INSTRUMENT_LIST_RE}(?:、${INSTRUMENT_LIST_RE})*[，,。；;]?`, 'g')
  out = out.replace(/太夹前|钛夹前|太夹钳|胎夹钳/g, '钛夹钳')
  out = out.replace(/动胆囊动脉/g, '胆囊动脉')
  out = out.replace(/动胆囊管/g, '胆囊管')
  out = out.replace(/(钛夹钳(?:正在)?夹闭(?:胆囊管|胆囊动脉))[，,]?\s*明显/g, '$1')
  out = out.replace(/(当前窗口|本段|术野|画面)出现/g, '$1有')
  out = out.replace(/出现了|出现/g, '')
  out = out.replace(/Hem[-\s]?o[-\s]?lok|Hemolok|hemlock/gi, 'Hem-o-lok')
  out = out.replace(/已被多枚(?:金属)?钛夹(?:夹闭|关闭|闭合)(并切断)?的管状结构残端/g, '多枚钛夹已夹闭$1的胆囊管残端')
  out = out.replace(/多枚(?:金属)?钛夹(?:夹闭|关闭|闭合)(并切断)?的管状结构残端/g, '多枚钛夹已夹闭$1的胆囊管残端')
  out = out.replace(/(?:当前)?(?:可见|见|视野中可见)(?:钛夹钳|施夹钳|施夹器)(?:正在)?对/g, '钛夹钳对')
  out = out.replace(/(?:当前)?(?:可见|见|视野中可见)(?:钛夹钳|施夹钳|施夹器)(?:正在)?在/g, '钛夹钳在')
  out = out.replace(/使用(?:钛夹钳|施夹钳|施夹器)进行/g, '使用钛夹钳进行')
  out = out.replace(/(?:钛夹钳|施夹钳|施夹器)对/g, '钛夹钳对')
  out = out.replace(new RegExp(`(?:当前)?(?:可见|见|视野中可见)(${NON_CLIP_INSTRUMENT_RE})在([^，。；;]+)[，,]`, 'g'), '在$2，')
  out = out.replace(new RegExp(`(?:当前)?(?:可见|见|视野中可见)(${NON_CLIP_INSTRUMENT_RE})对([^，。；;]+)[，,]`, 'g'), '对$2，')
  out = out.replace(new RegExp(`(?:${NON_CLIP_INSTRUMENT_RE})在([^，。；;]*?)(?:完成|进行)?(?:夹闭|关闭|闭合)处理`, 'g'), '在$1进行夹闭处理')
  out = out.replace(new RegExp(`(?:${NON_CLIP_INSTRUMENT_RE})在([^，。；;]*?)(?:完成|进行)?(?:夹闭|关闭|闭合)动作`, 'g'), '在$1进行夹闭处理')
  out = out.replace(new RegExp(`(?:${NON_CLIP_INSTRUMENT_RE})在([^，。；;]+)[，,]`, 'g'), '在$1，')
  out = out.replace(new RegExp(`(?:${NON_CLIP_INSTRUMENT_RE})对([^，。；;]+)[，,]`, 'g'), '对$1，')
  out = out.replace(visualOnly, '')
  out = out.replace(/当前处于[^，。；;]+[，,]\s*/g, '')
  out = out.replace(/抓钳(?:持续)?牵拉/g, '牵拉')
  out = out.replace(/夹持牵拉/g, '牵拉')
  out = out.replace(new RegExp(`(?:${NON_CLIP_INSTRUMENT_RE})(?:持续)?牵拉`, 'g'), '牵拉')
  out = out.replace(new RegExp(`(?:${NON_CLIP_INSTRUMENT_RE})(?:进行)?分离`, 'g'), '分离')
  out = out.replace(new RegExp(`(?:${NON_CLIP_INSTRUMENT_RE})(?:进行)?夹闭`, 'g'), '夹闭')
  out = out.replace(/使用(?:抓钳|器械)?持续?牵拉/g, '牵拉')
  out = out.replace(/使用电钩对组织进行点触和分离动作/g, '电钩分离组织')
  out = out.replace(/电钩对组织进行点触和分离动作/g, '电钩分离组织')
  out = out.replace(/使用(?:双极电凝|双极)?对组织进行点触和分离动作/g, '分离组织')
  out = out.replace(/对组织进行尖端接触和分离/g, '分离组织')
  out = out.replace(/尖端接触/g, '组织接触')
  out = out.replace(/电钩正?伸入([^，。；;]+)[，,]\s*/g, '在$1，')
  out = out.replace(/使用(?:剪刀|电剪|电钩|双极电凝)?对/g, '对')
  out = out.replace(/使用(?:剪刀|电剪|电钩|双极电凝)?在/g, '在')
  out = out.replace(/使用(?:冲洗器|吸引器|冲吸器)进行/g, '进行')
  out = out.replace(/使用(?:冲洗器|吸引器|冲吸器)持续/g, '持续')
  out = out.replace(/使用(?:钛夹钳|施夹钳|施夹器)进行/g, '使用钛夹钳进行')
  out = out.replace(/(?:钛夹钳|施夹钳|施夹器)对/g, '钛夹钳对')
  out = out.replace(/已被多枚金属钛夹(?:夹闭|关闭)的管状结构残端/g, '多枚钛夹已夹闭的胆囊管残端')
  out = out.replace(/已被多枚金属(?:夹闭|关闭|闭合)(并切断)?的管状结构残端/g, '已夹闭$1的胆囊管残端')
  out = out.replace(/在([^，。；;]+)[，,]\s*进行了?(?:夹闭|关闭|闭合)动作/g, '在$1进行夹闭处理')
  out = out.replace(/进行了?(?:夹闭|关闭|闭合)动作/g, '完成夹闭处理')
  out = out.replace(/在([^，。；;]+)[，,]\s*(?:夹闭|关闭|闭合)了?组织/g, '在$1进行夹闭处理')
  out = out.replace(/(?:夹闭|关闭|闭合)了?组织/g, '完成夹闭处理')
  out = out.replace(/进行钛夹的施加并闭合/g, '进行钛夹夹闭')
  out = out.replace(/钛夹的施加并闭合/g, '钛夹夹闭')
  out = out.replace(new RegExp(`(?:${NON_CLIP_INSTRUMENT_RE})进入视野[，,]?\\s*`, 'g'), '')
  out = out.replace(/(?:剪刀|电剪)进入视野[，,]?\s*/g, '')
  out = out.replace(/(?:冲洗器|吸引器|冲吸器)进入视野[，,]?\s*/g, '')
  out = out.replace(/已夹闭并切断的管状结构残端。已夹闭的管状结构残端/g, '已夹闭并切断的胆囊管残端')
  out = out.replace(/已夹闭的管状结构残端。已夹闭并切断的管状结构残端/g, '已夹闭并切断的胆囊管残端')
  out = out.replace(/视野中可见/g, '')
  out = out.replace(new RegExp(`可见${instrumentList.source}[，,]`, 'g'), '')
  out = out.replace(/可见/g, '')
  out = out.replace(/\s+/g, ' ')
  out = out.replace(/[，,]\s*[。；;]/g, '。')
  out = out.replace(/^[，,。；;\s]+|[，,。；;\s]+$/g, '')
  return out.trim()
}

function focusProgressText(text, maxChars = 260, maxSentences = 2) {
  const normalized = softenInstrumentLanguage(stripPhaseHeader(text))
  if (!normalized) return ''

  const risk = []
  const visibility = []
  const safety = []
  const actions = []
  const seen = new Set()

  for (const raw of splitSentences(normalized)) {
    const sentence = softenInstrumentLanguage(raw).replace(/^[，,。；;\s]+|[，,。；;\s]+$/g, '')
    if (!sentence) continue
    if (CVS_STATUS_RE.test(sentence)) continue
    if (LOW_VALUE_VISUAL_RE.test(sentence)) continue
    if (!PROGRESS_KEYWORDS.test(sentence)) continue

    const fp = sentenceFingerprint(sentence)
    if (!fp || seen.has(fp)) continue
    seen.add(fp)

    if (BLEEDING_KEYWORDS.test(sentence)) {
      risk.push(sentence)
    } else if (VISIBILITY_KEYWORDS.test(sentence)) {
      visibility.push(sentence)
    } else if (SAFETY_KEYWORDS.test(sentence)) {
      safety.push(sentence)
    } else {
      actions.push(sentence)
    }
  }

  const selected = []
  const pushFrom = (items, limit = maxSentences) => {
    for (const s of items) {
      if (selected.length >= limit) return
      if (selected.some(existing => jaccard(bigrams(existing), bigrams(s)) >= 0.55)) continue
      selected.push(s)
      const joined = selected.map(x => /[。；！？!?;]$/.test(x) ? x : `${x}。`).join('')
      if (joined.length >= maxChars) return
    }
  }
  pushFrom(risk, Math.min(maxSentences, risk.length || maxSentences))
  pushFrom(visibility, maxSentences)
  pushFrom(safety, maxSentences)
  pushFrom(actions, maxSentences)

  const result = selected
    .map(s => /[。；！？!?;]$/.test(s) ? s : `${s}。`)
    .join('')
    .slice(0, maxChars)
  if (result) return result
  const nonCvs = splitSentences(normalized)
    .map(s => s.replace(/^[，,。；;\s]+|[，,。；;\s]+$/g, '').trim())
    .filter(s => s && !CVS_STATUS_RE.test(s))
    .join('')
  return (nonCvs || normalized).slice(0, maxChars)
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
  const cleaned = stripPhaseHeader(text)
  return cleaned || fallback
}

// 章节文本只保留"关键节点"——窗口级 + 句子级双层去重，并对总长度设上限。
// 目标：用户看到的叙事像手术记录，而不是同一句话的几十次复述。
const CHAPTER_MAX_CHARS = 220
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
    const cleaned = focusProgressText(w.summary, 180, 1)
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
    const cleaned = stripPhaseHeader(s.summary)
    const focused = focusProgressText(cleaned, 240, 2)
    return {
      key: `window-${s.window_id}-${s.stage || 0}`,
      phase,
      phaseLabel: phaseLabel(phase),
      startTime: s.start_time,
      endTime: s.end_time,
      windows: [s],
      windowLabel: windowLabel(s.window_id + 1),
      mergedSummary: focused || cleaned || t('right.emptyWindowResult'),
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
  if (!props.isProcessing && playbackWindowId.value >= 0 && playbackWindowId.value < latestWindowId.value) {
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

    if (last && contiguous && (sim >= 0.35 || (samePhase && sim >= 0.22))) {
      last.windows = [...last.windows, ...ch.windows]
      last.startTime = Math.min(last.startTime, ch.startTime)
      last.endTime = Math.max(last.endTime, ch.endTime)
      const chronological = [...last.windows].sort((a, b) => (a.window_id ?? 0) - (b.window_id ?? 0))
      last.mergedSummary = mergeIncrementally(chronological)
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

.latest-chapters {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin: 0 0 22px;
}

.latest-chapter {
  margin: 0 0 22px;
  padding: 24px 26px 28px;
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
  font-size: 22px;
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
  font-size: 34px;
  line-height: 1.65;
  font-weight: 650;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.latest-chapter.secondary .latest-text {
  font-size: 31px;
}

.history-title {
  margin: 8px 0 6px;
  color: var(--text-tertiary);
  font-size: 22px;
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
  margin: 0;
  padding: 22px 0 24px;
  border-left-width: 0;
}

.an-actions {
  min-height: 92px;
  padding: 20px 24px;
}

.an-btn {
  padding: 16px 0;
}
</style>
