<template>
  <div class="video-wrapper">
    <div class="video-container" ref="containerRef">
      <!-- Video Content Area -->
      <template v-if="session">
        <!-- Loop Playback Frame (when in loop mode for streams) -->
        <img
          v-if="isLoopPlaybackMode && !isSimulatorStream && loopPlaybackFrame"
          :src="loopPlaybackFrame"
          class="stream-image loop-playback"
          :class="{ 'hidden-by-sam3': showSam3 && sam3Frame }"
          @click="togglePlay"
        />
        <!-- Loop Playback Loading (when loading frames) -->
        <div 
          v-else-if="isLoopPlaybackMode && !isSimulatorStream && !loopPlaybackFrame"
          class="loop-loading-overlay"
          @click="togglePlay"
        >
          <div class="loop-loading-spinner"></div>
          <div class="loop-loading-text">{{ t('video.loadingPlayback') }}</div>
        </div>
        
        <div
          v-else-if="useCanvasStream"
          class="stream-image stream-buffer"
          :class="{ 'hidden-by-sam3': showSam3 && sam3Frame }"
          @click="togglePlay"
        >
          <img
            v-if="streamBufferA"
            :src="streamBufferA"
            class="stream-buffer-frame"
            :class="{ active: activeStreamBuffer === 'a' }"
            draggable="false"
          />
          <img
            v-if="streamBufferB"
            :src="streamBufferB"
            class="stream-buffer-frame"
            :class="{ active: activeStreamBuffer === 'b' }"
            draggable="false"
          />
        </div>

        <!-- Native playback for the local capture-card simulator. Analysis still
             runs through the live simulator pipeline; preview should use the
             browser decoder instead of a JPEG frame stream. -->
        <video
          v-else-if="mode === 'stream' && isSimulatorStream"
          ref="videoRef"
          :src="`${backendUrl}/api/video/stream/${session.session_id}`"
          class="stream-image simulator-video"
          :class="{ 'hidden-by-sam3': showSam3 && sam3Frame }"
          autoplay
          muted
          playsinline
          @timeupdate="onTimeUpdate"
          @loadedmetadata="onLoadedMetadata"
          @loadeddata="onNativeDataReady"
          @canplay="onNativeCanPlay"
          @play="$emit('play')"
          @pause="onNativePause"
          @click="togglePlay"
        ></video>

        <!-- MJPEG Stream (for live streams, hidden when in loop playback mode) -->
        <!-- When paused with a frozen frame, hide this and show frozen frame -->
        <!-- When paused without frozen frame, keep showing this (stream will just not update) -->
        <img
          v-else-if="mode === 'stream' && isLiveStream"
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
          v-if="mode === 'stream' && isLiveStream && !isLoopPlaybackMode && isPaused && frozenFrame"
          :src="frozenFrame"
          class="stream-image frozen"
          :class="{ 'hidden-by-sam3': showSam3 && sam3Frame }"
        />
        <!-- Pause overlay when stream is paused but no frozen frame available -->
        <div 
          v-if="mode === 'stream' && isLiveStream && !isLoopPlaybackMode && isPaused && !frozenFrame"
          class="pause-overlay"
        >
          <div class="pause-icon">⏸</div>
          <div class="pause-text">{{ t('video.streamPaused') }}</div>
        </div>
        
        <!-- Video Element (for local files) -->
        <video
          v-else
          ref="videoRef"
          :src="`${backendUrl}/api/video/stream/${session.session_id}`"
          :class="{ 'hidden-by-sam3': showSam3 && sam3Frame }"
          @timeupdate="onTimeUpdate"
          @loadedmetadata="onLoadedMetadata"
          @play="$emit('play')"
          @pause="$emit('pause')"
          @ended="$emit('ended')"
          @click="togglePlay"
        ></video>
        
        <!-- SAM3 Segmented Frame Overlay (when SAM3 mode is enabled) -->
        <img
          v-if="showSam3 && sam3Frame"
          :src="sam3Frame"
          class="stream-image sam3-overlay"
          :alt="t('video.sam3Alt')"
        />
        
        <!-- SAM3 Loading Indicator -->
        <div v-if="showSam3 && sam3Loading" class="sam3-loading-overlay">
          <div class="sam3-loading-spinner"></div>
          <div class="sam3-loading-text">{{ t('video.loadingSam3') }}</div>
        </div>
        
        <!-- SAM3 Error Message -->
        <div v-if="showSam3 && sam3Error && !sam3Loading" class="sam3-error-overlay">
          <div class="sam3-error-icon">⚠️</div>
          <div class="sam3-error-text">{{ sam3Error }}</div>
          <button class="sam3-retry-btn" @click="fetchSam3Frame(currentTime)">{{ t('video.retry') }}</button>
        </div>
      </template>
      
      <!-- Placeholder when no video (Local Mode) -->
      <div v-else class="video-placeholder">
        <div class="upload-zone" @click="triggerUpload" @drop.prevent="onDrop" @dragover.prevent>
          <div class="upload-icon">📁</div>
          <div class="upload-text">{{ t('video.dropUpload') }}</div>
          <div class="upload-hint">{{ t('video.supportedFormats') }}</div>
          
          <div style="margin-top: 1.5rem; font-size: 0.9rem; color: var(--text-tertiary);">
            — {{ t('video.orLocalPath') }} —
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
              {{ t('video.load') }}
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
        <div class="loading-text">{{ mode === 'stream' ? t('video.connectingStream') : t('video.loadingVideo') }}</div>
      </div>
      
      <!-- Live/Ended Indicator -->
      <div v-if="session && mode === 'stream'" class="live-indicator" :class="{ 'ended': streamEnded }">
        <span class="live-dot" :class="{ 'ended': streamEnded }"></span>
        {{ streamEnded ? t('video.ended') : t('video.live') }}
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
    <div
      v-if="loopWindow"
      class="loop-indicator-bar"
      @mousedown.stop.prevent="emit('exitLoop')"
      @click.stop.prevent="emit('exitLoop')"
    >
      <span class="loop-icon">🔄</span>
      <span class="loop-text">{{ t('video.loopWindow', { num: loopWindow.window_id + 1 }) }}</span>
      <span class="loop-separator">|</span>
      <span class="loop-time">{{ formatTime(loopWindow.start_time) }} - {{ formatTime(loopWindow.end_time) }}</span>
      <span class="loop-separator">|</span>
      <span class="loop-hint">{{ t('video.loopExitHint') }}</span>
      <button
        class="loop-exit-btn"
        type="button"
        @mousedown.stop.prevent="emit('exitLoop')"
        @click.stop.prevent="emit('exitLoop')"
      >
        {{ t('video.exitLoop') }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onUnmounted, computed, nextTick } from 'vue'
import { isElectron, getBackendUrl, getCachedFrame, cacheFrame, getLocalFrame } from '@/utils/electronBridge'
import { useI18n } from '@/i18n'

const { t } = useI18n()

// 后端 URL（Electron 环境下从配置读取，浏览器环境为空使用相对路径）
const backendUrl = ref('')

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
  },
  resumeNonce: {
    type: Number,
    default: 0
  },
  windowDuration: {
    type: Number,
    default: 15  // From backend config, default 15s
  }
})

const emit = defineEmits(['timeupdate', 'play', 'pause', 'ended', 'seek', 'upload', 'load', 'sam3TimeUpdate', 'exitLoop', 'loopLoadFailed', 'loopCoverageWarning'])

const videoRef = ref(null)
const streamImgRef = ref(null)
const containerRef = ref(null)
const fileInput = ref(null)
const nativeMetadataReady = ref(false)
const pendingInitialSeek = ref(Math.max(0, Number(props.currentTime || 0)))
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
const streamBufferA = ref('')
const streamBufferB = ref('')
const activeStreamBuffer = ref('a')

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

// Computed: check if it's a local capture card device
const isCaptureDevice = computed(() => {
  if (!props.session?.video_path) return false
  return props.session.video_path.startsWith('device://') ||
    props.session.video_path.startsWith('decklink://') ||
    props.session.video_path.startsWith('simulator://')
})

const isSimulatorStream = computed(() => {
  return props.session?.video_path?.startsWith('simulator://') || false
})

// Combined: is any type of live source (HTTP/HTTPS/RTSP/capture card)
const isLiveStream = computed(() => isHttpMjpegStream.value || isRtspStream.value || isCaptureDevice.value)

const useCanvasStream = computed(() => {
  return props.mode === 'stream' && isLiveStream.value && !isSimulatorStream.value && !props.loopWindow
})

// Computed: get the stream URL
// Use the backend latest-frame display endpoint for live device/RTSP/HTTP
// sources. The simulator is intentionally excluded from this path so local
// test playback can use the browser's native video decoder.
const streamUrl = computed(() => {
  if (!props.session?.session_id) return ''
  if (!isLiveStream.value) return ''

  return `${backendUrl.value}/api/video/display-mjpeg/${props.session.session_id}?fps=20&quality=68&max_width=1280`
})

const onStreamLoad = () => {
  isLoading.value = false
}

const restoreNativeSimulatorPlayback = () => {
  if (!isSimulatorStream.value || props.loopWindow || !videoRef.value) return

  isLoading.value = false
  const targetTime = Number(props.currentTime || 0)
  if (
    targetTime > 1 &&
    Math.abs(videoRef.value.currentTime - targetTime) > 0.5
  ) {
    videoRef.value.currentTime = targetTime
  }

  if (!props.isPlaying || props.streamEnded) return

  const playCurrentVideo = () => {
    if (!videoRef.value || !props.isPlaying || props.streamEnded) return
    videoRef.value.play().catch(() => {})
  }

  playCurrentVideo()
  window.setTimeout(() => {
    if (videoRef.value?.paused) {
      playCurrentVideo()
    }
  }, 120)
}

const onNativeDataReady = () => {
  onStreamLoad()
  restoreNativeSimulatorPlayback()
}

const onNativeCanPlay = () => {
  restoreNativeSimulatorPlayback()
}

const resumeNativeSimulatorPlaybackSoon = async () => {
  await nextTick()
  restoreNativeSimulatorPlayback()
  window.setTimeout(restoreNativeSimulatorPlayback, 120)
  window.setTimeout(restoreNativeSimulatorPlayback, 360)
}

const onStreamError = () => {
  console.error('Stream error')
  isLoading.value = false
}

// Capture current frame when pausing stream
const captureFrame = () => {
  try {
    if (!streamImgRef.value) return null
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

let streamWs = null
let pendingFrameDecode = false
let lastObjectUrls = []

const stopCanvasStream = () => {
  if (streamWs) {
    streamWs.onopen = null
    streamWs.onmessage = null
    streamWs.onerror = null
    streamWs.onclose = null
    try {
      streamWs.close()
    } catch (_) {}
    streamWs = null
  }
  lastObjectUrls.forEach(url => URL.revokeObjectURL(url))
  lastObjectUrls = []
  streamBufferA.value = ''
  streamBufferB.value = ''
  activeStreamBuffer.value = 'a'
  pendingFrameDecode = false
}

const canvasStreamUrl = () => {
  if (!props.session?.session_id) return null
  const base = backendUrl.value || window.location.origin
  const url = new URL(`/api/video/ws-display/${props.session.session_id}`, base)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('fps', '30')
  url.searchParams.set('quality', '82')
  url.searchParams.set('max_width', '1920')
  return url.toString()
}

const startCanvasStream = async () => {
  if (!useCanvasStream.value || !props.session?.session_id || !backendUrl.value && !window.location.origin) {
    stopCanvasStream()
    return
  }

  stopCanvasStream()
  isLoading.value = true

  const url = canvasStreamUrl()
  if (!url) return
  streamWs = new WebSocket(url)
  streamWs.binaryType = 'arraybuffer'
  streamWs.onopen = () => {
    console.log('[CanvasStream] connected')
  }
  streamWs.onerror = () => {
    console.warn('[CanvasStream] websocket error')
    isLoading.value = false
  }
  streamWs.onclose = () => {
    console.log('[CanvasStream] closed')
  }
  streamWs.onmessage = async (event) => {
    if (pendingFrameDecode) return
    pendingFrameDecode = true
    const nextSlot = activeStreamBuffer.value === 'a' ? 'b' : 'a'
    const url = URL.createObjectURL(new Blob([event.data], { type: 'image/jpeg' }))
    const img = new Image()
    img.onload = () => {
      const previousUrl = nextSlot === 'a' ? streamBufferA.value : streamBufferB.value
      if (nextSlot === 'a') {
        streamBufferA.value = url
      } else {
        streamBufferB.value = url
      }
      activeStreamBuffer.value = nextSlot
      isLoading.value = false
      if (previousUrl) {
        setTimeout(() => URL.revokeObjectURL(previousUrl), 250)
      }
      lastObjectUrls = lastObjectUrls.filter(item => item !== previousUrl)
      lastObjectUrls.push(url)
      pendingFrameDecode = false
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      pendingFrameDecode = false
    }
    img.src = url
  }
}

// Fetch frame from backend API as fallback
const fetchFrameFromBackend = async () => {
  if (!props.session) return null
  
  try {
    // Try to get the current frame from backend analysis API
    const response = await fetch(
      `${backendUrl.value}/api/analysis/frame-at-timestamp/${props.session.session_id}?timestamp=${props.currentTime}&tolerance=2.0`
    )
    
    if (response.ok) {
      const data = await response.json()
      if (data.success && data.image_base64) {
        return `data:image/jpeg;base64,${data.image_base64}`
      }
    }
    
    // Fallback: try video frame API (works for local videos)
    const videoResponse = await fetch(
      `${backendUrl.value}/api/video/frame/${props.session.session_id}?timestamp=${props.currentTime}`
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

// 初始化后端 URL
;(async () => {
  backendUrl.value = await getBackendUrl()
  console.log('[VideoPlayer] Backend URL:', backendUrl.value || '(relative path)')
})()

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
  if (!nativeMetadataReady.value) {
    pendingInitialSeek.value = Math.max(pendingInitialSeek.value, Number(newTime || 0))
    return
  }
  if (isSimulatorStream.value && props.isPlaying && !props.loopWindow) {
    return
  }
  if (videoRef.value && Math.abs(videoRef.value.currentTime - newTime) > 0.1) {
    console.log(`[VideoPlayer] Seeking from ${videoRef.value.currentTime.toFixed(2)}s to ${newTime.toFixed(2)}s`)
    videoRef.value.currentTime = newTime
  }
})

// Watch for play/pause state
watch(() => props.isPlaying, async (playing) => {
  await nextTick()
  if (useCanvasStream.value) {
    if (playing) {
      startCanvasStream()
    } else {
      stopCanvasStream()
    }
    return
  }
  if (videoRef.value) {
    if (playing) {
      if (
        isSimulatorStream.value &&
        !props.loopWindow &&
        props.currentTime > 1 &&
        videoRef.value.currentTime < props.currentTime - 1
      ) {
        videoRef.value.currentTime = props.currentTime
      }
      videoRef.value.play().catch(() => {})
    } else {
      videoRef.value.pause()
    }
  }
}, { flush: 'post' })

watch(() => props.resumeNonce, () => {
  resumeNativeSimulatorPlaybackSoon()
}, { flush: 'post', immediate: true })

watch(
  () => [backendUrl.value, props.session?.session_id, props.mode, props.loopWindow, useCanvasStream.value],
  () => {
    if (useCanvasStream.value && props.isPlaying) {
      startCanvasStream()
    } else if (!useCanvasStream.value) {
      stopCanvasStream()
    }
  }
)

let lastNativeTimeEmit = 0
const onTimeUpdate = () => {
  if (videoRef.value && nativeMetadataReady.value) {
    if (
      isSimulatorStream.value &&
      !props.loopWindow &&
      props.currentTime > 3 &&
      videoRef.value.currentTime < props.currentTime - 2
    ) {
      videoRef.value.currentTime = props.currentTime
      return
    }
    if (isSimulatorStream.value && props.loopWindow) {
      const windowStart = props.loopWindow.start_time || 0
      const windowEnd = props.loopWindow.end_time || windowStart
      if (videoRef.value.currentTime < windowStart - 0.08) {
        videoRef.value.currentTime = windowStart
        return
      }
      if (windowEnd > windowStart && videoRef.value.currentTime >= windowEnd - 0.04) {
        videoRef.value.currentTime = windowStart
        loopPlaybackTime.value = windowStart
        emit('timeupdate', windowStart)
        return
      }
      loopPlaybackTime.value = videoRef.value.currentTime
    }
    const now = performance.now()
    if (props.mode === 'stream' && now - lastNativeTimeEmit < 250) {
      return
    }
    lastNativeTimeEmit = now
    emit('timeupdate', videoRef.value.currentTime)
  }
}

const onLoadedMetadata = () => {
  isLoading.value = false
  if (!isSimulatorStream.value && videoRef.value) {
    const targetTime = Math.max(pendingInitialSeek.value, Number(props.currentTime || 0))
    if (targetTime > 0.04 && Math.abs(videoRef.value.currentTime - targetTime) > 0.08) {
      videoRef.value.currentTime = targetTime
    }
  }
  nativeMetadataReady.value = true
  pendingInitialSeek.value = 0
  restoreNativeSimulatorPlayback()
}

watch(() => props.session?.session_id, () => {
  nativeMetadataReady.value = false
  pendingInitialSeek.value = Math.max(0, Number(props.currentTime || 0))
})

const onNativePause = () => {
  if (isSimulatorStream.value && !props.loopWindow) {
    return
  }
  emit('pause')
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
  return props.mode === 'stream' && isLiveStream.value && props.loopWindow !== null
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

// Load frames for the loop window - with local caching in Electron
const loadLoopWindowFrames = async () => {
  if (!props.session || !props.loopWindow) return false
  
  loopCacheLoading.value = true
  loopFrameCache.value = []
  backgroundLoadingAborted = false
  
  const sessionId = props.session.session_id
  // Use configured window duration from props
  const windowDuration = props.windowDuration
  // Loop preview is for review, not primary playback. Keep it bounded so opening
  // grid/loop views does not compete with the live stream renderer.
  const maxFrames = Math.min(Math.ceil(windowDuration * 15), 240)
  const useLocalCache = isElectron()
  // Electron: prefer full frames for real-time & coherent playback (preview may be partial)
  const usePreview = !useLocalCache  
  console.log(`[LoopPlayback] Loading frames for window ${props.loopWindow.window_id} (${props.loopWindow.start_time.toFixed(1)}-${props.loopWindow.end_time.toFixed(1)}s, max ${maxFrames} frames, localCache=${useLocalCache})`)  
  try {
    // Step 1: Get frame list from backend (metadata only)
    const batchResponse = await fetch(
      `${backendUrl.value}/api/analysis/frames-batch/${sessionId}?start=${props.loopWindow.start_time}&end=${props.loopWindow.end_time}&max_frames=${maxFrames}&use_url=true&use_preview=${usePreview}`
    )
    
    if (!batchResponse.ok) {
      throw new Error(`Failed to fetch frame list: ${batchResponse.status}`)
    }
    
    const batchData = await batchResponse.json()
    if (!batchData.success || !batchData.frames || batchData.frames.length === 0) {
      throw new Error('No frames found in response')
    }    
    // 【解耦增强】检查帧覆盖率信息
    if (batchData.coverage) {
      const coverage = batchData.coverage
      console.log(`[LoopPlayback] Coverage info: ${coverage.frame_count} frames, ${(coverage.coverage_ratio * 100).toFixed(1)}% coverage, actual range ${coverage.actual_start?.toFixed(2)}-${coverage.actual_end?.toFixed(2)}s`)
      
      // 如果覆盖率过低，发出警告
      if (coverage.coverage_ratio < 0.5) {
        console.warn(`[LoopPlayback] Low frame coverage (${(coverage.coverage_ratio * 100).toFixed(1)}%), playback may be choppy`)
        emit('loopCoverageWarning', {
          coverage_ratio: coverage.coverage_ratio,
          frame_count: coverage.frame_count,
          expected_frames: coverage.expected_frames,
          actual_start: coverage.actual_start,
          actual_end: coverage.actual_end
        })
      }
    }
    
    // Process and deduplicate frames
    const sortedFrames = batchData.frames
      .map(f => {
        // Extract filename from URL for caching
        const urlPath = f.url.startsWith('/') ? f.url : new URL(f.url).pathname
        const filename = urlPath.split('/').pop()
        return {
          timestamp: f.timestamp,
          filename: filename,
          remoteUrl: f.url.startsWith('/') ? `${backendUrl.value}${f.url}` : f.url,
          url: null  // Will be set to blob URL or remote URL
        }
      })
      .sort((a, b) => a.timestamp - b.timestamp)
    
    // Remove duplicates
    const seenTimestamps = new Set()
    const uniqueFrames = sortedFrames.filter(f => {
      const tsKey = Math.round(f.timestamp * 100)
      if (seenTimestamps.has(tsKey)) return false
      seenTimestamps.add(tsKey)
      return true
    })
    
    // 后端返回的 subfolder（frames 或 preview）
    const subfolder = batchData.subfolder || 'frames'
    console.log(`[LoopPlayback] Got ${uniqueFrames.length} ${subfolder} frames metadata, isElectron=${useLocalCache}`)
    
    // Step 2: Load frames (from local cache in Electron, or preload from network)
    if (useLocalCache) {
      // Electron mode: read directly from local file system (no HTTP!)
      console.log(`[LoopPlayback] Electron mode: reading frames from local storage`)
      let cacheHits = 0
      let cacheMisses = 0
      const blobUrls = []  // Track blob URLs for cleanup
      
      // Process frames - read directly from local file system (no HTTP!)
      const processFrame = async (frame) => {
        // Electron: Read directly from backend's session storage folder
        // This avoids HTTP transfer completely!
        try {
          const localResult = await getLocalFrame(sessionId, frame.filename, subfolder)
          if (localResult.success && localResult.data) {
            cacheHits++
            const blobUrl = URL.createObjectURL(localResult.data)
            blobUrls.push(blobUrl)
            frame.url = blobUrl
            return
          }
          
          // Log why local read failed
          if (cacheHits === 0 && cacheMisses === 0) {
            // First frame - log detailed error
            console.warn(`[LoopPlayback] First frame local read failed:`, localResult.error)
          }
          
          // Fallback: try 'frames' subfolder if current subfolder failed
          if (subfolder !== 'frames') {
            const framesResult = await getLocalFrame(sessionId, frame.filename, 'frames')
            if (framesResult.success && framesResult.data) {
              cacheHits++
              const blobUrl = URL.createObjectURL(framesResult.data)
              blobUrls.push(blobUrl)
              frame.url = blobUrl
              return
            }
          }
        } catch (e) {
          console.error(`[LoopPlayback] getLocalFrame threw error:`, e)
        }
        
        // Last resort: download from network (should rarely happen in Electron)
        cacheMisses++
        if (cacheMisses <= 3) {
          console.warn(`[LoopPlayback] Frame ${frame.filename} not found locally (session=${sessionId}, subfolder=${subfolder}), downloading from ${frame.remoteUrl}`)
        }
        try {
          const response = await fetch(frame.remoteUrl)
          if (response.ok) {
            const blob = await response.blob()
            const blobUrl = URL.createObjectURL(blob)
            blobUrls.push(blobUrl)
            frame.url = blobUrl
          } else {
            console.warn(`[LoopPlayback] Failed to download frame ${frame.filename}: HTTP ${response.status}`)
            frame.url = frame.remoteUrl
          }
        } catch (e) {
          console.warn(`[LoopPlayback] Failed to download frame ${frame.filename}:`, e)
          frame.url = frame.remoteUrl
        }
      }
      
      // Process first batch immediately for quick start
      const FIRST_BATCH_SIZE = 30
      const firstBatch = uniqueFrames.slice(0, FIRST_BATCH_SIZE)
      await Promise.all(firstBatch.map(processFrame))
      
      if (backgroundLoadingAborted) {
        // Cleanup blob URLs
        blobUrls.forEach(url => URL.revokeObjectURL(url))
        return false
      }
      
      // Filter out frames without valid URLs and set cache
      const validFrames = uniqueFrames.filter(f => f.url)
      const invalidCount = uniqueFrames.length - validFrames.length
      if (invalidCount > 0) {
        console.warn(`[LoopPlayback] ${invalidCount} frames have no URL, filtered out`)
      }
      
      loopFrameCache.value = validFrames
      if (validFrames[0]?.url) {
        loopPlaybackFrame.value = validFrames[0].url
      }
      
      console.log(`[LoopPlayback] First batch: ${validFrames.length} valid frames (cache: ${cacheHits} hits, ${cacheMisses} misses)`)
      loopCacheLoading.value = false
      
      // Load ALL remaining frames (wait for completion to ensure full window coverage)
      if (uniqueFrames.length > FIRST_BATCH_SIZE) {
        console.log(`[LoopPlayback] Loading remaining ${uniqueFrames.length - FIRST_BATCH_SIZE} frames...`)
        const remaining = uniqueFrames.slice(FIRST_BATCH_SIZE)
        for (let i = 0; i < remaining.length; i += 10) {
          if (backgroundLoadingAborted) {
            console.log(`[LoopPlayback] Background loading aborted`)
            return false
          }
          const batch = remaining.slice(i, i + 10)
          await Promise.all(batch.map(processFrame))
        }
        // Update cache with only valid frames
        const allValidFrames = uniqueFrames.filter(f => f.url)
        loopFrameCache.value = allValidFrames
        console.log(`[LoopPlayback] All frames loaded: ${allValidFrames.length} valid (cache: ${cacheHits} hits, ${cacheMisses} misses)`)
      }
      
      return true
    } else {
      // Browser mode: use remote URLs. 过去这里 Promise.all 同时并发 30 个
      // 预加载——经由 Vite proxy / SSH tunnel 会制造连接风暴，主线程表现为
      // "卡死"。改为先只预热 5 帧触发播放，剩余帧用串行小批次后台慢加载。
      uniqueFrames.forEach(f => { f.url = f.remoteUrl })
      loopFrameCache.value = uniqueFrames
      loopPlaybackFrame.value = uniqueFrames[0].url

      const WARM_SIZE = 5
      const BG_BATCH_SIZE = 4
      const warmBatch = uniqueFrames.slice(0, WARM_SIZE)
      await Promise.all(warmBatch.map(f => preloadImage(f.url)))

      if (backgroundLoadingAborted) return false

      console.log(`[LoopPlayback] ${warmBatch.length} frames warmed (browser mode), ${uniqueFrames.length - warmBatch.length} to load in background`)
      loopCacheLoading.value = false

      // Background preload rest with tiny concurrent pool
      if (uniqueFrames.length > WARM_SIZE) {
        const loadRemaining = async () => {
          const remaining = uniqueFrames.slice(WARM_SIZE)
          for (let i = 0; i < remaining.length; i += BG_BATCH_SIZE) {
            if (backgroundLoadingAborted) return
            const batch = remaining.slice(i, i + BG_BATCH_SIZE)
            await Promise.all(batch.map(f => preloadImage(f.url)))
            // 让出主线程，避免长时间霸占
            await new Promise(r => setTimeout(r, 16))
          }
          console.log(`[LoopPlayback] All ${uniqueFrames.length} images preloaded`)
        }
        loadRemaining()
      }
      
      return true
    }
  } catch (e) {
    console.error('[LoopPlayback] Failed to load frames:', e)
    
    // Fallback: try base64 mode
    try {
      console.log('[LoopPlayback] Trying base64 fallback...')
      
      const fallbackResponse = await fetch(
        `${backendUrl.value}/api/analysis/frames-batch/${props.session.session_id}?start=${props.loopWindow.start_time}&end=${props.loopWindow.end_time}&max_frames=${maxFrames}&use_url=false&use_preview=${usePreview}`
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
    } catch (fallbackError) {
      console.error('[LoopPlayback] Base64 fallback also failed:', fallbackError)
    }
    
    emit('loopLoadFailed', t('video.failedToLoad'))
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

// Start loop playback with real-time based playback (1 real second = 1 video second)
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
  
  const windowStart = props.loopWindow.start_time
  const windowEnd = props.loopWindow.end_time
  // Use configured window duration from props (from backend config)
  const windowDuration = props.windowDuration
  const frameCount = loopFrameCache.value.length
  
  // Calculate actual frame rate from timestamps (average interval between frames)
  let avgFrameInterval = 0
  if (frameCount > 1) {
    const firstTs = loopFrameCache.value[0].timestamp
    const lastTs = loopFrameCache.value[frameCount - 1].timestamp
    avgFrameInterval = (lastTs - firstTs) / (frameCount - 1)
  }
  const actualFps = avgFrameInterval > 0 ? (1 / avgFrameInterval).toFixed(1) : 'N/A'
  
  // Get frame timestamps for calculating actual playback range
  const frames = loopFrameCache.value
  const firstFrameTs = frames[0]?.timestamp || windowStart
  const lastFrameTs = frames[frameCount - 1]?.timestamp || windowEnd
  
  // Use the ACTUAL frame span for looping, not the window duration
  // This prevents freezing on last frame when frames don't cover the full window
  const frameSpan = lastFrameTs - firstFrameTs
  const effectiveLoopDuration = frameSpan > 0 ? frameSpan : windowDuration
  
  console.log(`[LoopPlayback] Frame-based loop: ${frameCount} frames, span ${firstFrameTs.toFixed(2)}-${lastFrameTs.toFixed(2)}s (${effectiveLoopDuration.toFixed(2)}s), window ${windowStart.toFixed(1)}-${windowEnd.toFixed(1)}s`)
  // Playback state
  let playbackStartTime = performance.now()
  let lastDisplayedFrameIndex = -1
  let lastEmittedTime = firstFrameTs
  
  const playbackLoop = (realTime) => {
    if (!props.loopWindow) {
      loopPlaybackTimer = requestAnimationFrame(playbackLoop)
      return
    }
    
    if (!props.isPlaying) {
      // When paused, keep current position
      const currentVideoTime = loopPlaybackTime.value || firstFrameTs
      const offsetMs = (currentVideoTime - firstFrameTs) * 1000
      playbackStartTime = realTime - offsetMs
      loopPlaybackTimer = requestAnimationFrame(playbackLoop)
      return
    }
    
    // Calculate elapsed time and loop within frame span
    const elapsedMs = realTime - playbackStartTime
    const elapsedSec = elapsedMs / 1000
    
    // Loop within the actual frame time range
    const loopPosition = elapsedSec % effectiveLoopDuration
    const currentVideoTime = firstFrameTs + loopPosition
    
    // Simple sequential frame selection based on loop progress
    // This ensures smooth playback through all frames
    const frameProgress = loopPosition / effectiveLoopDuration
    let frameIndex = Math.floor(frameProgress * frameCount)
    if (frameIndex >= frameCount) frameIndex = frameCount - 1
    if (frameIndex < 0) frameIndex = 0
    
    // Update display if frame changed
    if (frameIndex !== lastDisplayedFrameIndex) {
      const frame = frames[frameIndex]
      if (frame && frame.url) {
        loopPlaybackFrame.value = frame.url
        lastDisplayedFrameIndex = frameIndex
      }
    }
    
    // Update playback time for UI (show actual frame timestamp)
    loopPlaybackTime.value = frames[frameIndex]?.timestamp || currentVideoTime
    
    // Emit time update periodically
    if (Math.abs(currentVideoTime - lastEmittedTime) > 0.1) {
      lastEmittedTime = currentVideoTime
      emit('timeupdate', currentVideoTime)
    }
    
    loopPlaybackTimer = requestAnimationFrame(playbackLoop)
  }
  
  loopPlaybackTimer = requestAnimationFrame(playbackLoop)
  emit('timeupdate', windowStart)
}

// Start loop playback timer only (when frames are already cached)
// Uses frame-based timing: loops through all available frames smoothly
const startLoopPlaybackTimer = () => {
  if (loopPlaybackTimer) {
    cancelAnimationFrame(loopPlaybackTimer)
    loopPlaybackTimer = null
  }
  
  if (!props.loopWindow || loopFrameCache.value.length === 0) return
  
  const frames = loopFrameCache.value
  const frameCount = frames.length
  const firstFrameTs = frames[0]?.timestamp || props.loopWindow.start_time
  const lastFrameTs = frames[frameCount - 1]?.timestamp || props.loopWindow.end_time
  
  // Use actual frame span for looping
  const frameSpan = lastFrameTs - firstFrameTs
  const effectiveLoopDuration = frameSpan > 0 ? frameSpan : props.windowDuration
  
  // Start from current position within the frame range
  const currentTime = loopPlaybackTime.value || firstFrameTs
  const offsetInLoop = Math.max(0, currentTime - firstFrameTs)
  let playbackStartTime = performance.now() - (offsetInLoop * 1000)
  let lastDisplayedFrameIndex = -1
  let lastEmittedTime = currentTime
  
  const playbackLoop = (realTime) => {
    if (!props.loopWindow) {
      loopPlaybackTimer = requestAnimationFrame(playbackLoop)
      return
    }
    
    if (!props.isPlaying) {
      // Keep current position when paused
      const curTime = loopPlaybackTime.value || firstFrameTs
      const offset = Math.max(0, curTime - firstFrameTs) * 1000
      playbackStartTime = realTime - offset
      loopPlaybackTimer = requestAnimationFrame(playbackLoop)
      return
    }
    
    // Calculate loop position
    const elapsedMs = realTime - playbackStartTime
    const elapsedSec = elapsedMs / 1000
    const loopPosition = elapsedSec % effectiveLoopDuration
    
    // Sequential frame selection based on progress
    const frameProgress = loopPosition / effectiveLoopDuration
    let frameIndex = Math.floor(frameProgress * frameCount)
    if (frameIndex >= frameCount) frameIndex = frameCount - 1
    if (frameIndex < 0) frameIndex = 0
    
    // Update display if frame changed
    if (frameIndex !== lastDisplayedFrameIndex) {
      const frame = frames[frameIndex]
      if (frame && frame.url) {
        loopPlaybackFrame.value = frame.url
        lastDisplayedFrameIndex = frameIndex
      }
    }
    
    // Update playback time
    const currentVideoTime = frames[frameIndex]?.timestamp || (firstFrameTs + loopPosition)
    loopPlaybackTime.value = currentVideoTime
    
    if (Math.abs(currentVideoTime - lastEmittedTime) > 0.1) {
      lastEmittedTime = currentVideoTime
      emit('timeupdate', currentVideoTime)
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
  if (props.mode === 'stream' && isLiveStream.value) {
    if (isSimulatorStream.value) {
      stopLoopPlayback()
      if (newVal) {
        const seekToLoopStart = () => {
          if (!videoRef.value) return
          videoRef.value.currentTime = newVal.start_time || 0
          loopPlaybackTime.value = newVal.start_time || 0
          emit('timeupdate', loopPlaybackTime.value)
          if (props.isPlaying) {
            videoRef.value.play().catch(() => {})
          }
        }
        requestAnimationFrame(seekToLoopStart)
      } else if (oldVal && !newVal) {
        const restoreLivePlayback = () => {
          if (!videoRef.value) return
          const resumeAt = Number.isFinite(props.currentTime) ? props.currentTime : oldVal.end_time || 0
          if (Math.abs(videoRef.value.currentTime - resumeAt) > 0.08) {
            videoRef.value.currentTime = resumeAt
          }
          loopPlaybackTime.value = 0
          if (props.isPlaying) {
            videoRef.value.play().catch(() => {})
          } else {
            videoRef.value.pause()
          }
        }
        requestAnimationFrame(restoreLivePlayback)
      }
      return
    }
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
    if (isSimulatorStream.value) {
      if (videoRef.value) {
        if (playing) {
          videoRef.value.play().catch(() => {})
        } else {
          videoRef.value.pause()
        }
      }
      return
    }
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
// [perf] 墙钟时间门限，供 fetchSam3Frame 与 watch(currentTime) 共享，
// 避免 interval + watch 两条路径叠加成双倍请求
let _lastSam3FetchTs = 0
const fetchSam3Frame = async (timestamp, forceOnDemand = false) => {
  if (!props.session || !props.showSam3 || !props.sam3Available) return
  
  // Prevent concurrent requests - this avoids race conditions and flicker
  if (sam3FetchInProgress.value) return
  
  // Debounce: only fetch if timestamp changed by more than 0.15 seconds (matches SAM3 update rate)
  if (Math.abs(timestamp - lastSam3Timestamp.value) < 0.15 && sam3Frame.value) return
  lastSam3Timestamp.value = timestamp
  // [perf] 记录墙钟时间，watch(currentTime) 共享此门限，避免 interval + watch 双触发
  _lastSam3FetchTs = Date.now()
  
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
      `${backendUrl.value}/api/analysis/sam3/stream-frame/${props.session.session_id}`,
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
        `${backendUrl.value}/api/analysis/sam3/segmented-frame/${props.session.session_id}?timestamp=${timestamp}&alpha=0.4`,
        { signal: controller.signal }
      )
      
      // Check HTTP status
      if (!response.ok) {
        const errorText = await response.text()
        console.error(`SAM3 segmented-frame error: ${response.status}`, errorText.substring(0, 200))
        sam3Error.value = `${t('video.failedToLoad')} (${response.status})`
        // DON'T clear sam3Frame - keep showing last valid frame to prevent flicker
        sam3Loading.value = false
        return
      }
      
      try {
        data = await response.json()
      } catch (jsonError) {
        console.error('Failed to parse segmented-frame response:', jsonError)
        sam3Error.value = t('video.failedToLoad')
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
        sam3Error.value = t('video.noSam3Data')
      }
    }
  } catch (error) {
    console.error('Failed to fetch SAM3 frame:', error)
    if (error.name === 'AbortError') {
      sam3Error.value = t('video.requestTimeout')
    } else {
      sam3Error.value = error.message || t('video.failedToLoad')
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

// Also watch currentTime changes when SAM3 is enabled.
// [perf] 过去这里每次 currentTime 变化（直播 250ms / 本地 timeupdate）都触发 fetch，
// 与下面的 setInterval(250ms) 叠加造成实际 ~2x 请求。现在共享 _lastSam3FetchTs
// 墙钟门限 400ms，保证 watch + interval 合起来不会超过 ~2.5 Hz，
// 同时跳过正在加载中的情况。
watch(() => props.currentTime, (newTime) => {
  if (!(props.showSam3 && props.sam3Available && props.session)) return
  if (sam3Loading.value) return
  const now = Date.now()
  if (now - _lastSam3FetchTs < 400) return
  fetchSam3Frame(newTime)
})

onUnmounted(() => {
  // Clear SAM3 timer
  if (sam3LoadingTimer.value) {
    clearInterval(sam3LoadingTimer.value)
  }
  
  // Stop loop playback
  stopLoopPlayback()
  stopCanvasStream()
  
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
  font-size: 1.05rem;
  background: rgba(0, 0, 0, 0.72);
  padding: 0.45rem 0.85rem;
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
  font-size: 0.92rem;
}

.live-indicator {
  position: absolute;
  top: 1rem;
  left: 1rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-family: var(--font-mono);
  font-size: 0.95rem;
  font-weight: 600;
  background: rgba(255, 0, 0, 0.8);
  color: white;
  padding: 0.45rem 0.85rem;
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
  display: block;
}

.stream-image.simulator-video {
  object-fit: cover;
  object-position: center center;
}

.stream-image.stream-buffer {
  position: relative;
  overflow: hidden;
  background: #000;
}

.stream-buffer-frame {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center center;
  opacity: 0;
  display: block;
}

.stream-buffer-frame.active {
  opacity: 1;
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
  font-size: 3.4rem;
  color: white;
  opacity: 0.9;
  margin-bottom: 0.5rem;
}

.pause-text {
  color: white;
  font-size: 1.05rem;
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
  font-size: 1.05rem;
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
  font-size: 1.05rem;
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
  font-size: 1.15rem;
}

.loop-indicator-bar .loop-text {
  font-size: 1rem;
  font-weight: 600;
  color: white;
}

.loop-indicator-bar .loop-separator {
  color: rgba(255, 255, 255, 0.5);
  font-size: 0.95rem;
}

.loop-indicator-bar .loop-time {
  font-family: var(--font-mono, monospace);
  font-size: 1rem;
  color: rgba(255, 255, 255, 0.9);
}

.loop-indicator-bar .loop-hint {
  font-size: 0.9rem;
  color: rgba(255, 255, 255, 0.7);
  cursor: pointer;
}

.loop-indicator-bar .loop-hint:hover {
  color: white;
  text-decoration: underline;
}

.loop-exit-btn {
  border: 1px solid rgba(255, 255, 255, 0.55);
  background: rgba(0, 0, 0, 0.22);
  color: white;
  border-radius: 4px;
  padding: 0.28rem 0.65rem;
  font-size: 0.9rem;
  font-weight: 700;
  cursor: pointer;
}

.loop-exit-btn:hover {
  background: rgba(0, 0, 0, 0.36);
  border-color: rgba(255, 255, 255, 0.8);
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
