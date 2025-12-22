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
        <section class="video-section">
          <VideoPlayer
            :session="currentSession"
            :currentTime="currentTime"
            :isPlaying="isPlaying"
            :isPaused="!isPlaying && mode === 'stream'"
            :mode="mode"
            @timeupdate="handleTimeUpdate"
            @play="handlePlay"
            @pause="handlePause"
            @seek="handleSeek"
            @upload="handleUpload"
            @load="handleLoad"
          />
          <ControlBar
            :currentTime="currentTime"
            :duration="duration"
            :isPlaying="isPlaying"
            :volume="volume"
            :mode="mode"
            :isLive="mode === 'stream'"
            :analyzedWindows="analyzedWindows"
            :highlightedWindowId="highlightedWindowId"
            :isAnalyzing="isProcessing"
            :surgr1Status="surgr1Status"
            :glmStatus="glmStatus"
            :sam3Status="sam3Status"
            :asrStatus="asrStatus"
            :ttsStatus="ttsStatus"
            @play="handlePlay"
            @pause="handlePause"
            @seek="handleSeek"
            @volume="handleVolumeChange"
            @analyze="startAnalysis"
            @stopAnalyze="stopAnalysis"
            @seekToWindow="handleSeekToWindow"
            @hoverWindow="handleWindowHover"
            @dragSeek="handleDragSeek"
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
      
      <!-- Frame Analysis Popup (shown during drag) -->
      <FrameAnalysisPopup
        :visible="frameAnalysisPopup.visible"
        :frameData="frameAnalysisPopup.data"
        :isLoading="frameAnalysisPopup.isLoading"
        :position="frameAnalysisPopup.position"
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
const isDragging = ref(false)
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
  
  if (autoAnalyze) {
    startAnalysis()
  }
  
  // Start timer for live stream elapsed time
  startStreamTimer()
}

const goHome = () => {
  stopStreamPolling()
  // Close analysis EventSource if running
  if (analysisEventSource) {
    analysisEventSource.close()
    analysisEventSource = null
  }
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

const startAnalysis = async () => {
  if (!currentSession.value) return
  
  isProcessing.value = true
  
  try {
    // Use SurgR1 + GLM processing pipeline
    await axios.post('/api/analysis/process-video-surgr1-glm', {
      session_id: currentSession.value.session_id,
      use_chinese: true,  // Use Chinese for summaries
      use_glm_multimodal: false  // Text-only mode for faster processing
    })
    
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
      
      // Highlight new window briefly
      highlightedWindowId.value = data.window_id
      setTimeout(() => {
        if (highlightedWindowId.value === data.window_id) {
          highlightedWindowId.value = -1
        }
      }, 2000)
    }
    
    analysisEventSource.onerror = () => {
      isProcessing.value = false
      if (analysisEventSource) {
        analysisEventSource.close()
        analysisEventSource = null
      }
    }
    
  } catch (error) {
    console.error('Analysis failed:', error)
    isProcessing.value = false
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

// ===========================================================================
// New handlers for enhanced features
// ===========================================================================

// Handle window hover from progress bar
const handleWindowHover = (windowId) => {
  if (windowId >= 0 && summaries.value.find(s => s.window_id === windowId)) {
    highlightedWindowId.value = windowId
  } else {
    highlightedWindowId.value = -1
  }
}

// Handle seek to specific window
const handleSeekToWindow = (windowId) => {
  highlightedWindowId.value = windowId
  // Auto-clear highlight after 2 seconds
  setTimeout(() => {
    if (highlightedWindowId.value === windowId) {
      highlightedWindowId.value = -1
    }
  }, 2000)
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

// Fetch frame analysis from backend
const fetchFrameAnalysis = async (timestamp) => {
  if (!currentSession.value) return
  
  try {
    frameAnalysisPopup.value.isLoading = true
    
    const response = await axios.get(
      `/api/analysis/frame-analysis/${currentSession.value.session_id}`,
      { params: { timestamp } }
    )
    
    if (response.data.found) {
      frameAnalysisPopup.value.data = {
        timestamp: response.data.timestamp,
        surgical_phase: response.data.surgical_phase,
        surgical_action: response.data.surgical_action,
        tool_localization: response.data.tool_localization,
        window_id: response.data.window_id
      }
    } else {
      frameAnalysisPopup.value.data = {
        timestamp,
        surgical_phase: '',
        surgical_action: '',
        tool_localization: ''
      }
    }
  } catch (error) {
    console.error('Failed to fetch frame analysis:', error)
    frameAnalysisPopup.value.data = { timestamp }
  } finally {
    frameAnalysisPopup.value.isLoading = false
  }
}

// Hide popup when not dragging
watch(isDragging, (newVal) => {
  if (!newVal) {
    // Delay hiding popup to allow click
    setTimeout(() => {
      if (!isDragging.value) {
        frameAnalysisPopup.value.visible = false
      }
    }, 300)
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

onMounted(() => {
  checkAnalysisServices()
  // Refresh status every 30 seconds
  analysisStatusInterval = setInterval(checkAnalysisServices, 30000)
})

onUnmounted(() => {
  if (analysisStatusInterval) {
    clearInterval(analysisStatusInterval)
  }
  stopStreamPolling()
  if (dragDebounceTimer.value) {
    clearTimeout(dragDebounceTimer.value)
  }
  // Clean up analysis EventSource
  if (analysisEventSource) {
    analysisEventSource.close()
    analysisEventSource = null
  }
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
