<template>
  <div class="overview-container">
    <!-- Header -->
    <header class="overview-header">
      <div class="header-left">
        <button class="back-btn" @click="$emit('back')">
          <span class="back-arrow">←</span>
          <span>{{ t('overview.back') }}</span>
        </button>
        <div class="title-group">
          <h1 class="overview-title">{{ t('overview.title') }}</h1>
          <span class="window-count-badge">
            <span class="count-num">{{ filteredSummaries.length }}</span>
            <span class="count-sep">/</span>
            <span class="count-total">{{ summaries.length }}</span>
          </span>
        </div>
      </div>
      <div class="header-right">
        <div v-if="isProcessing" class="processing-badge">
          <span class="processing-dot"></span>
          <span>{{ t('overview.processing') }}</span>
        </div>
        <div class="total-duration">
          <span class="duration-icon">⏱</span>
          <span>{{ formatTime(totalDuration) }}</span>
        </div>
      </div>
    </header>

    <!-- Toolbar: Search + Chat toggle -->
    <div class="toolbar">
      <div class="toolbar-left">
        <!-- Search -->
        <div class="search-wrapper" :class="{ focused: searchFocused }">
          <svg class="search-svg-icon" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd"/>
          </svg>
          <input
            v-model="filterText"
            type="text"
            class="search-input"
            :placeholder="t('overview.searchPlaceholder')"
            @focus="searchFocused = true"
            @blur="searchFocused = false"
          />
          <button v-if="filterText" class="clear-btn" @click="filterText = ''">
            <svg viewBox="0 0 20 20" fill="currentColor" width="14" height="14">
              <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
            </svg>
          </button>
        </div>
      </div>
      <div class="toolbar-right">
        <button
          class="chat-toggle-btn"
          :class="{ active: showChat }"
          @click="showChat = !showChat"
        >
          <span class="chat-toggle-icon">💬</span>
          <span>{{ t('overview.smartChat') }}</span>
        </button>
      </div>
    </div>

    <!-- Main body: Grid + Chat side panel -->
    <div class="overview-body">
      <!-- Grid area -->
      <div class="grid-area" ref="gridAreaRef">
        <!-- Empty State -->
        <div v-if="summaries.length === 0" class="empty-state">
          <div class="empty-visual">
            <svg viewBox="0 0 80 80" fill="none" width="80" height="80">
              <rect x="8" y="16" width="64" height="48" rx="8" stroke="var(--text-tertiary)" stroke-width="2" stroke-dasharray="4 4"/>
              <circle cx="40" cy="40" r="12" stroke="var(--accent-primary)" stroke-width="2" opacity="0.5"/>
              <path d="M36 40l3 3 5-6" stroke="var(--accent-primary)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.5"/>
            </svg>
          </div>
          <div class="empty-text">{{ t('overview.noWindows') }}</div>
          <div class="empty-hint">{{ t('overview.noWindowsHint') }}</div>
        </div>

        <!-- No Results -->
        <div v-else-if="filteredSummaries.length === 0" class="empty-state">
          <div class="empty-visual">
            <svg viewBox="0 0 80 80" fill="none" width="80" height="80">
              <circle cx="36" cy="36" r="20" stroke="var(--text-tertiary)" stroke-width="2"/>
              <path d="M50 50l16 16" stroke="var(--text-tertiary)" stroke-width="2" stroke-linecap="round"/>
              <path d="M28 36h16M36 28v16" stroke="var(--error)" stroke-width="2" stroke-linecap="round" opacity="0.5" transform="rotate(45 36 36)"/>
            </svg>
          </div>
          <div class="empty-text">{{ t('overview.noResults') }}</div>
          <div class="empty-hint">{{ t('overview.noResultsHint', { query: filterText }) }}</div>
        </div>

        <!-- Window Grid -->
        <div v-else class="window-grid">
          <div
            v-for="(s, idx) in filteredSummaries"
            :key="s.window_id"
            class="window-card"
            :style="{ animationDelay: `${Math.min(idx, 8) * 20}ms` }"
            @click="handleCardClick(s)"
          >
            <!-- Thumbnail -->
            <div class="card-thumb" :ref="(el) => registerCardThumb(el, s.window_id)">
              <img
                v-if="thumbnails[s.window_id]"
                :src="thumbnails[s.window_id]"
                class="thumb-img"
                alt=""
              />
              <div v-else-if="thumbnailStatus[s.window_id] === 'loading'" class="thumb-placeholder">
                <div class="thumb-spinner"></div>
              </div>
              <div v-else-if="thumbnailStatus[s.window_id] === 'failed'" class="thumb-placeholder thumb-empty">
                <span>{{ t('overview.noPreview') }}</span>
              </div>
              <div v-else class="thumb-placeholder thumb-pending"></div>
              <span class="card-index-overlay">#{{ s.window_id }}</span>
              <span class="card-time-overlay">
                {{ formatTime(s.start_time) }} – {{ formatTime(s.end_time) }}
              </span>
            </div>

            <div class="card-body">
              <!-- Duration mini-bar -->
              <div class="card-duration-bar">
                <div
                  class="card-duration-fill"
                  :style="{ width: durationPercent(s) + '%' }"
                ></div>
              </div>

              <p class="card-summary" v-html="highlightText(truncate(displaySummary(s), 110))"></p>
            </div>
          </div>
        </div>
      </div>

      <!-- Smart Chat side panel -->
      <Transition name="slide-chat">
        <div v-if="showChat" class="chat-panel">
          <div class="chat-panel-header">
            <span class="chat-panel-title">
              <span class="chat-panel-icon">🤖</span>
              {{ t('overview.smartChat') }}
            </span>
            <button class="chat-panel-close" @click="showChat = false">✕</button>
          </div>

          <div class="chat-messages" ref="chatMessagesRef">
            <div v-if="chatMessages.length === 0" class="chat-empty">
              <div class="chat-empty-icon">💬</div>
              <div class="chat-empty-text">{{ t('overview.chatEmpty') }}</div>
              <div class="chat-empty-hints">
                <button class="hint-chip" @click="sendChat(t('overview.summarizeFindingsPrompt'))">{{ t('overview.summarizeFindings') }}</button>
                <button class="hint-chip" @click="sendChat(t('overview.keyStepsPrompt'))">{{ t('overview.keySteps') }}</button>
                <button class="hint-chip" @click="sendChat(t('overview.abnormalIssuesPrompt'))">{{ t('overview.abnormalIssues') }}</button>
              </div>
            </div>
            <div
              v-for="(msg, idx) in chatMessages"
              :key="idx"
              class="chat-msg"
              :class="msg.role"
            >
              <div class="chat-msg-avatar">{{ msg.role === 'user' ? '👤' : '🤖' }}</div>
              <div class="chat-msg-bubble">
                <div class="chat-msg-text">{{ msg.content }}</div>
              </div>
            </div>
            <div v-if="chatLoading" class="chat-msg assistant">
              <div class="chat-msg-avatar">🤖</div>
              <div class="chat-msg-bubble">
                <div class="chat-msg-text typing-indicator">
                  <span></span><span></span><span></span>
                </div>
              </div>
            </div>
          </div>

          <div class="chat-input-area">
            <input
              v-model="chatInput"
              type="text"
              class="chat-input"
              :placeholder="t('overview.chatPlaceholder')"
              @keyup.enter="sendChat()"
              :disabled="chatLoading"
            />
            <button
              class="chat-send-btn"
              @click="sendChat()"
              :disabled="!chatInput.trim() || chatLoading"
            >
              <svg viewBox="0 0 20 20" fill="currentColor" width="18" height="18">
                <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z"/>
              </svg>
            </button>
          </div>
        </div>
      </Transition>
    </div>

    <!-- Loop Playback Modal -->
    <Teleport to="body">
      <Transition name="modal">
        <div v-if="activeWindow" class="modal-overlay" @click.self="closeModal">
          <div class="modal-content">
            <div class="modal-header">
              <div class="modal-title-group">
                <span class="modal-index">#{{ activeWindow.window_id }}</span>
                <span class="modal-time-range">
                  {{ formatTime(activeWindow.start_time) }} – {{ formatTime(activeWindow.end_time) }}
                </span>
              </div>
              <button class="modal-close" @click="closeModal">
                <svg viewBox="0 0 20 20" fill="currentColor" width="16" height="16">
                  <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                </svg>
              </button>
            </div>

            <div class="modal-body">
              <div v-if="mode === 'stream'" class="modal-player">
                <VideoPlayer
                  :session="session"
                  :currentTime="modalCurrentTime"
                  :isPlaying="true"
                  :isPaused="false"
                  :mode="mode"
                  :loopWindow="activeLoopWindow"
                  :streamEnded="false"
                  :windowDuration="windowDuration"
                  @timeupdate="modalCurrentTime = $event"
                  @exitLoop="closeModal"
                  @loopLoadFailed="closeModal"
                />
              </div>
              <div class="modal-summary-section">
                <div class="modal-summary-label">{{ t('overview.analysisSummary') }}</div>
                <p class="modal-summary-text">{{ displaySummary(activeWindow) }}</p>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, reactive, watch, nextTick, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import { apiUrl } from '@/utils/electronBridge'
import VideoPlayer from './VideoPlayer.vue'
import { useI18n } from '@/i18n'

const { t, language } = useI18n()

const props = defineProps({
  summaries: {
    type: Array,
    default: () => []
  },
  session: Object,
  mode: {
    type: String,
    default: 'local'
  },
  isProcessing: {
    type: Boolean,
    default: false
  },
  windowDuration: {
    type: Number,
    default: 5
  }
})

const emit = defineEmits(['back', 'seekToWindow'])

// Search
const filterText = ref('')
const searchFocused = ref(false)

// Chat
const showChat = ref(false)
const chatInput = ref('')
const chatMessages = ref([])
const chatLoading = ref(false)
const chatMessagesRef = ref(null)
const gridAreaRef = ref(null)

// Modal
const activeWindow = ref(null)
const modalCurrentTime = ref(0)

// Thumbnails
const thumbnails = reactive({})
const thumbnailStatus = reactive({}) // loading | loaded | failed
const loadingThumbs = new Set()

// Computed
const filteredSummaries = computed(() => {
  if (!filterText.value.trim()) return props.summaries
  const keyword = filterText.value.trim().toLowerCase()
  return props.summaries.filter(s =>
    displaySummary(s).toLowerCase().includes(keyword)
  )
})

const activeLoopWindow = computed(() => {
  if (!activeWindow.value) return null
  return {
    window_id: activeWindow.value.window_id,
    start_time: activeWindow.value.start_time,
    end_time: activeWindow.value.end_time
  }
})

const totalDuration = computed(() => {
  if (props.summaries.length === 0) return 0
  return Math.max(...props.summaries.map(s => s.end_time || 0))
})

const maxWindowDuration = computed(() => {
  if (props.summaries.length === 0) return 1
  return Math.max(...props.summaries.map(s => (s.end_time || 0) - (s.start_time || 0)), 1)
})

const OVERVIEW_INSTRUMENT_RE = '(?:抓钳|钛夹钳|施夹钳|施夹器|剪刀|电剪|电钩|冲洗器|吸引器|冲吸器|双极电凝|双极|器械|钛夹)'
const OVERVIEW_NON_CLIP_INSTRUMENT_RE = '(?:抓钳|剪刀|电剪|电钩|冲洗器|吸引器|冲吸器|双极电凝|双极|器械)'
const OVERVIEW_SIGNAL_RE = /(牵拉|暴露|分离|剥离|剪切|切断|夹闭|闭合|施夹|胆囊|胆囊管|胆囊动脉|管状结构|肝床|肝胆三角|CVS|清理|冲洗|吸引|装袋|取出|穿刺|穿入|穿孔|出血|止血|渗血|凝血|视野)/
const OVERVIEW_RISK_RE = /(大量(?:活动性)?出血|活动性出血|明显出血|持续出血|出血点|出血|止血|渗血|凝血|无活动性出血|未见活动性出血|bleeding|hemostasis)/i

function compactChineseSummary(text, maxChars = 150) {
  let out = String(text || '')
    .replace(/太夹前|钛夹前|太夹钳|胎夹钳/g, '钛夹钳')
    .replace(/动胆囊动脉/g, '胆囊动脉')
    .replace(/动胆囊管/g, '胆囊管')
    .replace(/(钛夹钳(?:正在)?夹闭(?:胆囊管|胆囊动脉))[，,]?\s*明显/g, '$1')
    .replace(/(当前窗口|本段|术野|画面)出现/g, '$1有')
    .replace(/出现了|出现/g, '')
    .replace(/^【[^】]*】\s*/, '')
    .replace(/当前处于[^，。；;]+[，,。；;]?\s*/g, '')
    .replace(/Hem[-\s]?o[-\s]?lok|Hemolok|hemlock/gi, 'Hem-o-lok')
    .replace(/(?:当前)?(?:可见|见|视野中可见)(?:钛夹钳|施夹钳|施夹器)(?:正在)?对/g, '钛夹钳对')
    .replace(/(?:当前)?(?:可见|见|视野中可见)(?:钛夹钳|施夹钳|施夹器)(?:正在)?在/g, '钛夹钳在')
    .replace(/使用(?:钛夹钳|施夹钳|施夹器)进行/g, '使用钛夹钳进行')
    .replace(/(?:钛夹钳|施夹钳|施夹器)对/g, '钛夹钳对')
    .replace(new RegExp(`(?:当前)?(?:可见|见|视野中可见)${OVERVIEW_NON_CLIP_INSTRUMENT_RE}(?:、${OVERVIEW_NON_CLIP_INSTRUMENT_RE})*[，,。；;]?`, 'g'), '')
    .replace(new RegExp(`(?:${OVERVIEW_NON_CLIP_INSTRUMENT_RE})进入视野[，,]?\\s*`, 'g'), '')
    .replace(new RegExp(`(?:${OVERVIEW_NON_CLIP_INSTRUMENT_RE})在([^，。；;]*?)(?:完成|进行)?(?:夹闭|关闭|闭合)处理`, 'g'), '在$1进行夹闭处理')
    .replace(new RegExp(`(?:${OVERVIEW_NON_CLIP_INSTRUMENT_RE})在([^，。；;]*?)(?:完成|进行)?(?:夹闭|关闭|闭合)动作`, 'g'), '在$1进行夹闭处理')
    .replace(new RegExp(`(?:${OVERVIEW_NON_CLIP_INSTRUMENT_RE})在([^，。；;]+)[，,]`, 'g'), '在$1，')
    .replace(new RegExp(`(?:${OVERVIEW_NON_CLIP_INSTRUMENT_RE})对([^，。；;]+)[，,]`, 'g'), '对$1，')
    .replace(/电钩正?伸入([^，。；;]+)[，,]\s*/g, '在$1，')
    .replace(/使用(?:抓钳|器械)?持续?牵拉/g, '牵拉')
    .replace(/夹持牵拉/g, '牵拉')
    .replace(/使用(?:冲洗器|吸引器|冲吸器)持续/g, '持续')
    .replace(/使用(?:冲洗器|吸引器|冲吸器)进行/g, '进行')
    .replace(/使用(?:钛夹钳|施夹钳|施夹器)进行/g, '使用钛夹钳进行')
    .replace(/(?:钛夹钳|施夹钳|施夹器)对/g, '钛夹钳对')
    .replace(/在([^，。；;]+)[，,]\s*进行了?(?:夹闭|关闭|闭合)动作/g, '在$1进行夹闭处理')
    .replace(/进行了?(?:夹闭|关闭|闭合)动作/g, '完成夹闭处理')
    .replace(/(?:夹闭|关闭|闭合)了?组织/g, '完成夹闭处理')
    .replace(/已被多枚(?:金属)?钛夹(?:夹闭|关闭|闭合)(并切断)?的管状结构残端/g, '多枚钛夹已夹闭$1的胆囊管残端')
    .replace(/多枚(?:金属)?钛夹(?:夹闭|关闭|闭合)(并切断)?的管状结构残端/g, '多枚钛夹已夹闭$1的胆囊管残端')
    .replace(/视野中可见|可见/g, '')
    .replace(/\s+/g, ' ')
    .trim()

  const sentences = out
    .split(/(?<=[。；！？!?;])/)
    .map(s => s.replace(/^[，,。；;\s]+|[，,。；;\s]+$/g, '').trim())
    .filter(s => s && OVERVIEW_SIGNAL_RE.test(s))
  const risks = sentences.filter(s => OVERVIEW_RISK_RE.test(s))
  const actions = sentences.filter(s => !OVERVIEW_RISK_RE.test(s))
  const selected = [...risks, ...actions].slice(0, 2)
  out = (selected.length ? selected : sentences.slice(0, 1))
    .map(s => /[。；！？!?;]$/.test(s) ? s : `${s}。`)
    .join('')
  return (out || String(text || '')).slice(0, maxChars)
}

function fallbackEnglishSummary(text) {
  const src = compactChineseSummary(text, 260)
  const lower = src.toLowerCase()
  const parts = []
  const add = (s) => { if (s && !parts.includes(s)) parts.push(s) }

  if (/hem[-\s]?o[-\s]?lok|hemolok|hemlock/i.test(src)) add('A Hem-o-lok clip is placed on the cystic duct.')
  if (/(肝胆三角|胆囊三角|calot)/i.test(src) && /(游离|分离|电凝|剥离|点触)/.test(src)) {
    add('Dissection is performed in the hepatocystic triangle around the cystic duct.')
  }
  if (/(胆囊管|胆囊动脉|管状结构)/.test(src) && /(夹闭|施夹|闭合)/.test(src)) {
    add(/胆囊动脉/.test(src) ? 'The isolated cystic artery is clipped.' : 'The isolated cystic duct is clipped.')
  }
  if (/(剪切|切断|夹断)/.test(src)) {
    add(/胆囊动脉/.test(src) ? 'The clipped cystic artery is divided.' : 'The clipped cystic duct is divided.')
  }
  if (/(胆囊分离|胆囊与肝床|肝床|胆囊床)/.test(src)) add('The gallbladder is dissected from the liver bed.')
  if (/(冲吸|冲洗|吸引|清理)/.test(src)) add('Suction and irrigation are used to clear the operative field.')
  if (/(大量(?:活动性)?出血|明显(?:活动性)?出血|持续(?:活动性)?出血|喷涌出血|喷射性出血|涌血|明确出血源|影响视野的持续渗血)/.test(src)) add('Active bleeding is noted.')
  else if (/(少量出血|少量渗血|渗血|出血)/.test(src)) add('Minor local bleeding or oozing is noted.')
  if (/(止血|凝血|无活动性出血|未见活动性出血)/.test(src)) add('Hemostasis is achieved or no active bleeding is seen.')
  if (/(牵拉|暴露)/.test(src)) add('Traction is maintained for exposure.')
  if (parts.length) return parts.slice(0, 2).join(' ')

  return src
    .replace(/胆囊管/g, 'cystic duct')
    .replace(/胆囊动脉/g, 'cystic artery')
    .replace(/肝胆三角/g, 'hepatocystic triangle')
    .replace(/胆囊床/g, 'gallbladder bed')
    .replace(/肝床/g, 'liver bed')
    .replace(/钛夹/g, 'titanium clip')
    .replace(/施夹器|施夹钳/g, 'clip applier')
    .replace(/抓钳/g, 'grasper')
    .replace(/电钩/g, 'electrocautery hook')
    .replace(/剪刀/g, 'scissors')
    .replace(/出血/g, 'bleeding')
    .replace(/止血/g, 'hemostasis')
}

function displaySummary(summary) {
  if (!summary) return ''
  if (language.value === 'en') {
    return summary.summary_en || fallbackEnglishSummary(summary.summary || '')
  }
  return compactChineseSummary(summary.summary || '')
}

// Methods
function handleCardClick(summary) {
  if (props.mode === 'stream') {
    activeWindow.value = summary
    modalCurrentTime.value = summary.start_time
  } else {
    emit('seekToWindow', summary.window_id)
  }
}

function closeModal() {
  activeWindow.value = null
}

function handleKeydown(e) {
  if (e.key === 'Escape' && activeWindow.value) {
    closeModal()
  }
}

function formatTime(seconds) {
  if (seconds == null) return '00:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function truncate(text, maxLen) {
  if (!text) return ''
  return text.length > maxLen ? text.slice(0, maxLen) + '…' : text
}

function highlightText(text) {
  if (!filterText.value.trim() || !text) return text
  const keyword = filterText.value.trim()
  const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const regex = new RegExp(`(${escaped})`, 'gi')
  return text.replace(regex, '<mark class="hl">$1</mark>')
}

function durationPercent(s) {
  const dur = (s.end_time || 0) - (s.start_time || 0)
  return Math.min(100, (dur / maxWindowDuration.value) * 100)
}

// Smart chat
function buildContext() {
  return props.summaries.map(s =>
    `[窗口${s.window_id} ${formatTime(s.start_time)}-${formatTime(s.end_time)}] ${displaySummary(s)}`
  ).join('\n')
}

async function sendChat(preset) {
  const text = preset || chatInput.value.trim()
  if (!text || chatLoading.value) return

  chatInput.value = ''
  chatMessages.value.push({ role: 'user', content: text })
  await scrollChatBottom()

  chatLoading.value = true
  try {
    const response = await axios.post(`/api/voice/chat/${props.session?.session_id || 'default'}/send`, {
      role: 'user',
      content: text,
      timestamp: Date.now() / 1000
    })

    if (response.data.success && response.data.response) {
      chatMessages.value.push({
        role: 'assistant',
        content: response.data.response.content
      })
    } else {
      chatMessages.value.push({
        role: 'assistant',
        content: response.data.error || t('overview.chatFallback')
      })
    }
  } catch (e) {
    chatMessages.value.push({
      role: 'assistant',
      content: t('overview.networkError')
    })
  } finally {
    chatLoading.value = false
    await scrollChatBottom()
  }
}

async function scrollChatBottom() {
  await nextTick()
  if (chatMessagesRef.value) {
    chatMessagesRef.value.scrollTop = chatMessagesRef.value.scrollHeight
  }
}

// ============================================================
// Lazy thumbnail loading with IntersectionObserver + concurrency limit.
// 过去：挂载时对所有 summaries 一次性并发 fetch /frame-at-timestamp，每张
// 缩略图是几十 KB 的 base64，N 大时主线程长任务严重（> 50 条时肉眼可见卡顿）。
// 现在：
//   1. 只有卡片进入视口（IntersectionObserver）才触发 loadThumbnail
//   2. 全局最多 MAX_CONCURRENT_THUMBS 个并行请求，其余排队
// ============================================================
const INITIAL_THUMB_COUNT = 8
const MAX_CONCURRENT_THUMBS = 2
const thumbQueue = []          // FIFO of summaries waiting to be fetched
const queuedWids = new Set()   // window_id 已经入队或正在加载，避免重复
let activeThumbFetches = 0
const cardElToWid = new WeakMap()  // 卡片 DOM 元素 → window_id
const widToCardEl = new Map()      // window_id → card DOM 元素，供 observer 创建后补注册
const widToSummary = new Map()     // window_id → summary 对象（供队列消费时反查）
let thumbObserver = null

async function fetchThumbnail(summary) {
  const wid = summary.window_id
  if (thumbnails[wid]) return
  const midTime = ((summary.start_time || 0) + (summary.end_time || 0)) / 2
  const sid = props.session?.session_id
  if (!sid) return

  try {
    const start = Math.max(0, midTime - 1)
    const end = midTime + 1
    const batchRes = await axios.get(`/api/analysis/frames-batch/${sid}`, {
      params: {
        start,
        end,
        max_frames: 6,
        use_url: true,
        use_preview: true,
      }
    })
    const frames = batchRes.data?.frames || []
    if (batchRes.data?.success && frames.length > 0) {
      const best = frames.reduce((a, b) => {
        return Math.abs((b.timestamp || 0) - midTime) < Math.abs((a.timestamp || 0) - midTime) ? b : a
      }, frames[0])
      if (best?.url) {
        thumbnails[wid] = best.url
        thumbnailStatus[wid] = 'loaded'
        return
      }
    }

    // Fallback to a small, timestamped video thumbnail. Avoid pulling a full
    // frame into the overview grid; card covers only need a compact JPEG.
    const videoFrameRes = await axios.get(`/api/video/thumbnail/${sid}`, {
      params: { timestamp: midTime, width: 360, quality: 62 }
    })
    if (videoFrameRes.data?.thumbnail) {
      thumbnails[wid] = `data:image/jpeg;base64,${videoFrameRes.data.thumbnail}`
      thumbnailStatus[wid] = 'loaded'
      return
    }
  } catch {
    // Keep the UI deterministic: do not spin forever when no frame exists.
  }
  thumbnailStatus[wid] = 'failed'
}

function pumpThumbQueue() {
  while (activeThumbFetches < MAX_CONCURRENT_THUMBS && thumbQueue.length > 0) {
    const summary = thumbQueue.shift()
    if (!summary) continue
    const wid = summary.window_id
    if (thumbnails[wid]) {
      queuedWids.delete(wid)
      continue
    }
    activeThumbFetches++
    loadingThumbs.add(wid)
    thumbnailStatus[wid] = 'loading'
    fetchThumbnail(summary).finally(() => {
      activeThumbFetches--
      loadingThumbs.delete(wid)
      queuedWids.delete(wid)
      pumpThumbQueue()
    })
  }
}

function enqueueThumbnail(summary, priority = false) {
  if (!summary) return
  const wid = summary.window_id
  if (thumbnails[wid] || queuedWids.has(wid)) return
  widToSummary.set(wid, summary)
  queuedWids.add(wid)
  thumbnailStatus[wid] = 'queued'
  if (priority) {
    thumbQueue.unshift(summary)
  } else {
    thumbQueue.push(summary)
  }
  pumpThumbQueue()
}

// 函数 ref：每张 card-thumb 挂载时注册到 observer
function registerCardThumb(el, wid) {
  if (!el) {
    widToCardEl.delete(wid)
    return
  }
  widToCardEl.set(wid, el)
  cardElToWid.set(el, wid)
  if (thumbObserver) {
    thumbObserver.observe(el)
  }
}

function observeRegisteredCards() {
  if (!thumbObserver) return
  for (const [wid, el] of widToCardEl.entries()) {
    if (thumbnails[wid] || thumbnailStatus[wid] === 'loading' || thumbnailStatus[wid] === 'queued') continue
    thumbObserver.observe(el)
  }
}

function primeInitialThumbnails() {
  const firstVisible = filteredSummaries.value.slice(0, INITIAL_THUMB_COUNT)
  for (let i = firstVisible.length - 1; i >= 0; i--) {
    enqueueThumbnail(firstVisible[i], true)
  }
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
  for (const s of props.summaries) {
    widToSummary.set(s.window_id, s)
  }

  // IntersectionObserver 不可用时退化为"立即加载所有"，但仍走并发队列限流
  if (typeof IntersectionObserver !== 'undefined') {
    thumbObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        const wid = cardElToWid.get(entry.target)
        if (wid == null) continue
        const s = widToSummary.get(wid) || props.summaries.find(x => x.window_id === wid)
        if (s) enqueueThumbnail(s)
        // 一旦开始加载就不再观察该卡片，减少回调
        thumbObserver.unobserve(entry.target)
      }
    }, {
      root: gridAreaRef.value || null,
      rootMargin: '80px 0px',  // 小幅预加载；后续封面随滚动进入视口再取
      threshold: 0.01,
    })
    nextTick().then(() => {
      observeRegisteredCards()
      primeInitialThumbnails()
    })
  } else {
    for (const s of props.summaries.slice(0, INITIAL_THUMB_COUNT)) enqueueThumbnail(s)
  }
})

watch(() => props.summaries.length, async () => {
  // summaries 变化（新窗口到达）时，等下一帧 DOM 更新再让 observer 去 observe 新卡片
  for (const s of props.summaries) {
    widToSummary.set(s.window_id, s)
  }
  await nextTick()
  observeRegisteredCards()
  primeInitialThumbnails()
  // 无 IntersectionObserver 的环境下主动把新 summary 入队
  if (!thumbObserver) {
    for (const s of props.summaries.slice(0, INITIAL_THUMB_COUNT)) enqueueThumbnail(s)
  }
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
  if (thumbObserver) {
    thumbObserver.disconnect()
    thumbObserver = null
  }
  thumbQueue.length = 0
  queuedWids.clear()
  widToSummary.clear()
  widToCardEl.clear()
})
</script>

<style scoped>
/* ===== Container ===== */
.overview-container {
  min-height: 100vh;
  background: var(--bg-primary);
  display: flex;
  flex-direction: column;
}

/* ===== Header ===== */
.overview-header {
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-subtle);
  padding: 0.6rem 1.5rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(12px);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.back-btn {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-medium);
  color: var(--text-secondary);
  padding: 0.35rem 0.75rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 0.95rem;
  display: flex;
  align-items: center;
  gap: 0.35rem;
  transition: all 0.2s;
}
.back-btn:hover {
  background: var(--bg-elevated);
  color: var(--text-primary);
  border-color: var(--accent-primary);
}
.back-arrow { font-size: 1.05rem; }

.title-group {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.overview-title {
  font-size: 1.3rem;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.02em;
}

.window-count-badge {
  display: inline-flex;
  align-items: baseline;
  gap: 0.15rem;
  font-size: 0.9rem;
  background: var(--accent-glow);
  border: 1px solid rgba(0, 212, 170, 0.2);
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
}
.count-num { color: var(--accent-primary); font-weight: 700; font-size: 1rem; }
.count-sep { color: var(--text-tertiary); }
.count-total { color: var(--text-tertiary); }

.header-right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.total-duration {
  font-size: 0.95rem;
  color: var(--text-tertiary);
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-family: var(--font-mono);
}
.duration-icon { font-size: 0.85rem; }

.processing-badge {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.75rem;
  color: var(--accent-primary);
  background: var(--accent-glow);
  border: 1px solid rgba(0, 212, 170, 0.2);
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
}

.processing-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent-primary);
  animation: processingPulse 1.2s ease-in-out infinite;
}

@keyframes processingPulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.75); }
}

/* ===== Toolbar ===== */
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1.5rem;
  gap: 1rem;
}

.toolbar-left { flex: 1; max-width: 480px; }
.toolbar-right { flex-shrink: 0; }

.search-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  background: var(--bg-secondary);
  border: 1px solid var(--border-medium);
  border-radius: var(--radius-md);
  transition: all 0.2s;
}
.search-wrapper.focused {
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 2px rgba(0, 212, 170, 0.1);
}

.search-svg-icon {
  width: 16px;
  height: 16px;
  margin-left: 12px;
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.search-input {
  width: 100%;
  padding: 0.55rem 0.75rem;
  background: transparent;
  border: none;
  color: var(--text-primary);
  font-size: 0.85rem;
  outline: none;
}
.search-input::placeholder { color: var(--text-tertiary); }

.clear-btn {
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  padding: 4px 10px 4px 4px;
  display: flex;
  align-items: center;
}
.clear-btn:hover { color: var(--text-primary); }

.chat-toggle-btn {
  background: var(--bg-secondary);
  border: 1px solid var(--border-medium);
  color: var(--text-secondary);
  padding: 0.45rem 0.9rem;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 0.8rem;
  display: flex;
  align-items: center;
  gap: 0.4rem;
  transition: all 0.2s;
}
.chat-toggle-btn:hover {
  border-color: var(--accent-primary);
  color: var(--text-primary);
}
.chat-toggle-btn.active {
  background: var(--accent-glow);
  border-color: var(--accent-primary);
  color: var(--accent-primary);
}
.chat-toggle-icon { font-size: 0.95rem; }

/* ===== Body layout ===== */
.overview-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.grid-area {
  flex: 1;
  overflow-y: auto;
  padding: 0 1.5rem 2rem;
}

/* ===== Empty State ===== */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 5rem 1rem;
  gap: 0.75rem;
}

.empty-visual { opacity: 0.5; margin-bottom: 0.5rem; }
.empty-text { font-size: 1rem; color: var(--text-secondary); font-weight: 500; }
.empty-hint { font-size: 0.8rem; color: var(--text-tertiary); }

/* ===== Window Grid ===== */
.window-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 0.85rem;
}

/* Card */
.window-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.2s ease;
  overflow: hidden;
  animation: cardFadeIn 0.35s ease-out both;
}
.window-card:hover {
  border-color: var(--accent-primary);
  box-shadow: 0 0 20px rgba(0, 212, 170, 0.1);
  transform: translateY(-2px);
}
.window-card:active {
  transform: translateY(0);
}

@keyframes cardFadeIn {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}

.card-thumb {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: var(--bg-tertiary);
  overflow: hidden;
}

.thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.3s ease;
}
.window-card:hover .thumb-img {
  transform: scale(1.04);
}

.thumb-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.thumb-empty {
  color: var(--text-tertiary);
  font-size: 0.85rem;
  font-weight: 600;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.035), transparent),
    var(--bg-tertiary);
}

.thumb-pending {
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.035), transparent),
    var(--bg-tertiary);
}

.thumb-spinner {
  width: 22px;
  height: 22px;
  border: 2px solid var(--border-medium);
  border-top-color: var(--accent-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}

.card-index-overlay {
  position: absolute;
  top: 6px;
  left: 6px;
  background: rgba(0, 0, 0, 0.65);
  color: var(--accent-primary);
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 0.95rem;
  padding: 2px 8px;
  border-radius: 4px;
  backdrop-filter: blur(4px);
}

.card-time-overlay {
  position: absolute;
  bottom: 6px;
  right: 6px;
  background: rgba(0, 0, 0, 0.65);
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 0.85rem;
  padding: 2px 7px;
  border-radius: 3px;
  backdrop-filter: blur(4px);
}

.card-body { padding: 0.6rem 0.8rem; }


.card-duration-bar {
  height: 3px;
  background: var(--bg-tertiary);
  border-radius: 2px;
  margin-bottom: 0.6rem;
  overflow: hidden;
}

.card-duration-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent-primary), var(--accent-secondary));
  border-radius: 2px;
  transition: width 0.3s ease;
}

.card-summary {
  font-size: 0.95rem;
  color: var(--text-secondary);
  line-height: 1.65;
  word-break: break-word;
}

.card-summary :deep(.hl) {
  background: rgba(0, 212, 170, 0.2);
  color: var(--accent-tertiary);
  padding: 0 3px;
  border-radius: 2px;
}

/* ===== Chat Panel ===== */
.chat-panel {
  width: 360px;
  flex-shrink: 0;
  background: var(--bg-secondary);
  border-left: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
}

.chat-panel-header {
  padding: 0.65rem 1rem;
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.chat-panel-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.chat-panel-icon { font-size: 1rem; }

.chat-panel-close {
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  font-size: 0.9rem;
  padding: 2px 4px;
  border-radius: var(--radius-sm);
  transition: all 0.15s;
}
.chat-panel-close:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  text-align: center;
  padding: 2rem 1rem;
}
.chat-empty-icon { font-size: 2rem; opacity: 0.5; }
.chat-empty-text { font-size: 0.85rem; color: var(--text-secondary); }

.chat-empty-hints {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  justify-content: center;
  margin-top: 0.75rem;
}

.hint-chip {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-medium);
  color: var(--text-secondary);
  padding: 0.3rem 0.65rem;
  border-radius: 999px;
  font-size: 0.72rem;
  cursor: pointer;
  transition: all 0.2s;
}
.hint-chip:hover {
  border-color: var(--accent-primary);
  color: var(--accent-primary);
  background: var(--accent-glow);
}

.chat-msg {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
}
.chat-msg.user { flex-direction: row-reverse; }

.chat-msg-avatar {
  font-size: 0.9rem;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--bg-tertiary);
}

.chat-msg-bubble {
  max-width: 80%;
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-md);
  font-size: 0.8rem;
  line-height: 1.55;
}
.chat-msg.user .chat-msg-bubble {
  background: var(--accent-glow);
  color: var(--text-primary);
  border: 1px solid rgba(0, 212, 170, 0.2);
}
.chat-msg.assistant .chat-msg-bubble {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.chat-msg-text { white-space: pre-wrap; word-break: break-word; }

/* Typing indicator */
.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}
.typing-indicator span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-tertiary);
  animation: typingBounce 1.2s infinite;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }

@keyframes typingBounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
  30% { transform: translateY(-6px); opacity: 1; }
}

.chat-input-area {
  padding: 0.6rem;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  gap: 0.4rem;
}

.chat-input {
  flex: 1;
  padding: 0.5rem 0.7rem;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-medium);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: 0.8rem;
  outline: none;
  transition: border-color 0.2s;
}
.chat-input::placeholder { color: var(--text-tertiary); }
.chat-input:focus { border-color: var(--accent-primary); }

.chat-send-btn {
  background: var(--accent-primary);
  border: none;
  color: var(--bg-primary);
  width: 34px;
  height: 34px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: opacity 0.15s;
}
.chat-send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.chat-send-btn:not(:disabled):hover { opacity: 0.85; }

/* Chat slide transition */
.slide-chat-enter-active,
.slide-chat-leave-active {
  transition: all 0.25s ease;
}
.slide-chat-enter-from,
.slide-chat-leave-to {
  width: 0;
  opacity: 0;
  border-left-color: transparent;
}

/* ===== Modal ===== */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(6px);
}

.modal-content {
  background: var(--bg-secondary);
  border: 1px solid var(--border-medium);
  border-radius: var(--radius-lg);
  width: 90vw;
  max-width: 960px;
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1.2rem;
  border-bottom: 1px solid var(--border-subtle);
}

.modal-title-group {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.modal-index {
  font-weight: 700;
  font-size: 1rem;
  color: var(--accent-primary);
  font-family: var(--font-mono);
}

.modal-time-range {
  font-size: 0.8rem;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

.modal-close {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-medium);
  color: var(--text-secondary);
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.modal-close:hover {
  background: var(--error);
  color: #fff;
  border-color: var(--error);
}

.modal-body {
  overflow-y: auto;
  flex: 1;
}

.modal-player {
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
  position: relative;
}

.modal-player :deep(.video-container) { height: 100%; }

.modal-summary-section {
  padding: 1rem 1.2rem;
  border-top: 1px solid var(--border-subtle);
}

.modal-summary-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-tertiary);
  margin-bottom: 0.4rem;
  font-weight: 600;
}

.modal-summary-text {
  font-size: 0.88rem;
  color: var(--text-secondary);
  line-height: 1.7;
}

/* Modal transition */
.modal-enter-active,
.modal-leave-active {
  transition: all 0.25s ease;
}
.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}
.modal-enter-from .modal-content,
.modal-leave-to .modal-content {
  transform: scale(0.95) translateY(10px);
}

/* Larger review surface for OR monitoring. */
.overview-container {
  font-size: 1.18rem;
}

.overview-title {
  font-size: 1.8rem;
}

.back-btn,
.total-duration,
.search-input,
.chat-toggle-btn,
.filter-chip {
  font-size: 1.12rem;
}

.count-num {
  font-size: 1.28rem;
}

.window-grid {
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 1.1rem;
}

.window-card {
  min-height: 320px;
}

.card-summary {
  font-size: 1.22rem;
  line-height: 1.75;
}

.card-index-overlay {
  font-size: 1.22rem;
}

.card-time-overlay,
.card-phase {
  font-size: 1.08rem;
}

.modal-content {
  max-width: 1120px;
}

.modal-index {
  font-size: 1.35rem;
}

.modal-time-range,
.modal-summary-label {
  font-size: 1.12rem;
}

.modal-summary-text {
  font-size: 1.35rem;
  line-height: 1.75;
}

.chat-panel-title,
.chat-input,
.chat-message,
.message-content {
  font-size: 1.16rem;
}

.chat-empty-text,
.chat-empty-hint,
.message-time {
  font-size: 1rem;
}
</style>
