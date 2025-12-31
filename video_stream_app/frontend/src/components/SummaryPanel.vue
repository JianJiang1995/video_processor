<template>
  <div class="summary-panel">
    <!-- Header -->
    <div class="summary-header">
      <div class="summary-title">
        <span class="summary-title-icon">📝</span>
        GLM 分析摘要
      </div>
      <div class="window-indicator" v-if="currentSummary">
        窗口 {{ currentSummary.window_id + 1 }}
      </div>
    </div>
    
    <!-- Current Summary (Large) -->
    <div class="current-summary" v-if="currentSummary || isProcessing">
      <div class="summary-card active" :class="{ highlighted: isHighlighted }">
        <div class="summary-time">
          <span>⏱️</span>
          <span v-if="currentSummary">
            {{ formatTime(currentSummary.start_time) }} - {{ formatTime(currentSummary.end_time) }}
          </span>
          <span v-else>处理中...</span>
        </div>
        
        <div class="summary-text" :class="{ loading: !currentSummary }">
          {{ currentSummary?.summary || '正在分析视频片段...' }}
        </div>
        
        <div class="summary-actions" v-if="currentSummary">
          <button 
            class="action-btn" 
            :class="{ playing: isTTSPlaying }"
            @click="playTTS"
          >
            {{ isTTSPlaying ? '⏹️ 停止' : '🔊 朗读' }}
          </button>
          <button class="action-btn" @click="showSAM2">
            🎯 检测工具
          </button>
          <button class="action-btn" @click="copySummary">
            📋 复制
          </button>
        </div>
      </div>
    </div>
    
    <!-- Empty State -->
    <div v-else class="empty-state">
      <div class="empty-icon">📊</div>
      <div class="empty-text">暂无分析结果</div>
      <div class="empty-hint">点击「开始分析」开始处理视频</div>
    </div>
    
    <!-- Summary History -->
    <div class="summary-content" ref="historyContainer">
      <div class="summary-history-title" v-if="summaries.length > 0">
        历史记录
      </div>
      
      <div
        v-for="summary in sortedSummaries"
        :key="summary.window_id"
        :ref="el => setItemRef(summary.window_id, el)"
        class="summary-card"
        :class="{ 
          active: summary.window_id === currentSummary?.window_id,
          highlighted: summary.window_id === highlightedWindowId,
          'already-animated': animatedWindows.has(summary.window_id)
        }"
        @click="handleCardClick(summary)"
      >
        <div class="summary-badge">
          窗口 {{ summary.window_id + 1 }}
        </div>
        <div class="summary-time">
          <span>⏱️</span>
          {{ formatTime(summary.start_time) }} - {{ formatTime(summary.end_time) }}
        </div>
        <div class="summary-text-preview">
          {{ truncate(summary.summary, 120) }}
        </div>
        <div class="expand-hint">点击查看完整内容</div>
      </div>
      
      <!-- Loading indicator -->
      <div v-if="isProcessing" class="processing-indicator">
        <div class="loader-small"></div>
        <span>正在分析下一个片段...</span>
      </div>
    </div>
    
    <!-- Status Bar -->
    <div class="status-bar">
      <div class="status-dot" :class="{ active: summaries.length > 0, processing: isProcessing }"></div>
      <span v-if="isProcessing">SurgR1 + GLM 处理中...</span>
      <span v-else-if="summaries.length > 0">已分析 {{ summaries.length }} 个片段</span>
      <span v-else>就绪</span>
    </div>
    
    <!-- Full Content Popup -->
    <Teleport to="body">
      <Transition name="popup-fade">
        <div v-if="popupSummary" class="summary-popup-overlay" @click.self="closePopup">
          <div class="summary-popup">
            <div class="popup-header">
              <div class="popup-title">
                <span class="popup-icon">📝</span>
                <span>窗口 {{ popupSummary.window_id + 1 }} 分析摘要</span>
              </div>
              <button class="popup-close" @click="closePopup">✕</button>
            </div>
            <div class="popup-time">
              <span>⏱️</span>
              {{ formatTime(popupSummary.start_time) }} - {{ formatTime(popupSummary.end_time) }}
            </div>
            <div class="popup-content">
              {{ popupSummary.summary }}
            </div>
            <div class="popup-actions">
              <button class="popup-btn" @click="handlePopupSeek">
                ▶️ 跳转播放
              </button>
              <button class="popup-btn" @click="handlePopupCopy">
                📋 复制内容
              </button>
              <button class="popup-btn" @click="handlePopupTTS">
                🔊 语音朗读
              </button>
            </div>
            <div class="popup-hint">
              点击外部区域或 ✕ 关闭弹窗
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick, shallowRef } from 'vue'

const props = defineProps({
  summaries: {
    type: Array,
    default: () => []
  },
  currentSummary: Object,
  currentTime: Number,
  isProcessing: Boolean,
  highlightedWindowId: {
    type: Number,
    default: -1
  }
})

const emit = defineEmits(['tts', 'sam2', 'seek', 'seekToWindow', 'play'])

const isTTSPlaying = ref(false)
const currentAudio = ref(null)
const historyContainer = ref(null)
const itemRefs = ref({})

// Popup state
const popupSummary = ref(null)

// Track last scrolled window to prevent repeated scrolling
const lastScrolledWindowId = ref(-1)
// Track last highlighted window to prevent animation retrigger
const lastHighlightedWindowId = ref(-1)
// Track windows that have already been animated (to prevent re-animation)
// Using reactive Set for template access
const animatedWindows = reactive(new Set())

// Sorted summaries by window_id (ascending) - use shallowRef for better performance
// Only recompute when summaries array length changes
const sortedSummaries = computed(() => {
  // Create a stable sorted array that won't cause unnecessary re-renders
  const sorted = [...props.summaries].sort((a, b) => a.window_id - b.window_id)
  return sorted
})

// Check if current summary is highlighted
const isHighlighted = computed(() => {
  return props.currentSummary && props.highlightedWindowId === props.currentSummary.window_id
})

// Store refs for each summary card
const setItemRef = (windowId, el) => {
  if (el) {
    itemRefs.value[windowId] = el
  }
}

// Watch for highlighted window changes and scroll to it
// Add debounce to prevent excessive scrolling
let scrollDebounceTimer = null
watch(() => props.highlightedWindowId, async (newWindowId, oldWindowId) => {
  // Only scroll if window actually changed and is valid
  if (newWindowId >= 0 && newWindowId !== lastScrolledWindowId.value) {
    // Clear any pending scroll
    if (scrollDebounceTimer) {
      clearTimeout(scrollDebounceTimer)
    }
    // Debounce scroll to prevent flickering
    scrollDebounceTimer = setTimeout(async () => {
      await nextTick()
      scrollToWindow(newWindowId)
      lastScrolledWindowId.value = newWindowId
    }, 100)
  }
  
  // Track animated windows to prevent re-animation on same window
  // Only mark as animated after a short delay (so animation can play first)
  if (newWindowId >= 0 && !animatedWindows.has(newWindowId)) {
    setTimeout(() => {
      animatedWindows.add(newWindowId)
    }, 700)  // Slightly longer than animation duration (0.6s)
  }
})

// Watch for current summary changes - but only scroll on initial change, not during loop playback
watch(() => props.currentSummary?.window_id, async (newWindowId, oldWindowId) => {
  // Only scroll if this is a different window than before
  if (newWindowId !== undefined && newWindowId >= 0 && newWindowId !== oldWindowId) {
    // Don't scroll if we just scrolled to this window via highlight
    if (newWindowId !== lastScrolledWindowId.value) {
      await nextTick()
      scrollToWindow(newWindowId)
      lastScrolledWindowId.value = newWindowId
    }
  }
})

const scrollToWindow = (windowId) => {
  const element = itemRefs.value[windowId]
  if (element && historyContainer.value) {
    element.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

const playTTS = () => {
  if (isTTSPlaying.value && currentAudio.value) {
    currentAudio.value.pause()
    currentAudio.value = null
    isTTSPlaying.value = false
    return
  }
  
  if (props.currentSummary) {
    isTTSPlaying.value = true
    emit('tts', props.currentSummary)
    
    // Simulate TTS completion
    setTimeout(() => {
      isTTSPlaying.value = false
    }, 5000)
  }
}

const showSAM2 = () => {
  if (props.currentSummary) {
    emit('sam2', props.currentSummary.start_time)
  }
}

const copySummary = () => {
  if (props.currentSummary?.summary) {
    navigator.clipboard.writeText(props.currentSummary.summary)
  }
}

const seekToWindow = (summary) => {
  emit('seek', summary.start_time)
  emit('seekToWindow', summary.window_id)
}

// Handle card click - show popup with full content
const handleCardClick = (summary) => {
  // If clicking same card that's already showing, close popup and seek
  if (popupSummary.value?.window_id === summary.window_id) {
    closePopup()
    seekToWindow(summary)
    return
  }
  
  // Show popup with this summary (create a copy to prevent reactivity issues)
  popupSummary.value = { ...summary }
}

// Close popup
const closePopup = () => {
  popupSummary.value = null
}

// Popup actions
const handlePopupSeek = () => {
  if (popupSummary.value) {
    seekToWindow(popupSummary.value)
    // 跳转后自动播放
    emit('play')
    closePopup()
  }
}

const handlePopupCopy = () => {
  if (popupSummary.value?.summary) {
    navigator.clipboard.writeText(popupSummary.value.summary)
  }
}

const handlePopupTTS = () => {
  if (popupSummary.value) {
    emit('tts', popupSummary.value)
  }
}

const formatTime = (seconds) => {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
}

const truncate = (text, length) => {
  if (!text) return ''
  if (text.length <= length) return text
  return text.substring(0, length) + '...'
}
</script>

<style scoped>
.summary-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.current-summary {
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border-subtle);
}

.summary-card.highlighted {
  /* Use animation only on initial highlight, with fill-mode to keep final state */
  animation: highlight-pulse 0.6s ease-out forwards;
  animation-iteration-count: 1;
  border-color: var(--accent-primary);
  box-shadow: 0 0 12px rgba(0, 212, 170, 0.3);
  /* Prevent animation restart on component re-render */
  will-change: transform, box-shadow;
}

/* Prevent animation from restarting when window has already been animated */
.summary-card.highlighted.already-animated {
  animation: none;
  /* Keep the highlighted style without animation */
  border-color: var(--accent-primary);
  box-shadow: 0 0 12px rgba(0, 212, 170, 0.3);
}

@keyframes highlight-pulse {
  0% {
    transform: scale(1);
    box-shadow: 0 0 4px rgba(0, 212, 170, 0.1);
  }
  50% {
    transform: scale(1.01);
    box-shadow: 0 0 20px rgba(0, 212, 170, 0.4);
  }
  100% {
    transform: scale(1);
    box-shadow: 0 0 12px rgba(0, 212, 170, 0.3);
  }
}

.empty-state {
  padding: 3rem 1.25rem;
  text-align: center;
  color: var(--text-tertiary);
}

.empty-icon {
  font-size: 3rem;
  margin-bottom: 1rem;
  opacity: 0.5;
}

.empty-text {
  font-size: 1.1rem;
  margin-bottom: 0.5rem;
  color: var(--text-secondary);
}

.empty-hint {
  font-size: 0.875rem;
}

.summary-history-title {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--text-tertiary);
  margin-bottom: 0.75rem;
}

.summary-badge {
  display: inline-block;
  font-size: 0.7rem;
  padding: 0.15rem 0.5rem;
  background: var(--accent-primary);
  color: var(--bg-primary);
  border-radius: var(--radius-sm);
  margin-bottom: 0.5rem;
  font-weight: 600;
}

.summary-text-preview {
  font-size: 0.85rem;
  color: var(--text-secondary);
  line-height: 1.5;
}

.summary-card:not(.active) {
  cursor: pointer;
  opacity: 0.7;
  transition: all 0.2s ease;
}

.summary-card:not(.active):hover {
  opacity: 1;
  transform: translateX(4px);
}

.summary-card.highlighted:not(.active) {
  opacity: 1;
  border-left: 3px solid var(--accent-primary);
  padding-left: calc(1rem - 3px);
}

.processing-indicator {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem;
  color: var(--text-secondary);
  font-size: 0.875rem;
  background: var(--bg-tertiary);
  border-radius: var(--radius-md);
  margin-top: 0.5rem;
}

.loader-small {
  width: 20px;
  height: 20px;
  border: 2px solid var(--bg-elevated);
  border-top-color: var(--accent-primary);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Expand hint on cards */
.expand-hint {
  font-size: 0.7rem;
  color: var(--text-tertiary);
  margin-top: 0.5rem;
  opacity: 0;
  transition: opacity 0.2s;
}

.summary-card:hover .expand-hint {
  opacity: 0.7;
}

/* Popup Overlay */
.summary-popup-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 2rem;
}

/* Popup Container */
.summary-popup {
  background: linear-gradient(145deg, var(--bg-elevated, #1a1a2e) 0%, var(--bg-primary, #12122a) 100%);
  border: 2px solid var(--accent-primary, #00d4aa);
  border-radius: var(--radius-lg, 16px);
  max-width: 600px;
  width: 100%;
  max-height: 80vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow: 
    0 20px 60px rgba(0, 0, 0, 0.5),
    0 0 40px rgba(0, 212, 170, 0.15),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
  animation: popup-scale-in 0.25s ease-out;
}

.summary-popup.locked {
  border-color: var(--accent-secondary, #00bcd4);
  box-shadow: 
    0 20px 60px rgba(0, 0, 0, 0.5),
    0 0 50px rgba(0, 188, 212, 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

@keyframes popup-scale-in {
  from {
    opacity: 0;
    transform: scale(0.9) translateY(20px);
  }
  to {
    opacity: 1;
    transform: scale(1) translateY(0);
  }
}

.popup-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 1.25rem;
  background: rgba(0, 212, 170, 0.1);
  border-bottom: 1px solid rgba(0, 212, 170, 0.2);
}

.popup-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 1rem;
  font-weight: 600;
  color: var(--text-primary, #fff);
}

.popup-icon {
  font-size: 1.2rem;
}

.lock-badge {
  font-size: 0.7rem;
  padding: 0.2rem 0.5rem;
  background: rgba(0, 188, 212, 0.2);
  color: var(--accent-secondary, #00bcd4);
  border-radius: var(--radius-sm, 4px);
  margin-left: 0.5rem;
}

.popup-close {
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: var(--text-secondary, #aaa);
  width: 32px;
  height: 32px;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1rem;
  transition: all 0.2s;
}

.popup-close:hover {
  background: rgba(255, 100, 100, 0.3);
  color: #ff6b6b;
}

.popup-time {
  padding: 0.75rem 1.25rem;
  font-size: 0.875rem;
  font-family: var(--font-mono, monospace);
  color: var(--text-secondary, #8888aa);
  background: rgba(0, 0, 0, 0.2);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.popup-content {
  flex: 1;
  padding: 1.25rem;
  font-size: 1rem;
  line-height: 1.8;
  color: var(--text-primary, #e8e8f0);
  overflow-y: auto;
  min-height: 150px;
  max-height: 400px;
}

.popup-content::-webkit-scrollbar {
  width: 6px;
}

.popup-content::-webkit-scrollbar-track {
  background: transparent;
}

.popup-content::-webkit-scrollbar-thumb {
  background: rgba(0, 212, 170, 0.3);
  border-radius: 3px;
}

.popup-content::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 212, 170, 0.5);
}

.popup-actions {
  display: flex;
  gap: 0.75rem;
  padding: 1rem 1.25rem;
  background: rgba(0, 0, 0, 0.15);
  border-top: 1px solid rgba(255, 255, 255, 0.05);
}

.popup-btn {
  flex: 1;
  padding: 0.6rem 1rem;
  background: rgba(0, 212, 170, 0.15);
  border: 1px solid rgba(0, 212, 170, 0.3);
  border-radius: var(--radius-md, 8px);
  color: var(--accent-primary, #00d4aa);
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
}

.popup-btn:hover {
  background: rgba(0, 212, 170, 0.25);
  border-color: var(--accent-primary, #00d4aa);
  transform: translateY(-1px);
}

.popup-btn:active {
  transform: translateY(0);
}

.popup-hint {
  padding: 0.6rem 1.25rem;
  font-size: 0.75rem;
  color: var(--text-tertiary, #6666aa);
  text-align: center;
  background: rgba(0, 0, 0, 0.1);
}

/* Transition animations */
.popup-fade-enter-active,
.popup-fade-leave-active {
  transition: opacity 0.2s ease;
}

.popup-fade-enter-from,
.popup-fade-leave-to {
  opacity: 0;
}

.popup-fade-enter-active .summary-popup,
.popup-fade-leave-active .summary-popup {
  transition: transform 0.2s ease, opacity 0.2s ease;
}

.popup-fade-enter-from .summary-popup,
.popup-fade-leave-to .summary-popup {
  transform: scale(0.9) translateY(20px);
  opacity: 0;
}
</style>




