<template>
  <div class="stream-input-panel">
    <h3>📡 连接视频流</h3>
    
    <div class="input-group">
      <label>视频流地址</label>
      <input
        type="text"
        v-model="streamUrl"
        placeholder="rtsp://192.168.1.100:554/stream 或 http://..."
        class="input"
        @keyup.enter="connect"
      />
      <div class="input-hint">支持 RTSP, HTTP, HLS, WebRTC 视频流</div>
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
      <button class="btn btn-primary" @click="connect" :disabled="!streamUrl">
        🔗 连接视频流
      </button>
    </div>

    <!-- Connection Status -->
    <div v-if="isConnecting" class="connection-status">
      <div class="status-content">
        <div class="loader-small"></div>
        <span>正在连接视频流...</span>
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

const streamUrl = ref('')
const autoAnalyze = ref(true)
const isConnecting = ref(false)
const error = ref('')

// AbortController for cancelling requests
let abortController = null

const presets = [
  { name: '手术室1', url: 'rtsp://192.168.1.101:554/live' },
  { name: '手术室2', url: 'rtsp://192.168.1.102:554/live' },
  { name: '测试流', url: 'http://localhost:9001/stream' },
]

const selectPreset = (preset) => {
  streamUrl.value = preset.url
}

// Reset state when component is mounted (ensures clean state after navigation)
onMounted(() => {
  isConnecting.value = false
  error.value = ''
})

// Cleanup on unmount - cancel any pending requests
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

const connect = async () => {
  if (!streamUrl.value) return
  
  // Cancel any existing request
  if (abortController) {
    abortController.abort()
  }
  
  abortController = new AbortController()
  isConnecting.value = true
  error.value = ''
  
  try {
    const response = await axios.post('/api/video/connect-stream', {
      stream_url: streamUrl.value,
      auto_analyze: autoAnalyze.value
    }, {
      signal: abortController.signal,
      timeout: 20000  // 20 second timeout
    })
    
    emit('connect', {
      session: response.data,
      autoAnalyze: autoAnalyze.value
    })
  } catch (err) {
    // Don't show error if request was cancelled
    if (axios.isCancel(err) || err.name === 'CanceledError') {
      return
    }
    
    if (err.code === 'ECONNABORTED' || err.message?.includes('timeout')) {
      error.value = '连接超时，请检查视频流地址是否正确'
    } else {
      error.value = err.response?.data?.detail || '连接失败，请检查视频流地址'
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
  max-width: 500px;
  width: 100%;
}

.stream-input-panel h3 {
  margin: 0 0 1.5rem 0;
  font-size: 1.25rem;
}

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

.actions {
  display: flex;
  gap: 1rem;
  justify-content: space-between;
}

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




