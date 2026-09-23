<template>
  <div class="stream-input-panel">
    <h3>📡 {{ t('stream.connectSource') }}</h3>
      <ReplayImportButton @begin="cancelAutoConnect" @loaded="$emit('replay', $event)" />
    
    <!-- Source Type Tabs -->
    <div class="source-tabs">
      <button 
        class="tab-btn" 
        :class="{ active: sourceType === 'stream' }"
        @click="sourceType = 'stream'"
      >
        🌐 {{ t('stream.networkStream') }}
      </button>
      <button 
        class="tab-btn" 
        :class="{ active: sourceType === 'capture' }"
        @click="sourceType = 'capture'; loadCaptureDevices()"
      >
        📹 {{ t('stream.captureCard') }}
      </button>
    </div>

    <div class="input-layout-option">
      <div class="input-layout-label">{{ t('stream.inputLayoutQuestion') }}</div>
      <div class="input-layout-segment" role="radiogroup" :aria-label="t('stream.inputLayout')">
        <button
          type="button"
          class="layout-choice"
          :class="{ active: inputLayout === 'full' }"
          :aria-checked="inputLayout === 'full'"
          role="radio"
          @click="setInputLayout('full')"
        >
          {{ t('stream.layoutSingle') }}
        </button>
        <button
          type="button"
          class="layout-choice"
          :class="{ active: inputLayout === 'quad_right' }"
          :aria-checked="inputLayout === 'quad_right'"
          role="radio"
          @click="setInputLayout('quad_right')"
        >
          {{ t('stream.layoutQuadRight') }}
        </button>
      </div>
    </div>

    <!-- Network Stream Input -->
    <template v-if="sourceType === 'stream'">
      <div class="input-group">
        <label>{{ t('stream.url') }}</label>
        <input
          type="text"
          v-model="streamUrl"
          :placeholder="t('stream.urlPlaceholder')"
          class="input"
          @keyup.enter="connect"
        />
        <div class="input-hint">{{ t('stream.urlHint') }}</div>
      </div>

      <div class="preset-streams">
        <label>{{ t('stream.presets') }}</label>
        <div class="preset-list">
          <button 
            v-for="preset in presets" 
            :key="preset.url"
            class="preset-btn"
            @click="selectPreset(preset)"
          >
            {{ t(preset.nameKey) }}
          </button>
        </div>
      </div>
    </template>

    <!-- Capture Device Selection -->
    <template v-else>
      <div class="capture-section">
        <div class="capture-header">
          <label>{{ t('stream.selectCaptureDevice') }}</label>
          <button class="refresh-btn" @click="loadCaptureDevices" :disabled="isLoadingDevices">
            🔄 {{ isLoadingDevices ? t('stream.scanning') : t('stream.refresh') }}
          </button>
        </div>
        
        <div v-if="isLoadingDevices" class="devices-loading">
          <div class="loader-small"></div>
          <span>{{ t('stream.scanningDevices') }}</span>
        </div>
        
        <div v-else-if="captureDevices.length === 0" class="no-devices">
          <p>⚠️ {{ t('stream.noDevices') }}</p>
          <p class="hint">{{ t('stream.ensure') }}</p>
          <ul>
            <li>{{ t('stream.ensureInstalled') }}</li>
            <li>{{ t('stream.ensureDriver') }}</li>
            <li>{{ t('stream.ensureCable') }}</li>
          </ul>
        </div>
        
        <div v-else class="device-list">
          <div 
            v-for="device in captureDevices" 
            :key="`${device.backend || 'default'}-${device.device_id}`"
            class="device-card"
            :class="{ selected: isSelectedDevice(device) }"
            @click="selectDevice(device)"
          >
            <div class="device-icon">📹</div>
            <div class="device-info">
              <div class="device-name">{{ device.device_name }}</div>
              <div class="device-specs">
                {{ device.width }}×{{ device.height }} @ {{ device.fps?.toFixed(0) || '?' }}fps
              </div>
            </div>
            <div v-if="isSelectedDevice(device)" class="device-check">✓</div>
          </div>
        </div>

        <div v-if="selectedDevice?.backend === 'simulator'" class="input-group">
          <label for="simulator-path">{{ language === 'zh' ? '本地视频路径' : 'Local video path' }}</label>
          <input id="simulator-path" v-model="simulatorPath" class="input" type="text"
            :placeholder="language === 'zh' ? '留空使用默认模拟视频' : 'Leave empty for the default video'" />
        </div>

        <div
          v-if="selectedDevice?.supported_modes?.length"
          class="capture-settings-grid"
        >
          <div v-if="selectedDevice?.supported_connections?.length" class="input-group">
            <label>{{ t('stream.inputConnection') }}</label>
            <select v-model="selectedConnection" class="input">
              <option
                v-for="connection in selectedDevice.supported_connections"
                :key="connection"
                :value="connection"
              >
                {{ connectionLabel(connection) }}
              </option>
            </select>
          </div>

          <div class="input-group">
            <label>{{ t('stream.inputMode') }}</label>
            <select v-model="selectedMode" class="input">
              <option
                v-for="mode in selectedDevice.supported_modes"
                :key="mode"
                :value="mode"
              >
                {{ modeLabel(mode) }}
              </option>
            </select>
          </div>
        </div>
        
        <div class="capture-hint">
          <p>💡 <strong>{{ t('stream.deployTipTitle') }}</strong></p>
          <p>{{ selectedConnection === 'sdi' ? t('stream.deployTipSdi') : t('stream.deployTipHdmi') }}</p>
          <p class="capture-warning">{{ t('stream.sxgaWarning') }}</p>
        </div>
      </div>
    </template>

    <div class="stream-options">
      <label>
        <input type="checkbox" v-model="autoAnalyze" />
        {{ t('stream.autoAnalyze') }}
      </label>
    </div>

    <div class="actions">
      <button class="btn btn-secondary" @click="$emit('back')">
        ← {{ t('app.back') }}
      </button>
      <button 
        v-if="sourceType === 'stream'"
        class="btn btn-primary" 
        @click="connect" 
        :disabled="!streamUrl"
      >
        🔗 {{ t('stream.connectStream') }}
      </button>
      <button 
        v-else
        class="btn btn-primary" 
        @click="connectCapture" 
        :disabled="!selectedDevice"
      >
        📹 {{ t('stream.connectCapture') }}
      </button>
    </div>

    <!-- Connection Status -->
    <div v-if="isConnecting" class="connection-status">
      <div class="status-content">
        <div class="loader-small"></div>
        <span>{{ sourceType === 'stream' ? t('stream.connectingStream') : t('stream.connectingCapture') }}</span>
      </div>
      <button class="cancel-btn" @click="cancelConnect">{{ t('stream.cancel') }}</button>
    </div>

    <div v-if="error" class="connection-error">
      ❌ {{ error }}
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import { useI18n } from '@/i18n'
import ReplayImportButton from './ReplayImportButton.vue'

const { language, t } = useI18n()

const emit = defineEmits(['connect', 'back', 'replay'])

// Source type: 'stream' or 'capture'. Local Electron deployments can default to
// the capture-card workflow with VITE_DEFAULT_SOURCE=capture.
const defaultSourceType = import.meta.env.VITE_DEFAULT_SOURCE === 'capture' ? 'capture' : 'stream'
const defaultStreamUrl = import.meta.env.VITE_DEFAULT_STREAM_URL || 'http://localhost:9001/stream'
const sourceType = ref(defaultSourceType)
const autoConnectCapture = import.meta.env.VITE_AUTO_CONNECT_CAPTURE === '1'

// Stream mode state
const streamUrl = ref(defaultStreamUrl)
const presets = [
  { nameKey: 'stream.presetSimulator', url: defaultStreamUrl },
  { nameKey: 'stream.presetRoom1', url: 'rtsp://192.168.1.101:554/live' },
  { nameKey: 'stream.presetRoom2', url: 'rtsp://192.168.1.102:554/live' },
  { nameKey: 'stream.presetTest', url: 'http://localhost:9001/stream' },
]

// Capture mode state
const captureDevices = ref([])
const selectedDevice = ref(null)
const selectedMode = ref('auto')
const selectedConnection = ref('hdmi')
const isLoadingDevices = ref(false)
const inputLayout = ref(localStorage.getItem('surgr1_input_layout') || 'full')
const simulatorPath = ref('')

// Common state
const autoAnalyze = ref(import.meta.env.VITE_AUTO_ANALYZE_DEFAULT !== '0')
const isConnecting = ref(false)
const error = ref('')
const didAutoConnect = ref(false)
const AUTO_CONNECT_FLAG = 'surgr1_capture_auto_connect_started'

// AbortController for cancelling requests
let abortController = null
let autoConnectTimer = null
let autoConnectCancelled = false
const cancelAutoConnect = () => {
  autoConnectCancelled = true
  clearTimeout(autoConnectTimer)
  abortController?.abort()
}

const selectPreset = (preset) => {
  streamUrl.value = preset.url
}

const setInputLayout = (layout) => {
  inputLayout.value = layout === 'quad_right' ? 'quad_right' : 'full'
  localStorage.setItem('surgr1_input_layout', inputLayout.value)
}

const selectDevice = (device) => {
  selectedDevice.value = device
  selectedMode.value = device.default_mode || device.supported_modes?.[0] || 'auto'
  selectedConnection.value = device.default_connection || device.supported_connections?.[0] || 'auto'
}

const connectionLabel = (connection) => {
  if (connection === 'hdmi') return t('stream.connectionHdmi')
  if (connection === 'sdi') return t('stream.connectionSdi')
  return t('stream.connectionAuto')
}

const modeLabel = (mode) => {
  if (mode === 'auto') return t('stream.modeAuto')
  return mode
}

const isSelectedDevice = (device) => {
  return selectedDevice.value
    && selectedDevice.value.device_id === device.device_id
    && (selectedDevice.value.backend || 'default') === (device.backend || 'default')
}

// Load available capture devices
const loadCaptureDevices = async () => {
  isLoadingDevices.value = true
  error.value = ''
  
  try {
    const response = await axios.get('/api/video/capture-devices', { timeout: 15000 })
    captureDevices.value = response.data.devices || []
    
    // Auto-select first device if none selected
    if (captureDevices.value.length > 0 && !selectedDevice.value) {
      selectDevice(captureDevices.value[0])
    }

    if (
      autoConnectCapture &&
      !autoConnectCancelled &&
      !new URLSearchParams(window.location.search).has('replaySpec') &&
      sourceType.value === 'capture' &&
      selectedDevice.value &&
      !didAutoConnect.value &&
      sessionStorage.getItem(AUTO_CONNECT_FLAG) !== '1'
    ) {
      didAutoConnect.value = true
      sessionStorage.setItem(AUTO_CONNECT_FLAG, '1')
      autoConnectTimer = setTimeout(() => {
        if (!autoConnectCancelled) connectCapture()
      }, 250)
    }
  } catch (err) {
    console.error('Failed to load capture devices:', err)
    captureDevices.value = []
  } finally {
    isLoadingDevices.value = false
  }
}

// Reset state when component is mounted
onMounted(() => {
  isConnecting.value = false
  error.value = ''
  if (sourceType.value === 'capture') {
    loadCaptureDevices()
  }
})

// Cleanup on unmount
onUnmounted(() => {
  cancelAutoConnect()
  if (abortController) {
    abortController.abort()
    abortController = null
  }
})

// Cancel connection attempt
const cancelConnect = () => {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  isConnecting.value = false
  error.value = ''
}

// Connect to network stream
const connect = async () => {
  if (!streamUrl.value) return
  
  if (abortController) {
    abortController.abort()
  }
  
  abortController = new AbortController()
  isConnecting.value = true
  error.value = ''
  
  console.log('[StreamInput] Connecting to:', streamUrl.value)
  
  try {
    const response = await axios.post('/api/video/connect-stream', {
      stream_url: streamUrl.value,
      auto_analyze: autoAnalyze.value,
      input_layout: inputLayout.value
    }, {
      signal: abortController.signal,
      timeout: 30000  // 增加到 30 秒，因为视频流连接可能较慢
    })
    
    console.log('[StreamInput] Connected successfully:', response.data)
    
    emit('connect', {
      session: response.data,
      autoAnalyze: autoAnalyze.value
    })
  } catch (err) {
    console.error('[StreamInput] Connection error:', err)
    
    if (axios.isCancel(err) || err.name === 'CanceledError') {
      console.log('[StreamInput] Request was cancelled')
      return
    }
    
    if (err.code === 'ECONNABORTED' || err.message?.includes('timeout')) {
      error.value = language.value === 'zh'
        ? '连接超时（30秒），请检查视频流地址和后端服务'
        : 'Connection timed out after 30 seconds. Check the stream URL and backend service.'
    } else if (err.code === 'ERR_NETWORK' || err.message?.includes('Network Error')) {
      error.value = language.value === 'zh'
        ? '网络错误，请检查后端服务是否运行在 localhost:8001'
        : 'Network error. Check whether the backend is running on localhost:8001.'
    } else {
      error.value = err.response?.data?.detail || err.message || (language.value === 'zh' ? '连接失败，请检查视频流地址' : 'Connection failed. Check the stream URL.')
    }
  } finally {
    isConnecting.value = false
    abortController = null
  }
}

// Connect to capture device
const connectCapture = async () => {
  if (!selectedDevice.value) return
  
  if (abortController) {
    abortController.abort()
  }
  
  abortController = new AbortController()
  isConnecting.value = true
  error.value = ''
  
  try {
    const response = await axios.post('/api/video/connect-capture', {
      device_id: selectedDevice.value.device_id,
      device_name: selectedDevice.value.device_name || '',
      backend: selectedDevice.value.backend || 'auto',
      mode: selectedMode.value || selectedDevice.value.default_mode || 'auto',
      connection: selectedConnection.value || selectedDevice.value.default_connection || 'auto',
      auto_analyze: autoAnalyze.value,
      simulator_path: simulatorPath.value.trim(),
      input_layout: inputLayout.value
    }, {
      signal: abortController.signal,
      timeout: 25000
    })
    
    emit('connect', {
      session: response.data,
      autoAnalyze: autoAnalyze.value
    })
  } catch (err) {
    if (axios.isCancel(err) || err.name === 'CanceledError') {
      return
    }
    
    if (err.code === 'ECONNABORTED' || err.message?.includes('timeout')) {
      error.value = language.value === 'zh'
        ? '连接超时，请检查采集卡驱动和输入接口；可先选择“自动检测”制式重试'
        : 'Connection timed out. Check the driver/input, then retry with automatic mode detection.'
    } else {
      error.value = err.response?.data?.detail || (language.value === 'zh' ? '连接失败，请检查采集卡' : 'Connection failed. Check the capture card.')
    }
  } finally {
    isConnecting.value = false
    abortController = null
  }
}
</script>

<style scoped>
.stream-input-panel {
  background: var(--bg-secondary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 1rem 1.25rem;
  max-width: 820px;
  max-height: calc(100vh - 2rem);
  overflow-y: auto;
  width: 100%;
  box-shadow: var(--shadow-md);
}

.stream-input-panel h3 {
  margin: 0 0 0.75rem;
  font-size: 1rem;
}

.input-layout-option {
  margin: 0 0 0.65rem;
}

.input-layout-label {
  margin-bottom: 0.4rem;
  color: var(--text-secondary);
  font-size: 0.78rem;
  font-weight: 600;
}

.input-layout-segment {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3px;
  padding: 3px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
}

.layout-choice {
  min-height: 34px;
  padding: 0.35rem 0.55rem;
  border: 0;
  border-radius: calc(var(--radius-sm) - 2px);
  background: transparent;
  color: var(--text-tertiary);
  font-family: var(--font-display);
  font-size: 0.74rem;
  cursor: pointer;
}

.layout-choice.active {
  background: var(--bg-elevated);
  color: var(--accent-primary);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.24);
}

/* Source Type Tabs */
.source-tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.65rem;
  padding: 0.25rem;
  background: var(--bg-tertiary);
  border-radius: var(--radius-md);
}

.tab-btn {
  flex: 1;
  padding: 0.48rem 0.75rem;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s;
}

.tab-btn:hover {
  color: var(--text-primary);
}

.tab-btn.active {
  background: var(--bg-elevated);
  color: var(--accent-primary);
  font-weight: 500;
}

/* Input Group */
.input-group {
  margin-bottom: 0.75rem;
}

.input-group label {
  display: block;
  font-size: 0.9rem;
  color: var(--text-secondary);
  margin-bottom: 0.5rem;
}

.input-group .input {
  width: 100%;
}

.input-hint {
  font-size: 0.8rem;
  color: var(--text-tertiary);
  margin-top: 0.5rem;
}

/* Preset Streams */
.preset-streams {
  margin-bottom: 0.75rem;
}

.preset-streams label {
  display: block;
  font-size: 0.9rem;
  color: var(--text-secondary);
  margin-bottom: 0.5rem;
}

.preset-list {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.preset-btn {
  padding: 0.5rem 1rem;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.2s;
}

.preset-btn:hover {
  border-color: var(--accent-primary);
  color: var(--accent-primary);
}

/* Capture Section */
.capture-section {
  margin-bottom: 0.7rem;
}

.capture-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
}

.capture-header label {
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.refresh-btn {
  padding: 0.4rem 0.8rem;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.2s;
}

.refresh-btn:hover:not(:disabled) {
  border-color: var(--accent-primary);
  color: var(--accent-primary);
}

.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.devices-loading {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1.5rem;
  background: var(--bg-tertiary);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
}

.no-devices {
  padding: 1.5rem;
  background: var(--bg-tertiary);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
}

.no-devices p {
  margin: 0 0 0.5rem 0;
}

.no-devices .hint {
  font-size: 0.85rem;
  color: var(--text-tertiary);
}

.no-devices ul {
  margin: 0.5rem 0 0 1.5rem;
  padding: 0;
  font-size: 0.85rem;
  color: var(--text-tertiary);
}

.no-devices li {
  margin-bottom: 0.25rem;
}

/* Device List */
.device-list {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.device-card {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  padding: 0.62rem 0.75rem;
  background: var(--bg-tertiary);
  border: 2px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.2s;
}

.device-card:hover {
  border-color: var(--border-subtle);
}

.device-card.selected {
  border-color: var(--accent-primary);
  background: rgba(59, 130, 246, 0.1);
}

.device-icon {
  font-size: 1.1rem;
}

.device-info {
  flex: 1;
}

.device-name {
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 0.25rem;
  font-size: 0.82rem;
}

.device-specs {
  font-size: 0.8rem;
  color: var(--text-tertiary);
}

.device-check {
  width: 24px;
  height: 24px;
  background: var(--accent-primary);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 0.8rem;
  font-weight: bold;
}

.capture-settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.65rem;
  margin-top: 0.6rem;
}

.capture-settings-grid .input-group {
  min-width: 0;
  margin-bottom: 0;
}

.capture-hint {
  margin-top: 0.6rem;
  padding: 0.58rem 0.7rem;
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid rgba(59, 130, 246, 0.2);
  border-radius: var(--radius-md);
  font-size: 0.72rem;
  color: var(--text-secondary);
}

.capture-hint p {
  margin: 0;
}

.capture-hint p:first-child {
  margin-bottom: 0.25rem;
}

.capture-hint .capture-warning {
  margin-top: 0.3rem;
  color: var(--warning, #f5b942);
}

@media (max-width: 620px) {
  .capture-settings-grid {
    grid-template-columns: 1fr;
  }
}

/* Stream Options */
.stream-options {
  margin-bottom: 0.55rem;
}

.stream-options label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.76rem;
  color: var(--text-secondary);
  cursor: pointer;
}

.stream-options input[type="checkbox"] {
  accent-color: var(--accent-primary);
}

/* Actions */
.actions {
  display: flex;
  gap: 0.65rem;
  justify-content: space-between;
  position: sticky;
  bottom: -1rem;
  z-index: 2;
  margin: 0 -1.25rem -1rem;
  padding: 0.55rem 1.25rem 1rem;
  background: linear-gradient(180deg, rgba(32, 32, 32, 0), var(--bg-secondary) 28%);
}

/* Connection Status */
.connection-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 1.5rem;
  padding: 1rem;
  background: var(--bg-tertiary);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
}

.status-content {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.cancel-btn {
  padding: 0.4rem 0.8rem;
  background: transparent;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-tertiary);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.2s;
}

.cancel-btn:hover {
  border-color: var(--error);
  color: var(--error);
  background: rgba(255, 107, 107, 0.1);
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

.connection-error {
  margin-top: 1rem;
  padding: 1rem;
  background: rgba(255, 107, 107, 0.1);
  border: 1px solid var(--error);
  border-radius: var(--radius-md);
  color: var(--error);
  font-size: 0.9rem;
}
</style>
