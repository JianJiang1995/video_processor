<template>
  <div class="video-wrapper">
    <div class="video-container" ref="containerRef">
      <!-- Video Content Area -->
      <template v-if="session">
        <!-- Loop Playback Frame (when in loop mode for streams) -->
        <img
          v-if="isLoopPlaybackMode && loopPlaybackFrame"
          :src="loopPlaybackFrame"
          class="stream-image loop-playback"
          :class="{ 'hidden-by-sam3': showSam3 && sam3Frame }"
          @click="togglePlay"
        />
        <!-- Loop Playback Loading (when loading frames) -->
        <div 
          v-else-if="isLoopPlaybackMode && !loopPlaybackFrame"
          class="loop-loading-overlay"
          @click="togglePlay"
        >
          <div class="loop-loading-spinner"></div>
          <div class="loop-loading-text">加载回放帧...</div>
        </div>
        
        <!-- MJPEG Stream (for live streams, hidden when in loop playback mode) -->
        <!-- When paused with a frozen frame, hide this and show frozen frame -->
        <!-- When paused without frozen frame, keep showing this (stream will just not update) -->
        <img
          v-else-if="mode === 'stream' && isHttpStream"
          ref="streamImgRef"
          :src="streamUrl"
          class="stream-image"
          :class="{ 
            'hidden-by-sam3': showSam3 && sam3Frame,
            'stream-hidden': isPaused && frozenFrame
          }"
          @load="onStreamLoad"
          @error="onStreamError"
        />
        <!-- Frozen frame when paused (captured from stream or from backend) -->
        <img
          v-if="mode === 'stream' && isHttpStream && !isLoopPlaybackMode && isPaused && frozenFrame"
          :src="frozenFrame"
          class="stream-image frozen"
          :class="{ 'hidden-by-sam3': showSam3 && sam3Frame }"
        />
        <!-- Pause overlay when stream is paused but no frozen frame available -->
        <div 
          v-if="mode === 'stream' && isHttpStream && !isLoopPlaybackMode && isPaused && !frozenFrame"
          class="pause-overlay"
        >
          <div class="pause-icon">⏸</div>
          <div class="pause-text">实时流已暂停</div>
        </div>
        
        <!-- Video Element (for local files) -->
        <video
          v-else
          ref="videoRef"
          :src="`/api/video/stream/${session.session_id}`"
          :class="{ 'hidden-by-sam3': showSam3 && sam3Frame }"
          @timeupdate="onTimeUpdate"
          @loadedmetadata="onLoadedMetadata"
          @play="$emit('play')"
          @pause="$emit('pause')"
          @click="togglePlay"
        ></video>
        
        <!-- SAM3 Segmented Frame Overlay (when SAM3 mode is enabled) -->
        <img
          v-if="showSam3 && sam3Frame"
          :src="sam3Frame"
          class="stream-image sam3-overlay"
          alt="SAM3 Segmented View"
        />
        
        <!-- SAM3 Loading Indicator -->
        <div v-if="showSam3 && sam3Loading" class="sam3-loading-overlay">
          <div class="sam3-loading-spinner"></div>
          <div class="sam3-loading-text">加载器械分割...</div>
        </div>
        
        <!-- SAM3 Error Message -->
        <div v-if="showSam3 && sam3Error && !sam3Loading" class="sam3-error-overlay">
          <div class="sam3-error-icon">⚠️</div>
          <div class="sam3-error-text">{{ sam3Error }}</div>
          <button class="sam3-retry-btn" @click="fetchSam3Frame(currentTime)">重试</button>
        </div>
      </template>
      
      <!-- Placeholder when no video (Local Mode) -->
      <div v-else class="video-placeholder">
        <div class="upload-zone" @click="triggerUpload" @drop.prevent="onDrop" @dragover.prevent>
          <div class="upload-icon">📁</div>
          <div class="upload-text">拖放视频文件或点击上传</div>
          <div class="upload-hint">支持 MP4, AVI, MOV, MKV</div>
          
          <div style="margin-top: 1.5rem; font-size: 0.9rem; color: var(--text-tertiary);">
            — 或输入本地路径 —
          </div>
          
          <div style="margin-top: 1rem; display: flex; gap: 0.5rem; flex-wrap: wrap; justify-content: center;">
            <input
              type="text"
              v-model="videoPath"
              placeholder="/data/videos/surgery.mp4"
              class="input"
              style="flex: 1; min-width: 200px;"
              @click.stop
            />
            <button class="btn btn-primary" @click.stop="loadFromPath">
              加载
            </button>
          </div>
        </div>
        
        <input
          ref="fileInput"
          type="file"
          accept="video/*"
          @change="onFileSelect"
          style="display: none;"
        />
      </div>
      
      <!-- Loading Overlay -->
      <div v-if="isLoading" class="loading-overlay">
        <div class="loader"></div>
        <div class="loading-text">{{ mode === 'stream' ? '连接视频流...' : '加载视频...' }}</div>
      </div>
      
      <!-- Live/Ended Indicator -->
      <div v-if="session && mode === 'stream'" class="live-indicator" :class="{ 'ended': streamEnded }">
        <span class="live-dot" :class="{ 'ended': streamEnded }"></span>
        {{ streamEnded ? 'ENDED' : 'LIVE' }}
      </div>
      
      <!-- Current Time Overlay -->
      <div v-if="session" class="time-overlay" :class="{ 'sam3-mode': showSam3 && sam3FrameTime !== null, 'loop-mode': isLoopPlaybackMode }">
        <template v-if="isLoopPlaybackMode">
          <!-- In loop playback mode, show the loop playback time -->
          {{ formatTime(loopPlaybackTime) }}
        </template>
        <template v-else-if="showSam3 && sam3FrameTime !== null && Math.abs(sam3FrameTime - currentTime) > 0.5">
          <span class="sam3-time-label">🔬</span>
          {{ formatTime(sam3FrameTime) }}
        </template>
        <template v-else>
          {{ formatTime(currentTime) }}
        </template>
      </div>
      
    </div>
    
    <!-- Loop Playback Indicator - Outside video container to avoid blocking video -->
    <div v-if="loopWindow" class="loop-indicator-bar">
      <span class="loop-icon">🔄</span>
      <span class="loop-text">循环播放窗口 {{ loopWindow.window_id + 1 }}</span>
      <span class="loop-separator">|</span>
      <span class="loop-time">{{ formatTime(loopWindow.start_time) }} - {{ formatTime(loopWindow.end_time) }}</span>
      <span class="loop-separator">|</span>
      <span class="loop-hint">点击视频退出循环</span>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, computed } from 'vue'

const props = defineProps({
  session: Object,
  currentTime: Number,
  isPlaying: Boolean,
  isPaused: Boolean,
  mode: {
    type: String,
    default: 'local'  // 'local' or 'stream'
  },
  showSam3: {
    type: Boolean,
    default: false
  },
  sam3Available: {
    type: Boolean,
    default: false
  },
  loopWindow: {
    type: Object,
    default: null  // { window_id, start_time, end_time }
  },
  streamEnded: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['timeupdate', 'play', 'pause', 'seek', 'upload', 'load', 'sam3TimeUpdate', 'exitLoop', 'loopLoadFailed'])

const videoRef = ref(null)
const streamImgRef = ref(null)
const containerRef = ref(null)
const fileInput = ref(null)
const isLoading = ref(false)
const videoPath = ref('')
const frozenFrame = ref(null)  // Store captured frame when paused
const sam3Frame = ref(null)  // Store SAM3 segmented frame
const sam3LoadingTimer = ref(null)  // Timer for SAM3 frame updates
const lastSam3Timestamp = ref(-1)  // Track last requested timestamp
const sam3Error = ref(null)  // Track SAM3 errors
const sam3Loading = ref(false)  // Track SAM3 loading state
const sam3FrameTime = ref(null)  // Actual timestamp of the SAM3 frame being displayed
const sam3FetchInProgress = ref(false)  // Prevent concurrent SAM3 requests

// Loop playback state for stream mode
const loopPlaybackFrame = ref(null)  // Current frame being displayed during loop playback
const loopPlaybackTime = ref(0)  // Current playback time within the loop window
let loopPlaybackTimer = null  // Timer for simulating playback
const loopFrameCache = ref([])  // Cached frames for the loop window
const loopCacheLoading = ref(false)  // Whether we're loading the frame cache

// Computed: check if it's an HTTP stream URL (MJPEG from stream server)
const isHttpMjpegStream = computed(() => {
  if (!props.session?.video_path) return false
  const path = props.session.video_path
  return path.startsWith('http://') || path.startsWith('https://')
})

// Computed: check if it's an RTSP stream (needs proxy for MJPEG conversion)
const isRtspStream = computed(() => {
  if (!props.session?.video_path) return false
  return props.session.video_path.startsWith('rtsp://')
})

// Combined: is any type of live stream (HTTP/HTTPS/RTSP)
const isHttpStream = computed(() => isHttpMjpegStream.value || isRtspStream.value)

// Computed: get the stream URL
// - HTTP/HTTPS streams (like stream_simulator) are already MJPEG, use directly
// - RTSP streams need our proxy for MJPEG conversion
const streamUrl = computed(() => {
  if (!props.session?.session_id) return ''
  
  if (isRtspStream.value) {
    // RTSP needs proxy for MJPEG conversion with frame pacing
    return `/api/video/mjpeg-proxy/${props.session.session_id}?fps=25&quality=85`
  }
  
  if (isHttpMjpegStream.value) {
    // HTTP streams (like stream_simulator) are already MJPEG with frame pacing
    // Use directly to avoid double-encoding overhead
    return props.session.video_path
  }
  
  return ''
})

const onStreamLoad = () => {
  isLoading.value = false
}

const onStreamError = () => {
  console.error('Stream error')
  isLoading.value = false
}

// Capture current frame when pausing stream
const captureFrame = () => {
  if (!streamImgRef.value) return null
  
  try {
    const img = streamImgRef.value
    // Check if image is loaded and has valid dimensions
    if (!img.naturalWidth || !img.naturalHeight) {
      console.warn('Stream image not loaded yet')
      return null
    }
    const canvas = document.createElement('canvas')
    canvas.width = img.naturalWidth
    canvas.height = img.naturalHeight
    const ctx = canvas.getContext('2d')
    ctx.drawImage(img, 0, 0)
    return canvas.toDataURL('image/jpeg', 0.9)
  } catch (e) {
    // CORS error is expected for cross-origin streams
    console.warn('Failed to capture frame (CORS restriction):', e.message)
    return null
  }
}

// Fetch frame from backend API as fallback
const fetchFrameFromBackend = async () => {
  if (!props.session) return null
  
  try {
    // Try to get the current frame from backend analysis API
    const response = await fetch(
      `/api/analysis/frame-at-timestamp/${props.session.session_id}?timestamp=${props.currentTime}&tolerance=2.0`
    )
    
    if (response.ok) {
      const data = await response.json()
      if (data.success && data.image_base64) {
        return `data:image/jpeg;base64,${data.image_base64}`
      }
    }
    
    // Fallback: try video frame API (works for local videos)
    const videoResponse = await fetch(
      `/api/video/frame/${props.session.session_id}?timestamp=${props.currentTime}`
    )
    
    if (videoResponse.ok) {
      const videoData = await videoResponse.json()
      if (videoData.image_base64) {
        return `data:image/jpeg;base64,${videoData.image_base64}`
      }
    }
  } catch (e) {
    console.warn('Failed to fetch frame from backend:', e.message)
  }
  
  return null
}

// Watch for pause state changes to capture/clear frozen frame
watch(() => props.isPaused, async (paused) => {
  if (paused && props.mode === 'stream') {
    // First try to capture current frame from canvas
    let frame = captureFrame()
    
    // If canvas capture failed (CORS), try fetching from backend
    if (!frame) {
      frame = await fetchFrameFromBackend()
    }
    
    // If we still don't have a frame, keep the last stream image visible
    // The template handles this by not hiding the stream img when frozenFrame is null
    frozenFrame.value = frame
  } else {
    // Clear frozen frame when resuming
    frozenFrame.value = null
  }
})

// Watch for external time changes (seeking)
// Use a small threshold (0.1s) to avoid unnecessary seeks during normal playback
// but still allow precise seeking for loop playback and user interactions
watch(() => props.currentTime, (newTime) => {
  if (videoRef.value && Math.abs(videoRef.value.currentTime - newTime) > 0.1) {
    console.log(`[VideoPlayer] Seeking from ${videoRef.value.currentTime.toFixed(2)}s to ${newTime.toFixed(2)}s`)
    videoRef.value.currentTime = newTime
  }
})

// Watch for play/pause state
watch(() => props.isPlaying, (playing) => {
  if (videoRef.value) {
    if (playing) {
      videoRef.value.play()
    } else {
      videoRef.value.pause()
    }
  }
})

const onTimeUpdate = () => {
  if (videoRef.value) {
    emit('timeupdate', videoRef.value.currentTime)
  }
}

const onLoadedMetadata = () => {
  isLoading.value = false
}

const togglePlay = () => {
  // If in loop mode, clicking video exits loop mode
  if (props.loopWindow) {
    emit('exitLoop')
    return
  }
  
  if (props.isPlaying) {
    emit('pause')
  } else {
    emit('play')
  }
}

const triggerUpload = () => {
  fileInput.value?.click()
}

const onFileSelect = (event) => {
  const file = event.target.files[0]
  if (file) {
    isLoading.value = true
    emit('upload', file)
  }
}

const onDrop = (event) => {
  const file = event.dataTransfer.files[0]
  if (file && file.type.startsWith('video/')) {
    isLoading.value = true
    emit('upload', file)
  }
}

const loadFromPath = () => {
  if (videoPath.value.trim()) {
    isLoading.value = true
    emit('load', videoPath.value.trim())
  }
}

const formatTime = (seconds) => {
  // Handle negative or invalid values
  if (seconds < 0 || isNaN(seconds) || !isFinite(seconds)) {
    seconds = 0
  }
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
}

// ============================================================================
// Loop Playback for Stream Mode
// When in stream mode with loopWindow active, we fetch saved frames and play them
// ============================================================================

// Check if we're in loop playback mode (stream mode with loopWindow)
const isLoopPlaybackMode = computed(() => {
  return props.mode === 'stream' && isHttpStream.value && props.loopWindow !== null
})

// Background loading state
let backgroundLoadingAborted = false

// Preload a single image with optional createImageBitmap for better performance
const preloadImage = async (url) => {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = async () => {
      // Use createImageBitmap if available for pre-decoded bitmap (faster rendering)
      if (typeof createImageBitmap !== 'undefined') {
        try {
          await createImageBitmap(img)
        } catch (e) {
          // Ignore bitmap creation errors, image is still cached
        }
      }
      resolve(true)
    }
    img.onerror = () => resolve(false)
    img.src = url
  })
}

// Load frames for the loop window - optimized with preview frames and batch loading
const loadLoopWindowFrames = async () => {
  if (!props.session || !props.loopWindow) return false
  
  loopCacheLoading.value = true
  loopFrameCache.value = []
  backgroundLoadingAborted = false
  
  const windowDuration = props.loopWindow.end_time - props.loopWindow.start_time
  // Request enough frames to cover the entire window
  // Backend may have 10-15 fps, so request 15 fps worth of frames
  // Allow up to 300 frames for a 20-second window
  const maxFrames = Math.min(Math.ceil(windowDuration * 15), 300)
  
  console.log(`[LoopPlayback] Loading frames for window ${props.loopWindow.window_id} (${props.loopWindow.start_time.toFixed(1)}-${props.loopWindow.end_time.toFixed(1)}s, max ${maxFrames} frames)`)
  
  try {
    // Use batch API with URL mode and preview frames (faster - smaller files, ~40KB vs ~600KB)
    const batchResponse = await fetch(
      `/api/analysis/frames-batch/${props.session.session_id}?start=${props.loopWindow.start_time}&end=${props.loopWindow.end_time}&max_frames=${maxFrames}&use_url=true&use_preview=true`
    )
    
    if (batchResponse.ok) {
      const batchData = await batchResponse.json()
      if (batchData.success && batchData.frames && batchData.frames.length > 0) {
        // Frames with URLs - sort by timestamp and remove duplicates
        // Duplicates can occur when a session is analyzed multiple times
        const sortedFrames = batchData.frames
          .map(f => ({
            timestamp: f.timestamp,
            url: f.url  // Direct URL to static file
          }))
          .sort((a, b) => a.timestamp - b.timestamp)
        
        // Remove duplicate timestamps - keep first occurrence (most recent file)
        const seenTimestamps = new Set()
        const loadedFrames = sortedFrames.filter(f => {
          // Round to 2 decimal places to handle floating point comparison
          const tsKey = Math.round(f.timestamp * 100)
          if (seenTimestamps.has(tsKey)) {
            return false
          }
          seenTimestamps.add(tsKey)
          return true
        })
        
        if (sortedFrames.length !== loadedFrames.length) {
          console.log(`[LoopPlayback] Removed ${sortedFrames.length - loadedFrames.length} duplicate timestamp frames`)
        }
        
        loopFrameCache.value = loadedFrames
        
        // Show first frame immediately using URL
        loopPlaybackFrame.value = loadedFrames[0].url
        
        const subfolder = batchData.subfolder || 'preview'
        console.log(`[LoopPlayback] Got ${loadedFrames.length} ${subfolder} frame URLs, preloading images...`)
        
        // Preload images in batches for smooth playback
        // First batch: 30 frames (~3 seconds of content) - enough for smooth start
        // Rest loaded in background while playing
        const FIRST_BATCH_SIZE = 30
        const firstBatchSize = Math.min(FIRST_BATCH_SIZE, loadedFrames.length)
        
        // Preload first batch in parallel
        const firstBatchPromises = loadedFrames.slice(0, firstBatchSize).map(frame => preloadImage(frame.url))
        await Promise.all(firstBatchPromises)
        
        // Check if loading was aborted while waiting
        if (backgroundLoadingAborted) {
          console.log('[LoopPlayback] Loading aborted during first batch')
          return false
        }
        
        console.log(`[LoopPlayback] First ${firstBatchSize} images preloaded, starting playback`)
        loopCacheLoading.value = false
        
        // Continue loading rest in background (don't await)
        if (loadedFrames.length > firstBatchSize) {
          const loadRemainingFrames = async () => {
            const remaining = loadedFrames.slice(firstBatchSize)
            let loaded = 0
            
            // Load in smaller batches to avoid overwhelming the browser
            const BATCH_SIZE = 20
            for (let i = 0; i < remaining.length; i += BATCH_SIZE) {
              if (backgroundLoadingAborted) {
                console.log(`[LoopPlayback] Background loading aborted at ${loaded}/${remaining.length}`)
                return
              }
              
              const batch = remaining.slice(i, i + BATCH_SIZE)
              await Promise.all(batch.map(frame => preloadImage(frame.url)))
              loaded += batch.length
            }
            
            console.log(`[LoopPlayback] All ${loadedFrames.length} images preloaded`)
          }
          
          // Fire and forget - don't block playback start
          loadRemainingFrames()
        }
        
        return true
      }
    }
    
    // Fallback: try without URL mode (base64), still use preview
    console.log('[LoopPlayback] URL mode failed, trying base64 fallback...')
    
    const fallbackResponse = await fetch(
      `/api/analysis/frames-batch/${props.session.session_id}?start=${props.loopWindow.start_time}&end=${props.loopWindow.end_time}&max_frames=${maxFrames}&use_url=false&use_preview=true`
    )
    
    if (fallbackResponse.ok) {
      const fallbackData = await fallbackResponse.json()
      if (fallbackData.success && fallbackData.frames && fallbackData.frames.length > 0) {
        const sortedFrames = fallbackData.frames
          .filter(f => f.image_base64)
          .map(f => ({
            timestamp: f.timestamp,
            url: `data:image/jpeg;base64,${f.image_base64}`
          }))
          .sort((a, b) => a.timestamp - b.timestamp)
        
        // Remove duplicate timestamps
        const seenTimestamps = new Set()
        const loadedFrames = sortedFrames.filter(f => {
          const tsKey = Math.round(f.timestamp * 100)
          if (seenTimestamps.has(tsKey)) return false
          seenTimestamps.add(tsKey)
          return true
        })
        
        if (loadedFrames.length > 0) {
          loopFrameCache.value = loadedFrames
          loopPlaybackFrame.value = loadedFrames[0].url
          console.log(`[LoopPlayback] Loaded ${loadedFrames.length} frames via base64 fallback`)
          loopCacheLoading.value = false
          return true
        }
      }
    }
    
    emit('loopLoadFailed', '无法加载帧')
    emit('exitLoop')
    return false
    
  } catch (e) {
    console.error('[LoopPlayback] Failed to load frames:', e)
    emit('loopLoadFailed', '加载帧时发生错误')
    emit('exitLoop')
    return false
  }
}

// Binary search to find closest frame - O(log n) instead of O(n)
const findClosestFrameIndex = (frames, targetTime) => {
  if (frames.length === 0) return -1
  if (frames.length === 1) return 0
  
  let left = 0
  let right = frames.length - 1
  
  while (left < right) {
    const mid = Math.floor((left + right) / 2)
    if (frames[mid].timestamp < targetTime) {
      left = mid + 1
    } else {
      right = mid
    }
  }
  
  // Check if left-1 is closer
  if (left > 0) {
    const diffLeft = Math.abs(frames[left].timestamp - targetTime)
    const diffPrev = Math.abs(frames[left - 1].timestamp - targetTime)
    if (diffPrev < diffLeft) return left - 1
  }
  
  return left
}

// Get the frame closest to the current playback time using binary search
const getCurrentLoopFrame = () => {
  if (!props.loopWindow || loopFrameCache.value.length === 0) return null
  
  const idx = findClosestFrameIndex(loopFrameCache.value, loopPlaybackTime.value)
  return idx >= 0 ? loopFrameCache.value[idx] : null
}

// Start loop playback with fixed frame rate for smooth playback
const startLoopPlayback = async () => {
  if (loopPlaybackTimer) {
    cancelAnimationFrame(loopPlaybackTimer)
    loopPlaybackTimer = null
  }
  
  if (!props.loopWindow) return
  
  // Initialize playback time to start of window BEFORE loading frames
  loopPlaybackTime.value = props.loopWindow.start_time
  
  // Preload all frames for this window first
  const loadSuccess = await loadLoopWindowFrames()
  
  // If loading failed or no frames, don't start timer
  if (!loadSuccess || loopFrameCache.value.length === 0) {
    console.log('[LoopPlayback] No frames loaded, not starting timer')
    return
  }
  
  // Show first frame IMMEDIATELY after loading (before starting timer)
  const firstFrame = loopFrameCache.value[0]
  if (firstFrame && firstFrame.url) {
    loopPlaybackFrame.value = firstFrame.url
    console.log(`[LoopPlayback] Showing first frame at ${firstFrame.timestamp}s`)
  }
  
  // Calculate fixed frame rate based on frame count and window duration
  // This ensures smooth, consistent playback regardless of original frame timestamps
  const windowDuration = props.loopWindow.end_time - props.loopWindow.start_time
  const frameCount = loopFrameCache.value.length
  
  // Protect against invalid window duration (too small or zero)
  if (windowDuration < 0.5) {
    console.warn(`[LoopPlayback] Window duration too small (${windowDuration}s), using minimum 0.5s`)
  }
  const safeWindowDuration = Math.max(windowDuration, 0.5)
  
  // If too few frames, warn user and extend minimum loop time
  // A loop should take at least 2 seconds to feel natural
  const MIN_LOOP_DURATION_MS = 2000
  
  // Calculate natural frame rate with bounds (min 1 fps, max 30 fps)
  // This prevents abnormally fast playback when window is small or frames are many
  const naturalFps = frameCount / safeWindowDuration
  let targetFps = Math.max(1, Math.min(naturalFps, 30))  // Clamp between 1-30 fps
  
  // Ensure minimum loop duration: if loop would be too fast, slow down the fps
  const naturalLoopDurationMs = (frameCount / targetFps) * 1000
  if (naturalLoopDurationMs < MIN_LOOP_DURATION_MS && frameCount > 1) {
    // Adjust fps to make loop last at least MIN_LOOP_DURATION_MS
    targetFps = (frameCount * 1000) / MIN_LOOP_DURATION_MS
    console.warn(`[LoopPlayback] Loop too fast (${naturalLoopDurationMs.toFixed(0)}ms), slowing to ${targetFps.toFixed(1)} fps for ${MIN_LOOP_DURATION_MS}ms loop`)
  }
  
  const msPerFrame = 1000 / targetFps  // Milliseconds per frame
  const actualLoopDuration = (frameCount / targetFps * 1000).toFixed(0)
  
  console.log(`[LoopPlayback] Fixed rate: ${targetFps.toFixed(1)} fps (natural: ${naturalFps.toFixed(1)}), ${msPerFrame.toFixed(1)}ms/frame, ${frameCount} frames, loop=${actualLoopDuration}ms`)
  
  // Use fixed frame index playback for consistent frame rate
  let currentFrameIndex = 0
  let lastFrameChangeTime = performance.now()
  let lastEmittedTime = loopPlaybackTime.value
  
  const playbackLoop = (currentTime) => {
    // Check if playback should continue
    if (!props.loopWindow || !props.isPlaying) {
      // Request next frame but don't update (paused state)
      loopPlaybackTimer = requestAnimationFrame(playbackLoop)
      return
    }
    
    // Calculate elapsed time since last frame change
    const elapsedSinceLastFrame = currentTime - lastFrameChangeTime
    
    // Check if it's time to advance to the next frame
    if (elapsedSinceLastFrame >= msPerFrame) {
      // Calculate how many frames to advance (usually 1, but can be more if tab was inactive)
      const framesToAdvance = Math.floor(elapsedSinceLastFrame / msPerFrame)
      currentFrameIndex = (currentFrameIndex + framesToAdvance) % frameCount
      lastFrameChangeTime = currentTime - (elapsedSinceLastFrame % msPerFrame)  // Keep remainder for timing accuracy
      
      // Update frame display
      const frame = loopFrameCache.value[currentFrameIndex]
      if (frame && frame.url) {
        loopPlaybackFrame.value = frame.url
        loopPlaybackTime.value = frame.timestamp
        
        // Emit time update periodically (not every frame to reduce overhead)
        if (Math.abs(frame.timestamp - lastEmittedTime) > 0.1) {
          lastEmittedTime = frame.timestamp
          emit('timeupdate', frame.timestamp)
        }
      }
    }
    
    // Continue the loop
    loopPlaybackTimer = requestAnimationFrame(playbackLoop)
  }
  
  // Start the animation loop
  loopPlaybackTimer = requestAnimationFrame(playbackLoop)
  emit('timeupdate', loopPlaybackTime.value)
}

// Start loop playback timer only (when frames are already cached)
const startLoopPlaybackTimer = () => {
  if (loopPlaybackTimer) {
    cancelAnimationFrame(loopPlaybackTimer)
    loopPlaybackTimer = null
  }
  
  if (!props.loopWindow || loopFrameCache.value.length === 0) return
  
  // Calculate fixed frame rate based on frame count and window duration
  const windowDuration = props.loopWindow.end_time - props.loopWindow.start_time
  const frameCount = loopFrameCache.value.length
  
  // Protect against invalid window duration (too small or zero)
  const safeWindowDuration = Math.max(windowDuration, 0.5)
  
  // Minimum loop duration to avoid unnaturally fast loops
  const MIN_LOOP_DURATION_MS = 2000
  
  // Calculate natural frame rate with bounds (min 1 fps, max 30 fps)
  const naturalFps = frameCount / safeWindowDuration
  let targetFps = Math.max(1, Math.min(naturalFps, 30))
  
  // Ensure minimum loop duration
  const naturalLoopDurationMs = (frameCount / targetFps) * 1000
  if (naturalLoopDurationMs < MIN_LOOP_DURATION_MS && frameCount > 1) {
    targetFps = (frameCount * 1000) / MIN_LOOP_DURATION_MS
  }
  
  const msPerFrame = 1000 / targetFps
  
  // Find current frame index based on current playback time
  let currentFrameIndex = findClosestFrameIndex(loopFrameCache.value, loopPlaybackTime.value)
  if (currentFrameIndex < 0) currentFrameIndex = 0
  
  let lastFrameChangeTime = performance.now()
  let lastEmittedTime = loopPlaybackTime.value
  
  const playbackLoop = (currentTime) => {
    if (!props.loopWindow || !props.isPlaying) {
      loopPlaybackTimer = requestAnimationFrame(playbackLoop)
      return
    }
    
    const elapsedSinceLastFrame = currentTime - lastFrameChangeTime
    
    if (elapsedSinceLastFrame >= msPerFrame) {
      const framesToAdvance = Math.floor(elapsedSinceLastFrame / msPerFrame)
      currentFrameIndex = (currentFrameIndex + framesToAdvance) % frameCount
      lastFrameChangeTime = currentTime - (elapsedSinceLastFrame % msPerFrame)
      
      const frame = loopFrameCache.value[currentFrameIndex]
      if (frame && frame.url) {
        loopPlaybackFrame.value = frame.url
        loopPlaybackTime.value = frame.timestamp
        
        if (Math.abs(frame.timestamp - lastEmittedTime) > 0.1) {
          lastEmittedTime = frame.timestamp
          emit('timeupdate', frame.timestamp)
        }
      }
    }
    
    loopPlaybackTimer = requestAnimationFrame(playbackLoop)
  }
  
  loopPlaybackTimer = requestAnimationFrame(playbackLoop)
}

// Stop loop playback
const stopLoopPlayback = () => {
  // Abort any background frame loading
  backgroundLoadingAborted = true
  
  if (loopPlaybackTimer) {
    cancelAnimationFrame(loopPlaybackTimer)
    loopPlaybackTimer = null
  }
  loopPlaybackFrame.value = null
  loopPlaybackTime.value = 0
  loopFrameCache.value = []
  loopCacheLoading.value = false
}

// Watch for loopWindow changes to start/stop loop playback
watch(() => props.loopWindow, async (newVal, oldVal) => {
  if (props.mode === 'stream' && isHttpStream.value) {
    if (newVal) {
      console.log('[LoopPlayback] Loop window set for', newVal.window_id)
      // Always start playback (it will handle loading frames)
      await startLoopPlayback()
    } else if (oldVal && !newVal) {
      console.log('[LoopPlayback] Stopping loop playback')
      stopLoopPlayback()
    }
  }
}, { immediate: true })

// Watch for isPlaying changes during loop playback
watch(() => props.isPlaying, (playing) => {
  if (isLoopPlaybackMode.value) {
    if (playing) {
      // If we have cached frames, just restart the timer (don't reload)
      if (loopFrameCache.value.length > 0) {
        if (loopPlaybackTimer) {
          cancelAnimationFrame(loopPlaybackTimer)
          loopPlaybackTimer = null
        }
        // Restart playback timer without reloading frames
        startLoopPlaybackTimer()
      } else {
        // No cached frames, need to load them
        startLoopPlayback()
      }
    } else if (!playing && loopPlaybackTimer) {
      cancelAnimationFrame(loopPlaybackTimer)
      loopPlaybackTimer = null
    }
  }
})

// Fetch SAM3 segmented frame - uses streaming endpoint for efficiency
// The backend continuously processes frames, we just fetch the latest cached result
const fetchSam3Frame = async (timestamp, forceOnDemand = false) => {
  if (!props.session || !props.showSam3 || !props.sam3Available) return
  
  // Prevent concurrent requests - this avoids race conditions and flicker
  if (sam3FetchInProgress.value) return
  
  // Debounce: only fetch if timestamp changed by more than 0.15 seconds (matches SAM3 update rate)
  if (Math.abs(timestamp - lastSam3Timestamp.value) < 0.15 && sam3Frame.value) return
  lastSam3Timestamp.value = timestamp
  
  // Mark request as in progress
  sam3FetchInProgress.value = true
  
  // Only show loading on first fetch
  if (!sam3Frame.value) {
    sam3Loading.value = true
  }
  sam3Error.value = null
  
  try {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 10000)  // 10s timeout (faster for streaming)
    
    // Try streaming endpoint first (cached, fast)
    // Falls back to on-demand if streaming not active
    let response, data
    
    // Always try streaming endpoint first (cached frame from background processing)
    response = await fetch(
      `/api/analysis/sam3/stream-frame/${props.session.session_id}`,
      { signal: controller.signal }
    )
    clearTimeout(timeoutId)
    
    // Check HTTP status before parsing JSON
    if (!response.ok) {
      console.warn(`SAM3 stream-frame returned ${response.status}`)
      sam3Loading.value = false
      // Keep existing frame if available, don't clear it
      return
    }
    
    try {
      data = await response.json()
      
      // If streaming is active and has a frame, use it
      if (data.success && data.image_base64) {
        sam3Frame.value = `data:image/jpeg;base64,${data.image_base64}`
        sam3Error.value = null
        // Update and emit the SAM3 frame's actual timestamp
        if (data.timestamp !== undefined) {
          sam3FrameTime.value = data.timestamp
          emit('sam3TimeUpdate', data.timestamp)
        }
        sam3Loading.value = false
        return
      } else if (data.streaming_active) {
        // Streaming is active but no frame yet - keep existing frame, don't show error
        sam3Loading.value = false
        return
      }
    } catch (jsonError) {
      console.warn('Failed to parse stream-frame response:', jsonError)
      sam3Loading.value = false
      return
    }
    
    // Only fall back to on-demand if explicitly forced or streaming completely unavailable
    if (!forceOnDemand && !data?.streaming_active) {
      // Streaming not active - can try on-demand for initial frame
      response = await fetch(
        `/api/analysis/sam3/segmented-frame/${props.session.session_id}?timestamp=${timestamp}&alpha=0.4`,
        { signal: controller.signal }
      )
      
      // Check HTTP status
      if (!response.ok) {
        const errorText = await response.text()
        console.error(`SAM3 segmented-frame error: ${response.status}`, errorText.substring(0, 200))
        sam3Error.value = `服务错误 (${response.status})`
        // DON'T clear sam3Frame - keep showing last valid frame to prevent flicker
        sam3Loading.value = false
        return
      }
      
      try {
        data = await response.json()
      } catch (jsonError) {
        console.error('Failed to parse segmented-frame response:', jsonError)
        sam3Error.value = 'JSON 解析失败'
        // DON'T clear sam3Frame - keep showing last valid frame to prevent flicker
        sam3Loading.value = false
        return
      }
    } else {
      sam3Loading.value = false
      return
    }
    
    if (data.image_base64) {
      sam3Frame.value = `data:image/jpeg;base64,${data.image_base64}`
      sam3Error.value = null
    } else if (data.message) {
      // No segmentation available (e.g., no tools detected)
      // Don't show as error - this is normal when no tools are visible
      // Just keep displaying the last valid frame
      console.log('SAM3:', data.message)
      // Clear error since this is not an error condition
      sam3Error.value = null
    } else {
      // Only show error if we have no frame at all
      if (!sam3Frame.value) {
        sam3Error.value = 'No SAM3 data received'
      }
    }
  } catch (error) {
    console.error('Failed to fetch SAM3 frame:', error)
    if (error.name === 'AbortError') {
      sam3Error.value = 'Request timeout'
    } else {
      sam3Error.value = error.message || 'Failed to load'
    }
    // DON'T clear sam3Frame on error - keep showing the last valid frame
    // This prevents flickering when requests temporarily fail
    // sam3Frame.value = null  // Removed to prevent flicker
  } finally {
    sam3Loading.value = false
  }
}

// Watch for SAM3 mode changes
watch(() => props.showSam3, (enabled) => {
  if (enabled && props.session) {
    // Reset state - but DON'T clear sam3Frame yet to avoid flicker
    // We'll only show the new frame once it's loaded
    sam3Error.value = null
    sam3Loading.value = true
    lastSam3Timestamp.value = -1
    
    // Start fetching SAM3 frames
    fetchSam3Frame(props.currentTime)
    
    // Set up interval to periodically update SAM3 frame
    // Streaming endpoint is fast (cached) so we can update more frequently
    if (sam3LoadingTimer.value) {
      clearInterval(sam3LoadingTimer.value)
    }
    sam3LoadingTimer.value = setInterval(() => {
      if (props.isPlaying && props.showSam3 && !sam3Loading.value) {
        fetchSam3Frame(props.currentTime)
      }
    }, 250)  // Update SAM3 frame every 250ms - streaming endpoint returns cached frames quickly
  } else {
    // Clear SAM3 frame and timer
    sam3Frame.value = null
    sam3Error.value = null
    sam3Loading.value = false
    sam3FrameTime.value = null
    emit('sam3TimeUpdate', null)  // Reset SAM3 time in parent
    if (sam3LoadingTimer.value) {
      clearInterval(sam3LoadingTimer.value)
      sam3LoadingTimer.value = null
    }
  }
})

// Also watch currentTime changes when SAM3 is enabled
watch(() => props.currentTime, (newTime) => {
  if (props.showSam3 && props.sam3Available && props.session) {
    fetchSam3Frame(newTime)
  }
})

// Cleanup on unmount
import { onUnmounted } from 'vue'

onUnmounted(() => {
  // Clear SAM3 timer
  if (sam3LoadingTimer.value) {
    clearInterval(sam3LoadingTimer.value)
  }
  
  // Stop loop playback
  stopLoopPlayback()
  
  // IMPORTANT: Clear MJPEG stream img src to stop loading
  // This helps free up the HTTP connection to the stream
  if (streamImgRef.value) {
    streamImgRef.value.src = ''
  }
})
</script>

<style scoped>
.time-overlay {
  position: absolute;
  top: 1rem;
  right: 1rem;
  font-family: var(--font-mono);
  font-size: 0.9rem;
  background: rgba(0, 0, 0, 0.7);
  padding: 0.35rem 0.75rem;
  border-radius: var(--radius-sm);
  color: var(--accent-primary);
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.time-overlay.sam3-mode {
  background: rgba(0, 0, 0, 0.85);
  border: 1px solid var(--accent-tertiary, #00bcd4);
  box-shadow: 0 0 8px rgba(0, 188, 212, 0.3);
}

.sam3-time-label {
  font-size: 0.8rem;
}

.live-indicator {
  position: absolute;
  top: 1rem;
  left: 1rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  font-weight: 600;
  background: rgba(255, 0, 0, 0.8);
  color: white;
  padding: 0.35rem 0.75rem;
  border-radius: var(--radius-sm);
}

.live-dot {
  width: 8px;
  height: 8px;
  background: white;
  border-radius: 50%;
  animation: blink 1s ease-in-out infinite;
}

.live-dot.ended {
  animation: none;
  opacity: 0.7;
}

.live-indicator.ended {
  background: rgba(100, 100, 100, 0.8);
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.stream-image {
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: black;
}

.stream-image.frozen {
  /* Frozen frame - same styling as live stream */
  position: absolute;
  top: 0;
  left: 0;
}

.stream-image.stream-hidden {
  /* Hide when frozen frame is available */
  visibility: hidden;
}

.pause-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.5);
  z-index: 5;
  pointer-events: none;
}

.pause-icon {
  font-size: 3rem;
  color: white;
  opacity: 0.9;
  margin-bottom: 0.5rem;
}

.pause-text {
  color: white;
  font-size: 0.9rem;
  opacity: 0.8;
}

.stream-image.sam3-overlay {
  /* SAM3 segmented view overlay - positioned on top */
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 10;
  border: 2px solid var(--accent-primary, #00d4aa);
  box-shadow: 0 0 20px rgba(0, 212, 170, 0.3);
}

.hidden-by-sam3 {
  /* Hide original video when SAM3 overlay is shown */
  visibility: hidden;
}

.sam3-loading-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.6);
  z-index: 5;
}

.sam3-loading-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid rgba(0, 212, 170, 0.2);
  border-top-color: var(--accent-primary, #00d4aa);
  border-radius: 50%;
  animation: sam3-spin 0.8s linear infinite;
}

@keyframes sam3-spin {
  to { transform: rotate(360deg); }
}

.sam3-loading-text {
  margin-top: 1rem;
  color: var(--accent-primary, #00d4aa);
  font-size: 0.9rem;
  font-weight: 500;
}

.sam3-error-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.7);
  z-index: 5;
}

.sam3-error-icon {
  font-size: 2.5rem;
  margin-bottom: 0.5rem;
}

.sam3-error-text {
  color: var(--warning, #fdcb6e);
  font-size: 0.9rem;
  text-align: center;
  max-width: 80%;
  margin-bottom: 1rem;
}

.sam3-retry-btn {
  padding: 0.5rem 1.5rem;
  background: var(--accent-primary, #00d4aa);
  border: none;
  border-radius: 6px;
  color: #000;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.sam3-retry-btn:hover {
  transform: scale(1.05);
  box-shadow: 0 0 15px rgba(0, 212, 170, 0.5);
}

/* Loop Playback Indicator */
/* Video wrapper - vertical layout */
.video-wrapper {
  display: flex;
  flex-direction: column;
  width: 100%;
}

/* Loop indicator bar - placed below video container */
.loop-indicator-bar {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  background: linear-gradient(135deg, rgba(0, 150, 136, 0.95), rgba(0, 188, 212, 0.95));
  border-radius: var(--radius-sm, 4px);
  padding: 0.5rem 1.5rem;
  margin-top: 0.5rem;
  box-shadow: 0 2px 8px rgba(0, 188, 212, 0.3);
  flex-shrink: 0;
}

.loop-indicator-bar .loop-icon {
  font-size: 1rem;
}

.loop-indicator-bar .loop-text {
  font-size: 0.85rem;
  font-weight: 600;
  color: white;
}

.loop-indicator-bar .loop-separator {
  color: rgba(255, 255, 255, 0.5);
  font-size: 0.8rem;
}

.loop-indicator-bar .loop-time {
  font-family: var(--font-mono, monospace);
  font-size: 0.85rem;
  color: rgba(255, 255, 255, 0.9);
}

.loop-indicator-bar .loop-hint {
  font-size: 0.75rem;
  color: rgba(255, 255, 255, 0.7);
  cursor: pointer;
}

.loop-indicator-bar .loop-hint:hover {
  color: white;
  text-decoration: underline;
}

/* Loop Playback Frame */
.stream-image.loop-playback {
  cursor: pointer;
  border: 2px solid var(--accent-secondary, #00bcd4);
  box-shadow: 0 0 15px rgba(0, 188, 212, 0.3);
}

/* Loop Loading Overlay */
.loop-loading-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.8);
  z-index: 5;
  cursor: pointer;
}

.loop-loading-spinner {
  width: 50px;
  height: 50px;
  border: 4px solid rgba(0, 188, 212, 0.2);
  border-top-color: var(--accent-secondary, #00bcd4);
  border-radius: 50%;
  animation: loop-loading-spin 0.8s linear infinite;
}

@keyframes loop-loading-spin {
  to { transform: rotate(360deg); }
}

.loop-loading-text {
  margin-top: 1rem;
  color: var(--accent-secondary, #00bcd4);
  font-size: 1rem;
  font-weight: 500;
}
</style>

