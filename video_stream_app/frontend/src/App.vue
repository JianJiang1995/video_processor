<template>
  <div class="app-container">
    <!-- Mode Selection Screen -->
    <ModeSelector 
      v-if="currentView === 'select'"
      @select-mode="handleModeSelect"
      @resume-session="handleResumeSession"
    />

    <!-- Stream Input Screen -->
    <div v-else-if="currentView === 'stream-input'" class="stream-setup">
      <StreamInput 
        @connect="handleStreamConnect"
        @back="currentView = 'select'"
      />
    </div>

    <!-- Main Video Analyzer View -->
    <template v-else>
      <!-- Header -->
      <header class="app-header">
        <div class="logo" @click="goHome">
          <div class="logo-icon">🏥</div>
          <div class="logo-text">Surg-R1<span>手术助手</span></div>
        </div>
        
        <div class="header-center">
          <span class="mode-badge" :class="mode">
            {{ mode === 'local' ? '📁 本地视频' : '📡 实时视频流' }}
          </span>
          <span v-if="currentSession && mode === 'stream'" class="session-id-badge">
            ID: {{ currentSession.session_id?.substring(0, 8) || '' }}
          </span>
          <span v-if="currentSession" class="session-name">
            {{ currentSession.video_name }}
          </span>
        </div>

        <div class="header-actions">
          <button class="btn btn-secondary" @click="goHome">
            ← 返回
          </button>
        </div>
      </header>

      <!-- Main Content -->
      <main class="app-main">
        <!-- Video Section -->
        <section class="video-section" @click="handleVideoSectionClick">
          <VideoPlayer
            :session="currentSession"
            :currentTime="currentTime"
            :isPlaying="isPlaying"
            :isPaused="!isPlaying && mode === 'stream'"
            :mode="mode"
            :showSam3="showSam3"
            :sam3Available="sam3Status.available"
            :loopWindow="loopWindow"
            :streamEnded="streamEnded"
            @timeupdate="handleTimeUpdate"
            @play="handlePlay"
            @pause="handlePause"
            @seek="handleSeek"
            @upload="handleUpload"
            @load="handleLoad"
            @sam3TimeUpdate="handleSam3TimeUpdate"
            @exitLoop="exitLoopMode"
            @loopLoadFailed="handleLoopLoadFailed"
          />
          <ControlBar
            :currentTime="currentTime"
            :duration="duration"
            :isPlaying="isPlaying"
            :volume="volume"
            :mode="mode"
            :isLive="mode === 'stream'"
            :analyzedWindows="analyzedWindows"
            :summaries="summaries"
            :highlightedWindowId="highlightedWindowId"
            :isAnalyzing="isProcessing"
            :surgr1Status="surgr1Status"
            :glmStatus="glmStatus"
            :sam3Status="sam3Status"
            :asrStatus="asrStatus"
            :ttsStatus="ttsStatus"
            :showSam3="showSam3"
            :surgr1Processing="surgr1ProcessingStatus"
            :sam3Time="sam3Time"
            :windowDuration="windowDuration"
            @play="handlePlay"
            @pause="handlePause"
            @seek="handleSeek"
            @volume="handleVolumeChange"
            @analyze="startAnalysis"
            @stopAnalyze="stopAnalysis"
            @seekToWindow="handleSeekToWindow"
            @hoverWindow="handleWindowHover"
            @dragSeek="handleDragSeek"
            @toggleSam3="handleToggleSam3"
          />
        </section>

        <!-- Summary Panel -->
        <section class="summary-section">
          <SummaryPanel
            :summaries="summaries"
            :currentSummary="currentSummary"
            :currentTime="currentTime"
            :isProcessing="isProcessing"
            :mode="mode"
            :highlightedWindowId="highlightedWindowId"
            :sessionId="currentSession?.session_id || ''"
            @tts="handleTTS"
            @sam2="handleSAM2"
            @seek="handleSeek"
            @seekToWindow="handleSeekToWindow"
            @play="handlePlay"
          />
        </section>
      </main>
      
      <!-- Frame Analysis Popup (shown during drag/seek) -->
      <FrameAnalysisPopup
        :visible="frameAnalysisPopup.visible"
        :frameData="frameAnalysisPopup.data"
        :isLoading="frameAnalysisPopup.isLoading"
        :position="frameAnalysisPopup.position"
        @close="closeFrameAnalysisPopup"
      />
      
      <!-- Voice Chat Component -->
      <VoiceChat 
        :sessionId="currentSession?.session_id || 'default'"
        @message="handleVoiceMessage"
        @transcript="handleVoiceTranscript"
      />
      
      <!-- Toast Message -->
      <Transition name="toast">
        <div v-if="toastVisible" class="toast-message">
          <span class="toast-icon">⚠️</span>
          <span class="toast-text">{{ toastMessage }}</span>
        </div>
      </Transition>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import analysisQueue from './utils/AnalysisQueue.js'
import ModeSelector from './components/ModeSelector.vue'
import StreamInput from './components/StreamInput.vue'
import VideoPlayer from './components/VideoPlayer.vue'
import ControlBar from './components/ControlBar.vue'
import SummaryPanel from './components/SummaryPanel.vue'
import VoiceChat from './components/VoiceChat.vue'
import FrameAnalysisPopup from './components/FrameAnalysisPopup.vue'

// View state
const currentView = ref('select')  // 'select', 'stream-input', 'main'
const mode = ref('local')  // 'local' or 'stream'

// State
const currentSession = ref(null)
const currentTime = ref(0)
const duration = ref(0)
const isPlaying = ref(false)
const volume = ref(0.8)
const summaries = ref([])
const isProcessing = ref(false)

// New states for enhanced features
const highlightedWindowId = ref(-1)
const userSelectedWindow = ref(false)  // True when user manually selected a window
const isDragging = ref(false)
const showSam3 = ref(false)  // Toggle for SAM3 segmented view

// Loop playback state - when set, video will loop within this window
const loopWindow = ref(null)  // { window_id, start_time, end_time }
// Flag to prevent clearing loopWindow during loop-triggered seeks
let isLoopSeek = false
const sam3Time = ref(null)  // SAM3 frame timestamp (may differ from currentTime due to processing delay)
const frameAnalysisPopup = ref({
  visible: false,
  data: null,
  isLoading: false,
  position: { x: 0, y: 0 }
})
const dragDebounceTimer = ref(null)

// EventSource reference for SSE
let analysisEventSource = null

// Analysis service status
const surgr1Status = ref({ available: false, checking: true })
const glmStatus = ref({ available: false, checking: true })
const sam3Status = ref({ available: false, checking: true })
const asrStatus = ref({ available: false, checking: true })
const ttsStatus = ref({ available: false, checking: true })

// SurgR1 continuous processing status
const surgr1ProcessingStatus = ref({ running: false, framesAnalyzed: 0 })

// Stream polling and timing
let streamPollingInterval = null
let streamTimerInterval = null
let streamEndCheckInterval = null  // Check if stream has ended
const streamStartTime = ref(null)  // When stream started (for elapsed time)
const streamEnded = ref(false)  // Whether the video stream has ended
const streamWasActive = ref(false)  // Track if stream was ever active (for reliable end detection)

// Global AbortController for session-related requests
// When goHome is called, this will abort all pending requests
let sessionAbortController = null

// Create a new abort controller for the session
const createSessionAbortController = () => {
  // Abort any existing controller first
  if (sessionAbortController) {
    sessionAbortController.abort()
  }
  sessionAbortController = new AbortController()
  return sessionAbortController
}

// Get the current abort signal (for axios requests)
const getSessionSignal = () => {
  return sessionAbortController?.signal
}

// Abort all session requests
const abortAllSessionRequests = () => {
  if (sessionAbortController) {
    console.log('[Session] Aborting all session requests')
    sessionAbortController.abort()
    sessionAbortController = null
  }
}

// Window duration (从后端配置获取，默认5秒)
const windowDuration = ref(5)

// 获取后端配置
const fetchConfig = async () => {
  try {
    const res = await axios.get('/api/config')
    windowDuration.value = res.data.window_duration || 5
    console.log('[Config] Window duration:', windowDuration.value)
  } catch (e) {
    console.warn('[Config] Failed to fetch config, using defaults:', e.message)
  }
}

// Computed: current summary based on time
// When in loop playback mode, always show the loop window's summary to avoid flickering
const currentSummary = computed(() => {
  if (!summaries.value.length) return null
  
  // If in loop playback mode, use the fixed loop window ID
  // This prevents flickering caused by currentTime constantly changing
  if (loopWindow.value) {
    return summaries.value.find(s => s.window_id === loopWindow.value.window_id) || null
  }
  
  const windowId = Math.floor(currentTime.value / windowDuration.value)
  return summaries.value.find(s => s.window_id === windowId) || null
})

// Computed: list of analyzed window IDs
const analyzedWindows = computed(() => {
  return summaries.value.map(s => s.window_id)
})

// Mode selection handlers
const handleModeSelect = (selectedMode) => {
  mode.value = selectedMode
  if (selectedMode === 'stream') {
    currentView.value = 'stream-input'
  } else {
    currentView.value = 'main'
    // Restart service status polling when entering main view
    restartAnalysisStatusInterval()
  }
}

const handleResumeSession = (session) => {
  currentSession.value = session
  duration.value = session.duration
  
  // Detect if this is a stream session based on video_path
  const isStreamSession = session.video_path && (
    session.video_path.startsWith('http://') || 
    session.video_path.startsWith('https://') ||
    session.video_path.startsWith('rtsp://')
  )
  
  mode.value = isStreamSession ? 'stream' : 'local'
  currentView.value = 'main'
  loadExistingSummaries(session.session_id)
  // Restart service status polling when resuming session
  restartAnalysisStatusInterval()
  
  // If resuming a stream session, also set playing state
  if (isStreamSession) {
    isPlaying.value = true
    streamStartTime.value = Date.now()
  }
}

const handleStreamConnect = ({ session, autoAnalyze }) => {
  currentSession.value = session
  duration.value = 0  // Live stream has no fixed duration
  currentView.value = 'main'
  isPlaying.value = true
  currentTime.value = 0
  streamStartTime.value = Date.now()  // Track when stream started
  
  // Restart service status polling when entering main view
  restartAnalysisStatusInterval()
  
  // Auto-start SurgR1 continuous processing when stream connects
  startSurgR1Continuous(session.session_id)
  
  if (autoAnalyze) {
    startAnalysis()
  }
  
  // Start timer for live stream elapsed time
  startStreamTimer()
}

const goHome = () => {
  console.log('[goHome] Cleaning up and returning to home...')
  
  // 1. Abort all pending session requests immediately
  abortAllSessionRequests()
  
  // 2. Stop all polling intervals
  stopStreamPolling()
  stopSurgR1StatusPolling()
  
  // 3. Stop the analysis status interval (30s service check)
  if (analysisStatusInterval) {
    clearInterval(analysisStatusInterval)
    analysisStatusInterval = null
  }
  
  // 4. Clear analysis queue - this will also abort any pending batch requests
  const droppedFrames = analysisQueue.clear()
  if (droppedFrames > 0) {
    console.log(`[goHome] Dropped ${droppedFrames} queued frames`)
  }
  
  // 5. Stop SurgR1 continuous processing on backend (use sendBeacon for reliability)
  if (currentSession.value) {
    // Use sendBeacon to ensure the stop request is sent even if page navigation happens
    const sessionId = currentSession.value.session_id
    try {
      navigator.sendBeacon(`/api/analysis/stop-surgr1-continuous/${sessionId}`, '')
      console.log(`[goHome] Sent stop request for session ${sessionId}`)
    } catch (e) {
      console.warn('[goHome] sendBeacon failed, trying fetch:', e)
      // Fallback: fire-and-forget fetch
      fetch(`/api/analysis/stop-surgr1-continuous/${sessionId}`, { 
        method: 'POST',
        keepalive: true 
      }).catch(() => {})
    }
  }
  
  // 6. Close analysis EventSource if running
  if (analysisEventSource) {
    analysisEventSource.close()
    analysisEventSource = null
  }
  
  // 7. Clear any pending timers
  if (dragDebounceTimer.value) {
    clearTimeout(dragDebounceTimer.value)
    dragDebounceTimer.value = null
  }
  
  // 8. Reset all state
  showSam3.value = false
  currentView.value = 'select'
  currentSession.value = null
  summaries.value = []
  isProcessing.value = false
  isPlaying.value = false
  currentTime.value = 0
  surgr1ProcessingStatus.value = { running: false, framesAnalyzed: 0 }
  highlightedWindowId.value = -1
  userSelectedWindow.value = false
  loopWindow.value = null
  frameAnalysisPopup.value = { visible: false, data: null, isLoading: false, position: { x: 0, y: 0 } }
  streamEnded.value = false
  streamWasActive.value = false
  
  console.log('[goHome] Cleanup complete')
}

// Video handlers
const handleTimeUpdate = (time) => {
  currentTime.value = time
  
  // Check if we need to loop within window
  // IMPORTANT: For stream mode with HTTP stream, VideoPlayer.vue handles looping internally
  // via loopPlaybackTimer. We only handle looping here for local video mode.
  if (loopWindow.value && isPlaying.value && mode.value === 'local') {
    // If time has passed or is about to pass the end of the loop window, seek back to start
    if (time >= loopWindow.value.end_time - 0.1) {
      console.log(`[Loop] Reached end of window ${loopWindow.value.window_id}, looping back to ${loopWindow.value.start_time}`)
      isLoopSeek = true
      handleSeek(loopWindow.value.start_time)
      // Reset flag after a short delay
      setTimeout(() => { isLoopSeek = false }, 100)
    }
  }
}

// Handle SAM3 frame timestamp update (for sync display)
const handleSam3TimeUpdate = (time) => {
  sam3Time.value = time
}

const handlePlay = () => {
  isPlaying.value = true
  if (currentSession.value) {
    if (mode.value === 'stream') {
      // Resume stream timer
      resumeStreamTimer()
    }
    axios.post(`/api/video/control/${currentSession.value.session_id}`, {
      action: 'play'
    })
  }
}

const handlePause = () => {
  isPlaying.value = false
  if (currentSession.value) {
    if (mode.value === 'stream') {
      // Pause stream timer
      pauseStreamTimer()
    }
    axios.post(`/api/video/control/${currentSession.value.session_id}`, {
      action: 'pause'
    })
  }
}

const handleSeek = (time) => {
  currentTime.value = time
  
  // If this is a manual seek (not triggered by loop), exit loop mode
  if (!isLoopSeek && loopWindow.value) {
    console.log('[Loop] Manual seek detected, exiting loop mode')
    loopWindow.value = null
  }
  
  if (currentSession.value) {
    axios.post(`/api/video/control/${currentSession.value.session_id}`, {
      action: 'seek',
      position: time
    })
  }
}

// Exit loop playback mode
const exitLoopMode = () => {
  if (loopWindow.value) {
    console.log('[Loop] Exiting loop mode')
    loopWindow.value = null
    userSelectedWindow.value = false
    highlightedWindowId.value = -1
  }
}

// Handle loop load failure - show toast message
const handleLoopLoadFailed = (message) => {
  console.warn('[Loop] Load failed:', message)
  // Show temporary toast message
  showToast(message)
}

// Toast message state
const toastMessage = ref('')
const toastVisible = ref(false)
let toastTimer = null

const showToast = (message) => {
  toastMessage.value = message
  toastVisible.value = true
  if (toastTimer) {
    clearTimeout(toastTimer)
  }
  toastTimer = setTimeout(() => {
    toastVisible.value = false
  }, 3000)
}

const handleVolumeChange = (vol) => {
  volume.value = vol
}

const handleUpload = async (file) => {
  const formData = new FormData()
  formData.append('file', file)
  
  try {
    const response = await axios.post('/api/video/upload', formData)
    currentSession.value = response.data
    duration.value = response.data.duration
    summaries.value = []
    currentTime.value = 0
    
    // Auto-start SurgR1 continuous processing when video is uploaded
    startSurgR1Continuous(response.data.session_id)
  } catch (error) {
    console.error('Upload failed:', error)
    alert('Upload failed: ' + (error.response?.data?.detail || error.message))
  }
}

const handleLoad = async (path) => {
  try {
    const response = await axios.post('/api/video/load', null, {
      params: { video_path: path }
    })
    currentSession.value = response.data
    duration.value = response.data.duration
    summaries.value = []
    currentTime.value = 0
    
    // Auto-start SurgR1 continuous processing when video is loaded
    startSurgR1Continuous(response.data.session_id)
  } catch (error) {
    console.error('Load failed:', error)
    alert('Load failed: ' + (error.response?.data?.detail || error.message))
  }
}

const loadExistingSummaries = async (sessionId) => {
  try {
    const response = await axios.get(`/api/analysis/summaries/${sessionId}`)
    summaries.value = response.data  } catch (error) {
    console.error('Failed to load summaries:', error)
  }
}

// Start continuous SurgR1 processing in background
const startSurgR1Continuous = async (sessionId) => {
  // Create a new abort controller for this session
  createSessionAbortController()
  
  // Wait for status check to complete if still checking
  if (surgr1Status.value.checking) {
    console.log('Waiting for SurgR1 status check to complete...')
    // Wait up to 5 seconds for status check
    for (let i = 0; i < 50 && surgr1Status.value.checking; i++) {
      await new Promise(resolve => setTimeout(resolve, 100))
    }
  }
  
  // Try to start anyway - the backend will handle if service is unavailable
  // We just log a warning if status shows unavailable
  if (!surgr1Status.value.available) {
    console.warn('SurgR1 service may not be available, attempting to start anyway...')
  }
  
  // Initialize the analysis queue with callbacks
  analysisQueue.onResult = (result) => {
    console.log('[AnalysisQueue] Frame result:', result.frame_idx)
    // Results are auto-saved by backend, could update UI here if needed
  }
  analysisQueue.onBatchComplete = ({ batchId, success, frameCount }) => {
    if (success) {
      surgr1ProcessingStatus.value.framesAnalyzed += frameCount
    }
  }
  analysisQueue.init(sessionId)
  
  try {
    // Record time before request for synchronization
    const requestStartTime = Date.now()
    
    const response = await axios.post(`/api/analysis/start-surgr1-continuous/${sessionId}`, null, {
      signal: getSessionSignal()
    })
    console.log('SurgR1 continuous processing:', response.data)
    
    if (response.data.status === 'started' || response.data.status === 'running') {
      surgr1ProcessingStatus.value.running = true
      
      // Synchronize stream start time with backend for accurate timestamps
      // The backend returns server_time which is when it started processing
      // We adjust our streamStartTime to match, accounting for network latency
      if (response.data.server_time && mode.value === 'stream') {
        const networkLatency = (Date.now() - requestStartTime) / 2  // Estimate one-way latency
        const serverTimeMs = response.data.server_time * 1000  // Convert to milliseconds
        // Reset streamStartTime to match backend's start time
        streamStartTime.value = serverTimeMs + networkLatency
        currentTime.value = 0  // Reset current time
        console.log(`[TimeSync] Synchronized with backend. streamStartTime=${streamStartTime.value}, latency=${networkLatency}ms`)
      }
      
      // Start polling for status updates
      startSurgR1StatusPolling(sessionId)
    }
  } catch (error) {
    if (axios.isCancel(error) || error.name === 'AbortError') {
      console.log('SurgR1 start request was cancelled')
      return
    }
    console.error('Failed to start SurgR1 continuous processing:', error)
    // Don't block - SurgR1 is optional
  }
}

// Stop continuous SurgR1 processing
const stopSurgR1Continuous = async (sessionId) => {
  if (!sessionId) return
  
  // Clear the analysis queue first
  analysisQueue.clear()
  
  try {
    await axios.post(`/api/analysis/stop-surgr1-continuous/${sessionId}`)
    console.log('SurgR1 continuous processing stopped')
    surgr1ProcessingStatus.value.running = false
    surgr1ProcessingStatus.value.framesAnalyzed = 0
    stopSurgR1StatusPolling()
  } catch (error) {
    console.error('Failed to stop SurgR1 continuous processing:', error)
  }
}

// Poll SurgR1 continuous status
let surgr1StatusInterval = null

const startSurgR1StatusPolling = (sessionId) => {
  stopSurgR1StatusPolling()
  
  surgr1StatusInterval = setInterval(async () => {
    // Check if session is still active (abort controller exists)
    const signal = getSessionSignal()
    if (!signal || signal.aborted) {
      console.log('[SurgR1StatusPolling] Session aborted, stopping polling')
      stopSurgR1StatusPolling()
      return
    }
    
    try {
      const response = await axios.get(`/api/analysis/surgr1-continuous-status/${sessionId}`, {
        signal: signal
      })
      surgr1ProcessingStatus.value.running = response.data.is_running
      surgr1ProcessingStatus.value.framesAnalyzed = response.data.frames_analyzed
    } catch (error) {
      if (axios.isCancel(error) || error.name === 'AbortError') {
        console.log('[SurgR1StatusPolling] Request cancelled')
        stopSurgR1StatusPolling()
        return
      }
      // Ignore other errors
    }
  }, 3000)  // Check every 3 seconds
}

const stopSurgR1StatusPolling = () => {
  if (surgr1StatusInterval) {
    clearInterval(surgr1StatusInterval)
    surgr1StatusInterval = null
  }
}

const startAnalysis = async () => {
  if (!currentSession.value) return
  
  // Check if GLM is available
  if (!glmStatus.value.available) {
    alert('GLM 服务不可用，请确保 GLM 服务已启动')
    return
  }
  
  isProcessing.value = true
  
  try {
    // Use GLM-only summarization (SurgR1 is already running in background)
    const response = await axios.post('/api/analysis/start-glm-summarization', {
      session_id: currentSession.value.session_id,
      use_chinese: true,  // Use Chinese for summaries
      use_glm_multimodal: true  // Multimodal mode: send images to GLM for verification
    }, {
      signal: getSessionSignal()
    })
    
    console.log('GLM summarization started:', response.data)
    
    // Start SSE for summaries
    analysisEventSource = new EventSource(
      `/api/analysis/stream-summaries/${currentSession.value.session_id}`
    )
    
    analysisEventSource.onmessage = (event) => {
      const data = JSON.parse(event.data)
      
      if (data.status === 'completed' || data.status === 'cancelled') {
        isProcessing.value = false
        analysisEventSource.close()
        analysisEventSource = null
        return
      }
      
      const existingIndex = summaries.value.findIndex(
        s => s.window_id === data.window_id
      )
      
      // Track if this is a new window (not just an update)
      const isNewWindow = existingIndex < 0
      
      if (existingIndex >= 0) {
        summaries.value[existingIndex] = data
      } else {
        summaries.value.push(data)
        summaries.value.sort((a, b) => a.start_time - b.start_time)
      }
      
      // Highlight new window briefly - only for NEW windows, not updates
      // Also skip if user is in loop playback mode (userSelectedWindow is true)
      // Also skip if we're in loop playback mode
      if (isNewWindow && !userSelectedWindow.value && !loopWindow.value) {
        highlightedWindowId.value = data.window_id
        setTimeout(() => {
          if (highlightedWindowId.value === data.window_id && !userSelectedWindow.value) {
            highlightedWindowId.value = -1
          }
        }, 2000)
      }
    }
    
    analysisEventSource.onerror = (err) => {
      console.error('SSE error:', err)
      isProcessing.value = false
      if (analysisEventSource) {
        analysisEventSource.close()
        analysisEventSource = null
      }
    }
    
  } catch (error) {
    if (axios.isCancel(error) || error.name === 'AbortError') {
      console.log('Analysis start request was cancelled')
      isProcessing.value = false
      return
    }
    console.error('Analysis failed:', error)
    isProcessing.value = false
    
    // Show error message
    const errorMsg = error.response?.data?.detail || error.message || 'Unknown error'
    alert(`分析启动失败: ${errorMsg}`)
  }
}

const stopAnalysis = async () => {
  if (!currentSession.value) return
  
  try {
    // Request backend to stop analysis
    await axios.post(`/api/analysis/stop-analysis/${currentSession.value.session_id}`)
    
    // Close EventSource immediately
    if (analysisEventSource) {
      analysisEventSource.close()
      analysisEventSource = null
    }
    
    isProcessing.value = false
  } catch (error) {
    console.error('Stop analysis failed:', error)
    // Still try to close EventSource and update state
    if (analysisEventSource) {
      analysisEventSource.close()
      analysisEventSource = null
    }
    isProcessing.value = false
  }
}

// Stream timer for live video elapsed time
const startStreamTimer = () => {
  if (mode.value !== 'stream') return
  
  // Clear any existing timer
  stopStreamTimer()
  
  // Reset stream ended state
  streamEnded.value = false
  streamWasActive.value = false
  
  streamTimerInterval = setInterval(() => {
    // Don't update time if in loop playback mode (VideoPlayer handles time updates)
    if (loopWindow.value) return
    
    if (!isPlaying.value || !streamStartTime.value || streamEnded.value) return
    
    // Calculate elapsed time since stream started
    const elapsed = (Date.now() - streamStartTime.value) / 1000
    
    // Ensure elapsed time is never negative (can happen if server/client clocks are out of sync)
    const safeElapsed = Math.max(0, elapsed)
    currentTime.value = safeElapsed
    
    // Also update duration for display purposes
    duration.value = safeElapsed
  }, 100)  // Update every 100ms for smooth display
  
  // Start checking if stream has ended (check every 2 seconds)
  startStreamEndCheck()
}

// Check if the video stream has ended by polling the stream server
const startStreamEndCheck = () => {
  if (streamEndCheckInterval) {
    clearInterval(streamEndCheckInterval)
  }
  
  // Reset the streamWasActive flag for new stream
  streamWasActive.value = false
  
  // Backup detection: track last time currentTime updated
  let lastKnownTime = currentTime.value
  let staleTimeCounter = 0
  const STALE_TIME_THRESHOLD = 5  // 5 consecutive checks (10 seconds) without time change
  
  streamEndCheckInterval = setInterval(async () => {
    if (!currentSession.value || streamEnded.value) return
    
    // Skip stream end check if in loop playback mode (uses cached frames)
    // But still track stale time for backup detection
    const inLoopMode = loopWindow.value !== null
    
    try {
      // Get the stream URL from session
      const streamUrl = currentSession.value.video_path
      if (!streamUrl || !streamUrl.startsWith('http')) return
      
      // Extract base URL (e.g., http://localhost:9001 from http://localhost:9001/stream)
      const urlObj = new URL(streamUrl)
      const baseUrl = `${urlObj.protocol}//${urlObj.host}`
      
      // Check if this is a direct external stream (stream_simulator) vs proxy stream
      // Proxy streams go through our backend (localhost:5174 or localhost:8001) and don't have /info endpoint
      // Direct streams (e.g., localhost:9001) from stream_simulator have /info endpoint
      const isProxyStream = urlObj.pathname.includes('/api/video/') || 
                            urlObj.pathname.includes('/mjpeg-proxy/') ||
                            urlObj.host.includes(':5174') ||  // Vite dev server
                            urlObj.host.includes(':8001')     // Backend server
      
      // Only check /info for direct external streams (stream_simulator)
      if (!isProxyStream) {
        const response = await fetch(`${baseUrl}/info`, { 
          signal: AbortSignal.timeout(2000) 
        })
        
        if (response.ok) {
          const info = await response.json()
          
          // Track if stream becomes active (at least 1 connection)
          if (info.active_streams > 0) {
            streamWasActive.value = true
          }
          
          // Only consider video ended if:
          // 1. video_ended flag is set
          // 2. We've been playing for at least 20 seconds (allow time for first window analysis)
          // 3. Stream was active at some point (to avoid false positives from stale state)
          // 4. active_streams is 0 (all connections closed, indicating real end)
          const isReallyEnded = info.video_ended && 
            currentTime.value > 20 && 
            streamWasActive.value && 
            info.active_streams === 0
          
          if (isReallyEnded) {
            console.log('[Stream] Video stream has ended (video_ended flag, active_streams=0)')
            handleStreamEnded()
            return
          }
        }
        // Note: 404 or other errors are silently ignored - the endpoint may not exist
      }
    } catch (e) {
      // Silently ignore all errors (timeout, network, 404, etc.)
      // This is just a status check, not critical for functionality
    }
    
    // Backup detection: check if time is stale (not in loop mode)
    // In loop mode, time updates are from cached frames, not stream
    // Only trigger after 30 seconds to avoid false positives
    if (!inLoopMode && isPlaying.value && currentTime.value > 30) {
      if (Math.abs(currentTime.value - lastKnownTime) < 0.1) {
        staleTimeCounter++
        if (staleTimeCounter >= STALE_TIME_THRESHOLD) {
          console.log('[Stream] Time stale for too long, assuming stream ended')
          handleStreamEnded()
          return
        }
      } else {
        staleTimeCounter = 0
        lastKnownTime = currentTime.value
      }
    }
  }, 2000)  // Check every 2 seconds
}

// Handle when stream ends
const handleStreamEnded = () => {
  // Prevent multiple calls
  if (streamEnded.value) return
  
  streamEnded.value = true
  isPlaying.value = false
  
  // Stop the timer
  stopStreamTimer()
  
  // Stop the stream end check interval
  if (streamEndCheckInterval) {
    clearInterval(streamEndCheckInterval)
    streamEndCheckInterval = null
  }
  
  // Exit loop playback mode if active
  if (loopWindow.value) {
    console.log('[Stream] Exiting loop mode due to stream end')
    loopWindow.value = null
    userSelectedWindow.value = false
    highlightedWindowId.value = -1
  }
  
  // Stop SurgR1 continuous processing
  if (currentSession.value) {
    stopSurgR1Continuous(currentSession.value.session_id)
  }
  
  // Stop GLM analysis
  if (isProcessing.value) {
    stopAnalysis()
  }
  
  console.log(`[Stream] Stopped at ${currentTime.value.toFixed(1)}s - Analysis stopped`)
}

const stopStreamTimer = () => {
  if (streamTimerInterval) {
    clearInterval(streamTimerInterval)
    streamTimerInterval = null
  }
}

// Variables to track pause time
let pausedAt = null

const pauseStreamTimer = () => {
  pausedAt = Date.now()
  stopStreamTimer()
}

const resumeStreamTimer = () => {
  if (pausedAt && streamStartTime.value) {
    // Adjust start time to account for pause duration
    const pauseDuration = Date.now() - pausedAt
    streamStartTime.value += pauseDuration
    pausedAt = null
  }
  startStreamTimer()
}

const stopStreamPolling = () => {
  stopStreamTimer()
  if (streamPollingInterval) {
    clearInterval(streamPollingInterval)
    streamPollingInterval = null
  }
  if (streamEndCheckInterval) {
    clearInterval(streamEndCheckInterval)
    streamEndCheckInterval = null
  }
}

const handleTTS = async (summary) => {
  if (!currentSession.value || !summary) return
  
  try {
    const response = await axios.post(
      `/api/analysis/tts/summary/${currentSession.value.session_id}/${summary.window_id}`
    )
    
    if (response.data.success && response.data.audio_base64) {
      const audio = new Audio(`data:audio/mp3;base64,${response.data.audio_base64}`)
      audio.play()
    }
  } catch (error) {
    console.error('TTS failed:', error)
  }
}

const handleSAM2 = async (timestamp) => {
  if (!currentSession.value) return
  
  try {
    const response = await axios.post('/api/analysis/sam2/segment', {
      session_id: currentSession.value.session_id,
      timestamp: timestamp,
      auto_detect: true
    })
    
    if (response.data.overlay_base64) {
      console.log('SAM2 result:', response.data)
    }
  } catch (error) {
    console.error('SAM2 failed:', error)
  }
}

// Voice chat handlers
const handleVoiceMessage = (message) => {
  console.log('Voice message:', message)
}

const handleVoiceTranscript = (transcript) => {
  console.log('Voice transcript:', transcript)
}

// SAM3 toggle handler
const handleToggleSam3 = () => {
  if (!sam3Status.value.available) {
    console.warn('SAM3 service not available')
    return
  }
  showSam3.value = !showSam3.value
  console.log(`SAM3 view ${showSam3.value ? 'enabled' : 'disabled'}`)
}

// ===========================================================================
// New handlers for enhanced features
// ===========================================================================

// Handle window hover from progress bar
const handleWindowHover = (windowId) => {
  if (windowId >= 0 && summaries.value.find(s => s.window_id === windowId)) {
    highlightedWindowId.value = windowId
    // Mark as user interaction but don't set full selection (for hover only)
  } else if (!userSelectedWindow.value) {
    // Only reset if user hasn't selected a window
    highlightedWindowId.value = -1
  }
}

// Handle seek to specific window - enables loop playback mode
const handleSeekToWindow = (windowId) => {
  // Find the summary for this window to get time bounds
  const summary = summaries.value.find(s => s.window_id === windowId)
  
  if (summary) {
    // Set up loop playback for this window
    loopWindow.value = {
      window_id: windowId,
      start_time: summary.start_time,
      end_time: summary.end_time
    }
    console.log(`[Loop] Enabled loop playback for window ${windowId}: ${summary.start_time}s - ${summary.end_time}s`)    
    // Seek to the start of this window
    isLoopSeek = true
    handleSeek(summary.start_time)
    setTimeout(() => { isLoopSeek = false }, 100)
  }
  
  highlightedWindowId.value = windowId
  userSelectedWindow.value = true
}

// Handle drag seek with frame analysis popup
const handleDragSeek = async (time, isDragStart) => {
  if (isDragStart) {
    isDragging.value = true
    frameAnalysisPopup.value.visible = true
    frameAnalysisPopup.value.isLoading = true
    frameAnalysisPopup.value.data = { timestamp: time }
  }
  
  // Update position (centered above progress bar)
  const progressBar = document.querySelector('.progress-bar')
  if (progressBar) {
    const rect = progressBar.getBoundingClientRect()
    const percent = time / duration.value
    frameAnalysisPopup.value.position = {
      x: rect.left + rect.width * percent,
      y: rect.top
    }
  }
  
  // Update timestamp in popup
  if (frameAnalysisPopup.value.data) {
    frameAnalysisPopup.value.data.timestamp = time
  }
  
  // Debounced fetch for frame analysis
  if (dragDebounceTimer.value) {
    clearTimeout(dragDebounceTimer.value)
  }
  
  dragDebounceTimer.value = setTimeout(async () => {
    await fetchFrameAnalysis(time)
  }, 200)  // 200ms debounce
}

// Fetch frame analysis from backend (with saved frame image and window summary)
const fetchFrameAnalysis = async (timestamp) => {
  if (!currentSession.value) return
  
  const signal = getSessionSignal()
  if (!signal || signal.aborted) return
  
  try {
    frameAnalysisPopup.value.isLoading = true
    
    // Fetch both frame data and window summary in parallel
    const [frameResponse, summaryResponse] = await Promise.all([
      axios.get(
        `/api/analysis/frame-at-timestamp/${currentSession.value.session_id}`,
        { params: { timestamp, tolerance: 1.0 }, signal }
      ).catch(e => ({ data: { success: false } })),
      axios.get(
        `/api/analysis/window-summary-at-timestamp/${currentSession.value.session_id}`,
        { params: { timestamp }, signal }
      ).catch(e => ({ data: { success: false } }))
    ])
    
    const frameData = frameResponse.data
    const summaryData = summaryResponse.data
    
    frameAnalysisPopup.value.data = {
      timestamp: frameData.actual_timestamp || timestamp,
      // Frame image (base64)
      image_base64: frameData.image_base64 || null,
      has_saved_frame: frameData.has_saved_frame || false,
      // Frame analysis
      surgical_phase: frameData.analysis?.surgical_phase || '',
      surgical_action: frameData.analysis?.surgical_action || '',
      tool_localization: frameData.analysis?.tool_localization || '',
      // Window summary
      window_id: summaryData.window_id,
      window_summary: summaryData.summary || null,
      window_start: summaryData.window_start,
      window_end: summaryData.window_end
    }
    
    // If we got a window summary, also highlight it in the summary panel
    if (summaryData.success && summaryData.window_id !== null) {
      highlightWindow(summaryData.window_id)
    }
  } catch (error) {
    if (axios.isCancel(error) || error.name === 'AbortError') {
      console.log('Frame analysis request was cancelled')
      return
    }
    console.error('Failed to fetch frame analysis:', error)
    frameAnalysisPopup.value.data = { timestamp }
  } finally {
    frameAnalysisPopup.value.isLoading = false
  }
}

// Close frame analysis popup
const closeFrameAnalysisPopup = () => {
  frameAnalysisPopup.value.visible = false
  if (popupAutoHideTimer) {
    clearTimeout(popupAutoHideTimer)
    popupAutoHideTimer = null
  }
}

// Handle click on video section (close popup when clicking video area, exit loop mode)
const handleVideoSectionClick = (event) => {
  // Don't handle if clicking on progress bar or controls
  const target = event.target
  const isProgressBar = target.closest('.progress-bar') || target.closest('.progress-container')
  const isControlBtn = target.closest('.control-btn') || target.closest('.controls-row')
  const isSummaryPanel = target.closest('.summary-section') || target.closest('.summary-panel')
  
  if (!isProgressBar && !isControlBtn && !isSummaryPanel) {
    // Close frame analysis popup if visible
    if (frameAnalysisPopup.value.visible) {
      closeFrameAnalysisPopup()
    }
    
    // Exit loop mode when clicking on video area
    if (loopWindow.value) {
      exitLoopMode()
    }
  }
}

// Auto-hide timer for popup
let popupAutoHideTimer = null

// Auto-hide popup after data is loaded (3 seconds)
watch(() => frameAnalysisPopup.value.isLoading, (isLoading) => {
  if (!isLoading && frameAnalysisPopup.value.visible) {
    // Clear any existing timer
    if (popupAutoHideTimer) {
      clearTimeout(popupAutoHideTimer)
    }
    // Set new auto-hide timer (5 seconds after data loads)
    popupAutoHideTimer = setTimeout(() => {
      if (frameAnalysisPopup.value.visible && !isDragging.value) {
        frameAnalysisPopup.value.visible = false
      }
    }, 5000)
  }
})

// Hide popup when not dragging (with shorter delay)
watch(isDragging, (newVal) => {
  if (!newVal) {
    // Clear auto-hide timer when dragging stops
    if (popupAutoHideTimer) {
      clearTimeout(popupAutoHideTimer)
    }
    // Auto-hide after 3 seconds when dragging ends
    popupAutoHideTimer = setTimeout(() => {
      if (!isDragging.value) {
        frameAnalysisPopup.value.visible = false
      }
    }, 3000)
  }
})

// Update highlighted window when seeking
watch(currentTime, (newTime) => {
  const windowId = Math.floor(newTime / windowDuration.value)
  if (summaries.value.find(s => s.window_id === windowId)) {
    // Don't auto-highlight during playback, only during seek
    if (!isPlaying.value && highlightedWindowId.value === -1) {
      highlightedWindowId.value = windowId
      setTimeout(() => {
        highlightedWindowId.value = -1
      }, 1000)
    }
  }
})

// Check all service statuses
const checkAnalysisServices = async () => {
  // #region agent log
  const startTime = Date.now();
  fetch('http://localhost:7244/ingest/4c43c557-65ee-4714-a50a-64ce4a04fdbb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'App.vue:checkAnalysisServices',message:'Service check START',data:{},timestamp:Date.now(),sessionId:'debug-session',hypothesisId:'D'})}).catch(()=>{});
  // #endregion
  
  // Check all services in parallel with individual timing
  const checks = [
    // SurgR1
    axios.get('/api/analysis/surgr1/status', { timeout: 5000 })
      .then(res => { 
        // #region agent log
        fetch('http://localhost:7244/ingest/4c43c557-65ee-4714-a50a-64ce4a04fdbb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'App.vue:surgr1Status',message:'SurgR1 check done',data:{elapsed:Date.now()-startTime,available:res.data.available},timestamp:Date.now(),sessionId:'debug-session',hypothesisId:'D'})}).catch(()=>{});
        // #endregion
        surgr1Status.value = { available: res.data.available, checking: false } 
      })
      .catch((e) => { 
        // #region agent log
        fetch('http://localhost:7244/ingest/4c43c557-65ee-4714-a50a-64ce4a04fdbb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'App.vue:surgr1Status',message:'SurgR1 check FAILED',data:{elapsed:Date.now()-startTime,error:e.message},timestamp:Date.now(),sessionId:'debug-session',hypothesisId:'D'})}).catch(()=>{});
        // #endregion
        surgr1Status.value = { available: false, checking: false } 
      }),
    
    // GLM
    axios.get('/api/analysis/glm/status', { timeout: 5000 })
      .then(res => { glmStatus.value = { available: res.data.available, checking: false } })
      .catch(() => { glmStatus.value = { available: false, checking: false } }),
    
    // SAM3
    axios.get('/api/analysis/sam3/status', { timeout: 5000 })
      .then(res => { sam3Status.value = { available: res.data.available, checking: false } })
      .catch(() => { sam3Status.value = { available: false, checking: false } }),
    
    // ASR
    axios.get('/api/voice/asr/status', { timeout: 5000 })
      .then(res => { asrStatus.value = { available: res.data.available, checking: false } })
      .catch(() => { asrStatus.value = { available: false, checking: false } }),
    
    // TTS - This was causing 60s timeout!
    axios.get('/api/voice/tts/status', { timeout: 5000 })
      .then(res => { 
        // #region agent log
        fetch('http://localhost:7244/ingest/4c43c557-65ee-4714-a50a-64ce4a04fdbb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'App.vue:ttsStatus',message:'TTS check done',data:{elapsed:Date.now()-startTime,available:res.data.available},timestamp:Date.now(),sessionId:'debug-session',hypothesisId:'D'})}).catch(()=>{});
        // #endregion
        ttsStatus.value = { available: res.data.available, checking: false } 
      })
      .catch((e) => { 
        // #region agent log
        fetch('http://localhost:7244/ingest/4c43c557-65ee-4714-a50a-64ce4a04fdbb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'App.vue:ttsStatus',message:'TTS check FAILED/TIMEOUT',data:{elapsed:Date.now()-startTime,error:e.message},timestamp:Date.now(),sessionId:'debug-session',hypothesisId:'D'})}).catch(()=>{});
        // #endregion
        ttsStatus.value = { available: false, checking: false } 
      })
  ]
  
  await Promise.allSettled(checks)
  
  // #region agent log
  fetch('http://localhost:7244/ingest/4c43c557-65ee-4714-a50a-64ce4a04fdbb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'App.vue:checkAnalysisServices',message:'Service check END',data:{totalElapsed:Date.now()-startTime},timestamp:Date.now(),sessionId:'debug-session',hypothesisId:'D'})}).catch(()=>{});
  // #endregion
}

// Status check interval
let analysisStatusInterval = null

// Restart the analysis status interval (30s service check)
// Call this when entering the main view
const restartAnalysisStatusInterval = () => {
  if (analysisStatusInterval) {
    clearInterval(analysisStatusInterval)
  }
  // Check immediately and then every 30 seconds
  checkAnalysisServices()
  analysisStatusInterval = setInterval(checkAnalysisServices, 30000)
}

// Handle page close/refresh - use sendBeacon for reliable cleanup
const handleBeforeUnload = () => {
  if (currentSession.value) {
    // sendBeacon is reliable even when page is closing
    navigator.sendBeacon(
      `/api/analysis/stop-surgr1-continuous/${currentSession.value.session_id}`,
      ''
    )
  }
}

onMounted(async () => {
  // #region agent log
  fetch('http://localhost:7244/ingest/4c43c557-65ee-4714-a50a-64ce4a04fdbb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'App.vue:onMounted',message:'App mounted START',data:{},timestamp:Date.now(),sessionId:'debug-session',hypothesisId:'A'})}).catch(()=>{});
  // #endregion
  
  // 首先获取配置
  await fetchConfig()
  
  // #region agent log
  fetch('http://localhost:7244/ingest/4c43c557-65ee-4714-a50a-64ce4a04fdbb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'App.vue:onMounted',message:'Config fetched, starting service check',data:{},timestamp:Date.now(),sessionId:'debug-session',hypothesisId:'A'})}).catch(()=>{});
  // #endregion
  
  checkAnalysisServices()
  // Refresh status every 30 seconds
  analysisStatusInterval = setInterval(checkAnalysisServices, 30000)
  
  // Add beforeunload handler for reliable cleanup
  window.addEventListener('beforeunload', handleBeforeUnload)
  
  // #region agent log
  fetch('http://localhost:7244/ingest/4c43c557-65ee-4714-a50a-64ce4a04fdbb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'App.vue:onMounted',message:'App mounted END',data:{},timestamp:Date.now(),sessionId:'debug-session',hypothesisId:'A'})}).catch(()=>{});
  // #endregion
})

onUnmounted(() => {
  // Abort all pending session requests
  abortAllSessionRequests()
  
  if (analysisStatusInterval) {
    clearInterval(analysisStatusInterval)
    analysisStatusInterval = null
  }
  stopStreamPolling()
  stopSurgR1StatusPolling()
  
  // Clear analysis queue to cancel pending requests
  analysisQueue.clear()
  
  // Stop SurgR1 continuous processing when leaving
  if (currentSession.value) {
    // Use sendBeacon for reliable cleanup
    navigator.sendBeacon(
      `/api/analysis/stop-surgr1-continuous/${currentSession.value.session_id}`,
      ''
    )
  }
  
  if (dragDebounceTimer.value) {
    clearTimeout(dragDebounceTimer.value)
  }
  // Clean up analysis EventSource
  if (analysisEventSource) {
    analysisEventSource.close()
    analysisEventSource = null
  }
  
  // Remove beforeunload handler
  window.removeEventListener('beforeunload', handleBeforeUnload)
})
</script>

<style scoped>
.stream-setup {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, var(--bg-primary) 0%, var(--bg-secondary) 100%);
}

.header-center {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.mode-badge {
  font-size: 0.8rem;
  padding: 0.35rem 0.75rem;
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
}

.mode-badge.stream {
  background: rgba(0, 212, 170, 0.15);
  color: var(--accent-primary);
}

.mode-badge.local {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.session-id-badge {
  font-size: 0.75rem;
  padding: 0.25rem 0.5rem;
  border-radius: var(--radius-sm);
  background: rgba(255, 193, 7, 0.15);
  color: #ffc107;
  font-family: 'Courier New', monospace;
  letter-spacing: 0.5px;
}

.session-name {
  font-size: 0.9rem;
  color: var(--text-secondary);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logo {
  cursor: pointer;
}

/* Toast Message */
.toast-message {
  position: fixed;
  bottom: 100px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(255, 193, 7, 0.95);
  color: #1a1a1a;
  padding: 0.75rem 1.5rem;
  border-radius: var(--radius-md, 8px);
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 500;
  font-size: 0.9rem;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
  z-index: 9999;
}

.toast-icon {
  font-size: 1.1rem;
}

.toast-text {
  max-width: 400px;
}

/* Toast animation */
.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(20px);
}
</style>
