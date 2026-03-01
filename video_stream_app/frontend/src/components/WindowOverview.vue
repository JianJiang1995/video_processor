<template>
  <div class="overview-container">
    <!-- Header -->
    <header class="overview-header">
      <button class="back-btn" @click="$emit('back')">← 返回</button>
      <h1 class="overview-title">窗口一览</h1>
      <span class="window-count">{{ filteredSummaries.length }} / {{ summaries.length }} 个窗口</span>
    </header>

    <!-- Filter Bar -->
    <div class="filter-bar">
      <div class="search-wrapper">
        <span class="search-icon">🔍</span>
        <input
          v-model="filterText"
          type="text"
          class="search-input"
          placeholder="输入关键词筛选窗口..."
        />
        <button v-if="filterText" class="clear-btn" @click="filterText = ''">✕</button>
      </div>
    </div>

    <!-- Empty State -->
    <div v-if="summaries.length === 0" class="empty-state">
      <div class="empty-icon">📊</div>
      <div class="empty-text">暂无分析窗口</div>
    </div>

    <!-- No Results -->
    <div v-else-if="filteredSummaries.length === 0" class="empty-state">
      <div class="empty-icon">🔎</div>
      <div class="empty-text">没有匹配「{{ filterText }}」的窗口</div>
    </div>

    <!-- Window Grid -->
    <div v-else class="window-grid" ref="gridRef">
      <div
        v-for="s in filteredSummaries"
        :key="s.window_id"
        class="window-card"
        @click="handleCardClick(s)"
      >
        <div class="card-header">
          <span class="card-label">窗口 {{ s.window_id }}</span>
          <span class="card-time">{{ formatTime(s.start_time) }} – {{ formatTime(s.end_time) }}</span>
        </div>
        <p class="card-summary" v-html="highlightText(truncate(s.summary, 120))"></p>
      </div>
    </div>

    <!-- Loop Playback Modal (stream mode only) -->
    <Teleport to="body">
      <div v-if="activeWindow" class="modal-overlay" @click.self="closeModal">
        <div class="modal-content">
          <div class="modal-header">
            <span class="modal-title">
              窗口 {{ activeWindow.window_id }}
              <span class="modal-time">{{ formatTime(activeWindow.start_time) }} – {{ formatTime(activeWindow.end_time) }}</span>
            </span>
            <button class="modal-close" @click="closeModal">✕</button>
          </div>

          <div class="modal-body">
            <!-- Stream mode: embed VideoPlayer for loop playback -->
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

            <!-- Summary text -->
            <div class="modal-summary">
              <p>{{ activeWindow.summary }}</p>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
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
  }
})

const emit = defineEmits(['back', 'seekToWindow'])

const filterText = ref('')
const activeWindow = ref(null)
const modalCurrentTime = ref(0)
const gridRef = ref(null)

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
  return text.replace(regex, '<mark class="highlight">$1</mark>')
}
</script>

<style scoped>
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
  padding: 0.75rem 1.5rem;
  display: flex;
  align-items: center;
  gap: 1rem;
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(12px);
}

.back-btn {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-medium);
  color: var(--text-secondary);
  padding: 0.4rem 0.9rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 0.85rem;
  transition: all 0.2s;
}
.back-btn:hover {
  background: var(--bg-elevated);
  color: var(--text-primary);
}

.overview-title {
  font-size: 1.2rem;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.02em;
}

.window-count {
  margin-left: auto;
  font-size: 0.8rem;
  color: var(--text-tertiary);
  background: var(--bg-tertiary);
  padding: 0.25rem 0.7rem;
  border-radius: var(--radius-sm);
}

/* ===== Filter Bar ===== */
.filter-bar {
  padding: 1rem 1.5rem 0.5rem;
}

.search-wrapper {
  position: relative;
  max-width: 480px;
}

.search-icon {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 0.9rem;
  pointer-events: none;
}

.search-input {
  width: 100%;
  padding: 0.6rem 2.2rem 0.6rem 2.4rem;
  background: var(--bg-secondary);
  border: 1px solid var(--border-medium);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-size: 0.9rem;
  outline: none;
  transition: border-color 0.2s;
}
.search-input::placeholder {
  color: var(--text-tertiary);
}
.search-input:focus {
  border-color: var(--accent-primary);
}

.clear-btn {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  font-size: 0.85rem;
  padding: 2px 4px;
}
.clear-btn:hover {
  color: var(--text-primary);
}

/* ===== Empty State ===== */
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  color: var(--text-tertiary);
  padding: 4rem 1rem;
}

.empty-icon {
  font-size: 2.5rem;
  opacity: 0.6;
}

.empty-text {
  font-size: 1rem;
}

/* ===== Window Grid ===== */
.window-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 1rem;
  padding: 1rem 1.5rem 2rem;
  overflow-y: auto;
  flex: 1;
}

.window-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 1rem 1.2rem;
  cursor: pointer;
  transition: all 0.2s ease;
}
.window-card:hover {
  border-color: var(--accent-primary);
  box-shadow: var(--shadow-glow);
  transform: translateY(-2px);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.6rem;
}

.card-label {
  font-weight: 600;
  font-size: 0.9rem;
  color: var(--accent-primary);
}

.card-time {
  font-size: 0.75rem;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

.card-summary {
  font-size: 0.85rem;
  color: var(--text-secondary);
  line-height: 1.55;
  word-break: break-all;
}

.card-summary :deep(.highlight) {
  background: rgba(0, 212, 170, 0.25);
  color: var(--accent-tertiary);
  padding: 0 2px;
  border-radius: 2px;
}

/* ===== Modal ===== */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(4px);
}

.modal-content {
  background: var(--bg-secondary);
  border: 1px solid var(--border-medium);
  border-radius: var(--radius-lg);
  width: 90vw;
  max-width: 960px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: var(--shadow-lg);
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.8rem 1.2rem;
  border-bottom: 1px solid var(--border-subtle);
}

.modal-title {
  font-weight: 600;
  font-size: 1rem;
  color: var(--accent-primary);
}

.modal-time {
  font-size: 0.8rem;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  margin-left: 0.75rem;
}

.modal-close {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-medium);
  color: var(--text-secondary);
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 1rem;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
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

.modal-player :deep(.video-container) {
  height: 100%;
}

.modal-summary {
  padding: 1rem 1.2rem;
  font-size: 0.9rem;
  color: var(--text-secondary);
  line-height: 1.7;
  border-top: 1px solid var(--border-subtle);
}
</style>
