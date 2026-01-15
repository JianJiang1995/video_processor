<template>
  <div class="voice-chat" :class="{ collapsed: isCollapsed }">
    <!-- Header -->
    <div class="voice-chat-header" @click="toggleCollapse">
      <div class="voice-chat-title">
        <span class="voice-icon">🤖</span>
        <span>智能助手</span>
        <span class="mode-badge" :class="mode">
          {{ modeLabel }}
        </span>
      </div>
      <div class="header-actions">
        <span class="status-indicator" :class="connectionStatus"></span>
        <button class="collapse-btn">
          {{ isCollapsed ? '▲' : '▼' }}
        </button>
      </div>
    </div>
    
    <!-- Chat Content -->
    <div class="voice-chat-content" v-show="!isCollapsed">
      <!-- Messages -->
      <div class="chat-messages" ref="messagesContainer">
        <div
          v-for="(msg, idx) in messages"
          :key="idx"
          class="chat-message"
          :class="msg.role"
        >
          <div class="message-avatar">
            {{ msg.role === 'user' ? '👤' : '🤖' }}
          </div>
          <div class="message-content">
            <div class="message-text">{{ msg.content }}</div>
            <div class="message-actions" v-if="msg.role === 'assistant' && msg.audio_base64">
              <button 
                class="play-btn"
                :class="{ playing: currentPlayingIdx === idx }"
                @click="playAudio(msg, idx)"
              >
                {{ currentPlayingIdx === idx ? '⏹️' : '🔊' }}
              </button>
            </div>
            <div class="message-time">
              {{ formatTime(msg.timestamp) }}
            </div>
          </div>
        </div>
        
        <!-- Interim transcript -->
        <div v-if="interimText" class="chat-message user interim">
          <div class="message-avatar">👤</div>
          <div class="message-content">
            <div class="message-text">{{ interimText }}...</div>
          </div>
        </div>
        
        <!-- Empty state -->
        <div v-if="messages.length === 0 && !interimText" class="chat-empty">
          <div class="empty-icon">💬</div>
          <div class="empty-text">开始对话</div>
          <div class="empty-hint">输入文字或点击麦克风进行语音对话</div>
        </div>
      </div>
      
      <!-- Text Input -->
      <div class="text-input-area">
        <input
          type="text"
          v-model="textInput"
          @keyup.enter="sendTextMessage"
          placeholder="输入消息..."
          class="text-input"
          :disabled="isSending"
        />
        <button 
          class="send-btn"
          @click="sendTextMessage"
          :disabled="!textInput.trim() || isSending"
        >
          {{ isSending ? '⏳' : '📤' }}
        </button>
      </div>
      
      <!-- Voice Controls -->
      <div class="voice-controls">
        <!-- Mode Switch -->
        <div class="mode-switch">
          <button 
            class="mode-btn"
            :class="{ active: mode === 'normal' }"
            @click="setMode('normal')"
          >
            🎙️ 普通
          </button>
          <button 
            class="mode-btn"
            :class="{ active: mode === 'wakeword' }"
            @click="setMode('wakeword')"
          >
            👂 唤醒词
          </button>
        </div>
        
        <!-- Main Control -->
        <div class="main-control">
          <button 
            class="record-btn"
            :class="{ recording: isRecording, listening: isListening, disabled: !asrStatus.available }"
            @click="toggleRecording"
            :disabled="!asrStatus.available && !isRecording && !isListening"
          >
            <span class="record-icon">
              {{ isRecording ? '⏹️' : isListening ? '👂' : '🎤' }}
            </span>
            <span class="record-label">
              {{ recordButtonLabel }}
            </span>
          </button>
        </div>
        
        <!-- Status -->
        <div class="voice-status" v-if="isRecording || isListening">
          <span v-if="isRecording" class="status-recording">
            🔴 录音中...
          </span>
          <span v-else-if="isListening" class="status-listening">
            👂 监听唤醒词中...
          </span>
        </div>
      </div>
      
      <!-- Keyword Display -->
      <div class="keywords-display" v-if="mode === 'wakeword' && keywords.length > 0">
        <span class="keywords-label">唤醒词:</span>
        <span 
          v-for="kw in keywords" 
          :key="kw" 
          class="keyword-tag"
        >
          {{ kw }}
        </span>
      </div>
      
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import axios from 'axios'

const props = defineProps({
  sessionId: {
    type: String,
    default: 'default'
  }
})

const emit = defineEmits(['message', 'transcript'])

// State
const isCollapsed = ref(false)
const mode = ref('normal')  // 'normal' or 'wakeword'
const isRecording = ref(false)
const isListening = ref(false)
const connectionStatus = ref('disconnected')  // 'connected', 'disconnected', 'error'
const messages = ref([])
const interimText = ref('')
const keywords = ref(['你好小助', '小助小助', '开始识别'])
const currentPlayingIdx = ref(-1)
const currentAudio = ref(null)
const textInput = ref('')
const isSending = ref(false)

// Service status (only ASR needed for button state)
const asrStatus = ref({ available: true, checking: false })

// Refs
const messagesContainer = ref(null)

// Audio context and WebSocket
let audioContext = null
let mediaStream = null
let processor = null
let ws = null

// Computed
const modeLabel = computed(() => {
  if (isRecording.value) return '录音中'
  if (isListening.value) return '监听中'
  return mode.value === 'wakeword' ? '唤醒词模式' : '普通模式'
})

const recordButtonLabel = computed(() => {
  if (isRecording.value) return '停止录音'
  if (isListening.value) return '停止监听'
  return mode.value === 'wakeword' ? '开始监听' : '开始录音'
})

// Methods
const toggleCollapse = () => {
  isCollapsed.value = !isCollapsed.value
}

const setMode = (newMode) => {
  if (isRecording.value || isListening.value) {
    stopRecording()
  }
  mode.value = newMode
}

const toggleRecording = async () => {
  if (isRecording.value || isListening.value) {
    stopRecording()
  } else {
    await startRecording()
  }
}

const startRecording = async () => {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    
    audioContext = new AudioContext({ sampleRate: 16000 })
    const source = audioContext.createMediaStreamSource(mediaStream)
    
    // Connect WebSocket - use unified conversation endpoint for wakeword mode
    // Use relative path to go through vite proxy, or direct connection for production
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsHost = window.location.host  // Uses vite proxy in development
    let wsPath
    if (mode.value === 'wakeword') {
      // Use new conversation endpoint with GLM integration
      wsPath = '/api/voice/ws/conversation'
    } else {
      wsPath = '/api/voice/ws/stream'
    }
    const wsUrl = `${wsProtocol}//${wsHost}${wsPath}`
    console.log('Connecting WebSocket to:', wsUrl)
    
    ws = new WebSocket(wsUrl)
    
    ws.onopen = () => {
      connectionStatus.value = 'connected'
      if (mode.value === 'wakeword') {
        // Start conversation with session ID
        ws.send(JSON.stringify({ 
          action: 'start', 
          session_id: props.sessionId 
        }))
        isListening.value = true
      } else {
        isRecording.value = true
      }
    }
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      handleWebSocketMessage(data)
    }
    
    ws.onerror = () => {
      connectionStatus.value = 'error'
    }
    
    ws.onclose = () => {
      connectionStatus.value = 'disconnected'
      isRecording.value = false
      isListening.value = false
    }
    
    // Audio processing
    processor = audioContext.createScriptProcessor(4096, 1, 1)
    source.connect(processor)
    processor.connect(audioContext.destination)
    
    processor.onaudioprocess = (e) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        const inputData = e.inputBuffer.getChannelData(0)
        const pcm = new Int16Array(inputData.length)
        for (let i = 0; i < inputData.length; i++) {
          pcm[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768))
        }
        const base64 = btoa(String.fromCharCode(...new Uint8Array(pcm.buffer)))
        ws.send(JSON.stringify({ action: 'audio', audio_data: base64 }))
      }
    }
    
  } catch (error) {
    console.error('Failed to start recording:', error)
    connectionStatus.value = 'error'
  }
}

const stopRecording = () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: 'stop' }))
    ws.close()
  }
  
  if (processor) {
    processor.disconnect()
    processor = null
  }
  
  if (audioContext) {
    audioContext.close()
    audioContext = null
  }
  
  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop())
    mediaStream = null
  }
  
  isRecording.value = false
  isListening.value = false
  interimText.value = ''
}

const handleWebSocketMessage = (data) => {
  switch (data.type) {
    case 'wakeword_detected':
      // Wake word detected - switch to active mode
      isListening.value = false
      isRecording.value = true
      playBeep()
      addMessage({
        role: 'assistant',
        content: `唤醒词检测到: ${data.keyword || '未知'}，请开始说话...`,
        timestamp: Date.now() / 1000
      })
      break
    
    case 'mode_change':
      // Handle mode changes from server
      if (data.mode === 'monitoring') {
        isRecording.value = false
        isListening.value = true
        interimText.value = ''
      } else if (data.mode === 'listening') {
        isRecording.value = true
        isListening.value = false
      } else if (data.mode === 'processing') {
        // Show processing indicator
        interimText.value = '正在处理...'
      }
      break
      
    case 'transcript':
      // Transcription result from conversation endpoint
      if (data.text) {
        interimText.value = data.text
        if (data.is_final) {
          addMessage({
            role: 'user',
            content: data.text,
            timestamp: Date.now() / 1000
          })
          interimText.value = ''
          emit('transcript', data.text)
        }
      }
      break
    
    case 'response':
      // GLM response with surgical context analysis
      if (data.text) {
        addMessage({
          role: 'assistant',
          content: data.text,
          timestamp: Date.now() / 1000,
          audio_base64: data.audio_base64,
          glm_id: data.glm_id
        })
        
        // Auto-play the response if audio is available
        if (data.audio_base64) {
          playResponseAudio(data.audio_base64)
        }
      }
      interimText.value = ''
      break
      
    case 'result':
      // Transcription result (for normal mode)
      if (data.data && data.data.text) {
        if (data.data.is_final) {
          // Final result - add as message
          addMessage({
            role: 'user',
            content: data.data.text,
            timestamp: Date.now() / 1000
          })
          interimText.value = ''
          emit('transcript', data.data.text)
          
          // In normal mode, generate TTS response
          if (mode.value === 'normal') {
            generateTTSResponse(data.data.text)
          }
        } else {
          // Interim result - show as typing indicator
          interimText.value = data.data.text
        }
      }
      break
      
    case 'listening_text':
      // Listening mode text (for debugging)
      const text = data.text || (data.data && data.data.text)
      if (text) {
        interimText.value = text
      }
      break
      
    case 'back_to_monitoring':
      // Return to monitoring mode
      isRecording.value = false
      isListening.value = true
      interimText.value = ''
      addMessage({
        role: 'assistant',
        content: `${data.reason === 'standby_timeout' ? '待机超时' : '无有效输入'}，已返回监控模式`,
        timestamp: Date.now() / 1000
      })
      break
    
    case 'invalid_input':
      // Invalid input detected (noise)
      interimText.value = ''
      break
    
    case 'error':
      // Error from server
      console.error('Server error:', data.error)
      addMessage({
        role: 'assistant',
        content: `错误: ${data.error}`,
        timestamp: Date.now() / 1000
      })
      break
      
    case 'event':
      if (data.data && data.data.event === 'session_completed') {
        stopRecording()
      }
      break
    
    case 'session_ended':
      stopRecording()
      break
  }
}

const playResponseAudio = (audioBase64) => {
  if (currentAudio.value) {
    currentAudio.value.pause()
    currentAudio.value = null
  }
  
  currentAudio.value = new Audio(`data:audio/wav;base64,${audioBase64}`)
  currentAudio.value.onended = () => {
    currentAudio.value = null
    currentPlayingIdx.value = -1
  }
  currentAudio.value.play().catch(err => {
    console.log('Auto-play blocked:', err)
  })
}

const addMessage = (msg) => {
  messages.value.push(msg)
  emit('message', msg)
  
  // Scroll to bottom
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

const generateTTSResponse = async (userText) => {
  try {
    // Generate a response (could be AI response in future)
    const responseText = `收到您的消息: "${userText.slice(0, 30)}${userText.length > 30 ? '...' : ''}"`
    
    const response = await axios.post('/api/voice/tts/synthesize', {
      text: responseText,
      speaker: '中文女'
    })
    
    if (response.data.success) {
      addMessage({
        role: 'assistant',
        content: responseText,
        timestamp: Date.now() / 1000,
        audio_base64: response.data.audio_base64
      })
    }
  } catch (error) {
    console.error('TTS error:', error)
  }
}

const playAudio = (msg, idx) => {
  if (currentPlayingIdx.value === idx) {
    // Stop current audio
    if (currentAudio.value) {
      currentAudio.value.pause()
      currentAudio.value = null
    }
    currentPlayingIdx.value = -1
    return
  }
  
  // Play new audio
  if (msg.audio_base64) {
    currentAudio.value = new Audio(`data:audio/wav;base64,${msg.audio_base64}`)
    currentPlayingIdx.value = idx
    
    currentAudio.value.onended = () => {
      currentPlayingIdx.value = -1
      currentAudio.value = null
    }
    
    currentAudio.value.play()
  }
}

const playBeep = () => {
  const ctx = new AudioContext()
  const oscillator = ctx.createOscillator()
  const gain = ctx.createGain()
  oscillator.connect(gain)
  gain.connect(ctx.destination)
  oscillator.frequency.value = 880
  gain.gain.setValueAtTime(0.3, ctx.currentTime)
  gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.15)
  oscillator.start(ctx.currentTime)
  oscillator.stop(ctx.currentTime + 0.15)
}

const formatTime = (timestamp) => {
  if (!timestamp) return ''
  const date = new Date(timestamp * 1000)
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

// Send text message
const sendTextMessage = async () => {
  const text = textInput.value.trim()
  if (!text || isSending.value) return
  
  isSending.value = true
  textInput.value = ''
  
  // Add user message
  addMessage({
    role: 'user',
    content: text,
    timestamp: Date.now() / 1000
  })
  
  try {
    // Call chat API with ChatMessage format
    const response = await axios.post(`/api/voice/chat/${props.sessionId}/send`, {
      role: 'user',
      content: text,
      timestamp: Date.now() / 1000
    })
    
    if (response.data.success && response.data.response) {
      const assistantResponse = response.data.response
      addMessage({
        role: 'assistant',
        content: assistantResponse.content,
        timestamp: assistantResponse.timestamp || Date.now() / 1000,
        audio_base64: assistantResponse.audio_base64
      })
      
      // Auto-play the response if audio is available
      if (assistantResponse.audio_base64) {
        playResponseAudio(assistantResponse.audio_base64)
      }
    } else {
      addMessage({
        role: 'assistant',
        content: `错误: ${response.data.error || '未知错误'}`,
        timestamp: Date.now() / 1000
      })
    }
  } catch (error) {
    console.error('Chat error:', error)
    addMessage({
      role: 'assistant',
      content: `发送失败: ${error.response?.data?.detail || error.message || '网络错误'}`,
      timestamp: Date.now() / 1000
    })
  } finally {
    isSending.value = false
  }
}

// Check ASR status for button state
const checkAsrStatus = async () => {
  try {
    const asrResponse = await axios.get('/api/voice/asr/status')
    asrStatus.value.available = asrResponse.data.available
    if (asrResponse.data.keywords) {
      keywords.value = asrResponse.data.keywords
    }
  } catch (error) {
    asrStatus.value.available = false
  }
}

// Load ASR status on mount
onMounted(() => {
  checkAsrStatus()
})

onUnmounted(() => {
  stopRecording()
})
</script>

<style scoped>
.voice-chat {
  position: fixed;
  bottom: 20px;
  right: 20px;
  width: 380px;
  max-height: 70vh;
  background: var(--bg-secondary);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  border: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  z-index: 1000;
  overflow: hidden;
  transition: all 0.3s ease;
}

.voice-chat.collapsed {
  max-height: 52px;
}

.voice-chat-header {
  padding: 0.75rem 1rem;
  background: var(--bg-tertiary);
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
}

.voice-chat-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 600;
}

.voice-icon {
  font-size: 1.25rem;
}

.mode-badge {
  font-size: 0.7rem;
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  color: var(--text-secondary);
}

.mode-badge.wakeword {
  background: rgba(253, 203, 110, 0.2);
  color: var(--warning);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.status-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-tertiary);
}

.status-indicator.connected {
  background: var(--success);
  box-shadow: 0 0 8px var(--success);
}

.status-indicator.error {
  background: var(--error);
}

.collapse-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 0.8rem;
}

.voice-chat-content {
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  min-height: 200px;
  max-height: 300px;
}

.chat-message {
  display: flex;
  gap: 0.75rem;
  animation: fadeIn 0.3s;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(5px); }
  to { opacity: 1; transform: translateY(0); }
}

.chat-message.user {
  flex-direction: row-reverse;
}

.chat-message.interim {
  opacity: 0.6;
}

.message-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--bg-elevated);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1rem;
  flex-shrink: 0;
}

.message-content {
  max-width: 70%;
  padding: 0.75rem 1rem;
  border-radius: var(--radius-md);
  background: var(--bg-tertiary);
}

.chat-message.user .message-content {
  background: var(--accent-glow);
  border: 1px solid var(--accent-primary);
}

.chat-message.assistant .message-content {
  border-left: 3px solid var(--accent-primary);
}

.message-text {
  font-size: 0.9rem;
  line-height: 1.5;
}

.message-actions {
  margin-top: 0.5rem;
  display: flex;
  gap: 0.5rem;
}

.play-btn {
  background: var(--bg-elevated);
  border: none;
  border-radius: var(--radius-sm);
  padding: 0.25rem 0.5rem;
  cursor: pointer;
  transition: all 0.2s;
}

.play-btn:hover, .play-btn.playing {
  background: var(--accent-primary);
  color: var(--bg-primary);
}

.message-time {
  font-size: 0.7rem;
  color: var(--text-tertiary);
  margin-top: 0.25rem;
}

.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--text-tertiary);
  text-align: center;
  padding: 2rem;
}

.empty-icon {
  font-size: 2.5rem;
  margin-bottom: 0.75rem;
  opacity: 0.5;
}

.empty-text {
  font-size: 1rem;
  margin-bottom: 0.5rem;
  color: var(--text-secondary);
}

.empty-hint {
  font-size: 0.8rem;
}

.voice-controls {
  padding: 1rem;
  background: var(--bg-tertiary);
  border-top: 1px solid var(--border-subtle);
}

.mode-switch {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.mode-btn {
  flex: 1;
  padding: 0.5rem;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.2s;
}

.mode-btn.active {
  background: var(--accent-primary);
  border-color: var(--accent-primary);
  color: var(--bg-primary);
}

.mode-btn:hover:not(.active) {
  border-color: var(--accent-primary);
  color: var(--text-primary);
}

.main-control {
  display: flex;
  justify-content: center;
  margin-bottom: 0.75rem;
}

.record-btn {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 1.5rem;
  border: none;
  border-radius: var(--radius-xl);
  background: var(--bg-elevated);
  color: var(--text-primary);
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s;
}

.record-btn:hover {
  background: var(--accent-primary);
  color: var(--bg-primary);
}

.record-btn.recording {
  background: var(--error);
  animation: pulse 1s infinite;
}

.record-btn.listening {
  background: var(--warning);
  color: var(--bg-primary);
  animation: pulse 2s infinite;
}

.record-btn.disabled {
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
  cursor: not-allowed;
  opacity: 0.6;
}

.record-btn.disabled:hover {
  background: var(--bg-tertiary);
  transform: none;
}

.record-icon {
  font-size: 1.25rem;
}

.voice-status {
  text-align: center;
  font-size: 0.8rem;
  color: var(--text-secondary);
}

.status-recording {
  color: var(--error);
}

.status-listening {
  color: var(--warning);
}

.keywords-display {
  padding: 0.75rem 1rem;
  background: rgba(253, 203, 110, 0.1);
  border-top: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  font-size: 0.75rem;
}

.keywords-label {
  color: var(--text-secondary);
}

.keyword-tag {
  padding: 0.2rem 0.5rem;
  background: var(--bg-elevated);
  border-radius: var(--radius-sm);
  color: var(--warning);
}

/* Text Input Area */
.text-input-area {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  background: var(--bg-tertiary);
  border-top: 1px solid var(--border-subtle);
}

.text-input {
  flex: 1;
  padding: 0.6rem 1rem;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 0.9rem;
  outline: none;
  transition: all 0.2s;
}

.text-input:focus {
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 2px var(--accent-glow);
}

.text-input::placeholder {
  color: var(--text-tertiary);
}

.text-input:disabled {
  background: var(--bg-tertiary);
  cursor: not-allowed;
}

.send-btn {
  padding: 0.6rem 1rem;
  border: none;
  border-radius: var(--radius-md);
  background: var(--accent-primary);
  color: var(--bg-primary);
  font-size: 1rem;
  cursor: pointer;
  transition: all 0.2s;
}

.send-btn:hover:not(:disabled) {
  background: var(--accent-hover);
  transform: scale(1.05);
}

.send-btn:disabled {
  background: var(--bg-elevated);
  color: var(--text-tertiary);
  cursor: not-allowed;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}

/* Responsive */
@media (max-width: 640px) {
  .voice-chat {
    width: calc(100% - 40px);
    right: 20px;
    left: 20px;
    bottom: 20px;
  }
}
</style>


