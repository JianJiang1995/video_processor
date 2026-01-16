<template>
  <div class="stream-input-panel">
    <h3>📡 连接视频源</h3>
    
    <!-- Source Type Tabs -->
    <div class="source-tabs">
      <button 
        class="tab-btn" 
        :class="{ active: sourceType === 'stream' }"
        @click="sourceType = 'stream'"
      >
        🌐 网络视频流
      </button>
      <button 
        class="tab-btn" 
        :class="{ active: sourceType === 'capture' }"
        @click="sourceType = 'capture'; loadCaptureDevices()"
      >
        📹 本地采集卡
      </button>
    </div>

    <!-- Network Stream Input -->
    <template v-if="sourceType === 'stream'">
      <div class="input-group">
        <label>视频流地址</label>
        <input
          type="text"
          v-model="streamUrl"
          placeholder="rtsp://192.168.1.100:554/stream 或 http://..."
          class="input"
          @keyup.enter="connect"
        />
        <div class="input-hint">支持 RTSP, HTTP, HLS 视频流</div>
      </div>

      <div class="preset-streams">
        <label>常用地址</label>
        <div class="preset-list">
          <button 
            v-for="preset in presets" 
            :key="preset.url"
            class="preset-btn"
            @click="selectPreset(preset)"
          >
            {{ preset.name }}
          </button>
        </div>
      </div>
    </template>

    <!-- Capture Device Selection -->
    <template v-else>
      <div class="capture-section">
        <div class="capture-header">
          <label>选择采集设备</label>
          <button class="refresh-btn" @click="loadCaptureDevices" :disabled="isLoadingDevices">
            🔄 {{ isLoadingDevices ? '扫描中...' : '刷新' }}
          </button>
        </div>
        
        <div v-if="isLoadingDevices" class="devices-loading">
          <div class="loader-small"></div>
          <span>正在扫描采集设备...</span>
        </div>
        
        <div v-else-if="captureDevices.length === 0" class="no-devices">
          <p>⚠️ 未检测到采集设备</p>
          <p class="hint">请确保：</p>
          <ul>
            <li>采集卡已正确安装并连接</li>
            <li>驱动程序已安装</li>
            <li>SDI/DVI 线缆已连接到达芬奇机器人</li>
          </ul>
        </div>
        
        <div v-else class="device-list">
          <div 
            v-for="device in captureDevices" 
            :key="device.device_id"
            class="device-card"
            :class="{ selected: selectedDevice?.device_id === device.device_id }"
            @click="selectDevice(device)"
          >
            <div class="device-icon">📹</div>
            <div class="device-info">
              <div class="device-name">{{ device.device_name }}</div>
              <div class="device-specs">
                {{ device.width }}×{{ device.height }} @ {{ device.fps?.toFixed(0) || '?' }}fps
              </div>
            </div>
            <div v-if="selectedDevice?.device_id === device.device_id" class="device-check">✓</div>
          </div>
        </div>
        
        <div class="capture-hint">
          <p>💡 <strong>达芬奇机器人部署提示：</strong></p>
          <p>使用 SDI 线缆连接机器人输出到 Blackmagic 采集卡</p>
        </div>
      </div>
    </template>

    <div class="stream-options">
      <label>
        <input type="checkbox" v-model="autoAnalyze" />
        连接后自动开始分析
      </label>
    </div>

    <div class="actions">
      <button class="btn btn-secondary" @click="$emit('back')">
        ← 返回
      </button>
      <button 
        v-if="sourceType === 'stream'"
        class="btn btn-primary" 
        @click="connect" 
        :disabled="!streamUrl"
      >
        🔗 连接视频流
      </button>
      <button 
        v-else
        class="btn btn-primary" 
        @click="connectCapture" 
        :disabled="!selectedDevice"
      >
        📹 连接采集卡
      </button>
    </div>

    <!-- Connection Status -->
    <div v-if="isConnecting" class="connection-status">
      <div class="status-content">
        <div class="loader-small"></div>
        <span>{{ sourceType === 'stream' ? '正在连接视频流...' : '正在连接采集卡...' }}</span>
      </div>
      <button class="cancel-btn" @click="cancelConnect">取消</button>
    </div>

    <div v-if="error" class="connection-error">
      ❌ {{ error }}
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import axios from 'axios'

const emit = defineEmits(['connect', 'back'])

// Source type: 'stream' or 'capture'
const sourceType = ref('stream')

// Stream mode state
const streamUrl = ref('')
const presets = [
  { name: '手术室1', url: 'rtsp://192.168.1.101:554/live' },
  { name: '手术室2', url: 'rtsp://192.168.1.102:554/live' },
  { name: '测试流', url: 'http://localhost:9001/stream' },
]

// Capture mode state
const captureDevices = ref([])
const selectedDevice = ref(null)
const isLoadingDevices = ref(false)

// Common state
const autoAnalyze = ref(true)
const isConnecting = ref(false)
const error = ref('')

// AbortController for cancelling requests
let abortController = null

const selectPreset = (preset) => {
  streamUrl.value = preset.url
}

const selectDevice = (device) => {
  selectedDevice.value = device
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
      selectedDevice.value = captureDevices.value[0]
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
})

// Cleanup on unmount
onUnmounted(() => {
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
      auto_analyze: autoAnalyze.value
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
      error.value = '连接超时（30秒），请检查视频流地址和后端服务'
    } else if (err.code === 'ERR_NETWORK' || err.message?.includes('Network Error')) {
      error.value = '网络错误，请检查后端服务是否运行在 localhost:8001'
    } else {
      error.value = err.response?.data?.detail || err.message || '连接失败，请检查视频流地址'
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
      auto_analyze: autoAnalyze.value
    }, {
      signal: abortController.signal,
      timeout: 15000
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
      error.value = '连接超时，请检查采集卡是否正常工作'
    } else {
      error.value = err.response?.data?.detail || '连接失败，请检查采集卡'
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
  border-radius: var(--radius-xl);
  padding: 2rem;
  max-width: 550px;
  width: 100%;
}

.stream-input-panel h3 {
  margin: 0 0 1.5rem 0;
  font-size: 1.25rem;
}

/* Source Type Tabs */
.source-tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1.5rem;
  padding: 0.25rem;
  background: var(--bg-tertiary);
  border-radius: var(--radius-md);
}

.tab-btn {
  flex: 1;
  padding: 0.75rem 1rem;
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
  margin-bottom: 1.5rem;
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
  margin-bottom: 1.5rem;
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
  margin-bottom: 1.5rem;
}

.capture-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
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
  gap: 0.5rem;
}

.device-card {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem;
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
  font-size: 1.5rem;
}

.device-info {
  flex: 1;
}

.device-name {
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 0.25rem;
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

.capture-hint {
  margin-top: 1rem;
  padding: 1rem;
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid rgba(59, 130, 246, 0.2);
  border-radius: var(--radius-md);
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.capture-hint p {
  margin: 0;
}

.capture-hint p:first-child {
  margin-bottom: 0.25rem;
}

/* Stream Options */
.stream-options {
  margin-bottom: 1.5rem;
}

.stream-options label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
  color: var(--text-secondary);
  cursor: pointer;
}

.stream-options input[type="checkbox"] {
  accent-color: var(--accent-primary);
}

/* Actions */
.actions {
  display: flex;
  gap: 1rem;
  justify-content: space-between;
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
