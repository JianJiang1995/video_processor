<template>
  <div class="summary-panel">
    <!-- Header -->
    <div class="summary-header">
      <div class="summary-title">
        <span class="summary-title-icon">📝</span>
        分析摘要
      </div>
      <div class="window-indicator" v-if="displaySummary">
        窗口 {{ displaySummary.window_id + 1 }}
      </div>
    </div>
    
    <!-- Current Summary (Large) - 始终显示最新的分析结果 -->
    <div class="current-summary" v-if="displaySummary">
      <div class="summary-card active" :class="{ highlighted: isHighlighted }">
        <div class="summary-time">
          <span>⏱️</span>
          <span>
            {{ formatTime(displaySummary.start_time) }} - {{ formatTime(displaySummary.end_time) }}
          </span>
        </div>
        
        <div class="summary-text">
          {{ displaySummary.summary }}
        </div>
        
        <div class="summary-actions">
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
          active: summary.window_id === displaySummary?.window_id,
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
      <div class="status-left">
        <div class="status-dot" :class="{ active: summaries.length > 0, processing: isProcessing }"></div>
        <span v-if="isProcessing">SurgR1 + GLM 处理中...</span>
        <span v-else-if="summaries.length > 0">已分析 {{ summaries.length }} 个片段</span>
        <span v-else>就绪</span>
      </div>
      <button 
        v-if="summaries.length > 0" 
        class="export-btn"
        @click="showExportDialog"
        :disabled="isExporting"
      >
        {{ isExporting ? '⏳ 导出中...' : '📤 导出视频' }}
      </button>
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
      
      <!-- Export Dialog -->
      <Transition name="popup-fade">
        <div v-if="showExportPopup" class="summary-popup-overlay" @click.self="closeExportDialog">
          <div class="export-popup">
            <div class="popup-header">
              <div class="popup-title">
                <span class="popup-icon">📤</span>
                <span>导出分析视频</span>
              </div>
              <button class="popup-close" @click="closeExportDialog">✕</button>
            </div>
            
            <div class="export-content">
              <div class="export-info">
                选择要导出的窗口，每个窗口将生成带右侧分析文字的独立视频片段。
              </div>
              
              <div class="export-select-all">
                <label class="checkbox-label">
                  <input 
                    type="checkbox" 
                    :checked="isAllSelected"
                    @change="toggleSelectAll"
                  />
                  <span>{{ isAllSelected ? '取消全选' : '全选所有窗口' }}</span>
                </label>
                <span class="selected-count">已选择 {{ selectedWindowIds.length }} / {{ summaries.length }}</span>
              </div>
              
              <div class="export-window-list">
                <label 
                  v-for="summary in sortedSummaries" 
                  :key="summary.window_id"
                  class="window-checkbox"
                >
                  <input 
                    type="checkbox" 
                    :value="summary.window_id"
                    v-model="selectedWindowIds"
                  />
                  <div class="window-info">
                    <div class="window-header">
                      <span class="window-badge">窗口 {{ summary.window_id + 1 }}</span>
                      <span class="window-time">
                        {{ formatTime(summary.start_time) }} - {{ formatTime(summary.end_time) }}
                      </span>
                    </div>
                    <div class="window-preview">
                      {{ truncate(summary.summary, 80) }}
                    </div>
                  </div>
                </label>
              </div>
              
              <!-- Export Progress -->
              <div v-if="exportProgress" class="export-progress">
                <div class="progress-bar">
                  <div class="progress-fill" :style="{ width: exportProgress.progress + '%' }"></div>
                </div>
                <div class="progress-text">
                  {{ exportProgress.status === 'processing' ? '正在生成视频...' : '' }}
                  {{ exportProgress.completed || 0 }} / {{ exportProgress.total || 0 }} 完成
                </div>
              </div>
              
              <!-- Export Results -->
              <div v-if="exportResults.length > 0" class="export-results">
                <div class="results-title">导出完成</div>
                <div 
                  v-for="result in exportResults" 
                  :key="result.window_id"
                  class="result-item"
                  :class="{ failed: result.status === 'failed' }"
                >
                  <span>窗口 {{ result.window_id + 1 }}</span>
                  <a 
                    v-if="result.status === 'success'"
                    :href="result.download_url"
                    class="download-link"
                    download
                  >
                    ⬇️ 下载
                  </a>
                  <span v-else class="failed-badge">失败</span>
                </div>
              </div>
            </div>
            
            <div class="export-actions">
              <button 
                class="export-cancel-btn" 
                @click="closeExportDialog"
              >
                取消
              </button>
              <button 
                class="export-start-btn"
                @click="startExport"
                :disabled="selectedWindowIds.length === 0 || isExporting"
              >
                {{ isExporting ? '⏳ 导出中...' : `📤 开始导出 (${selectedWindowIds.length})` }}
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick, shallowRef, onUnmounted } from 'vue'

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
  },
  sessionId: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['tts', 'sam2', 'seek', 'seekToWindow', 'play'])

const isTTSPlaying = ref(false)
const currentAudio = ref(null)
const historyContainer = ref(null)
const itemRefs = ref({})

// Popup state
const popupSummary = ref(null)

// Export state
const showExportPopup = ref(false)
const selectedWindowIds = ref([])
const isExporting = ref(false)
const exportProgress = ref(null)
const exportResults = ref([])
let exportPollTimer = null

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

// 用于顶部显示的摘要：优先显示 currentSummary，否则显示最新的历史记录
const displaySummary = computed(() => {
  if (props.currentSummary) {
    return props.currentSummary
  }
  // 如果没有 currentSummary，显示最新的历史记录（window_id 最大的）
  if (props.summaries.length > 0) {
    return [...props.summaries].sort((a, b) => b.window_id - a.window_id)[0]
  }
  return null
})

// Check if current summary is highlighted
const isHighlighted = computed(() => {
  return displaySummary.value && props.highlightedWindowId === displaySummary.value.window_id
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
  
  if (displaySummary.value) {
    isTTSPlaying.value = true
    emit('tts', displaySummary.value)
    
    // Simulate TTS completion
    setTimeout(() => {
      isTTSPlaying.value = false
    }, 5000)
  }
}

const showSAM2 = () => {
  if (displaySummary.value) {
    emit('sam2', displaySummary.value.start_time)
  }
}

const copySummary = () => {
  if (displaySummary.value?.summary) {
    navigator.clipboard.writeText(displaySummary.value.summary)
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

// ============================================================
// Export functionality
// ============================================================

// Check if all windows are selected
const isAllSelected = computed(() => {
  return props.summaries.length > 0 && 
         selectedWindowIds.value.length === props.summaries.length
})

// Show export dialog
const showExportDialog = () => {
  // Default select all windows
  selectedWindowIds.value = props.summaries.map(s => s.window_id)
  exportProgress.value = null
  exportResults.value = []
  showExportPopup.value = true
}

// Close export dialog
const closeExportDialog = () => {
  showExportPopup.value = false
  stopExportPolling()
}

// Toggle select all
const toggleSelectAll = () => {
  if (isAllSelected.value) {
    selectedWindowIds.value = []
  } else {
    selectedWindowIds.value = props.summaries.map(s => s.window_id)
  }
}

// Start export process
const startExport = async () => {
  if (!props.sessionId || selectedWindowIds.value.length === 0) {
    console.warn('No session ID or no windows selected')
    return
  }
  
  isExporting.value = true
  exportProgress.value = { status: 'starting', progress: 0 }
  exportResults.value = []
  
  try {
    // Call export API
    const response = await fetch(`/api/analysis/export-clips/${props.sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ window_ids: selectedWindowIds.value })
    })
    
    if (!response.ok) {
      throw new Error(`Export failed: ${response.statusText}`)
    }
    
    const data = await response.json()
    const taskId = data.task_id
    
    // Start polling for progress
    startExportPolling(taskId)
    
  } catch (error) {
    console.error('Export error:', error)
    exportProgress.value = { status: 'failed', error: error.message }
    isExporting.value = false
  }
}

// Poll for export progress
const startExportPolling = (taskId) => {
  stopExportPolling()
  
  const poll = async () => {
    try {
      const response = await fetch(`/api/analysis/export-status/${taskId}`)
      if (!response.ok) throw new Error('Failed to get status')
      
      const status = await response.json()
      exportProgress.value = status
      
      if (status.status === 'completed') {
        exportResults.value = status.results || []
        isExporting.value = false
        stopExportPolling()
      } else if (status.status === 'failed') {
        isExporting.value = false
        stopExportPolling()
      } else {
        // Continue polling
        exportPollTimer = setTimeout(poll, 1000)
      }
    } catch (error) {
      console.error('Poll error:', error)
      exportPollTimer = setTimeout(poll, 2000)
    }
  }
  
  poll()
}

// Stop polling
const stopExportPolling = () => {
  if (exportPollTimer) {
    clearTimeout(exportPollTimer)
    exportPollTimer = null
  }
}

// Cleanup on unmount
onUnmounted(() => {
  stopExportPolling()
})
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

/* ============================================================
   Status Bar & Export Button
   ============================================================ */
.status-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1.25rem;
  border-top: 1px solid var(--border-subtle);
  background: var(--bg-tertiary);
}

.status-left {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.export-btn {
  padding: 0.4rem 0.8rem;
  background: linear-gradient(135deg, rgba(0, 212, 170, 0.15), rgba(0, 212, 170, 0.25));
  border: 1px solid rgba(0, 212, 170, 0.4);
  border-radius: var(--radius-md, 8px);
  color: var(--accent-primary, #00d4aa);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.2s;
}

.export-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, rgba(0, 212, 170, 0.25), rgba(0, 212, 170, 0.35));
  border-color: var(--accent-primary, #00d4aa);
  transform: translateY(-1px);
}

.export-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* ============================================================
   Export Popup
   ============================================================ */
.export-popup {
  background: linear-gradient(145deg, var(--bg-elevated, #1a1a2e) 0%, var(--bg-primary, #12122a) 100%);
  border: 2px solid var(--accent-primary, #00d4aa);
  border-radius: var(--radius-lg, 16px);
  max-width: 700px;
  width: 100%;
  max-height: 85vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow: 
    0 20px 60px rgba(0, 0, 0, 0.5),
    0 0 40px rgba(0, 212, 170, 0.15),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
  animation: popup-scale-in 0.25s ease-out;
}

.export-content {
  flex: 1;
  padding: 1rem 1.25rem;
  overflow-y: auto;
}

.export-info {
  font-size: 0.9rem;
  color: var(--text-secondary, #aaa);
  margin-bottom: 1rem;
  padding: 0.75rem;
  background: rgba(0, 212, 170, 0.1);
  border-radius: var(--radius-md, 8px);
  border-left: 3px solid var(--accent-primary, #00d4aa);
}

.export-select-all {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1rem;
  background: rgba(0, 0, 0, 0.2);
  border-radius: var(--radius-md, 8px);
  margin-bottom: 1rem;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  color: var(--text-primary, #fff);
  font-size: 0.9rem;
}

.checkbox-label input[type="checkbox"] {
  width: 18px;
  height: 18px;
  accent-color: var(--accent-primary, #00d4aa);
  cursor: pointer;
}

.selected-count {
  font-size: 0.8rem;
  color: var(--text-tertiary, #888);
}

.export-window-list {
  max-height: 300px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.export-window-list::-webkit-scrollbar {
  width: 6px;
}

.export-window-list::-webkit-scrollbar-track {
  background: transparent;
}

.export-window-list::-webkit-scrollbar-thumb {
  background: rgba(0, 212, 170, 0.3);
  border-radius: 3px;
}

.window-checkbox {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.75rem;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: var(--radius-md, 8px);
  cursor: pointer;
  transition: all 0.2s;
}

.window-checkbox:hover {
  background: rgba(255, 255, 255, 0.05);
  border-color: rgba(0, 212, 170, 0.3);
}

.window-checkbox input[type="checkbox"] {
  margin-top: 2px;
  width: 16px;
  height: 16px;
  accent-color: var(--accent-primary, #00d4aa);
  cursor: pointer;
  flex-shrink: 0;
}

.window-info {
  flex: 1;
  min-width: 0;
}

.window-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.4rem;
}

.window-badge {
  font-size: 0.7rem;
  padding: 0.15rem 0.5rem;
  background: var(--accent-primary);
  color: var(--bg-primary);
  border-radius: var(--radius-sm);
  font-weight: 600;
}

.window-time {
  font-size: 0.75rem;
  color: var(--text-tertiary, #888);
  font-family: var(--font-mono, monospace);
}

.window-preview {
  font-size: 0.8rem;
  color: var(--text-secondary, #aaa);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

/* Export Progress */
.export-progress {
  padding: 1rem;
  background: rgba(0, 0, 0, 0.2);
  border-radius: var(--radius-md, 8px);
  margin-bottom: 1rem;
}

.progress-bar {
  height: 8px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 0.5rem;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent-primary, #00d4aa), #00bcd4);
  border-radius: 4px;
  transition: width 0.3s ease;
}

.progress-text {
  font-size: 0.8rem;
  color: var(--text-secondary, #aaa);
  text-align: center;
}

/* Export Results */
.export-results {
  border: 1px solid rgba(0, 212, 170, 0.3);
  border-radius: var(--radius-md, 8px);
  overflow: hidden;
}

.results-title {
  padding: 0.75rem 1rem;
  background: rgba(0, 212, 170, 0.1);
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--accent-primary, #00d4aa);
}

.result-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.6rem 1rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  font-size: 0.85rem;
  color: var(--text-primary, #fff);
}

.result-item:last-child {
  border-bottom: none;
}

.result-item.failed {
  background: rgba(255, 100, 100, 0.1);
}

.download-link {
  padding: 0.3rem 0.6rem;
  background: rgba(0, 212, 170, 0.15);
  border: 1px solid rgba(0, 212, 170, 0.3);
  border-radius: var(--radius-sm, 4px);
  color: var(--accent-primary, #00d4aa);
  text-decoration: none;
  font-size: 0.75rem;
  transition: all 0.2s;
}

.download-link:hover {
  background: rgba(0, 212, 170, 0.25);
  border-color: var(--accent-primary, #00d4aa);
}

.failed-badge {
  font-size: 0.75rem;
  color: #ff6b6b;
}

/* Export Actions */
.export-actions {
  display: flex;
  gap: 0.75rem;
  padding: 1rem 1.25rem;
  background: rgba(0, 0, 0, 0.15);
  border-top: 1px solid rgba(255, 255, 255, 0.05);
}

.export-cancel-btn,
.export-start-btn {
  flex: 1;
  padding: 0.7rem 1rem;
  border-radius: var(--radius-md, 8px);
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s;
}

.export-cancel-btn {
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: var(--text-secondary, #aaa);
}

.export-cancel-btn:hover {
  background: rgba(255, 255, 255, 0.15);
  border-color: rgba(255, 255, 255, 0.3);
}

.export-start-btn {
  background: linear-gradient(135deg, rgba(0, 212, 170, 0.2), rgba(0, 212, 170, 0.3));
  border: 1px solid rgba(0, 212, 170, 0.4);
  color: var(--accent-primary, #00d4aa);
  font-weight: 600;
}

.export-start-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, rgba(0, 212, 170, 0.3), rgba(0, 212, 170, 0.4));
  border-color: var(--accent-primary, #00d4aa);
  transform: translateY(-1px);
}

.export-start-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>




