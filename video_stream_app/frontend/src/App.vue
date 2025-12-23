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
          <div class="logo-icon">🎬</div>
          <div class="logo-text">Video<span>Analyzer</span></div>
        </div>
        
        <div class="header-center">
          <span class="mode-badge" :class="mode">
            {{ mode === 'local' ? '📁 本地视频' : '📡 实时视频流' }}
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
            @timeupdate="handleTimeUpdate"
            @play="handlePlay"
            @pause="handlePause"
            @seek="handleSeek"
            @upload="handleUpload"
            @load="handleLoad"
            @sam3TimeUpdate="handleSam3TimeUpdate"
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
            @tts="handleTTS"
            @sam2="handleSAM2"
            @seek="handleSeek"
            @seekToWindow="handleSeekToWindow"
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
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
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
const streamStartTime = ref(null)  // When stream started (for elapsed time)

// Window duration (5 seconds)
const WINDOW_DURATION = 5

// Computed: current summary based on time
const currentSummary = computed(() => {
  if (!summaries.value.length) return null
  
  const windowId = Math.floor(currentTime.value / WINDOW_DURATION)
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
  }
}

const handleResumeSession = (session) => {
  currentSession.value = session
  duration.value = session.duration
  mode.value = 'local'
  currentView.value = 'main'
  loadExistingSummaries(session.session_id)
}

const handleStreamConnect = ({ session, autoAnalyze }) => {
  currentSession.value = session
  duration.value = 0  // Live stream has no fixed duration
  currentView.value = 'main'
  isPlaying.value = true
  currentTime.value = 0
  streamStartTime.value = Date.now()  // Track when stream started
  
  // Auto-start SurgR1 continuous processing when stream connects
  startSurgR1Continuous(session.session_id)
  
  if (autoAnalyze) {
    startAnalysis()
  }
  
  // Start timer for live stream elapsed time
  startStreamTimer()
}

const goHome = () => {
  stopStreamPolling()
  stopSurgR1StatusPolling()
  // Stop SurgR1 continuous processing
  if (currentSession.value) {
    stopSurgR1Continuous(currentSession.value.session_id)
  }
  // Close analysis EventSource if running
  if (analysisEventSource) {
    analysisEventSource.close()
    analysisEventSource = null
  }
  // Reset SAM3 view
  showSam3.value = false
  currentView.value = 'select'
  currentSession.value = null
  summaries.value = []
  isProcessing.value = false
  isPlaying.value = false
  currentTime.value = 0
}

// Video handlers
const handleTimeUpdate = (time) => {
  currentTime.value = time
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
  if (currentSession.value) {
    axios.post(`/api/video/control/${currentSession.value.session_id}`, {
      action: 'seek',
      position: time
    })
  }
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
    summaries.value = response.data
  } catch (error) {
    console.error('Failed to load summaries:', error)
  }
}

// Start continuous SurgR1 processing in background
const startSurgR1Continuous = async (sessionId) => {
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
  
  try {
    const response = await axios.post(`/api/analysis/start-surgr1-continuous/${sessionId}`)
    console.log('SurgR1 continuous processing:', response.data)
    
    if (response.data.status === 'started' || response.data.status === 'running') {
      surgr1ProcessingStatus.value.running = true
      // Start polling for status updates
      startSurgR1StatusPolling(sessionId)
    }
  } catch (error) {
    console.error('Failed to start SurgR1 continuous processing:', error)
    // Don't block - SurgR1 is optional
  }
}

// Stop continuous SurgR1 processing
const stopSurgR1Continuous = async (sessionId) => {
  if (!sessionId) return
  
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
    try {
      const response = await axios.get(`/api/analysis/surgr1-continuous-status/${sessionId}`)
      surgr1ProcessingStatus.value.running = response.data.is_running
      surgr1ProcessingStatus.value.framesAnalyzed = response.data.frames_analyzed
    } catch (error) {
      // Ignore errors
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
      use_glm_multimodal: false  // Text-only mode for faster processing
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
      
      if (existingIndex >= 0) {
        summaries.value[existingIndex] = data
      } else {
        summaries.value.push(data)
        summaries.value.sort((a, b) => a.start_time - b.start_time)
      }
      
      // Highlight new window briefly (only if user hasn't manually selected a window)
      if (!userSelectedWindow.value) {
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
  
  streamTimerInterval = setInterval(() => {
    if (!isPlaying.value || !streamStartTime.value) return
    
    // Calculate elapsed time since stream started
    const elapsed = (Date.now() - streamStartTime.value) / 1000
    currentTime.value = elapsed
    
    // Also update duration for display purposes
    duration.value = elapsed
  }, 100)  // Update every 100ms for smooth display
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

// Handle seek to specific window
const handleSeekToWindow = (windowId) => {
  highlightedWindowId.value = windowId
  userSelectedWindow.value = true
  
  // Auto-clear user selection after 5 seconds to allow auto-highlight to resume
  setTimeout(() => {
    if (highlightedWindowId.value === windowId) {
      userSelectedWindow.value = false
      highlightedWindowId.value = -1
    }
  }, 5000)
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
  
  try {
    frameAnalysisPopup.value.isLoading = true
    
    // Fetch both frame data and window summary in parallel
    const [frameResponse, summaryResponse] = await Promise.all([
      axios.get(
        `/api/analysis/frame-at-timestamp/${currentSession.value.session_id}`,
        { params: { timestamp, tolerance: 1.0 } }
      ).catch(e => ({ data: { success: false } })),
      axios.get(
        `/api/analysis/window-summary-at-timestamp/${currentSession.value.session_id}`,
        { params: { timestamp } }
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

// Handle click on video section (close popup when clicking video area)
const handleVideoSectionClick = (event) => {
  // Don't close if clicking on progress bar or controls
  const target = event.target
  const isProgressBar = target.closest('.progress-bar') || target.closest('.progress-container')
  const isControlBtn = target.closest('.control-btn') || target.closest('.controls-row')
  
  if (!isProgressBar && !isControlBtn && frameAnalysisPopup.value.visible) {
    closeFrameAnalysisPopup()
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
  const windowId = Math.floor(newTime / WINDOW_DURATION)
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
  // Check all services in parallel
  const checks = [
    // SurgR1
    axios.get('/api/analysis/surgr1/status')
      .then(res => { surgr1Status.value = { available: res.data.available, checking: false } })
      .catch(() => { surgr1Status.value = { available: false, checking: false } }),
    
    // GLM
    axios.get('/api/analysis/glm/status')
      .then(res => { glmStatus.value = { available: res.data.available, checking: false } })
      .catch(() => { glmStatus.value = { available: false, checking: false } }),
    
    // SAM3
    axios.get('/api/analysis/sam3/status')
      .then(res => { sam3Status.value = { available: res.data.available, checking: false } })
      .catch(() => { sam3Status.value = { available: false, checking: false } }),
    
    // ASR
    axios.get('/api/voice/asr/status')
      .then(res => { asrStatus.value = { available: res.data.available, checking: false } })
      .catch(() => { asrStatus.value = { available: false, checking: false } }),
    
    // TTS
    axios.get('/api/voice/tts/status')
      .then(res => { ttsStatus.value = { available: res.data.available, checking: false } })
      .catch(() => { ttsStatus.value = { available: false, checking: false } })
  ]
  
  await Promise.allSettled(checks)
}

// Status check interval
let analysisStatusInterval = null

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

onMounted(() => {
  checkAnalysisServices()
  // Refresh status every 30 seconds
  analysisStatusInterval = setInterval(checkAnalysisServices, 30000)
  
  // Add beforeunload handler for reliable cleanup
  window.addEventListener('beforeunload', handleBeforeUnload)
})

onUnmounted(() => {
  if (analysisStatusInterval) {
    clearInterval(analysisStatusInterval)
  }
  stopStreamPolling()
  stopSurgR1StatusPolling()
  
  // Stop SurgR1 continuous processing when leaving
  if (currentSession.value) {
    stopSurgR1Continuous(currentSession.value.session_id)
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
</style>
