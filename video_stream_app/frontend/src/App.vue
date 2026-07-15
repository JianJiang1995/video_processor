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
      <!-- Nav Rail (left) -->
      <NavRail
        :activeView="navActiveView"
        :isAnalyzing="isProcessing"
        :summaryCount="summaries.length"
        :summaryReady="summaryReady"
        @navigate="handleNavigation"
        @toggleAnalyze="toggleAnalysis"
      />

      <!-- Center + Right layout -->
      <div class="app-main-wrapper">
        <!-- Header -->
        <header class="app-header">
          <div class="logo" @click="goHome">
            <div class="logo-icon">&#x1F3E5;</div>
            <div class="logo-text">Surg-R1<span>手术助手</span></div>
          </div>

          <div class="header-center">
            <span class="mode-badge" :class="mode">
              {{ replayMode
                ? `⏱ ${t('app.offlineReplay')}`
                : (mode === 'local' ? `📁 ${t('app.localVideo')}` : `📡 ${t('app.liveStream')}`) }}
            </span>
            <span v-if="currentSession" class="header-session-id">
              {{ t('app.session') }} {{ currentSession.session_id?.substring(0, 8) || '' }}
            </span>
            <span v-if="currentSession" class="session-name">
              {{ currentSession.video_name }}
            </span>
          </div>

          <div class="header-actions">
            <button
              v-if="summaryReady"
              class="btn summary-ready-btn"
              @click="handleNavigation('report')"
            >
              <span aria-hidden="true">&#x1F4C4;</span>
              {{ t('app.videoSummary') }}
            </button>
            <div class="language-switch" :aria-label="t('lang.label')">
              <button
                class="language-option"
                :class="{ active: language === 'zh' }"
                @click="setLanguage('zh')"
              >
                {{ t('lang.zh') }}
              </button>
              <button
                class="language-option"
                :class="{ active: language === 'en' }"
                @click="setLanguage('en')"
              >
                {{ t('lang.en') }}
              </button>
            </div>
            <button class="btn btn-secondary" @click="goHome">
              &#x2190; {{ t('app.back') }}
            </button>
          </div>
        </header>

        <!-- Main Content -->
        <main class="app-main">
          <!-- Video + Controls (center) -->
          <div class="app-main-center">
            <section
              class="video-section"
              :style="{ minHeight: videoSectionMinHeight + 'px' }"
              @click="handleVideoSectionClick"
            >
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
                :resumeNonce="playbackResumeNonce"
                :windowDuration="windowDuration"
                @timeupdate="handleTimeUpdate"
                @play="handlePlay"
                @pause="handleVideoPause"
                @ended="handleVideoEnded"
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
                :summaries="localizedSummaries"
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
              />
            </section>

            <!-- Key event nodes / full window history (always visible) -->
            <div
              class="video-resize-handle"
              :title="t('app.resizeHistoryTitle')"
              @pointerdown="startVideoResize"
              @dblclick="resetBottomStripHeight"
            ></div>

            <div
              class="bottom-card-strip"
              :style="{ height: bottomStripHeight + 'px' }"
            >
              <div class="bcs-header">
                <span class="bcs-title">{{ t('app.keyEventNodes') }}</span>
                <span class="bcs-count" v-if="bottomViewMode === 'events' && sortedEventNodes.length > 0">
                  {{ t('app.eventCount', { count: sortedEventNodes.length }) }}
                </span>
                <span class="bcs-count" v-else-if="bottomViewMode === 'windows' && summaries.length > 0">
                  {{ t('app.windowCount', { count: summaries.length }) }}
                </span>
                <div class="bcs-mode-toggle" :aria-label="t('app.bottomMode')">
                  <button
                    class="bcs-mode-btn"
                    :class="{ active: bottomViewMode === 'events' }"
                    @click="setBottomViewMode('events')"
                  >
                    {{ t('app.eventNodes') }}
                  </button>
                  <button
                    class="bcs-mode-btn"
                    :class="{ active: bottomViewMode === 'windows' }"
                    @click="setBottomViewMode('windows')"
                  >
                    {{ t('app.allWindows') }}
                  </button>
                </div>
                <button
                  class="bcs-refresh-btn"
                  :title="t('app.regenerateEvents')"
                  :disabled="summaries.length === 0 || eventNodesLoading"
                  @click="requestEventNodes({ force: true })"
                >
                  &#x21BB;
                </button>
                <button
                  class="bcs-overview-btn"
                  :disabled="summaries.length === 0"
                  @click="enterOverview"
                >
                  &#x2B1A; {{ t('app.gridView') }}
                </button>
              </div>
              <div
                class="bcs-scroll event-node-scroll"
                v-if="bottomViewMode === 'events' && sortedEventNodes.length > 0"
                ref="bottomScrollRef"
                :class="{ dragging: bottomScrollDragging }"
                @pointerdown="startBottomScrollDrag"
              >
                <div
                  v-for="node in sortedEventNodes"
                  :key="node.id"
                  class="event-node-card"
                  :class="[
                    `event-type-${node.type}`,
                    `event-severity-${node.severity}`,
                    { selected: node.window_ids?.includes(selectedWindowId) }
                  ]"
                  :title="node.summary"
                  @pointerdown.stop
                  @click="handleEventNodeClick(node)"
                >
                  <div class="event-node-head">
                    <span class="event-node-kind">{{ eventNodeTypeLabel(node.type) }}</span>
                    <span class="event-node-time">{{ eventNodeTimeLabel(node) }}</span>
                  </div>
                  <div class="event-node-thumb">
                    <img
                      v-if="eventNodeThumbnail(node)"
                      :src="eventNodeThumbnail(node)"
                      alt=""
                      @error="handleEventNodeThumbError(node, $event)"
                    />
                    <div
                      v-else
                      class="bcs-card-thumb-placeholder"
                      :class="{ loading: eventNodeThumbLoading(node) }"
                    ></div>
                  </div>
                  <div class="event-node-title">{{ node.title }}</div>
                  <div class="event-node-summary">{{ node.summary }}</div>
                  <div class="event-node-meta">
                    <span>{{ eventNodeWindowLabel(node) }}</span>
                    <span v-if="node.confidence != null">{{ t('app.eventConfidence', { value: Math.round(node.confidence * 100) }) }}</span>
                  </div>
                </div>
              </div>
              <div
                class="bcs-scroll"
                v-else-if="bottomViewMode === 'windows' && summaries.length > 0"
                ref="bottomScrollRef"
                :class="{ dragging: bottomScrollDragging }"
                @pointerdown="startBottomScrollDrag"
              >
                <div
                  v-for="s in sortedSummaries"
                  :key="s.window_id"
                  class="bcs-card"
                  :class="{
                    selected: s.window_id === selectedWindowId,
                    bleeding: isSevereBleedingSummary(s),
                    resolved: isBleedingResolvedSummary(s),
                  }"
                  :title="s.summary"
                  @pointerdown.stop
                  @click="handleBottomCardClick(s)"
                >
                  <div class="bcs-card-top">
                    <span class="bcs-card-win">{{ bottomWindowLabel(s.window_id + 1) }}</span>
                    <span class="bcs-card-time">
                      {{ formatWindowTime(s.start_time) }}
                    </span>
                  </div>
                  <div class="bcs-card-thumb">
                    <img
                      v-if="bottomThumbnails[s.window_id]"
                      :src="bottomThumbnails[s.window_id]"
                      alt=""
                      @error="handleBottomThumbError(s, $event)"
                    />
                    <div
                      v-else
                      class="bcs-card-thumb-placeholder"
                      :class="{ loading: bottomThumbLoading[s.window_id] }"
                    ></div>
                  </div>
                  <div class="bcs-card-text">{{ s.summary }}</div>
                </div>
              </div>
              <div v-else class="bcs-empty">
                <span class="bcs-empty-hint">
                  {{ bottomEmptyText }}
                </span>
              </div>
            </div>
          </div>

          <!-- Right Panel (Analysis/Chat tabs) -->
          <div
            class="right-panel-resize-handle"
            :title="t('app.resizeRightPanelTitle')"
            @pointerdown="startRightPanelResize"
            @dblclick="resetRightPanelWidth"
          ></div>

          <RightPanel
            v-model:activeTab="rightPanelTab"
            :style="{ width: rightPanelWidth + 'px' }"
            :summaries="localizedSummaries"
            :currentSummary="localizedCurrentSummary"
            :selectedWindowId="selectedWindowId"
            :sessionId="currentSession?.session_id || ''"
            :isProcessing="isProcessing"
            @tts="handleTTS"
            @seekToWindow="handleSeekToWindow"
            @chatMessage="handleVoiceMessage"
          />
        </main>
      </div>

      <!-- Frame Analysis Popup (shown during drag/seek) -->
      <FrameAnalysisPopup
        :visible="frameAnalysisPopup.visible"
        :frameData="frameAnalysisPopup.data"
        :isLoading="frameAnalysisPopup.isLoading"
        :position="frameAnalysisPopup.position"
        @close="closeFrameAnalysisPopup"
      />

      <!-- Toast Message -->
      <Transition name="toast">
        <div v-if="toastVisible" class="toast-message">
          <span class="toast-icon">&#x26A0;&#xFE0F;</span>
          <span class="toast-text">{{ toastMessage }}</span>
        </div>
      </Transition>

      <!-- Overview Toast -->
      <Transition name="toast">
        <div v-if="showOverviewToast" class="overview-toast">
          <span class="overview-toast-text">{{ t('app.analysisComplete') }}</span>
          <button class="overview-toast-btn" @click="enterOverview">{{ t('app.enterOverview') }} &#x2192;</button>
          <button class="overview-toast-dismiss" @click="showOverviewToast = false">&#x2715;</button>
        </div>
      </Transition>

      <!-- Window Overview Mode: overlay the main view so live playback stays mounted. -->
      <WindowOverview
        v-if="currentView === 'overview'"
        class="overview-layer"
        :summaries="localizedSummaries"
        :session="currentSession"
        :mode="mode"
        :isProcessing="isProcessing"
        :windowDuration="windowDuration"
        @back="handleOverviewBack"
        @seekToWindow="handleOverviewSeekToWindow"
      />

      <ClinicalReportView
        v-if="currentView === 'report'"
        :session="currentSession"
        :language="language"
        :initialReport="replayReport"
        @back="handleReportBack"
      />
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import { apiUrl, isElectron, loadReplayBundle } from './utils/electronBridge.js'
import analysisQueue from './utils/AnalysisQueue.js'
import ModeSelector from './components/ModeSelector.vue'
import StreamInput from './components/StreamInput.vue'
import VideoPlayer from './components/VideoPlayer.vue'
import ControlBar from './components/ControlBar.vue'
import SummaryPanel from './components/SummaryPanel.vue'
import VoiceChat from './components/VoiceChat.vue'
import FrameAnalysisPopup from './components/FrameAnalysisPopup.vue'
import WindowOverview from './components/WindowOverview.vue'
import ClinicalReportView from './components/ClinicalReportView.vue'
import NavRail from './components/NavRail.vue'
import RightPanel from './components/RightPanel.vue'
import { useI18n } from './i18n.js'

const { language, setLanguage, t } = useI18n()

// View state
const currentView = ref('select')  // 'select', 'stream-input', 'main', 'overview', 'report'
const mode = ref('local')  // 'local' or 'stream'

// State
const currentSession = ref(null)
const currentTime = ref(0)
const duration = ref(0)
const isPlaying = ref(false)
const volume = ref(0.8)
const summaries = ref([])
const replayMode = ref(false)
const replayAllSummaries = ref([])
const replayAllEvents = ref([])
const replayReport = ref(null)
const replayFinalUpdateDelay = ref(1.25)
let replaySummarySignature = ''
let replayEventSignature = ''
const bottomViewMode = ref(localStorage.getItem('surg_bottom_view_mode') || 'events')
const eventNodes = ref([])
const eventNodesLoading = ref(false)
const eventNodesError = ref('')
const bottomThumbnails = reactive({})
const bottomThumbLoading = reactive({})
const isProcessing = ref(false)

// New states for enhanced features
const highlightedWindowId = ref(-1)
const userSelectedWindow = ref(false)  // True when user manually selected a window
const isDragging = ref(false)
const showSam3 = ref(false)  // Toggle for SAM3 segmented view

// Loop playback state - when set, video will loop within this window
const loopWindow = ref(null)  // { window_id, start_time, end_time }
const livePlaybackTime = ref(0)
const loopReturnTime = ref(null)
const loopWasPlaying = ref(false)
const playbackResumeNonce = ref(0)
// Flag to prevent clearing loopWindow during loop-triggered seeks
let isLoopSeek = false
let detachedPlaybackWallStart = null
let detachedPlaybackBaseTime = 0
const sam3Time = ref(null)  // SAM3 frame timestamp (may differ from currentTime due to processing delay)

// New E3 layout state
const rightPanelTab = ref('analysis')  // 'analysis' or 'chat'
const selectedWindowId = ref(-1)  // Selected window in bottom card strip
const navActiveView = ref('analysis')  // Nav rail active view
const viewportWidth = ref(window.innerWidth)
const viewportHeight = ref(window.innerHeight)
const clamp = (value, min, max) => Math.min(max, Math.max(min, value))
const RIGHT_PANEL_STORAGE_KEY = 'surg_right_panel_width_v2'
const BOTTOM_STRIP_STORAGE_KEY = 'surg_bottom_strip_height_v3'
const minRightPanelWidth = () => clamp(Math.round(viewportWidth.value * 0.26), 480, 540)
const defaultRightPanelWidth = () => clamp(Math.round(viewportWidth.value * 0.28), 500, 680)
const maxRightPanelWidth = () => clamp(Math.round(viewportWidth.value * 0.42), 680, 960)
const minBottomStripHeight = () => clamp(Math.round(viewportHeight.value * 0.24), 360, 420)
const defaultBottomStripHeight = () => clamp(Math.round(viewportHeight.value * 0.30), 430, 520)
const maxBottomStripHeight = () => clamp(Math.round(viewportHeight.value * 0.50), 520, 700)
const rightPanelWidth = ref(clamp(
  Number(localStorage.getItem(RIGHT_PANEL_STORAGE_KEY)) || defaultRightPanelWidth(),
  minRightPanelWidth(),
  maxRightPanelWidth()
))
const bottomStripHeight = ref(clamp(
  Number(localStorage.getItem(BOTTOM_STRIP_STORAGE_KEY)) || defaultBottomStripHeight(),
  minBottomStripHeight(),
  maxBottomStripHeight()
))
const frameAnalysisPopup = ref({
  visible: false,
  data: null,
  isLoading: false,
  position: { x: 0, y: 0 }
})
const dragDebounceTimer = ref(null)

// Overview mode toast
const showOverviewToast = ref(false)
let overviewToastTimer = null

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
let lastSummaryRefreshAt = 0
let lastSummaryRefreshWindow = -1
const translationInFlight = new Set()
let eventNodesTimer = null
let eventNodesRequestSeq = 0
let eventNodesRefreshPending = false
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

const bottomThumbQueue = []
const bottomThumbQueued = new Set()
const bottomThumbRetries = reactive({})
const bottomThumbFailedUrls = new Map()
let bottomThumbActive = 0
const BOTTOM_THUMB_CONCURRENCY = 2
const bottomScrollRef = ref(null)
const bottomScrollDragging = ref(false)
const bottomScrollClickSuppressed = ref(false)
let bottomScrollPointerId = null
let bottomScrollMoveHandler = null
let bottomScrollUpHandler = null

const clearBottomThumbnails = () => {
  Object.keys(bottomThumbnails).forEach(key => delete bottomThumbnails[key])
  Object.keys(bottomThumbLoading).forEach(key => delete bottomThumbLoading[key])
  Object.keys(bottomThumbRetries).forEach(key => delete bottomThumbRetries[key])
  bottomThumbFailedUrls.clear()
  bottomThumbQueue.length = 0
  bottomThumbQueued.clear()
  bottomThumbActive = 0
}

const clearEventNodes = () => {
  if (eventNodesTimer) {
    clearTimeout(eventNodesTimer)
    eventNodesTimer = null
  }
  eventNodesRequestSeq += 1
  eventNodesRefreshPending = false
  eventNodes.value = []
  eventNodesError.value = ''
  eventNodesLoading.value = false
}

const fetchBottomThumbnail = async (summary) => {
  const wid = summary?.window_id
  const sid = currentSession.value?.session_id
  if (wid == null || !sid || bottomThumbnails[wid]) return

  const startTime = Number(summary.start_time ?? summary.window_start ?? 0)
  const endTime = Number(summary.end_time ?? summary.window_end ?? startTime + windowDuration.value)
  const midTime = Math.max(0, (startTime + endTime) / 2)

  const setThumbnail = (frame) => {
    if (!frame || currentSession.value?.session_id !== sid) return false
    if (frame.url) {
      if (bottomThumbFailedUrls.get(wid)?.has(frame.url)) return false
      bottomThumbnails[wid] = frame.url
      delete bottomThumbRetries[wid]
      return true
    }
    if (frame.image_base64) {
      bottomThumbnails[wid] = `data:image/jpeg;base64,${frame.image_base64}`
      delete bottomThumbRetries[wid]
      return true
    }
    return false
  }

  const closestFrame = (frames, targetTime) => {
    const badUrls = bottomThumbFailedUrls.get(wid)
    const candidates = (frames || []).filter(frame => {
      if (frame?.image_base64) return true
      if (frame?.url) return !badUrls?.has(frame.url)
      return false
    })
    if (!candidates.length) return null
    return candidates.reduce((a, b) => {
      return Math.abs((Number(b.timestamp) || 0) - targetTime) < Math.abs((Number(a.timestamp) || 0) - targetTime) ? b : a
    }, candidates[0])
  }

  const requestBatch = async ({ start, end, maxFrames = 8, usePreview = true, target = midTime }) => {
    const safeStart = Math.max(0, Number(start) || 0)
    const safeEnd = Math.max(safeStart + 0.2, Number(end) || safeStart + 0.2)
    const batchRes = await axios.get(`/api/analysis/frames-batch/${sid}`, {
      params: {
        start: safeStart,
        end: safeEnd,
        max_frames: maxFrames,
        use_url: true,
        use_preview: usePreview,
      },
      signal: getSessionSignal(),
    })
    if (!batchRes.data?.success) return false
    return setThumbnail(closestFrame(batchRes.data?.frames || [], target))
  }

  try {
    if (await requestBatch({ start: midTime - 1, end: midTime + 1, usePreview: true })) return
    if (await requestBatch({ start: startTime, end: endTime, maxFrames: 10, usePreview: true })) return
    if (await requestBatch({ start: startTime, end: endTime, maxFrames: 12, usePreview: false })) return

    const frameRes = await axios.get(`/api/analysis/frame-at-timestamp/${sid}`, {
      params: { timestamp: midTime, tolerance: 8.0 },
      signal: getSessionSignal(),
    })
    if (frameRes.data?.success && frameRes.data.image_base64) {
      if (currentSession.value?.session_id !== sid) return
      bottomThumbnails[wid] = `data:image/jpeg;base64,${frameRes.data.image_base64}`
    }
  } catch (error) {
    if (axios.isCancel(error) || error.name === 'AbortError') return
    console.warn(`[BottomStrip] thumbnail failed for window ${wid}:`, error.message)
  }
}

const pumpBottomThumbQueue = () => {
  while (bottomThumbActive < BOTTOM_THUMB_CONCURRENCY && bottomThumbQueue.length > 0) {
    const summary = bottomThumbQueue.shift()
    const wid = summary?.window_id
    if (wid == null || bottomThumbnails[wid]) {
      if (wid != null) bottomThumbQueued.delete(wid)
      continue
    }

    bottomThumbActive += 1
    bottomThumbLoading[wid] = true
    fetchBottomThumbnail(summary).finally(() => {
      bottomThumbActive = Math.max(0, bottomThumbActive - 1)
      bottomThumbQueued.delete(wid)
      bottomThumbLoading[wid] = false
      pumpBottomThumbQueue()
    })
  }
}

const enqueueBottomThumbnail = (summary, priority = false) => {
  const wid = summary?.window_id
  if (wid == null || bottomThumbnails[wid] || bottomThumbQueued.has(wid)) return
  bottomThumbQueued.add(wid)
  if (priority) bottomThumbQueue.unshift(summary)
  else bottomThumbQueue.push(summary)
  pumpBottomThumbQueue()
}

const handleBottomThumbError = (summary, event = null) => {
  const wid = summary?.window_id
  if (wid == null) return
  const failedUrl = event?.target?.currentSrc || event?.target?.src || bottomThumbnails[wid]
  if (failedUrl) {
    if (!bottomThumbFailedUrls.has(wid)) bottomThumbFailedUrls.set(wid, new Set())
    bottomThumbFailedUrls.get(wid).add(failedUrl)
  }
  delete bottomThumbnails[wid]
  const retries = Number(bottomThumbRetries[wid] || 0)
  if (retries >= 2) return
  bottomThumbRetries[wid] = retries + 1
  setTimeout(() => enqueueBottomThumbnail(summary, true), 350 + retries * 700)
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

const cleanUserSummaryText = (text) => {
  return String(text || '')
    .replace(/太夹前|钛夹前|太夹钳|胎夹钳/g, '钛夹钳')
    .replace(/动胆囊动脉/g, '胆囊动脉')
    .replace(/动胆囊管/g, '胆囊管')
    .replace(/(钛夹钳(?:正在)?夹闭(?:胆囊管|胆囊动脉))[，,]?\s*明显/g, '$1')
    .replace(/(当前窗口|本段|术野|画面)出现/g, '$1有')
    .replace(/出现了|出现/g, '')
    .replace(/【专家实时快照[^】]*】/g, '')
    .replace(/该段为实时快照，?\s*R1\/Gemini\s*精修结果稍后覆盖。?/g, '')
    .replace(/已基于\s*\d+\s*帧快速更新手术进程，?\s*R1\/Gemini\s*精修结果稍后覆盖。?/g, '')
    .replace(/R1\/Gemini\s*精修结果稍后覆盖。?/g, '')
    .replace(/精修(?:后|结果)?(?:将|会|稍后)?覆盖。?/g, '')
    .replace(/YOLO\s*(?:暂定)?(?:检出|检测出)/gi, '检出')
    .replace(/(?:暂定|暂时|稳定)?检出暂未稳定检出器械/g, '未见明确器械')
    .replace(/暂未稳定检出器械/g, '未见明确器械')
    .replace(/当前判断为/g, '当前处于')
    .replace(/[，,。；;\s]*暂无明确关键操作变化[。；;\s]*/g, '。')
    .replace(/当前处于当前阶段[，,]/g, '当前画面')
    .replace(/(?:动作三元组提示|主要动作)[:：]\s*\[[^\n。]*。?/g, '')
    .replace(/(?:动作三元组提示|主要动作)[:：]\s*(?:\[[^\]]+\](?:-[^；。,\s]+)*[；,，、\s]*)+。?/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

const CVS_STATUS_RE = /(CVS评估[:：]?[^。；;]*(?:[。；;]|$)|CVS(?:尚未达成|未达成|达成|已达成|确认|评估中)[^。；;]*(?:[。；;]|$)|安全关键视野[^。；;]*(?:[。；;]|$)|关键安全视野[^。；;]*(?:[。；;]|$)|critical view(?: of safety)?[^.;]*(?:[.;]|$))/gi
const CVS_RELEVANT_RE = /(CVS|安全关键视野|关键安全视野|critical view|胆囊管|胆囊动脉|胆囊板|两条结构)/
const CVS_ACHIEVED_RE = /(CVS(?:已经|已)?达成|CVS确认|CVS已确认|安全关键视野(?:已经|已)?确认|三要素(?:均)?(?:满足|达成)|critical view(?: of safety)? (?:achieved|confirmed))/i

const stripCvsStatusText = (text) => {
  return cleanUserSummaryText(text)
    .replace(CVS_STATUS_RE, '')
    .replace(/[，,]\s*[。；;]/g, '。')
    .replace(/^[，,。；;\s]+|[，,。；;\s]+$/g, '')
    .trim()
}

const cleanSummaryPayload = (payload) => {
  if (!payload || typeof payload !== 'object') return payload
  let others = payload.others && typeof payload.others === 'object' ? payload.others : {}
  if (typeof payload.others === 'string') {
    try {
      others = JSON.parse(payload.others)
    } catch {
      others = {}
    }
  }
  const windowId = payload.window_id ?? payload.windowId
  const startTime = payload.start_time ?? payload.window_start ?? payload.startTime
  const endTime = payload.end_time ?? payload.window_end ?? payload.endTime
  const summaryText = payload.summary ?? payload.glm_summary ?? payload.summary_text ?? payload.window_summary ?? ''
  return {
    ...payload,
    others,
    window_id: windowId == null ? windowId : Number(windowId),
    start_time: startTime == null ? startTime : Number(startTime),
    end_time: endTime == null ? endTime : Number(endTime),
    summary: cleanUserSummaryText(summaryText),
    phase: payload.phase || payload.dominant_phase || payload.surgical_phase || others.phase || 'Unknown',
    stage: Number(payload.stage ?? others.stage ?? 2),
    summary_en: payload.summary_en ?? others.summary_en ?? '',
    stage1_summary: cleanUserSummaryText(payload.stage1_summary ?? others.stage1_summary),
  }
}

const upsertSummary = (payload) => {
  const data = cleanSummaryPayload(payload)
  if (!data || !Number.isFinite(data.window_id)) return null

  const existingIndex = summaries.value.findIndex(s => s.window_id === data.window_id)
  if (existingIndex >= 0) {
    summaries.value[existingIndex] = {
      ...summaries.value[existingIndex],
      ...data,
      summary: data.summary || summaries.value[existingIndex].summary,
      summary_en: data.summary_en || summaries.value[existingIndex].summary_en,
    }
  } else {
    const arr = summaries.value
    const last = arr.length > 0 ? arr[arr.length - 1] : null
    if (!last || (data.start_time ?? 0) >= (last.start_time ?? 0)) {
      arr.push(data)
    } else {
      let lo = 0, hi = arr.length
      const st = data.start_time ?? 0
      while (lo < hi) {
        const mid = (lo + hi) >> 1
        if ((arr[mid].start_time ?? 0) <= st) lo = mid + 1
        else hi = mid
      }
      arr.splice(lo, 0, data)
    }
  }

  enqueueBottomThumbnail(data)
  scheduleEventNodesRefresh()
  return data
}

const syncReplayTimeline = (time, { forceFinal = false } = {}) => {
  if (!replayMode.value) return
  const replayTime = Math.max(0, Number(time || 0))
  const all = replayAllSummaries.value

  let lo = 0
  let hi = all.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    const endTime = Number(all[mid]?.end_time ?? all[mid]?.window_end ?? 0)
    if (endTime <= replayTime + 0.04) lo = mid + 1
    else hi = mid
  }

  const visibleCount = lo
  const latest = visibleCount > 0 ? all[visibleCount - 1] : null
  const latestEnd = Number(latest?.end_time ?? latest?.window_end ?? 0)
  const stage1 = cleanUserSummaryText(latest?.stage1_summary || latest?.others?.stage1_summary || '')
  const finalText = cleanUserSummaryText(latest?.summary || '')
  const showExpertStage = Boolean(
    latest
    && !forceFinal
    && stage1
    && finalText
    && stage1 !== finalText
    && replayTime < latestEnd + replayFinalUpdateDelay.value
  )
  const nextSummarySignature = `${visibleCount}:${showExpertStage ? 'expert' : 'final'}:${forceFinal ? 1 : 0}`

  if (nextSummarySignature !== replaySummarySignature) {
    const previousIds = new Set(summaries.value.map(item => Number(item.window_id)))
    const visible = all.slice(0, visibleCount).map((item, index) => {
      const cleaned = cleanSummaryPayload(item)
      if (showExpertStage && index === visibleCount - 1) {
        return {
          ...cleaned,
          summary: stage1,
          stage: 1,
          replay_stage: 'expert',
        }
      }
      return { ...cleaned, replay_stage: 'final' }
    })
    summaries.value = visible
    visible.forEach((item) => {
      if (!previousIds.has(Number(item.window_id))) enqueueBottomThumbnail(item)
    })
    replaySummarySignature = nextSummarySignature
  }

  const visibleEvents = replayAllEvents.value.filter((node) => {
    const availableAt = Number(node?.end_time ?? node?.start_time ?? 0)
    return availableAt <= replayTime + 0.04 || forceFinal
  })
  const nextEventSignature = visibleEvents.map(node => node?.id || `${node?.start_time}-${node?.title}`).join('|')
  if (nextEventSignature !== replayEventSignature) {
    eventNodes.value = visibleEvents
      .map(normalizeEventNode)
      .filter(node => node.window_ids.length > 0)
    eventNodesError.value = ''
    eventNodesLoading.value = false
    replayEventSignature = nextEventSignature
    resetEventNodeScroll()
  }

  const endAt = Number(duration.value || currentSession.value?.duration || 0)
  isProcessing.value = isPlaying.value && (!endAt || replayTime < endAt - 0.04)
}

const loadOfflineReplayFromQuery = async () => {
  const query = new URLSearchParams(window.location.search)
  const specPath = query.get('replaySpec')
  if (!specPath) return false
  if (!isElectron()) throw new Error('Offline replay requires the Electron application')

  const bundle = await loadReplayBundle(specPath)
  if (!bundle?.success) throw new Error(bundle?.error || 'Failed to load offline replay')

  abortAllSessionRequests()
  createSessionAbortController()
  replayMode.value = true
  replayAllSummaries.value = (bundle.summaries || []).map(cleanSummaryPayload)
  replayAllEvents.value = Array.isArray(bundle.events) ? bundle.events : []
  replayReport.value = bundle.report || null
  replayFinalUpdateDelay.value = Math.max(0, Number(bundle.spec?.final_update_delay || 1.25))
  replaySummarySignature = ''
  replayEventSignature = ''

  setLanguage(bundle.spec?.language || 'zh')
  mode.value = 'local'
  currentView.value = 'main'
  navActiveView.value = 'analysis'
  rightPanelTab.value = 'analysis'
  bottomViewMode.value = 'events'
  currentSession.value = bundle.session
  duration.value = Number(bundle.session?.duration || 0)
  const requestedStartAt = Number(query.get('replayStartAt') || 0)
  const initialTime = Number.isFinite(requestedStartAt)
    ? Math.min(duration.value, Math.max(0, requestedStartAt))
    : 0
  currentTime.value = initialTime
  livePlaybackTime.value = initialTime
  isPlaying.value = false
  isProcessing.value = false
  streamEnded.value = false
  summaries.value = []
  eventNodes.value = []
  clearBottomThumbnails()
  rightPanelWidth.value = defaultRightPanelWidth()
  bottomStripHeight.value = defaultBottomStripHeight()
  syncReplayTimeline(initialTime, { forceFinal: initialTime >= duration.value - 0.04 })

  await nextTick()
  if (bundle.spec?.auto_play !== false) {
    const delay = Math.max(100, Number(bundle.spec?.auto_start_delay_ms || 900))
    window.setTimeout(() => {
      if (!replayMode.value || currentSession.value?.session_id !== bundle.session?.session_id) return
      handlePlay()
    }, delay)
  }
  console.log('[Replay] Loaded offline analysis bundle:', bundle.paths)
  return true
}

const isSevereBleedingSummary = (summary) => {
  const text = `${summary?.summary || ''} ${summary?.dominant_phase || ''}`.toLowerCase()
  if (/(无(?:明显)?出血|未见(?:明显)?出血|没有(?:明显)?出血|无活动性出血|未见活动性出血|no bleeding|without bleeding)/i.test(text)) return false
  return /(大量(?:活动性)?出血|明显(?:活动性)?出血|持续(?:活动性)?出血|喷涌出血|喷射性出血|涌血|明确出血源|影响视野的持续渗血|heavy bleeding|massive bleeding|profuse bleeding|significant bleeding)/i.test(text)
}

const isBleedingResolvedSummary = (summary) => {
  const text = `${summary?.summary || ''} ${summary?.dominant_phase || ''}`.toLowerCase()
  return /(出血(?:已经|已)?(?:停止|控制|解决)|已(?:完成)?止血|止血(?:完成|成功|有效)|凝血后(?:未见|无)活动性出血|未见活动性出血|无活动性出血|bleeding (?:stopped|controlled|resolved)|hemostasis achieved)/i.test(text)
}

// Computed: current summary based on the live playback clock.
const currentSummary = computed(() => {
  if (!summaries.value.length) return null
  
  // During history-loop preview, the native video element seeks inside an old
  // window. Keep the analysis panel tied to livePlaybackTime so review mode
  // does not make "latest" jump backward.
  const displayTime = loopWindow.value ? livePlaybackTime.value : currentTime.value
  const windowId = Math.floor(displayTime / windowDuration.value)
  return cleanSummaryPayload(summaries.value.find(s => s.window_id === windowId)
    || [...summaries.value]
      .filter(s => s.window_id <= windowId)
      .sort((a, b) => b.window_id - a.window_id)[0]
    || summaries.value[summaries.value.length - 1]
    || null)
})

const localizedSummary = (summary) => {
  const s = cleanSummaryPayload(summary)
  if (!s) return s
  if (language.value === 'en' && s.summary_en) {
    return { ...s, summary: stripCvsStatusText(s.summary_en) || s.summary_en }
  }
  return { ...s, summary: stripCvsStatusText(s.summary) || s.summary }
}

const localizedSummaries = computed(() => summaries.value.map(localizedSummary))
const localizedCurrentSummary = computed(() => localizedSummary(currentSummary.value))

const isSimulatorSession = computed(() => (
  mode.value === 'stream' &&
  currentSession.value?.video_path?.startsWith('simulator://')
))

const maxPlayableTime = () => {
  const sessionDuration = Number(currentSession.value?.duration || 0)
  const displayDuration = Number(duration.value || 0)
  const maxTime = Math.max(sessionDuration, displayDuration)
  return maxTime > 0 ? maxTime : Infinity
}

const startDetachedPlaybackClock = () => {
  if (mode.value !== 'stream' || !isSimulatorSession.value || !isPlaying.value || streamEnded.value) return
  detachedPlaybackWallStart = performance.now()
  detachedPlaybackBaseTime = Math.max(
    Number(livePlaybackTime.value || 0),
    Number(currentTime.value || 0),
    Number(loopReturnTime.value || 0)
  )
}

const advanceDetachedPlaybackClock = () => {
  if (detachedPlaybackWallStart == null) return null
  const elapsed = isPlaying.value ? (performance.now() - detachedPlaybackWallStart) / 1000 : 0
  const nextTime = Math.min(maxPlayableTime(), Math.max(0, detachedPlaybackBaseTime + elapsed))
  livePlaybackTime.value = nextTime
  currentTime.value = nextTime
  return nextTime
}

const stopDetachedPlaybackClock = () => {
  const nextTime = advanceDetachedPlaybackClock()
  detachedPlaybackWallStart = null
  detachedPlaybackBaseTime = 0
  return nextTime
}

// Computed: list of analyzed window IDs
const analyzedWindows = computed(() => {
  return summaries.value.map(s => s.window_id)
})

const summaryReady = computed(() => Boolean(
  currentSession.value
  && summaries.value.length > 0
  && !isProcessing.value
))

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
  clearEventNodes()
  
  // Detect if this is a stream session based on video_path
  const isStreamSession = session.video_path && (
    session.video_path.startsWith('http://') || 
    session.video_path.startsWith('https://') ||
    session.video_path.startsWith('rtsp://') ||
    session.video_path.startsWith('device://') ||
    session.video_path.startsWith('decklink://') ||
    session.video_path.startsWith('simulator://')
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
  // Clear previous session data first
  summaries.value = []
  clearEventNodes()
  clearBottomThumbnails()
  highlightedWindowId.value = -1
  userSelectedWindow.value = false
  loopWindow.value = null
  surgr1ProcessingStatus.value = { running: false, framesAnalyzed: 0 }
  isProcessing.value = false
  streamEnded.value = false
  streamWasActive.value = false
  
  currentSession.value = session
  duration.value = Number(session.duration || 0) || 0
  currentView.value = 'main'
  isPlaying.value = true
  currentTime.value = 0
  livePlaybackTime.value = 0
  streamStartTime.value = Date.now()  // Track when stream started
  
  // Restart service status polling when entering main view
  restartAnalysisStatusInterval()
  
  // Auto-start SurgR1 continuous processing when stream connects
  startSurgR1Continuous(session.session_id)
  
  if (autoAnalyze) {
    startAnalysis()
  }
  
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
  if (currentSession.value && !replayMode.value) {
    // Use sendBeacon to ensure the stop request is sent even if page navigation happens
    const sessionId = currentSession.value.session_id
    try {
      navigator.sendBeacon(apiUrl(`/api/analysis/stop-surgr1-continuous/${sessionId}`), '')
      console.log(`[goHome] Sent stop request for session ${sessionId}`)
    } catch (e) {
      console.warn('[goHome] sendBeacon failed, trying fetch:', e)
      // Fallback: fire-and-forget fetch
      fetch(apiUrl(`/api/analysis/stop-surgr1-continuous/${sessionId}`), { 
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
  replayMode.value = false
  replayAllSummaries.value = []
  replayAllEvents.value = []
  replayReport.value = null
  replaySummarySignature = ''
  replayEventSignature = ''
  clearEventNodes()
  clearBottomThumbnails()
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
let lastAcceptedTimeUpdate = 0
let lastPositionHeartbeat = 0
const handleTimeUpdate = (time) => {
  const now = performance.now()
  if (mode.value === 'stream' && now - lastAcceptedTimeUpdate < 250) {
    return
  }
  lastAcceptedTimeUpdate = now
  if (loopWindow.value) {
    // History-loop preview must not rewrite the live playback clock. The
    // native video element is temporarily seeking within an analyzed window,
    // but backend position, latest-analysis selection, and resume point stay
    // tied to the live stream timeline.
    advanceDetachedPlaybackClock()
    return
  }
  if (
    mode.value === 'stream' &&
    isSimulatorSession.value &&
    isPlaying.value &&
    !loopWindow.value &&
    livePlaybackTime.value > 3 &&
    time < livePlaybackTime.value - 0.03
  ) {
    return
  }
  currentTime.value = time
  if (replayMode.value) syncReplayTimeline(time)
  if (mode.value === 'stream') {
    livePlaybackTime.value = time
    duration.value = Math.max(duration.value || 0, time)
    if (currentSession.value && now - lastPositionHeartbeat >= 1000) {
      lastPositionHeartbeat = now
      axios.post(`/api/video/control/${currentSession.value.session_id}`, {
        action: 'position',
        position: time
      }).catch(() => {})
    }
    refreshCurrentWindowSummary(time)
  }
  
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
  if (loopWindow.value) {
    exitLoopMode(true)
    return
  }
  isPlaying.value = true
  if (replayMode.value) {
    syncReplayTimeline(currentTime.value)
    return
  }
  if (currentSession.value) {
    if (mode.value === 'stream') {
      // Resume stream timer
      resumeStreamTimer()
    }
    axios.post(`/api/video/control/${currentSession.value.session_id}`, {
      action: 'play',
      position: loopWindow.value ? livePlaybackTime.value : currentTime.value
    })
  }
}

const handlePause = () => {
  if (loopWindow.value) {
    exitLoopMode(false)
    return
  }
  isPlaying.value = false
  if (replayMode.value) {
    syncReplayTimeline(currentTime.value)
    return
  }
  if (currentSession.value) {
    if (mode.value === 'stream') {
      // Pause stream timer
      pauseStreamTimer()
    }
    axios.post(`/api/video/control/${currentSession.value.session_id}`, {
      action: 'pause',
      position: loopWindow.value ? livePlaybackTime.value : currentTime.value
    })
  }
}

const handleVideoPause = () => {
  // The simulator preview uses a latest-frame WebSocket path for recording.
  // Ignore renderer/native-video pause events in this mode; the bottom control
  // bar remains the explicit user pause path.
  if (mode.value === 'stream' && isSimulatorSession.value && !loopWindow.value) {
    return
  }
  handlePause()
}

const handleVideoEnded = () => {
  const endAt = Number(duration.value || currentSession.value?.duration || currentTime.value || 0)
  if (endAt > 0) currentTime.value = endAt
  isPlaying.value = false
  if (replayMode.value) {
    syncReplayTimeline(endAt, { forceFinal: true })
    isProcessing.value = false
  }
}

const handleSeek = (time) => {
  currentTime.value = time
  if (replayMode.value) syncReplayTimeline(time)
  
  // If this is a manual seek (not triggered by loop), exit loop mode
  if (!isLoopSeek && loopWindow.value) {
    console.log('[Loop] Manual seek detected, exiting loop mode')
    loopWindow.value = null
  }
  
  if (currentSession.value && !replayMode.value) {
    axios.post(`/api/video/control/${currentSession.value.session_id}`, {
      action: 'seek',
      position: time
    })
  }
}

// Exit loop playback mode
const exitLoopMode = (forcePlaying = null) => {
  if (loopWindow.value) {
    console.log('[Loop] Exiting loop mode')
    const detachedResumeAt = stopDetachedPlaybackClock()
    const resumeAt = Number.isFinite(detachedResumeAt)
      ? detachedResumeAt
      : (Number.isFinite(loopReturnTime.value) ? loopReturnTime.value : livePlaybackTime.value)
    const shouldPlay = typeof forcePlaying === 'boolean' ? forcePlaying : loopWasPlaying.value
    currentTime.value = Math.max(0, resumeAt || 0)
    livePlaybackTime.value = currentTime.value
    loopWindow.value = null
    loopReturnTime.value = null
    loopWasPlaying.value = false
    isPlaying.value = shouldPlay
    playbackResumeNonce.value += 1
    userSelectedWindow.value = false
    highlightedWindowId.value = -1
    selectedWindowId.value = -1
    if (currentSession.value && !replayMode.value) {
      axios.post(`/api/video/control/${currentSession.value.session_id}`, {
        action: shouldPlay ? 'play' : 'pause',
        position: currentTime.value
      }).catch(() => {})
    }
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
    clearEventNodes()
    clearBottomThumbnails()
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
    clearEventNodes()
    clearBottomThumbnails()
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
    summaries.value = (response.data || []).map(cleanSummaryPayload)
    summaries.value.forEach(enqueueBottomThumbnail)
    scheduleEventNodesRefresh(300)
  } catch (error) {
    console.error('Failed to load summaries:', error)
  }
}

const refreshSurgr1Status = async () => {
  surgr1Status.value = { ...surgr1Status.value, checking: true }
  try {
    const res = await axios.get('/api/analysis/surgr1/status', { timeout: 12000 })
    const available = !!res.data.available
    surgr1Status.value = { available, checking: false }
    return available
  } catch (error) {
    console.warn('[Status] SurgR1 check failed:', error.message)
    surgr1Status.value = { available: false, checking: false }
    return false
  }
}

const refreshGlmStatus = async () => {
  glmStatus.value = { ...glmStatus.value, checking: true }
  try {
    const res = await axios.get('/api/analysis/glm/status', { timeout: 12000 })
    const available = !!res.data.available
    glmStatus.value = { available, checking: false }
    return available
  } catch (error) {
    console.warn('[Status] VLM check failed:', error.message)
    glmStatus.value = { available: false, checking: false }
    return false
  }
}

const ensureRequiredAnalysisServices = async () => {
  const [surgr1Available, glmAvailable] = await Promise.all([
    refreshSurgr1Status(),
    refreshGlmStatus(),
  ])
  return { surgr1Available, glmAvailable }
}

// Start continuous SurgR1 processing in background
const startSurgR1Continuous = async (sessionId) => {
  // Create a new abort controller for this session
  createSessionAbortController()
  
  if (surgr1Status.value.checking || !surgr1Status.value.available) {
    await refreshSurgr1Status()
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
          // Reset streamStartTime to match backend's start time only at the
          // beginning of a new wall-clock live stream. For local simulator
          // playback the native video clock is authoritative; resetting here
          // makes pause/resume jump back to 0.
          if (!isSimulatorSession.value && currentTime.value < 0.5) {
            streamStartTime.value = serverTimeMs + networkLatency
            currentTime.value = 0
          }
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
  if (replayMode.value) {
    if (isPlaying.value) handlePause()
    else handlePlay()
    return
  }

  const { surgr1Available, glmAvailable } = await ensureRequiredAnalysisServices()

  // These status checks are advisory. The backend can still produce a local
  // expert Stage 1 summary when cloud/local VLM health checks fail.
  if (!glmAvailable) {
    console.warn('VLM service is unavailable; starting expert-only Stage 1 summaries.')
  }

  if (!surgr1Available) {
    console.warn('SurgR1 service is unavailable; backend will use available local experts.')
  }
  
  isProcessing.value = true
  
  try {
    // Use GLM-only summarization (SurgR1 is already running in background)
    const response = await axios.post('/api/analysis/start-glm-summarization', {
      session_id: currentSession.value.session_id,
      use_chinese: true,  // Use Chinese for summaries
      use_glm_multimodal: true,  // Multimodal mode: send images to GLM for verification
      is_live: mode.value === 'stream'  // 在线模式：速度优先；离线模式：准确率优先
    }, {
      signal: getSessionSignal()
    })
    
    console.log('GLM summarization started:', response.data)
    
    // Start SSE for summaries
    analysisEventSource = new EventSource(
      apiUrl(`/api/analysis/stream-summaries/${currentSession.value.session_id}`)
    )
    
    analysisEventSource.onmessage = (event) => {
      const data = cleanSummaryPayload(JSON.parse(event.data))
      
      if (data.status === 'completed' || data.status === 'cancelled') {
        isProcessing.value = false
        analysisEventSource.close()
        analysisEventSource = null
        if ((data.status === 'completed' || data.status === 'cancelled') && summaries.value.length > 0) {
          requestEventNodes({ force: true })
          if (data.status === 'completed') showOverviewToastPrompt()
        }
        return
      }
      
      const existingIndex = summaries.value.findIndex(
        s => s.window_id === data.window_id
      )
      
      // Track if this is a new window (not just an update)
      const isNewWindow = existingIndex < 0
      
      upsertSummary(data)
      
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

// Overview mode toast prompt
const showOverviewToastPrompt = () => {
  showOverviewToast.value = true
  if (overviewToastTimer) clearTimeout(overviewToastTimer)
  overviewToastTimer = setTimeout(() => {
    showOverviewToast.value = false
  }, 10000)
}

const enterOverview = () => {
  if (loopWindow.value) {
    exitLoopMode(true)
  }
  navActiveView.value = 'overview'
  showOverviewToast.value = false
  if (overviewToastTimer) clearTimeout(overviewToastTimer)
  currentView.value = 'overview'
}

// --- E3 Layout handlers ---
// [perf] summaries 已按 start_time 升序维护（见 SSE 顺序插入逻辑），
// 底部条需要最新的在前（window_id 降序 ≈ start_time 降序），直接 reverse 即可，
// O(n) 代替 O(n log n) 的 sort。
const sortedSummaries = computed(() => {
  return localizedSummaries.value.slice().reverse()
})

const eventNodeDisplayWindowIds = (node) => {
  return Array.isArray(node?.window_ids)
    ? node.window_ids.filter(id => Number.isFinite(Number(id))).map(id => Number(id) + 1)
    : []
}

const EVENT_INSTRUMENT_RE = '(?:抓钳|钛夹钳|施夹钳|施夹器|剪刀|电剪|电钩|冲洗器|吸引器|冲吸器|双极电凝|双极|器械|钛夹)'
const EVENT_NON_CLIP_INSTRUMENT_RE = '(?:抓钳|剪刀|电剪|电钩|冲洗器|吸引器|冲吸器|双极电凝|双极|器械)'
const EVENT_SIGNAL_RE = /(牵拉|暴露|分离|剥离|剪切|切断|夹闭|闭合|施夹|胆囊|胆囊管|胆囊动脉|管状结构|肝床|肝胆三角|CVS|清理|冲洗|吸引|装袋|取出|穿刺|穿入|穿孔|出血|止血|渗血|凝血|视野|起雾|雾|烟雾|模糊|镜头|体外|手术室|腹腔外|套管口|腹壁外)/
const EVENT_RISK_RE = /(CVS未达成|安全视野|剪刀|scissors|大量(?:活动性)?出血|活动性出血|明显出血|持续出血|出血点|出血|止血|渗血|凝血|无活动性出血|未见活动性出血|bleeding|hemostasis)/i
const SCISSORS_ACTIVITY_RE = /(?:剪刀|电剪|scissors).{0,20}(?:出现|可见|进入|操作|接触|靠近|分离|剪切|剪断|切断)|(?:剪切|剪断|切断).{0,16}(?:胆囊管|胆囊动脉|cystic duct|cystic artery)/i
const FOG_ACTIVE_RE = /(镜头)?(?:起雾|雾气|烟雾|烟雾弥漫|水汽|模糊|视野不清|视野受遮挡|视野受限|fogging|foggy|fog (?:obscures|obscured|limits|blocks)|smoke|smoky|haze|hazy|blur(?:red|ry)?|obscur(?:ed|ing))/i
const FOG_RESOLVED_RE = /(雾(?:已|已经)?(?:去除|清除|消散|解除)|烟雾(?:已|已经)?(?:清除|消散)|视野(?:恢复|转为|变得)(?:清晰|可辨)|镜头(?:恢复|转为|变得)清晰|fog (?:cleared|resolved)|smoke (?:cleared|resolved)|view (?:restored|clear))/i
const OUT_OF_BODY_RE = /(镜头|腹腔镜|视野|画面).{0,12}(?:移出体外|退出体外|离开腹腔|腹腔外|套管口|腹壁外|切换至手术室|手术室场景)|(?:体外|腹腔外|套管口|腹壁外|手术室场景|器械台|trocar|trocar outside|outside the body|outside-body|extracorporeal|operating room scene|extra-abdominal)/i

const compactEventNodeText = (text, maxChars = 120) => {
  let out = cleanUserSummaryText(text)
    .replace(/^【[^】]*】\s*/, '')
    .replace(/Hem[-\s]?o[-\s]?lok|Hemolok|hemlock/gi, 'Hem-o-lok')
    .replace(/(?:当前)?(?:可见|见|视野中可见)(?:钛夹钳|施夹钳|施夹器)(?:正在)?对/g, '钛夹钳对')
    .replace(/(?:当前)?(?:可见|见|视野中可见)(?:钛夹钳|施夹钳|施夹器)(?:正在)?在/g, '钛夹钳在')
    .replace(/使用(?:钛夹钳|施夹钳|施夹器)进行/g, '使用钛夹钳进行')
    .replace(/(?:钛夹钳|施夹钳|施夹器)对/g, '钛夹钳对')
    .replace(new RegExp(`(?:当前)?(?:可见|见|视野中可见)${EVENT_NON_CLIP_INSTRUMENT_RE}(?:、${EVENT_NON_CLIP_INSTRUMENT_RE})*[，,。；;]?`, 'g'), '')
    .replace(new RegExp(`(?:${EVENT_NON_CLIP_INSTRUMENT_RE})进入视野[，,]?\\s*`, 'g'), '')
    .replace(new RegExp(`(?:${EVENT_NON_CLIP_INSTRUMENT_RE})在([^，。；;]*?)(?:完成|进行)?(?:夹闭|关闭|闭合)处理`, 'g'), '在$1进行夹闭处理')
    .replace(new RegExp(`(?:${EVENT_NON_CLIP_INSTRUMENT_RE})在([^，。；;]*?)(?:完成|进行)?(?:夹闭|关闭|闭合)动作`, 'g'), '在$1进行夹闭处理')
    .replace(new RegExp(`(?:${EVENT_NON_CLIP_INSTRUMENT_RE})在([^，。；;]+)[，,]`, 'g'), '在$1，')
    .replace(new RegExp(`(?:${EVENT_NON_CLIP_INSTRUMENT_RE})对([^，。；;]+)[，,]`, 'g'), '对$1，')
    .replace(/使用(?:抓钳|器械)?持续?牵拉/g, '牵拉')
    .replace(/夹持牵拉/g, '牵拉')
    .replace(/使用(?:冲洗器|吸引器|冲吸器)持续/g, '持续')
    .replace(/使用(?:冲洗器|吸引器|冲吸器)进行/g, '进行')
    .replace(/使用(?:钛夹钳|施夹钳|施夹器)进行/g, '使用钛夹钳进行')
    .replace(/(?:钛夹钳|施夹钳|施夹器)对/g, '钛夹钳对')
    .replace(/在([^，。；;]+)[，,]\s*进行了?(?:夹闭|关闭|闭合)动作/g, '在$1进行夹闭处理')
    .replace(/进行了?(?:夹闭|关闭|闭合)动作/g, '完成夹闭处理')
    .replace(/(?:夹闭|关闭|闭合)了?组织/g, '完成夹闭处理')
    .replace(/已被多枚(?:金属)?钛夹(?:夹闭|关闭|闭合)(并切断)?的管状结构残端/g, '多枚钛夹已夹闭$1的胆囊管残端')
    .replace(/多枚(?:金属)?钛夹(?:夹闭|关闭|闭合)(并切断)?的管状结构残端/g, '多枚钛夹已夹闭$1的胆囊管残端')
    .replace(/视野中可见|可见/g, '')
    .replace(/\s+/g, ' ')
    .trim()

  const sentences = out
    .split(/(?<=[。；！？!?;])/)
    .map(s => s.replace(/^[，,。；;\s]+|[，,。；;\s]+$/g, '').trim())
    .filter(s => s && EVENT_SIGNAL_RE.test(s))

  const risks = sentences.filter(s => EVENT_RISK_RE.test(s))
  const actions = sentences.filter(s => !EVENT_RISK_RE.test(s))
  const selected = [...risks, ...actions].slice(0, 2)
  out = (selected.length ? selected : sentences.slice(0, 1))
    .map(s => /[。；！？!?;]$/.test(s) ? s : `${s}。`)
    .join('')

  return (out || cleanUserSummaryText(text)).slice(0, maxChars)
}

const relatedSummariesForEventNode = (node, representativeId = null, windowIds = null) => {
  const ids = Array.isArray(windowIds)
    ? windowIds.map(id => Number(id)).filter(id => Number.isFinite(id))
    : (Array.isArray(node?.window_ids)
      ? node.window_ids.map(id => Number(id)).filter(id => Number.isFinite(id))
      : [])
  const rep = Number.isFinite(Number(representativeId))
    ? Number(representativeId)
    : (Number.isFinite(Number(node?.representative_window_id))
      ? Number(node.representative_window_id)
      : (ids.length ? Math.max(...ids) : -1))

  const orderedIds = [...new Set([
    rep,
    ...ids.slice().sort((a, b) => Math.abs(a - rep) - Math.abs(b - rep)),
  ].filter(id => Number.isFinite(id) && id >= 0))]

  const direct = orderedIds
    .map(id => summaries.value.find(s => Number(s.window_id) === id))
    .filter(Boolean)
  if (direct.length) return direct

  const start = Number(node?.start_time)
  const end = Number(node?.end_time)
  if (Number.isFinite(start) && Number.isFinite(end)) {
    return summaries.value
      .filter(s => Number(s.end_time ?? 0) >= start && Number(s.start_time ?? 0) <= end)
      .slice(0, 4)
  }
  return []
}

const normalizeEventNode = (node, index) => {
  const windowIds = Array.isArray(node?.window_ids)
    ? node.window_ids.map(id => Number(id)).filter(id => Number.isFinite(id))
    : []
  const representativeId = Number.isFinite(Number(node?.representative_window_id))
    ? Number(node.representative_window_id)
    : (windowIds.length ? Math.max(...windowIds) : -1)

  const relatedSummaries = relatedSummariesForEventNode(node, representativeId, windowIds)
  relatedSummaries.slice(0, 4).forEach((summary, idx) => enqueueBottomThumbnail(summary, idx === 0))
  const relatedSummary = relatedSummaries[0] || null
  const normalizedWindowIds = windowIds.length
    ? windowIds
    : (representativeId >= 0 ? [representativeId] : [])

  return {
    id: String(node?.id || `event_${index + 1}`),
    type: String(node?.type || 'other'),
    severity: String(node?.severity || 'normal'),
    title: String(node?.title || t('app.keyEventNode')).trim(),
    summary: compactEventNodeText(node?.summary || relatedSummary?.summary || ''),
    window_ids: normalizedWindowIds,
    representative_window_id: representativeId,
    start_time: Number(node?.start_time ?? relatedSummary?.start_time ?? 0),
    end_time: Number(node?.end_time ?? relatedSummary?.end_time ?? 0),
    confidence: Number.isFinite(Number(node?.confidence)) ? Number(node.confidence) : null,
    source: node?.source || 'llm',
  }
}

const cvsStatusNode = computed(() => {
  const cvsSummaries = summaries.value.filter(s => CVS_RELEVANT_RE.test(String(s.summary || '')))
  if (!cvsSummaries.length) return null

  const achieved = cvsSummaries.some(s => CVS_ACHIEVED_RE.test(String(s.summary || '')))
  const first = cvsSummaries[0]
  const latest = cvsSummaries[cvsSummaries.length - 1]
  const windowIds = cvsSummaries
    .map(s => Number(s.window_id))
    .filter(id => Number.isFinite(id))

  if (!windowIds.length) return null
  const representativeWindowId = Number(latest.window_id)
  const latestEnd = Number(latest.end_time ?? latest.window_end ?? 0)
  const endTime = achieved
    ? latestEnd
    : Math.max(latestEnd, Number(currentTime.value || 0))

  return {
    id: 'event_cvs_status',
    type: 'cvs',
    severity: achieved ? 'resolved' : 'critical',
    title: achieved ? 'CVS已达成' : 'CVS尚未达成',
    summary: achieved
      ? 'CVS三要素已确认，可作为夹闭/剪断前的安全节点。'
      : 'CVS评估中：尚未确认三要素，夹闭或剪断胆囊管和胆囊动脉前需继续核查。',
    window_ids: [...new Set(windowIds)],
    representative_window_id: representativeWindowId,
    start_time: Number(first.start_time ?? first.window_start ?? 0),
    end_time: endTime,
    confidence: 0.8,
    source: 'cvs-status',
    pinned: true,
  }
})

const summaryScissorsFlags = (summary) => {
  const text = `${summary?.summary || ''} ${summary?.summary_en || ''}`
  const visual = summary?.others?.visual_gpt || summary?.others?.experts?.open_vlm?.visual || {}
  const scissors = visual?.scissors || {}
  const confidence = Number(scissors.confidence || 0)
  const visualScissors = (
    (Boolean(scissors.visible) || Boolean(scissors.cutting))
    && (!confidence || confidence >= 0.35)
  )
  return visualScissors || SCISSORS_ACTIVITY_RE.test(text)
}

const scissorsBeforeCvsNode = computed(() => {
  if (!cvsStatusNode.value || cvsStatusNode.value.severity !== 'critical') return null
  const scissorsSummaries = summaries.value.filter(summaryScissorsFlags)
  if (!scissorsSummaries.length) return null

  const first = scissorsSummaries[0]
  const latest = scissorsSummaries[scissorsSummaries.length - 1]
  const windowIds = scissorsSummaries
    .map(s => Number(s.window_id))
    .filter(id => Number.isFinite(id))
  if (!windowIds.length) return null

  const zh = language.value !== 'en'
  return {
    id: 'event_scissors_before_cvs',
    type: 'risk',
    severity: 'critical',
    title: zh ? 'CVS未达成时出现剪刀操作' : 'Scissors before CVS confirmation',
    summary: zh
      ? '检测到剪刀操作，但CVS尚未达成，剪断胆囊管或胆囊动脉前需继续核查。'
      : 'Scissors activity is detected before CVS confirmation; verify safety before dividing the cystic duct or artery.',
    window_ids: [...new Set(windowIds)],
    representative_window_id: Number(latest.window_id),
    start_time: Number(first.start_time ?? first.window_start ?? 0),
    end_time: Number(latest.end_time ?? latest.window_end ?? 0),
    confidence: 0.76,
    source: 'scissors-cvs-status',
    pinned: true,
  }
})

const summaryVisibilityFlags = (summary) => {
  const text = `${summary?.summary || ''} ${summary?.summary_en || ''}`
  const visual = summary?.others?.visual_gpt || summary?.others?.experts?.open_vlm?.visual || {}
  const visibility = visual?.visibility || {}
  const status = String(visibility.status || '').toLowerCase()
  const confidence = Number(visibility.confidence || 0)
  const trusted = !confidence || confidence >= 0.35
  return {
    fogActive: trusted && (
      Boolean(visibility.fog)
      || ['foggy', 'blurred', 'blocked', 'smoke', 'smoky', 'hazy'].includes(status)
      || (FOG_ACTIVE_RE.test(text) && !FOG_RESOLVED_RE.test(text))
    ),
    fogResolved: trusted && (
      Boolean(visibility.fog_cleared || visibility.cleared || visibility.resolved)
      || ['clear_after_fog', 'fog_cleared'].includes(status)
      || FOG_RESOLVED_RE.test(text)
    ),
    outOfBody: trusted && (
      Boolean(visibility.out_of_body || visibility.outside_body)
      || status === 'out_of_body'
      || OUT_OF_BODY_RE.test(text)
    ),
    confidence: confidence || null,
  }
}

const fogStatusNode = computed(() => {
  let fogActive = false
  let firstActive = null
  let latestActive = null
  let latestResolved = null
  const windowIds = []

  summaries.value
    .slice()
    .sort((a, b) => Number(a.window_id ?? 0) - Number(b.window_id ?? 0))
    .forEach((summary) => {
      const flags = summaryVisibilityFlags(summary)
      const wid = Number(summary.window_id)
      if (flags.fogActive) {
        if (!fogActive) firstActive = summary
        fogActive = true
        latestActive = summary
        if (Number.isFinite(wid)) windowIds.push(wid)
      }
      if (flags.fogResolved) {
        latestResolved = summary
        fogActive = false
        if (Number.isFinite(wid)) windowIds.push(wid)
      }
    })

  if (!firstActive && !latestResolved) return null
  const latest = fogActive ? latestActive : latestResolved
  if (!latest) return null
  const ids = [...new Set(windowIds)]
  const zh = language.value !== 'en'
  return {
    id: 'event_fog_status',
    type: 'visibility',
    severity: fogActive ? 'critical' : 'resolved',
    title: fogActive
      ? (zh ? '视野起雾' : 'Fog obscures view')
      : (zh ? '雾已去除' : 'Fog cleared'),
    summary: fogActive
      ? (zh ? '镜头起雾，手术视野受遮挡。' : 'Lens fogging obscures the surgical field.')
      : (zh ? '雾已去除，腹腔视野恢复。' : 'The fog has cleared and the laparoscopic view is restored.'),
    window_ids: ids.length ? ids : [Number(latest.window_id)],
    representative_window_id: Number(latest.window_id),
    start_time: Number((firstActive || latest).start_time ?? (firstActive || latest).window_start ?? 0),
    end_time: fogActive
      ? Math.max(Number(latest.end_time ?? latest.window_end ?? 0), Number(currentTime.value || 0))
      : Number(latest.end_time ?? latest.window_end ?? 0),
    confidence: 0.78,
    source: 'visibility-status',
    pinned: true,
  }
})

const isFogVisibilityNode = (node) => {
  if (node?.type !== 'visibility') return false
  return FOG_ACTIVE_RE.test(`${node.title || ''} ${node.summary || ''}`)
    || FOG_RESOLVED_RE.test(`${node.title || ''} ${node.summary || ''}`)
}

const sortedEventNodes = computed(() => {
  const nodes = [
    ...(scissorsBeforeCvsNode.value ? [scissorsBeforeCvsNode.value] : []),
    ...(cvsStatusNode.value ? [cvsStatusNode.value] : []),
    ...(fogStatusNode.value ? [fogStatusNode.value] : []),
    ...eventNodes.value.filter(node => {
      if (scissorsBeforeCvsNode.value && /CVS未达成时出现剪刀操作|Scissors before CVS/i.test(`${node.title || ''} ${node.summary || ''}`)) return false
      if (cvsStatusNode.value && node.type === 'cvs') return false
      if (fogStatusNode.value && isFogVisibilityNode(node)) return false
      return true
    }),
  ]
  return nodes
    .slice()
    .sort((a, b) => {
      if (a.pinned && !b.pinned) return -1
      if (!a.pinned && b.pinned) return 1
      const bt = Number(b.start_time ?? 0)
      const at = Number(a.start_time ?? 0)
      if (bt !== at) return bt - at
      return Number(b.representative_window_id ?? 0) - Number(a.representative_window_id ?? 0)
    })
})

const bottomEmptyText = computed(() => {
  if (bottomViewMode.value === 'events') {
    if (eventNodesLoading.value) return t('app.eventLoading')
    if (eventNodesError.value) return t('app.eventFailed')
    return isProcessing.value ? t('app.eventLoading') : t('app.eventEmpty')
  }
  return isProcessing.value ? t('app.historyLoading') : t('app.historyEmpty')
})

const setBottomViewMode = (modeName) => {
  bottomViewMode.value = modeName === 'windows' ? 'windows' : 'events'
  localStorage.setItem('surg_bottom_view_mode', bottomViewMode.value)
  if (bottomViewMode.value === 'events') {
    resetEventNodeScroll()
    scheduleEventNodesRefresh(150)
  }
}

const resetEventNodeScroll = () => {
  nextTick(() => {
    if (bottomViewMode.value !== 'events') return
    const el = document.querySelector('.event-node-scroll')
    if (el) el.scrollLeft = 0
  })
}

const scheduleEventNodesRefresh = (delay = 2500) => {
  if (replayMode.value) return
  if (!currentSession.value || summaries.value.length < 1) return
  if (eventNodesTimer) {
    clearTimeout(eventNodesTimer)
  }
  eventNodesTimer = setTimeout(() => {
    eventNodesTimer = null
    requestEventNodes()
  }, delay)
}

const requestEventNodes = async ({ force = false } = {}) => {
  if (replayMode.value) {
    syncReplayTimeline(currentTime.value, { forceFinal: currentTime.value >= duration.value - 0.04 })
    return
  }
  const sid = currentSession.value?.session_id
  if (!sid || summaries.value.length < 1) return
  if (eventNodesLoading.value && !force) {
    eventNodesRefreshPending = true
    return
  }

  const seq = ++eventNodesRequestSeq
  eventNodesLoading.value = true
  eventNodesError.value = ''
  try {
    const res = await axios.post(`/api/analysis/event-nodes/${sid}`, {
      language: language.value,
      force,
      max_windows: 120,
    }, {
      signal: getSessionSignal(),
      timeout: 45000,
    })
    if (seq !== eventNodesRequestSeq || currentSession.value?.session_id !== sid) return
    const nodes = Array.isArray(res.data?.events) ? res.data.events : []
    eventNodes.value = nodes.map(normalizeEventNode).filter(node => node.window_ids.length > 0)
    resetEventNodeScroll()
    eventNodesError.value = res.data?.source === 'fallback' && !eventNodes.value.length
      ? (res.data?.error || 'event-node fallback empty')
      : ''
  } catch (error) {
    if (axios.isCancel(error) || error.name === 'AbortError') return
    console.warn('[EventNodes] refresh failed:', error.message)
    if (seq !== eventNodesRequestSeq) return
    eventNodesError.value = error.message || 'failed'
  } finally {
    if (seq === eventNodesRequestSeq) {
      eventNodesLoading.value = false
      if (eventNodesRefreshPending) {
        eventNodesRefreshPending = false
        scheduleEventNodesRefresh(500)
      }
    }
  }
}

const eventNodeTypeLabel = (type) => {
  const key = {
    phase: 'event.phase',
    cvs: 'event.cvs',
    action: 'event.action',
    risk: 'event.risk',
    resolution: 'event.resolution',
    visibility: 'event.visibility',
    other: 'event.other',
  }[type] || 'event.other'
  return t(key)
}

const eventNodeWindowLabel = (node) => {
  const displayIds = eventNodeDisplayWindowIds(node)
  if (!displayIds.length) return ''
  const start = Math.min(...displayIds)
  const end = Math.max(...displayIds)
  return start === end ? bottomWindowLabel(start) : t('app.windowRange', { start, end })
}

const eventNodeTimeLabel = (node) => {
  const start = formatWindowTime(node?.start_time)
  const end = formatWindowTime(node?.end_time)
  return start === end || end === '--:--' ? start : `${start}-${end}`
}

const eventNodeRepresentativeSummary = (node) => {
  const wid = Number(node?.representative_window_id)
  return summaries.value.find(s => s.window_id === wid)
    || summaries.value.find(s => node?.window_ids?.includes(s.window_id))
    || null
}

const eventNodeThumbnailSummary = (node) => {
  const related = relatedSummariesForEventNode(node)
  const representative = eventNodeRepresentativeSummary(node) || related[0] || null
  if (representative) {
    const retries = Number(bottomThumbRetries[representative.window_id] || 0)
    if (bottomThumbnails[representative.window_id] || retries < 2) return representative
  }
  return related.find(summary => bottomThumbnails[summary.window_id])
    || representative
}

const eventNodeThumbnail = (node) => {
  const summary = eventNodeThumbnailSummary(node)
  return summary ? bottomThumbnails[summary.window_id] : ''
}

const eventNodeThumbLoading = (node) => {
  const related = relatedSummariesForEventNode(node)
  return related.some(summary => bottomThumbLoading[summary.window_id] || bottomThumbQueued.has(summary.window_id))
}

const handleEventNodeThumbError = (node, event = null) => {
  const summary = eventNodeThumbnailSummary(node)
  if (summary) {
    handleBottomThumbError(summary, event)
    return
  }
  relatedSummariesForEventNode(node).slice(0, 3).forEach(summary => handleBottomThumbError(summary))
}

const handleEventNodeClick = (node) => {
  if (bottomScrollClickSuppressed.value) return
  const targetWindow = Number.isFinite(Number(node?.representative_window_id))
    ? Number(node.representative_window_id)
    : (Array.isArray(node?.window_ids) && node.window_ids.length ? Math.max(...node.window_ids) : -1)
  if (targetWindow < 0) return
  selectedWindowId.value = targetWindow
  rightPanelTab.value = 'analysis'
  handleSeekToWindow(targetWindow)
}

const requestSummaryTranslation = async (summary) => {
  const s = cleanSummaryPayload(summary)
  if (!s?.summary || s.summary_en || !currentSession.value) return
  const key = `${currentSession.value.session_id}:${s.window_id}:en`
  if (translationInFlight.has(key)) return
  translationInFlight.add(key)
  try {
    const res = await axios.post('/api/analysis/translate-summary', {
      text: s.summary,
      target_lang: 'en',
    }, {
      signal: getSessionSignal(),
      timeout: 20000,
    })
    if (res.data?.text) {
      const idx = summaries.value.findIndex(item => item.window_id === s.window_id)
      if (idx >= 0) {
        summaries.value[idx] = {
          ...summaries.value[idx],
          summary_en: cleanUserSummaryText(res.data.text),
        }
      }
    }
  } catch (error) {
    if (!axios.isCancel(error) && error.name !== 'AbortError') {
      console.debug('[Translate] summary translation failed:', error.message)
    }
  } finally {
    translationInFlight.delete(key)
  }
}

const ensureEnglishSummaries = () => {
  if (language.value !== 'en') return
  const ordered = [
    ...summaries.value.slice().reverse(),
  ].filter(s => s?.summary && !s.summary_en)
  ordered.slice(0, 24).forEach((s, index) => {
    setTimeout(() => requestSummaryTranslation(s), index * 250)
  })
}

const refreshCurrentWindowSummary = async (time) => {
  if (replayMode.value) return
  if (!currentSession.value || loopWindow.value) return
  const now = performance.now()
  const windowId = Math.floor(time / windowDuration.value)
  const alreadyLoaded = summaries.value.some(s => s.window_id === windowId && s.summary)
  if (alreadyLoaded && lastSummaryRefreshWindow === windowId) return
  if (now - lastSummaryRefreshAt < 1200) return

  lastSummaryRefreshAt = now
  lastSummaryRefreshWindow = windowId
  try {
    const res = await axios.get(
      `/api/analysis/window-summary-at-timestamp/${currentSession.value.session_id}`,
      { params: { timestamp: time }, signal: getSessionSignal() }
    )
    if (res.data?.success && res.data.window_id != null) {
      upsertSummary(res.data)
    }
  } catch (error) {
    if (!axios.isCancel(error) && error.name !== 'AbortError') {
      console.debug('[SummaryRefresh] current window summary not ready:', error.message)
    }
  }
}

const videoSectionMinHeight = computed(() => {
  return Math.max(360, viewportHeight.value - bottomStripHeight.value - 190)
})

const startRightPanelResize = (event) => {
  event.preventDefault()
  event.currentTarget.setPointerCapture?.(event.pointerId)
  const startX = event.clientX
  const startWidth = rightPanelWidth.value
  document.body.classList.add('is-resizing-panel')

  const onMove = (e) => {
    rightPanelWidth.value = clamp(startWidth - (e.clientX - startX), minRightPanelWidth(), maxRightPanelWidth())
  }
  const onUp = () => {
    localStorage.setItem(RIGHT_PANEL_STORAGE_KEY, String(rightPanelWidth.value))
    document.body.classList.remove('is-resizing-panel')
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }

  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp, { once: true })
}

const startVideoResize = (event) => {
  event.preventDefault()
  event.currentTarget.setPointerCapture?.(event.pointerId)
  const startY = event.clientY
  const startHeight = bottomStripHeight.value
  document.body.classList.add('is-resizing-video')

  const onMove = (e) => {
    bottomStripHeight.value = clamp(startHeight - (e.clientY - startY), minBottomStripHeight(), maxBottomStripHeight())
  }
  const onUp = () => {
    localStorage.setItem(BOTTOM_STRIP_STORAGE_KEY, String(bottomStripHeight.value))
    document.body.classList.remove('is-resizing-video')
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }

  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp, { once: true })
}

const resetRightPanelWidth = () => {
  rightPanelWidth.value = defaultRightPanelWidth()
  localStorage.setItem(RIGHT_PANEL_STORAGE_KEY, String(rightPanelWidth.value))
}

const resetBottomStripHeight = () => {
  bottomStripHeight.value = defaultBottomStripHeight()
  localStorage.setItem(BOTTOM_STRIP_STORAGE_KEY, String(bottomStripHeight.value))
}

const handleViewportResize = () => {
  viewportWidth.value = window.innerWidth
  viewportHeight.value = window.innerHeight
  rightPanelWidth.value = clamp(rightPanelWidth.value, minRightPanelWidth(), maxRightPanelWidth())
  bottomStripHeight.value = clamp(bottomStripHeight.value, minBottomStripHeight(), maxBottomStripHeight())
}

const formatWindowTime = (seconds) => {
  if (seconds == null || !isFinite(seconds)) return '--:--'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

const CN_DIGITS = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九']
const toChineseNumeral = (n) => {
  if (n == null || !isFinite(n)) return ''
  const num = Math.floor(n)
  if (num < 0) return String(num)
  if (num < 10) return CN_DIGITS[num]
  if (num === 10) return '十'
  if (num < 20) return '十' + CN_DIGITS[num - 10]
  if (num < 100) {
    const tens = Math.floor(num / 10)
    const ones = num % 10
    return CN_DIGITS[tens] + '十' + (ones === 0 ? '' : CN_DIGITS[ones])
  }
  return String(num)
}

const bottomWindowLabel = (n) => {
  return language.value === 'zh'
    ? `${t('app.windowPrefix')}${toChineseNumeral(n)}`
    : `${t('app.windowPrefix')} ${n}`
}

const handleNavigation = (view) => {
  navActiveView.value = view
  if (view === 'analysis') {
    rightPanelTab.value = 'analysis'
  } else if (view === 'chat') {
    rightPanelTab.value = 'chat'
  } else if (view === 'overview') {
    enterOverview()
  } else if (view === 'report') {
    navActiveView.value = 'report'
    currentView.value = 'report'
  } else if (view === 'home') {
    goHome()
  }
}

const handleReportBack = () => {
  currentView.value = 'main'
  navActiveView.value = 'analysis'
}

const toggleAnalysis = () => {
  if (isProcessing.value) {
    stopAnalysis()
  } else {
    startAnalysis()
  }
}

const handleBottomCardClick = (summary) => {
  if (bottomScrollClickSuppressed.value) return
  selectedWindowId.value = summary.window_id
  rightPanelTab.value = 'analysis'
  // Also seek to this window
  handleSeekToWindow(summary.window_id)
}

const clearBottomScrollDragHandlers = () => {
  if (bottomScrollMoveHandler) {
    window.removeEventListener('pointermove', bottomScrollMoveHandler)
    bottomScrollMoveHandler = null
  }
  if (bottomScrollUpHandler) {
    window.removeEventListener('pointerup', bottomScrollUpHandler)
    window.removeEventListener('pointercancel', bottomScrollUpHandler)
    bottomScrollUpHandler = null
  }
}

const startBottomScrollDrag = (event) => {
  if (event.button !== undefined && event.button !== 0) return
  const el = bottomScrollRef.value
  if (!el) return

  const startX = event.clientX
  const startScrollLeft = el.scrollLeft
  bottomScrollPointerId = event.pointerId
  bottomScrollDragging.value = true
  bottomScrollClickSuppressed.value = false
  el.setPointerCapture?.(event.pointerId)

  bottomScrollMoveHandler = (moveEvent) => {
    const dx = moveEvent.clientX - startX
    if (Math.abs(dx) > 4) {
      bottomScrollClickSuppressed.value = true
      moveEvent.preventDefault()
    }
    el.scrollLeft = startScrollLeft - dx
  }

  bottomScrollUpHandler = () => {
    bottomScrollDragging.value = false
    el.releasePointerCapture?.(bottomScrollPointerId)
    clearBottomScrollDragHandlers()
    setTimeout(() => {
      bottomScrollClickSuppressed.value = false
    }, 0)
  }

  window.addEventListener('pointermove', bottomScrollMoveHandler, { passive: false })
  window.addEventListener('pointerup', bottomScrollUpHandler)
  window.addEventListener('pointercancel', bottomScrollUpHandler)
}

const handleOverviewBack = () => {
  stopDetachedPlaybackClock()
  currentView.value = 'main'
  navActiveView.value = 'analysis'
  playbackResumeNonce.value += 1
}

const handleOverviewSeekToWindow = (windowId) => {
  stopDetachedPlaybackClock()
  currentView.value = 'main'
  navActiveView.value = 'analysis'
  playbackResumeNonce.value += 1
  handleSeekToWindow(windowId)
}

// Stream timer for live video elapsed time
const startStreamTimer = () => {
  if (mode.value !== 'stream') return
  
  // Clear any existing timer
  stopStreamTimer()
  // Reset stream ended state
  streamEnded.value = false
  streamWasActive.value = false

  const fixedDuration = isSimulatorSession.value ? Number(currentSession.value?.duration || 0) : 0
  if (isSimulatorSession.value) {
    // The simulator preview is rendered by the native <video> element, so its
    // currentTime is the only playback clock. Do not also write currentTime
    // from a wall-clock timer; doing both makes the UI flip between adjacent
    // seconds/windows near boundaries.
    if (fixedDuration > 0) {
      duration.value = fixedDuration
    }
    streamTimerInterval = setInterval(() => {
      if (!isPlaying.value || streamEnded.value || loopWindow.value) return
      const endAt = Number(currentSession.value?.duration || duration.value || 0)
      if (endAt > 0 && currentTime.value >= endAt - 0.25) {
        currentTime.value = endAt
        livePlaybackTime.value = endAt
        handleStreamEnded()
      }
    }, 500)
    startStreamEndCheck()
    return
  }

  // [perf] 原来每 100ms 更新 currentTime，会驱动 ControlBar 的 progressPercent /
  // windowCount / currentSummary 等一串 computed 跟着重算，与 MJPEG 解码争主线程。
  // 直播的时间显示精度到 250ms 肉眼无感（秒级时间戳），但 CPU 负载显著降低。
  streamTimerInterval = setInterval(() => {
    // Don't update time if in loop playback mode (VideoPlayer handles time updates)
    if (loopWindow.value) return
    
    if (!isPlaying.value || !streamStartTime.value || streamEnded.value) return
    
    // Calculate elapsed time since stream started
    const elapsed = (Date.now() - streamStartTime.value) / 1000
    
    // Ensure elapsed time is never negative (can happen if server/client clocks are out of sync)
    const safeElapsed = Math.max(0, elapsed)
    const displayTime = safeElapsed
    currentTime.value = displayTime
    livePlaybackTime.value = displayTime
    
    // Also update duration for display purposes
    duration.value = fixedDuration > 0 ? fixedDuration : safeElapsed

    if (fixedDuration > 0 && safeElapsed >= fixedDuration) {
      handleStreamEnded()
    }
  }, 250)  // 直播时间显示刷新：250ms 足够，避免主线程与视频解码争用
  
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
      // Proxy streams go through our backend (localhost:5133 or localhost:8001) and don't have /info endpoint
      // Direct streams (e.g., localhost:9001) from stream_simulator have /info endpoint
      const isProxyStream = urlObj.pathname.includes('/api/video/') || 
                            urlObj.pathname.includes('/mjpeg-proxy/') ||
                            urlObj.host.includes(':5133') ||  // Vite dev server
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
    
    // Do not infer stream end from a stale renderer clock. MJPEG/local capture
    // can briefly stop advancing while frame capture or analysis catches up,
    // and treating that as end-of-stream pauses the live demo mid-video. The
    // simulator /info.video_ended flag above is the authoritative end signal.
    if (!inLoopMode && isPlaying.value && currentTime.value > 30) {
      if (Math.abs(currentTime.value - lastKnownTime) >= 0.1) {
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

  // A finite capture-card simulation has a real EOF. Its frame writer may
  // stop just before the duration boundary (for example at 99.5s for a 100s
  // source), while the summarizer still needs to flush the final 95-100s
  // window. Treating this as a user cancellation used to discard that tail.
  // Keep the analysis/SSE alive and let the backend mark it completed after
  // all persisted capture frames have been summarized.
  if (isSimulatorSession.value && currentSession.value) {
    const endAt = Number(currentSession.value.duration || duration.value || currentTime.value || 0)
    if (endAt > 0) {
      currentTime.value = endAt
      livePlaybackTime.value = endAt
      axios.post(`/api/video/control/${currentSession.value.session_id}`, {
        action: 'position',
        position: endAt
      }).catch((error) => {
        console.warn('[Stream] Failed to persist simulator EOF position:', error)
      })
    }
    console.log(`[Stream] Simulator reached EOF at ${currentTime.value.toFixed(1)}s; waiting for final analysis window`)
    return
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
    if (!loopWindow.value) {
      loopReturnTime.value = currentTime.value
      livePlaybackTime.value = currentTime.value
      loopWasPlaying.value = isPlaying.value
      startDetachedPlaybackClock()
    }
    // Set up loop playback for this window
    loopWindow.value = {
      window_id: windowId,
      start_time: summary.start_time,
      end_time: summary.end_time
    }
    console.log(`[Loop] Enabled loop playback for window ${windowId}: ${summary.start_time}s - ${summary.end_time}s`)    
    // Seek only the frontend preview. Do not call backend seek here; history
    // loop playback is a temporary review mode and must not alter the live
    // analysis/playback clock.
    if (!isPlaying.value) {
      handlePlay()
    }
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
  const isLoopBar = target.closest('.loop-indicator-bar')
  
  if (!isProgressBar && !isControlBtn && !isSummaryPanel && !isLoopBar) {
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

watch(
  () => [language.value, summaries.value.length],
  () => {
    ensureEnglishSummaries()
    scheduleEventNodesRefresh(language.value === 'en' ? 250 : 800)
  }
)

// Check all service statuses
const checkAnalysisServices = async () => {
  // Check all services in parallel with individual timing
  const checks = [
    // SurgR1
    refreshSurgr1Status(),
    
    // GLM
    refreshGlmStatus(),
    
    // ASR
    axios.get('/api/voice/asr/status', { timeout: 5000 })
      .then(res => { asrStatus.value = { available: res.data.available, checking: false } })
      .catch(() => { asrStatus.value = { available: false, checking: false } }),
    
    // TTS - This was causing 60s timeout!
    axios.get('/api/voice/tts/status', { timeout: 5000 })
      .then(res => { ttsStatus.value = { available: res.data.available, checking: false } })
      .catch(() => { ttsStatus.value = { available: false, checking: false } })
  ]
  sam3Status.value = { available: false, checking: false }
  
  await Promise.allSettled(checks)
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
  if (currentSession.value && !replayMode.value) {
    navigator.sendBeacon(
      apiUrl(`/api/analysis/stop-surgr1-continuous/${currentSession.value.session_id}`),
      ''
    )
  }
}

onMounted(async () => {
  // 首先获取配置
  await fetchConfig()

  try {
    await loadOfflineReplayFromQuery()
  } catch (error) {
    console.error('[Replay] Startup failed:', error)
    alert(`离线分析回放加载失败: ${error.message}`)
  }
  
  checkAnalysisServices()
  // Refresh status every 30 seconds
  analysisStatusInterval = setInterval(checkAnalysisServices, 30000)
  
  // Add beforeunload handler for reliable cleanup
  window.addEventListener('beforeunload', handleBeforeUnload)
  window.addEventListener('resize', handleViewportResize)
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
  if (currentSession.value && !replayMode.value) {
    navigator.sendBeacon(
      apiUrl(`/api/analysis/stop-surgr1-continuous/${currentSession.value.session_id}`),
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
  if (overviewToastTimer) {
    clearTimeout(overviewToastTimer)
  }
  clearEventNodes()
  clearBottomScrollDragHandlers()
  
  // Remove beforeunload handler
  window.removeEventListener('beforeunload', handleBeforeUnload)
  window.removeEventListener('resize', handleViewportResize)
})
</script>

<style scoped>
/* E3 Layout: NavRail + Main Wrapper */
.app-container {
  display: flex;
  /* 100vh 而非 min-height：整个 app 锁在视口内，底部"历史窗口分析"条不再掉到屏幕下方 */
  height: 100vh;
  overflow: hidden;
}

.app-main-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

.video-resize-handle {
  height: 14px;
  flex: 0 0 14px;
  cursor: row-resize;
  background: var(--bg-secondary);
  border-top: 1px solid var(--border-subtle);
  border-bottom: 1px solid var(--border-subtle);
  position: relative;
  transition: background 0.12s ease;
}

.video-resize-handle::before {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  width: 108px;
  height: 4px;
  border-radius: 999px;
  background: var(--bg-elevated);
  transform: translate(-50%, -50%);
  box-shadow: 0 -4px 0 var(--bg-elevated), 0 4px 0 var(--bg-elevated);
}

.video-resize-handle:hover {
  background: rgba(240, 160, 48, 0.08);
}

.video-resize-handle:hover::before {
  background: var(--accent-primary);
  box-shadow: 0 -4px 0 var(--accent-primary), 0 4px 0 var(--accent-primary);
}

.right-panel-resize-handle {
  width: 14px;
  flex: 0 0 14px;
  cursor: col-resize;
  background: var(--bg-secondary);
  border-left: 1px solid var(--border-subtle);
  border-right: 1px solid var(--border-subtle);
  position: relative;
  transition: background 0.12s ease;
}

.right-panel-resize-handle::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  width: 4px;
  height: 108px;
  border-radius: 999px;
  background: var(--bg-elevated);
  transform: translate(-50%, -50%);
  box-shadow: -4px 0 0 var(--bg-elevated), 4px 0 0 var(--bg-elevated);
}

.right-panel-resize-handle:hover {
  background: rgba(240, 160, 48, 0.08);
}

.right-panel-resize-handle:hover::before {
  background: var(--accent-primary);
  box-shadow: -4px 0 0 var(--accent-primary), 4px 0 0 var(--accent-primary);
}

:global(body.is-resizing-panel),
:global(body.is-resizing-video) {
  user-select: none;
}

:global(body.is-resizing-panel *) {
  cursor: col-resize !important;
}

:global(body.is-resizing-video *) {
  cursor: row-resize !important;
}

.stream-setup {
  min-height: 100vh;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.025), transparent 260px),
    #202020;
  padding: 2rem;
}

.header-center {
  display: flex;
  align-items: center;
  gap: 1.1rem;
}

.mode-badge {
  font-size: 0.9rem;
  padding: 0.42rem 0.85rem;
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
}

.mode-badge.stream {
  background: var(--accent-glow);
  color: var(--accent-primary);
}

.mode-badge.local {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.session-id-badge {
  font-size: 0.85rem;
  padding: 0.25rem 0.5rem;
  border-radius: var(--radius-sm);
  background: rgba(255, 193, 7, 0.15);
  color: #ffc107;
  font-family: 'Courier New', monospace;
  letter-spacing: 0.5px;
}

.session-name {
  font-size: 1rem;
  color: var(--text-secondary);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logo {
  cursor: pointer;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.summary-ready-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 40px;
  border: 1px solid rgba(48, 196, 126, 0.72);
  background: rgba(36, 151, 94, 0.18);
  color: #79e2ad;
  font-size: 16px;
  font-weight: 700;
  white-space: nowrap;
}

.summary-ready-btn:hover {
  background: rgba(36, 151, 94, 0.28);
  border-color: rgba(80, 220, 150, 0.9);
}

.language-switch {
  display: inline-flex;
  align-items: center;
  padding: 3px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

.language-option {
  min-width: 64px;
  padding: 0.38rem 0.7rem;
  border: 0;
  border-radius: calc(var(--radius-sm) - 2px);
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  font-family: var(--font-display);
  font-size: 0.85rem;
  line-height: 1;
  transition: background 0.15s, color 0.15s;
}

.language-option:hover {
  color: var(--text-secondary);
}

.language-option.active {
  background: var(--accent-primary);
  color: var(--bg-primary);
  font-weight: 700;
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
  font-size: 1rem;
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

/* Overview Toast */
.overview-toast {
  position: fixed;
  bottom: 32px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--bg-elevated);
  border: 1px solid var(--accent-primary);
  padding: 0.6rem 1rem;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  gap: 0.75rem;
  box-shadow: var(--shadow-glow), var(--shadow-md);
  z-index: 9999;
}

.overview-toast-text {
  font-size: 1rem;
  color: var(--text-secondary);
}

.overview-toast-btn {
  background: var(--accent-primary);
  color: var(--bg-primary);
  border: none;
  padding: 0.4rem 1rem;
  border-radius: var(--radius-sm);
  font-weight: 600;
  font-size: 0.95rem;
  cursor: pointer;
  transition: opacity 0.2s;
  white-space: nowrap;
}
.overview-toast-btn:hover {
  opacity: 0.85;
}

.overview-toast-dismiss {
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  font-size: 1rem;
  padding: 2px 4px;
}
.overview-toast-dismiss:hover {
  color: var(--text-primary);
}

.overview-layer {
  position: fixed;
  inset: 0;
  z-index: 2000;
}

/* Overview Header Button */
.btn-overview {
  background: transparent;
  border: 1px solid var(--accent-primary);
  color: var(--accent-primary);
  padding: 0.35rem 0.8rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 0.95rem;
  font-weight: 500;
  transition: all 0.2s;
}
.btn-overview:hover {
  background: var(--accent-primary);
  color: var(--bg-primary);
}
.btn-overview.pulsing {
  animation: overviewPulse 2s ease-in-out infinite;
}
@keyframes overviewPulse {
  0%, 100% { box-shadow: none; }
  50% { box-shadow: 0 0 10px rgba(240, 160, 48, 0.35); }
}
</style>
