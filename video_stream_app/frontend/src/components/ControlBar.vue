<template>
  <div class="controls-bar">
    <!-- Progress Bar -->
    <div class="progress-container">
      <div 
        class="progress-bar" 
        ref="progressRef"
        @click="handleProgressClick"
        @mousedown="startDragging"
        @mousemove="handleDragging"
        @mouseup="stopDragging"
        @mouseleave="handleMouseLeave"
      >
        <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
        
        <!-- Window segments (clickable) -->
        <div class="window-segments">
          <div
            v-for="i in windowCount"
            :key="i"
            class="window-segment"
            :class="{ 
              active: currentWindowId === i - 1,
              analyzed: analyzedWindows.includes(i - 1),
              highlighted: highlightedWindowId === i - 1
            }"
            :style="{ 
              left: (((i - 1) * windowDuration) / duration * 100) + '%',
              width: (windowDuration / duration * 100) + '%'
            }"
            @click.stop="handleWindowClick($event, i - 1)"
            @mouseenter="handleWindowMouseEnter($event, i - 1)"
            @mouseleave="handleWindowMouseLeave"
          >
            <span class="window-label" v-if="analyzedWindows.includes(i - 1)">{{ i }}</span>
          </div>
        </div>
        
        <!-- Window Summary Tooltip -->
        <div 
          v-if="hoveredWindowSummary" 
          class="window-summary-tooltip"
          :class="{ locked: isTooltipLocked }"
          :style="{ left: tooltipPosition.x + 'px' }"
        >
          <div class="tooltip-header">
            <span class="tooltip-window">
              <span class="lock-icon" v-if="isTooltipLocked">📌</span>
              窗口 {{ hoveredWindowSummary.window_id + 1 }}
            </span>
            <button v-if="isTooltipLocked" class="tooltip-close" @click.stop="closeTooltip">✕</button>
            <span v-else class="tooltip-time">{{ formatTime(hoveredWindowSummary.start_time) }} - {{ formatTime(hoveredWindowSummary.end_time) }}</span>
          </div>
          <div class="tooltip-time-row" v-if="isTooltipLocked">
            ⏱️ {{ formatTime(hoveredWindowSummary.start_time) }} - {{ formatTime(hoveredWindowSummary.end_time) }}
          </div>
          <div class="tooltip-content">
            {{ hoveredWindowSummary.summary }}
          </div>
          <div class="tooltip-hint" v-if="!isTooltipLocked">点击固定显示</div>
          <div class="tooltip-hint" v-else>再次点击窗口或按 ✕ 关闭</div>
        </div>
        
        <!-- Playhead -->
        <div class="playhead" :style="{ left: progressPercent + '%' }">
          <div class="playhead-handle"></div>
        </div>
        
        <!-- Hover tooltip -->
        <div 
          v-if="hoverTime !== null" 
          class="hover-tooltip"
          :style="{ left: hoverPercent + '%' }"
        >
          {{ formatTime(hoverTime) }}
        </div>
      </div>
    </div>
    
    <!-- Controls Row -->
    <div class="controls-row">
      <!-- Playback Controls -->
      <button 
        class="control-btn" 
        @click="skipBack" 
        title="后退5秒"
      >
        ⏪
      </button>
      
      <button class="control-btn primary" @click="togglePlay">
        {{ isPlaying ? '⏸' : '▶️' }}
      </button>
      
      <button 
        class="control-btn" 
        @click="skipForward" 
        title="前进5秒"
      >
        ⏩
      </button>
      
      <!-- Time Display -->
      <div class="time-display">
        <span v-if="isLive" class="live-badge">🔴 LIVE</span>
        <!-- Show SAM3 frame time when in SAM3 mode with different timestamp -->
        <template v-if="showSam3 && sam3Time !== null && Math.abs(sam3Time - currentTime) > 0.5">
          <span class="time-current sam3-time" title="器械分割帧时间">{{ formatTime(sam3Time) }}</span>
          <span class="time-delay">(延迟 {{ formatTimeOffset(currentTime - sam3Time) }})</span>
        </template>
        <template v-else>
          <span class="time-current">{{ formatTime(currentTime) }}</span>
        </template>
        <span v-if="!isLive"> / </span>
        <span v-if="!isLive">{{ formatTime(duration) }}</span>
        <span class="window-display" v-if="currentWindowId >= 0">
          · 窗口 {{ currentWindowId + 1 }}
        </span>
      </div>
      
      <div class="controls-spacer"></div>
      
      <!-- Analysis Service Status -->
      <div class="analysis-services">
        <!-- SurgR1: 医生机器人图标 -->
        <div class="service-badge" :class="{ available: surgr1Status.available, processing: surgr1Processing.running }" :title="surgr1Processing.running ? `SurgR1 处理中 (${surgr1Processing.framesAnalyzed} 帧)` : 'SurgR1 手术图像分析'">
          <svg class="service-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <!-- 机器人头部 -->
            <rect x="6" y="7" width="12" height="10" rx="2" />
            <!-- 机器人天线 -->
            <line x1="12" y1="4" x2="12" y2="7" />
            <circle cx="12" cy="3" r="1.5" fill="currentColor" />
            <!-- 眼睛 (一个正常一个十字医疗符号) -->
            <circle cx="9" cy="11" r="1.5" fill="currentColor" />
            <!-- 医疗十字眼睛 -->
            <line x1="15" y1="9.5" x2="15" y2="12.5" stroke-width="2" />
            <line x1="13.5" y1="11" x2="16.5" y2="11" stroke-width="2" />
            <!-- 嘴巴 -->
            <line x1="9" y1="14.5" x2="15" y2="14.5" />
            <!-- 耳朵/传感器 -->
            <rect x="4" y="10" width="2" height="4" rx="0.5" />
            <rect x="18" y="10" width="2" height="4" rx="0.5" />
            <!-- 医生帽子 -->
            <path d="M7 7 L7 5.5 C7 4.5 8 3.5 12 3.5 C16 3.5 17 4.5 17 5.5 L17 7" stroke-width="1.2" />
            <line x1="9" y1="5.5" x2="15" y2="5.5" stroke-width="1.2" />
          </svg>
          <span class="service-label">SurgR1</span>
          <span v-if="surgr1Processing.running" class="frame-count">{{ surgr1Processing.framesAnalyzed }}</span>
        </div>
        
        <!-- LLM: 大脑/神经网络图标 -->
        <div class="service-badge" :class="{ available: glmStatus.available }" title="LLM 智能总结">
          <svg class="service-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <!-- 大脑轮廓 -->
            <path d="M12 4C8 4 5 6.5 5 10C5 12 6 13.5 6.5 14.5C6 15.5 5.5 17 6.5 18.5C7.5 20 9 20.5 10.5 20C11 20.5 11.5 21 12 21" />
            <path d="M12 4C16 4 19 6.5 19 10C19 12 18 13.5 17.5 14.5C18 15.5 18.5 17 17.5 18.5C16.5 20 15 20.5 13.5 20C13 20.5 12.5 21 12 21" />
            <!-- 大脑沟回 -->
            <path d="M12 6C10 6 8 7.5 8 9.5" />
            <path d="M12 6C14 6 16 7.5 16 9.5" />
            <path d="M7 12C8 12.5 10 13 12 12.5" />
            <path d="M17 12C16 12.5 14 13 12 12.5" />
            <!-- 神经元连接点 -->
            <circle cx="9" cy="9" r="0.8" fill="currentColor" />
            <circle cx="15" cy="9" r="0.8" fill="currentColor" />
            <circle cx="8" cy="14" r="0.8" fill="currentColor" />
            <circle cx="16" cy="14" r="0.8" fill="currentColor" />
            <circle cx="12" cy="17" r="0.8" fill="currentColor" />
          </svg>
          <span class="service-label">LLM</span>
        </div>
        
        <!-- SAM3: 放大镜+手术刀图标 -->
        <div class="service-badge" :class="{ available: sam3Status.available }" title="SAM3 器械分割">
          <svg class="service-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <!-- 放大镜 -->
            <circle cx="10" cy="10" r="5" />
            <line x1="14" y1="14" x2="18" y2="18" stroke-width="2" stroke-linecap="round" />
            <!-- 手术刀在放大镜内 -->
            <path d="M8 8L12 12" stroke-width="1.8" stroke-linecap="round" />
            <path d="M7 7L8.5 8.5" stroke-width="2.5" stroke-linecap="round" />
            <!-- 分割虚线 -->
            <path d="M19 6L21 8" stroke-dasharray="1 1" />
            <path d="M17 4L19 6" stroke-dasharray="1 1" />
          </svg>
          <span class="service-label">SAM3</span>
        </div>
        
        <!-- ASR: 麦克风图标 -->
        <div class="service-badge" :class="{ available: asrStatus.available }" title="ASR 语音识别">
          <svg class="service-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <!-- 麦克风主体 -->
            <rect x="9" y="4" width="6" height="10" rx="3" />
            <!-- 麦克风底座 -->
            <path d="M6 12C6 15.3 8.7 18 12 18C15.3 18 18 15.3 18 12" />
            <line x1="12" y1="18" x2="12" y2="21" />
            <line x1="9" y1="21" x2="15" y2="21" />
            <!-- 声波 -->
            <path d="M19 9C19.5 10 19.5 11 19 12" stroke-linecap="round" />
            <path d="M21 8C22 10 22 12 21 14" stroke-linecap="round" />
          </svg>
          <span class="service-label">ASR</span>
        </div>
        
        <!-- TTS: 扬声器+声波图标 -->
        <div class="service-badge" :class="{ available: ttsStatus.available }" title="TTS 语音合成">
          <svg class="service-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <!-- 扬声器主体 -->
            <path d="M4 9V15H8L14 20V4L8 9H4Z" fill="currentColor" opacity="0.2" />
            <path d="M4 9V15H8L14 20V4L8 9H4Z" />
            <!-- 声波 -->
            <path d="M17 9C18 10.5 18 13.5 17 15" stroke-linecap="round" />
            <path d="M19.5 7C21.5 10 21.5 14 19.5 17" stroke-linecap="round" />
          </svg>
          <span class="service-label">TTS</span>
        </div>
      </div>
      
      <!-- SAM3 Toggle Button -->
      <button 
        class="btn sam3-toggle-btn"
        :class="{ active: showSam3, disabled: !sam3Status.available }"
        @click="$emit('toggleSam3')"
        :disabled="!sam3Status.available"
        :title="sam3Status.available ? (showSam3 ? '切换到原始视频' : '显示器械分割视图') : 'SAM3 服务不可用'"
      >
        <svg class="sam3-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <circle cx="10" cy="10" r="5" />
          <line x1="14" y1="14" x2="18" y2="18" stroke-width="2" stroke-linecap="round" />
          <path d="M8 8L12 12" stroke-width="1.8" stroke-linecap="round" />
        </svg>
        {{ showSam3 ? '原始视频' : '器械分割' }}
      </button>
      
      <!-- Analyze Button -->
      <button 
        v-if="!isAnalyzing"
        class="btn btn-primary analyze-btn" 
        @click="$emit('analyze')" 
        :disabled="(mode === 'local' && !duration) || !surgr1Status.available || !glmStatus.available"
        :title="!surgr1Status.available || !glmStatus.available ? '需要 SurgR1 和 GLM 服务' : ''"
      >
        {{ isLive ? '开始分析' : '🔍 开始分析' }}
      </button>
      
      <!-- Stop Analyze Button -->
      <button 
        v-else
        class="btn btn-danger stop-btn" 
        @click="$emit('stopAnalyze')"
      >
        <span class="btn-spinner"></span>
        ⏹ 停止分析
      </button>
      
      <!-- Volume Control -->
      <div class="volume-control">
        <button class="control-btn" @click="toggleMute">
          {{ isMuted ? '🔇' : volume > 0.5 ? '🔊' : '🔉' }}
        </button>
        <input
          type="range"
          class="volume-slider"
          min="0"
          max="1"
          step="0.1"
          :value="volume"
          @input="handleVolumeChange"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  currentTime: {
    type: Number,
    default: 0
  },
  duration: {
    type: Number,
    default: 0
  },
  isPlaying: Boolean,
  volume: {
    type: Number,
    default: 0.8
  },
  mode: {
    type: String,
    default: 'local'
  },
  isLive: {
    type: Boolean,
    default: false
  },
  analyzedWindows: {
    type: Array,
    default: () => []
  },
  summaries: {
    type: Array,
    default: () => []
  },
  highlightedWindowId: {
    type: Number,
    default: -1
  },
  isAnalyzing: {
    type: Boolean,
    default: false
  },
  surgr1Status: {
    type: Object,
    default: () => ({ available: false, checking: true })
  },
  glmStatus: {
    type: Object,
    default: () => ({ available: false, checking: true })
  },
  sam3Status: {
    type: Object,
    default: () => ({ available: false, checking: true })
  },
  asrStatus: {
    type: Object,
    default: () => ({ available: false, checking: true })
  },
  ttsStatus: {
    type: Object,
    default: () => ({ available: false, checking: true })
  },
  showSam3: {
    type: Boolean,
    default: false
  },
  surgr1Processing: {
    type: Object,
    default: () => ({ running: false, framesAnalyzed: 0 })
  },
  sam3Time: {
    type: Number,
    default: null
  },
  windowDuration: {
    type: Number,
    default: 5
  }
})

const emit = defineEmits(['play', 'pause', 'seek', 'volume', 'analyze', 'stopAnalyze', 'seekToWindow', 'hoverWindow', 'dragSeek', 'toggleSam3'])

const progressRef = ref(null)
const isMuted = ref(false)
const previousVolume = ref(0.8)
const isDragging = ref(false)
const hoverTime = ref(null)
const hoverPercent = ref(0)

// Tooltip state for window summary
const hoveredWindowId = ref(-1)
const lockedWindowId = ref(-1)  // Locked window (clicked, stays visible)
const tooltipPosition = ref({ x: 0 })

// Get summary for displayed window (locked takes priority over hovered)
const displayedWindowId = computed(() => {
  if (lockedWindowId.value >= 0) return lockedWindowId.value
  return hoveredWindowId.value
})

// Cache the locked window's summary to prevent flickering during updates
const lockedWindowSummaryCache = ref(null)

const hoveredWindowSummary = computed(() => {
  if (displayedWindowId.value < 0) return null
  
  const summary = props.summaries.find(s => s.window_id === displayedWindowId.value)
  
  // If we have a locked window, cache and return the cached summary
  if (lockedWindowId.value >= 0) {
    if (summary) {
      lockedWindowSummaryCache.value = { ...summary }
    }
    // Return cached or current summary
    return lockedWindowSummaryCache.value || summary
  }
  
  return summary
})

const isTooltipLocked = computed(() => lockedWindowId.value >= 0)

const progressPercent = computed(() => {
  if (!props.duration) return 0
  // Clamp values to ensure valid percentage
  const safeTime = Math.max(0, Math.min(props.currentTime, props.duration))
  const percent = (safeTime / props.duration) * 100
  // Clamp final percentage to prevent visual glitches
  return Math.max(0, Math.min(100, percent))
})

const windowCount = computed(() => {
  // Ensure duration is positive to avoid negative window count
  const safeDuration = Math.max(0, props.duration || 0)
  return Math.max(0, Math.ceil(safeDuration / props.windowDuration))
})

const currentWindowId = computed(() => {
  return Math.floor(props.currentTime / props.windowDuration)
})

const togglePlay = () => {
  if (props.isPlaying) {
    emit('pause')
  } else {
    emit('play')
  }
}

const getTimeFromEvent = (event) => {
  if (!progressRef.value || !props.duration) return null
  const rect = progressRef.value.getBoundingClientRect()
  const percent = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
  return percent * props.duration
}

const handleProgressClick = (event) => {
  const time = getTimeFromEvent(event)
  if (time !== null) {
    emit('seek', time)
  }
}

const startDragging = (event) => {
  event.preventDefault()  // Prevent text selection while dragging
  isDragging.value = true
  const time = getTimeFromEvent(event)
  if (time !== null) {
    emit('dragSeek', time, true)  // true = drag started
    emit('seek', time)  // Also seek immediately
  }
}

// Global mousemove handler for dragging (works even outside progress bar)
const handleGlobalMouseMove = (event) => {
  if (!isDragging.value) return
  const time = getTimeFromEvent(event)
  if (time !== null) {
    // Update hover tooltip
    hoverTime.value = time
    hoverPercent.value = (time / props.duration) * 100
    emit('dragSeek', time, false)  // false = dragging
    emit('seek', time)  // Update video position while dragging
  }
}

// Global mouseup handler for dragging (works even outside progress bar)
const handleGlobalMouseUp = (event) => {
  if (!isDragging.value) return
  isDragging.value = false
  const time = getTimeFromEvent(event)
  if (time !== null) {
    emit('seek', time)
    emit('dragSeek', time, false)  // emit final position
  }
  hoverTime.value = null
}

const handleDragging = (event) => {
  const time = getTimeFromEvent(event)
  if (time !== null) {
    // Update hover tooltip
    hoverTime.value = time
    hoverPercent.value = (time / props.duration) * 100
    
    if (isDragging.value) {
      emit('dragSeek', time, false)  // false = dragging
      emit('seek', time)  // Update video position while dragging
    }
  }
}

const stopDragging = (event) => {
  // Handled by global mouseup event, but keep for safety
}

const handleMouseLeave = () => {
  // Don't stop dragging on mouse leave - continue with global handlers
  if (!isDragging.value) {
    hoverTime.value = null
  }
}

// Set up global event listeners for drag handling
onMounted(() => {
  document.addEventListener('mousemove', handleGlobalMouseMove)
  document.addEventListener('mouseup', handleGlobalMouseUp)
})

onUnmounted(() => {
  document.removeEventListener('mousemove', handleGlobalMouseMove)
  document.removeEventListener('mouseup', handleGlobalMouseUp)
})

const seekToWindow = (windowId) => {
  const time = windowId * props.windowDuration
  emit('seek', time)
  emit('seekToWindow', windowId)
}

const handleWindowClick = (event, windowId) => {
  // Only handle analyzed windows for locking
  if (props.analyzedWindows.includes(windowId)) {
    // Toggle lock: if already locked on this window, unlock; otherwise lock this window
    if (lockedWindowId.value === windowId) {
      lockedWindowId.value = -1
    } else {
      lockedWindowId.value = windowId
      // Update tooltip position for locked state
      if (progressRef.value) {
        const rect = progressRef.value.getBoundingClientRect()
        const windowStart = (windowId * props.windowDuration) / props.duration
        const windowCenter = windowStart + (props.windowDuration / props.duration / 2)
        let xPos = windowCenter * rect.width
        xPos = Math.max(100, Math.min(rect.width - 100, xPos))
        tooltipPosition.value = { x: xPos }
      }
    }
  }
  // Seek to window
  seekToWindow(windowId)
}

const handleWindowMouseEnter = (event, windowId) => {
  // Only show tooltip for analyzed windows
  if (props.analyzedWindows.includes(windowId)) {
    hoveredWindowId.value = windowId
    // Calculate tooltip position based on window segment position
    if (progressRef.value) {
      const rect = progressRef.value.getBoundingClientRect()
      const windowStart = (windowId * props.windowDuration) / props.duration
      const windowCenter = windowStart + (props.windowDuration / props.duration / 2)
      // Position tooltip at center of window, but keep within bounds
      let xPos = windowCenter * rect.width
      // Clamp to ensure tooltip stays within progress bar
      xPos = Math.max(100, Math.min(rect.width - 100, xPos))
      tooltipPosition.value = { x: xPos }
    }
  }
  emit('hoverWindow', windowId)
}

const handleWindowMouseLeave = () => {
  hoveredWindowId.value = -1
  // Don't emit hover change if locked (tooltip still visible)
  if (lockedWindowId.value < 0) {
    emit('hoverWindow', -1)
  }
}

const closeTooltip = () => {
  lockedWindowId.value = -1
  hoveredWindowId.value = -1
  lockedWindowSummaryCache.value = null
}

const skipBack = () => {
  const newTime = Math.max(0, props.currentTime - props.windowDuration)
  emit('seek', newTime)
}

const skipForward = () => {
  const newTime = Math.min(props.duration, props.currentTime + props.windowDuration)
  emit('seek', newTime)
}

const handleVolumeChange = (event) => {
  const vol = parseFloat(event.target.value)
  emit('volume', vol)
  isMuted.value = vol === 0
}

const toggleMute = () => {
  if (isMuted.value) {
    emit('volume', previousVolume.value)
    isMuted.value = false
  } else {
    previousVolume.value = props.volume
    emit('volume', 0)
    isMuted.value = true
  }
}

const formatTime = (seconds) => {
  // Handle negative, NaN, or infinite values
  if (seconds < 0 || isNaN(seconds) || !isFinite(seconds)) {
    seconds = 0
  }
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
}

const formatTimeOffset = (seconds) => {
  // Handle NaN or infinite values
  if (isNaN(seconds) || !isFinite(seconds)) {
    return '<1秒'
  }
  const absSeconds = Math.abs(seconds)
  if (absSeconds < 1) return '<1秒'
  if (absSeconds < 60) return `${Math.round(absSeconds)}秒`
  const mins = Math.floor(absSeconds / 60)
  const secs = Math.round(absSeconds % 60)
  return `${mins}分${secs}秒`
}
</script>

<style scoped>
/* Progress Bar Enhancements */
.progress-bar {
  position: relative;
  cursor: pointer;
  user-select: none;
}

.window-segments {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
}

.window-segment {
  position: absolute;
  top: 0;
  height: 100%;
  border-right: 1px solid var(--border-subtle);
  transition: all 0.2s ease;
  pointer-events: none;  /* Disable pointer events - let progress bar handle dragging */
  cursor: pointer;
}

/* Only enable pointer events for analyzed windows that show labels */
.window-segment.analyzed {
  pointer-events: auto;
}

.window-segment:hover {
  background: rgba(0, 212, 170, 0.1);
}

.window-segment.active {
  background: rgba(0, 212, 170, 0.2);
}

.window-segment.analyzed {
  background: rgba(0, 212, 170, 0.15);
  border-bottom: 2px solid var(--accent-primary);
}

.window-segment.highlighted {
  background: rgba(0, 212, 170, 0.35);
  animation: pulse-highlight 1s ease-in-out;
}

@keyframes pulse-highlight {
  0%, 100% { background: rgba(0, 212, 170, 0.35); }
  50% { background: rgba(0, 212, 170, 0.5); }
}

.window-label {
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  font-size: 0.65rem;
  color: var(--text-tertiary);
  opacity: 0;
  transition: opacity 0.2s;
  white-space: nowrap;
}

.window-segment:hover .window-label,
.window-segment.active .window-label {
  opacity: 1;
}

/* Playhead */
.playhead {
  position: absolute;
  top: -6px;
  bottom: -6px;
  width: 20px;
  transform: translateX(-50%);
  pointer-events: auto;  /* Enable pointer events for dragging */
  z-index: 15;
  cursor: grab;
}

.playhead:active {
  cursor: grabbing;
}

.playhead-handle {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 14px;
  height: 14px;
  background: var(--accent-primary);
  border-radius: 50%;
  box-shadow: 0 0 8px rgba(0, 212, 170, 0.7);
  transition: transform 0.1s ease, box-shadow 0.1s ease;
}

.progress-bar:hover .playhead-handle,
.playhead:hover .playhead-handle {
  transform: translate(-50%, -50%) scale(1.3);
  box-shadow: 0 0 12px rgba(0, 212, 170, 0.9);
}

/* Hover Tooltip */
.hover-tooltip {
  position: absolute;
  bottom: 100%;
  transform: translateX(-50%);
  background: var(--bg-elevated);
  color: var(--text-primary);
  font-size: 0.75rem;
  font-family: var(--font-mono);
  padding: 0.25rem 0.5rem;
  border-radius: var(--radius-sm);
  margin-bottom: 8px;
  white-space: nowrap;
  pointer-events: none;
  z-index: 20;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}

/* Window Display in Time */
.window-display {
  color: var(--accent-primary);
  font-size: 0.8rem;
  margin-left: 0.25rem;
}

/* SAM3 Time Display */
.time-current.sam3-time {
  color: var(--accent-tertiary, #00bcd4);
  text-shadow: 0 0 4px rgba(0, 188, 212, 0.5);
}

.time-delay {
  color: var(--warning, #fdcb6e);
  font-size: 0.7rem;
  margin-left: 0.35rem;
  opacity: 0.9;
}

/* Analysis Services Status */
.analysis-services {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin-right: 0.75rem;
  padding: 0.25rem 0.5rem;
  background: var(--bg-tertiary);
  border-radius: var(--radius-md);
}

.service-badge {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.4rem;
  border-radius: var(--radius-sm);
  font-size: 0.65rem;
  color: var(--text-tertiary);
  transition: all 0.2s;
  cursor: default;
  position: relative;
}

.service-badge:hover {
  background: rgba(255, 255, 255, 0.05);
}

.service-badge.available {
  color: var(--text-secondary);
}

.service-badge.available .service-icon {
  color: var(--accent-primary, #00d4aa);
  filter: drop-shadow(0 0 3px rgba(0, 212, 170, 0.5));
}

.service-badge:not(.available) .service-icon {
  color: var(--error, #ff6b6b);
  opacity: 0.7;
}

.service-badge.processing {
  background: rgba(0, 212, 170, 0.15);
  animation: pulse-processing 1.5s ease-in-out infinite;
}

.service-badge.processing .service-icon {
  animation: rotate-icon 2s linear infinite;
}

@keyframes pulse-processing {
  0%, 100% { background: rgba(0, 212, 170, 0.15); }
  50% { background: rgba(0, 212, 170, 0.25); }
}

@keyframes rotate-icon {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.frame-count {
  font-size: 0.55rem;
  font-weight: 600;
  background: var(--accent-primary, #00d4aa);
  color: #000;
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
  min-width: 18px;
  text-align: center;
}

.service-label {
  font-weight: 500;
  font-size: 0.6rem;
  letter-spacing: 0.02em;
}

.service-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  transition: all 0.2s;
}

/* Analyze Button */
.analyze-btn {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.analyze-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Stop Button */
.stop-btn {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.btn-danger {
  background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
  border: none;
  color: white;
  padding: 0.5rem 1rem;
  border-radius: var(--radius-md, 6px);
  cursor: pointer;
  font-weight: 500;
  transition: all 0.2s ease;
}

.btn-danger:hover {
  background: linear-gradient(135deg, #c0392b 0%, #a93226 100%);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(231, 76, 60, 0.4);
}

.btn-danger:active {
  transform: translateY(0);
}

.btn-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}


.live-badge {
  background: rgba(255, 0, 0, 0.8);
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
  font-size: 0.7rem;
  font-weight: 600;
  margin-right: 0.5rem;
  animation: pulse-live 2s ease-in-out infinite;
}

@keyframes pulse-live {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}

/* SAM3 Toggle Button */
.sam3-toggle-btn {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 0.9rem;
  background: var(--bg-tertiary, rgba(255, 255, 255, 0.05));
  border: 1px solid var(--border-subtle, rgba(255, 255, 255, 0.1));
  border-radius: var(--radius-md, 6px);
  color: var(--text-secondary, #a0a0a0);
  font-size: 0.8rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.25s ease;
  margin-right: 0.5rem;
}

.sam3-toggle-btn:hover:not(.disabled) {
  background: rgba(0, 212, 170, 0.1);
  border-color: rgba(0, 212, 170, 0.3);
  color: var(--text-primary, #fff);
}

.sam3-toggle-btn.active {
  background: linear-gradient(135deg, rgba(0, 212, 170, 0.2) 0%, rgba(0, 180, 150, 0.15) 100%);
  border-color: var(--accent-primary, #00d4aa);
  color: var(--accent-primary, #00d4aa);
  box-shadow: 0 0 12px rgba(0, 212, 170, 0.3);
}

.sam3-toggle-btn.active .sam3-icon {
  filter: drop-shadow(0 0 4px rgba(0, 212, 170, 0.6));
}

.sam3-toggle-btn.disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.sam3-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  transition: all 0.2s;
}

/* Window Summary Tooltip */
.window-summary-tooltip {
  position: absolute;
  bottom: calc(100% + 16px);
  transform: translateX(-50%);
  width: 320px;
  max-width: 90vw;
  background: linear-gradient(145deg, var(--bg-elevated, #1a1a2e) 0%, var(--bg-primary, #16162a) 100%);
  border: 1px solid var(--accent-primary, #00d4aa);
  border-radius: var(--radius-lg, 12px);
  box-shadow: 
    0 8px 32px rgba(0, 0, 0, 0.5),
    0 0 16px rgba(0, 212, 170, 0.15),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
  z-index: 100;
  pointer-events: none;
  animation: tooltip-appear 0.2s ease-out;
  overflow: hidden;
}

@keyframes tooltip-appear {
  from {
    opacity: 0;
    transform: translateX(-50%) translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
}

.window-summary-tooltip::before {
  content: '';
  position: absolute;
  bottom: -8px;
  left: 50%;
  transform: translateX(-50%);
  border: 8px solid transparent;
  border-top-color: var(--accent-primary, #00d4aa);
  border-bottom: none;
}

.window-summary-tooltip::after {
  content: '';
  position: absolute;
  bottom: -6px;
  left: 50%;
  transform: translateX(-50%);
  border: 6px solid transparent;
  border-top-color: var(--bg-primary, #16162a);
  border-bottom: none;
}

.tooltip-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1rem;
  background: rgba(0, 212, 170, 0.1);
  border-bottom: 1px solid rgba(0, 212, 170, 0.2);
}

.tooltip-window {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--accent-primary, #00d4aa);
}

.tooltip-time {
  font-size: 0.75rem;
  font-family: var(--font-mono, monospace);
  color: var(--text-secondary, #8888aa);
  background: rgba(0, 0, 0, 0.2);
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm, 4px);
}

.tooltip-content {
  padding: 0.875rem 1rem;
  font-size: 0.875rem;
  line-height: 1.65;
  color: var(--text-primary, #e0e0e8);
  max-height: 180px;
  overflow-y: auto;
}

.tooltip-content::-webkit-scrollbar {
  width: 4px;
}

.tooltip-content::-webkit-scrollbar-track {
  background: transparent;
}

.tooltip-content::-webkit-scrollbar-thumb {
  background: rgba(0, 212, 170, 0.3);
  border-radius: 2px;
}

.tooltip-hint {
  padding: 0.5rem 1rem;
  font-size: 0.7rem;
  color: var(--text-tertiary, #6666aa);
  text-align: center;
  background: rgba(0, 0, 0, 0.15);
  border-top: 1px solid rgba(255, 255, 255, 0.05);
}

/* Locked tooltip styles */
.window-summary-tooltip.locked {
  pointer-events: auto;
  border-width: 2px;
  box-shadow: 
    0 12px 48px rgba(0, 0, 0, 0.6),
    0 0 24px rgba(0, 212, 170, 0.25),
    inset 0 1px 0 rgba(255, 255, 255, 0.08);
}

.tooltip-close {
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: var(--text-secondary, #aaa);
  width: 24px;
  height: 24px;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  transition: all 0.2s;
}

.tooltip-close:hover {
  background: rgba(255, 100, 100, 0.3);
  color: #ff6b6b;
}

.lock-icon {
  margin-right: 0.35rem;
}

.tooltip-time-row {
  padding: 0.5rem 1rem;
  font-size: 0.75rem;
  font-family: var(--font-mono, monospace);
  color: var(--text-secondary, #8888aa);
  background: rgba(0, 0, 0, 0.15);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}
</style>

