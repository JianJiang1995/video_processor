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
      
      <!-- Live Indicator -->
      <div v-if="session && mode === 'stream'" class="live-indicator">
        <span class="live-dot"></span>
        LIVE
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
      
      <!-- Loop Playback Indicator -->
      <div v-if="loopWindow" class="loop-indicator">
        <span class="loop-icon">🔄</span>
        <span class="loop-text">循环播放窗口 {{ loopWindow.window_id + 1 }}</span>
        <span class="loop-time">{{ formatTime(loopWindow.start_time) }} - {{ formatTime(loopWindow.end_time) }}</span>
        <span class="loop-hint">点击视频退出循环</span>
      </div>
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
  }
})

const emit = defineEmits(['timeupdate', 'play', 'pause', 'seek', 'upload', 'load', 'sam3TimeUpdate', 'exitLoop'])

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

// Computed: check if it's an HTTP stream URL
const isHttpStream = computed(() => {
  if (!props.session?.video_path) return false
  const path = props.session.video_path
  return path.startsWith('http://') || path.startsWith('https://')
})

// Computed: get the stream URL directly
const streamUrl = computed(() => {
  if (isHttpStream.value) {
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

// Load frames for the loop window from backend - preload all saved frames
const loadLoopWindowFrames = async () => {
  if (!props.session || !props.loopWindow) return
  
  loopCacheLoading.value = true
  loopFrameCache.value = []
  
  console.log(`[LoopPlayback] Loading saved frames for window ${props.loopWindow.window_id} (${props.loopWindow.start_time}-${props.loopWindow.end_time}s)`)
  
  try {
    // First, get the list of actually saved frames in this time range
    const listResponse = await fetch(
      `/api/analysis/frames-in-range/${props.session.session_id}?start=${props.loopWindow.start_time}&end=${props.loopWindow.end_time}`
    )
    
    if (!listResponse.ok) {
      console.warn('[LoopPlayback] Failed to get frames list')
      return
    }
    
    const listData = await listResponse.json()
    const savedFrames = listData.frames || []
    
    console.log(`[LoopPlayback] Found ${savedFrames.length} saved frames in range`)
    
    if (savedFrames.length === 0) {
      console.log('[LoopPlayback] No saved frames - will show loading state')
      return
    }
    
    // Load frame images in batches
    const batchSize = 10
    const loadedFrames = []
    
    for (let i = 0; i < savedFrames.length; i += batchSize) {
      const batch = savedFrames.slice(i, i + batchSize)
      const promises = batch.map(async (frameInfo) => {
        try {
          const response = await fetch(
            `/api/analysis/frame-at-timestamp/${props.session.session_id}?timestamp=${frameInfo.timestamp}&tolerance=0.2`
          )
          if (response.ok) {
            const data = await response.json()
            if (data.image_base64 && data.has_saved_frame) {
              return { 
                timestamp: frameInfo.timestamp, 
                image_base64: data.image_base64,
                is_saved: true
              }
            }
          }
        } catch (e) {
          console.warn(`[LoopPlayback] Failed to load frame at ${frameInfo.timestamp}:`, e)
        }
        return null
      })
      
      const results = await Promise.all(promises)
      loadedFrames.push(...results.filter(f => f !== null))
      
      // Show progress by displaying first loaded frame
      if (i === 0 && loadedFrames.length > 0) {
        loopPlaybackFrame.value = `data:image/jpeg;base64,${loadedFrames[0].image_base64}`
      }
    }
    
    // Sort by timestamp
    loadedFrames.sort((a, b) => a.timestamp - b.timestamp)
    loopFrameCache.value = loadedFrames
    
    console.log(`[LoopPlayback] Loaded ${loadedFrames.length} saved frames for playback`)
  } catch (e) {
    console.error('[LoopPlayback] Failed to preload frames:', e)
  } finally {
    loopCacheLoading.value = false
  }
}

// Get the frame closest to the current playback time from preloaded cache
const getCurrentLoopFrame = () => {
  if (!props.loopWindow || loopFrameCache.value.length === 0) return null
  
  const targetTime = loopPlaybackTime.value
  
  // Find the closest frame from preloaded cache
  let closestFrame = loopFrameCache.value[0]
  let minDiff = Math.abs(closestFrame.timestamp - targetTime)
  
  for (const frame of loopFrameCache.value) {
    const diff = Math.abs(frame.timestamp - targetTime)
    if (diff < minDiff) {
      minDiff = diff
      closestFrame = frame
    }
  }
  
  return closestFrame
}

// Start loop playback timer
const startLoopPlayback = async () => {
  if (loopPlaybackTimer) {
    clearInterval(loopPlaybackTimer)
  }
  
  if (!props.loopWindow) return
  
  // Initialize playback time to start of window
  loopPlaybackTime.value = props.loopWindow.start_time
  
  // Preload all frames for this window first
  await loadLoopWindowFrames()
  
  // After preloading, start the playback timer
  // Using higher framerate since frames are preloaded (no network delay)
  let lastFrameTime = Date.now()
  const frameInterval = 100  // 100ms = 10 fps, matches preload target
  
  loopPlaybackTimer = setInterval(() => {
    if (!props.loopWindow || !props.isPlaying) return
    
    const now = Date.now()
    const elapsed = (now - lastFrameTime) / 1000  // seconds
    lastFrameTime = now
    
    // Advance time based on actual elapsed time
    loopPlaybackTime.value += elapsed
    
    // Check if we need to loop
    if (loopPlaybackTime.value >= props.loopWindow.end_time) {
      loopPlaybackTime.value = props.loopWindow.start_time
    }
    
    // Emit time update to parent
    emit('timeupdate', loopPlaybackTime.value)
    
    // Get frame from preloaded cache (synchronous, no network delay)
    const frame = getCurrentLoopFrame()
    if (frame && frame.image_base64) {
      loopPlaybackFrame.value = `data:image/jpeg;base64,${frame.image_base64}`
    }
  }, frameInterval)
  
  // Show first frame immediately
  const firstFrame = getCurrentLoopFrame()
  if (firstFrame && firstFrame.image_base64) {
    loopPlaybackFrame.value = `data:image/jpeg;base64,${firstFrame.image_base64}`
  }
}

// Stop loop playback
const stopLoopPlayback = () => {
  if (loopPlaybackTimer) {
    clearInterval(loopPlaybackTimer)
    loopPlaybackTimer = null
  }
  loopPlaybackFrame.value = null
  loopPlaybackTime.value = 0
  loopFrameCache.value = []
}

// Watch for loopWindow changes to start/stop loop playback
watch(() => props.loopWindow, (newVal, oldVal) => {
  if (props.mode === 'stream' && isHttpStream.value) {
    if (newVal) {
      console.log('[LoopPlayback] Loop window set for', newVal.window_id)
      // Only start playback if already playing, otherwise wait for isPlaying to become true
      if (props.isPlaying) {
        console.log('[LoopPlayback] Starting loop playback immediately (already playing)')
        startLoopPlayback()
      } else {
        // Load frames in advance but don't start timer until playing
        console.log('[LoopPlayback] Loading frames, waiting for play state...')
        loadLoopWindowFrames()
        // Show first frame immediately so user sees something
        loopPlaybackTime.value = newVal.start_time
        getCurrentLoopFrame().then(frame => {
          if (frame && frame.image_base64) {
            loopPlaybackFrame.value = `data:image/jpeg;base64,${frame.image_base64}`
          }
        })
      }
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
      // If playing and timer exists but was paused, restart it to reset timing
      // This ensures frames start updating immediately without time jumps
      if (loopPlaybackTimer) {
        clearInterval(loopPlaybackTimer)
        loopPlaybackTimer = null
      }
      startLoopPlayback()
    } else if (!playing && loopPlaybackTimer) {
      clearInterval(loopPlaybackTimer)
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
.loop-indicator {
  position: absolute;
  top: 1rem;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.25rem;
  background: rgba(0, 0, 0, 0.85);
  border: 2px solid var(--accent-secondary, #00bcd4);
  border-radius: var(--radius-md, 8px);
  padding: 0.75rem 1.25rem;
  z-index: 20;
  animation: loop-pulse 2s ease-in-out infinite;
  box-shadow: 0 0 20px rgba(0, 188, 212, 0.4);
}

@keyframes loop-pulse {
  0%, 100% {
    box-shadow: 0 0 20px rgba(0, 188, 212, 0.4);
  }
  50% {
    box-shadow: 0 0 30px rgba(0, 188, 212, 0.6);
  }
}

.loop-icon {
  font-size: 1.5rem;
  animation: loop-spin 2s linear infinite;
}

@keyframes loop-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.loop-text {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--accent-secondary, #00bcd4);
}

.loop-time {
  font-family: var(--font-mono, monospace);
  font-size: 0.8rem;
  color: var(--text-secondary, #aaa);
}

.loop-hint {
  font-size: 0.7rem;
  color: var(--text-tertiary, #666);
  margin-top: 0.25rem;
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

