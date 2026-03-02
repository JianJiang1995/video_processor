<template>
  <div class="overview-container">
    <!-- Header -->
    <header class="overview-header">
      <div class="header-left">
        <button class="back-btn" @click="$emit('back')">
          <span class="back-arrow">←</span>
          <span>返回</span>
        </button>
        <div class="title-group">
          <h1 class="overview-title">窗口一览</h1>
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
          <span>分析中…</span>
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
            placeholder="搜索窗口内容..."
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
          <span>智能问答</span>
        </button>
      </div>
    </div>

    <!-- Main body: Grid + Chat side panel -->
    <div class="overview-body">
      <!-- Grid area -->
      <div class="grid-area">
        <!-- Empty State -->
        <div v-if="summaries.length === 0" class="empty-state">
          <div class="empty-visual">
            <svg viewBox="0 0 80 80" fill="none" width="80" height="80">
              <rect x="8" y="16" width="64" height="48" rx="8" stroke="var(--text-tertiary)" stroke-width="2" stroke-dasharray="4 4"/>
              <circle cx="40" cy="40" r="12" stroke="var(--accent-primary)" stroke-width="2" opacity="0.5"/>
              <path d="M36 40l3 3 5-6" stroke="var(--accent-primary)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.5"/>
            </svg>
          </div>
          <div class="empty-text">暂无分析窗口</div>
          <div class="empty-hint">返回主界面开始分析视频</div>
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
          <div class="empty-text">没有匹配的窗口</div>
          <div class="empty-hint">尝试其他关键词 · 当前搜索「{{ filterText }}」</div>
        </div>

        <!-- Window Grid -->
        <div v-else class="window-grid">
          <div
            v-for="(s, idx) in filteredSummaries"
            :key="s.window_id"
            class="window-card"
            :style="{ animationDelay: `${idx * 40}ms` }"
            @click="handleCardClick(s)"
          >
            <!-- Card top accent line -->
            <div class="card-accent-line"></div>

            <div class="card-body">
              <div class="card-header">
                <span class="card-index">#{{ s.window_id }}</span>
                <span class="card-time">
                  {{ formatTime(s.start_time) }} – {{ formatTime(s.end_time) }}
                </span>
              </div>

              <!-- Duration mini-bar -->
              <div class="card-duration-bar">
                <div
                  class="card-duration-fill"
                  :style="{ width: durationPercent(s) + '%' }"
                ></div>
              </div>

              <p class="card-summary" v-html="highlightText(truncate(s.summary, 140))"></p>
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
              智能问答
            </span>
            <button class="chat-panel-close" @click="showChat = false">✕</button>
          </div>

          <div class="chat-messages" ref="chatMessagesRef">
            <div v-if="chatMessages.length === 0" class="chat-empty">
              <div class="chat-empty-icon">💬</div>
              <div class="chat-empty-text">关于这些分析窗口有什么想了解的？</div>
              <div class="chat-empty-hints">
                <button class="hint-chip" @click="sendChat('总结所有窗口的主要发现')">总结主要发现</button>
                <button class="hint-chip" @click="sendChat('哪些窗口涉及关键操作步骤？')">关键操作步骤</button>
                <button class="hint-chip" @click="sendChat('有没有需要注意的异常情况？')">异常情况</button>
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
              placeholder="问一个关于视频的问题..."
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
                  @timeupdate="modalCurrentTime = $event"
                  @exitLoop="closeModal"
                  @loopLoadFailed="closeModal"
                />
              </div>
              <div class="modal-summary-section">
                <div class="modal-summary-label">分析摘要</div>
                <p class="modal-summary-text">{{ activeWindow.summary }}</p>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import VideoPlayer from './VideoPlayer.vue'

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

// Modal
const activeWindow = ref(null)
const modalCurrentTime = ref(0)

// Computed
const filteredSummaries = computed(() => {
  if (!filterText.value.trim()) return props.summaries
  const keyword = filterText.value.trim().toLowerCase()
  return props.summaries.filter(s =>
    s.summary && s.summary.toLowerCase().includes(keyword)
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
    `[窗口${s.window_id} ${formatTime(s.start_time)}-${formatTime(s.end_time)}] ${s.summary}`
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
        content: response.data.error || '抱歉，暂时无法回答。'
      })
    }
  } catch (e) {
    chatMessages.value.push({
      role: 'assistant',
      content: '网络错误，请稍后再试。'
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

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
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
  font-size: 0.8rem;
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
.back-arrow { font-size: 0.9rem; }

.title-group {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.overview-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.02em;
}

.window-count-badge {
  display: inline-flex;
  align-items: baseline;
  gap: 0.15rem;
  font-size: 0.75rem;
  background: var(--accent-glow);
  border: 1px solid rgba(0, 212, 170, 0.2);
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
}
.count-num { color: var(--accent-primary); font-weight: 700; font-size: 0.85rem; }
.count-sep { color: var(--text-tertiary); }
.count-total { color: var(--text-tertiary); }

.header-right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.total-duration {
  font-size: 0.8rem;
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

.card-accent-line {
  height: 2px;
  background: linear-gradient(90deg, var(--accent-primary), var(--accent-secondary));
  opacity: 0;
  transition: opacity 0.2s;
}
.window-card:hover .card-accent-line { opacity: 1; }

.card-body { padding: 0.85rem 1rem; }

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.45rem;
}

.card-index {
  font-weight: 700;
  font-size: 0.85rem;
  color: var(--accent-primary);
  font-family: var(--font-mono);
}

.card-time {
  font-size: 0.7rem;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

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
  font-size: 0.8rem;
  color: var(--text-secondary);
  line-height: 1.6;
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
</style>
