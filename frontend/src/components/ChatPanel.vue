<template>
  <div class="chat-panel">
    <!-- Messages -->
    <div class="chat-messages" ref="messagesRef">
      <!-- Welcome message -->
      <div v-if="messages.length === 0" class="chat-empty">
        <div class="chat-empty-icon">&#x1F4AC;</div>
        <div class="chat-empty-text">Ask questions about the surgical video</div>
        <div class="chat-empty-hint">Supports text, voice input, and similarity search</div>
        <div class="chat-hints">
          <button class="chat-hint-chip" @click="sendQuickMessage('What surgical phase is currently being performed?')">Current phase?</button>
          <button class="chat-hint-chip" @click="sendQuickMessage('Are there any signs of bleeding?')">Bleeding signs?</button>
          <button class="chat-hint-chip" @click="sendQuickMessage('Find similar windows to the current one')">Find similar</button>
        </div>
      </div>

      <div
        v-for="(msg, idx) in messages"
        :key="idx"
        class="chat-msg"
        :class="msg.role"
      >
        <div class="chat-msg-content">{{ msg.content }}</div>

        <!-- Context reference -->
        <div v-if="msg.context" class="chat-msg-context">
          &#x1F4CB; {{ msg.context }}
        </div>

        <!-- Similarity results -->
        <div v-if="msg.similarWindows && msg.similarWindows.length" class="chat-similar">
          <div
            v-for="sim in msg.similarWindows"
            :key="sim.window_id"
            class="chat-similar-item"
            @click="$emit('seekToWindow', sim.window_id)"
          >
            W#{{ sim.window_id + 1 }}
            <span class="sim-score">{{ sim.similarity.toFixed(2) }}</span>
          </div>
        </div>

        <div class="chat-msg-time">{{ msg.time || '' }}</div>
      </div>

      <!-- Loading -->
      <div v-if="isLoading" class="chat-msg assistant">
        <div class="chat-msg-content typing">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
        </div>
      </div>
    </div>

    <!-- Input bar -->
    <div class="chat-input-bar">
      <input
        ref="inputRef"
        class="chat-input"
        v-model="inputText"
        placeholder="Ask about the video or search..."
        @keydown.enter="sendMessage"
      />
      <button
        class="chat-voice-btn"
        :class="{ recording: isRecording }"
        @click="toggleRecording"
        title="Voice input"
      >
        &#x1F3A4;
      </button>
      <button
        class="chat-send-btn"
        @click="sendMessage"
        :disabled="!inputText.trim() && !isRecording"
      >
        &#x27A4;
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, watch } from 'vue'
import axios from 'axios'

const props = defineProps({
  sessionId: { type: String, default: '' },
  summaries: { type: Array, default: () => [] },
})

const emit = defineEmits(['seekToWindow', 'message'])

const messages = ref([])
const inputText = ref('')
const isLoading = ref(false)
const isRecording = ref(false)
const messagesRef = ref(null)
const inputRef = ref(null)

// Audio recording
let mediaRecorder = null
let audioChunks = []

const scrollToBottom = () => {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

const formatTime = () => {
  const now = new Date()
  return `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`
}

const sendQuickMessage = (text) => {
  inputText.value = text
  sendMessage()
}

const formatError = (error) => {
  const detail = error?.response?.data?.detail ?? error?.response?.data?.error ?? error?.response?.data
  if (Array.isArray(detail)) {
    return detail.map(item => {
      if (typeof item === 'string') return item
      const loc = Array.isArray(item?.loc) ? item.loc.join('.') : ''
      const msg = item?.msg || item?.message || JSON.stringify(item)
      return loc ? `${loc}: ${msg}` : msg
    }).join('; ')
  }
  if (detail && typeof detail === 'object') {
    return detail.message || detail.msg || detail.error || JSON.stringify(detail)
  }
  return detail || error?.message || '网络错误'
}

const sendMessage = async () => {
  const text = inputText.value.trim()
  if (!text || !props.sessionId) return

  // Add user message
  messages.value.push({
    role: 'user',
    content: text,
    time: formatTime(),
  })
  inputText.value = ''
  isLoading.value = true
  scrollToBottom()

  try {
    // Check if this is a similarity search request
    const isSimilarityQuery = /similar|相似|find.*like|像.*窗口/i.test(text)

    // Send to chat API
    const response = await axios.post(`/api/voice/chat/${props.sessionId}/send`, {
      role: 'user',
      content: text,
      timestamp: Date.now() / 1000,
    })

    const assistantContent = response.data?.response?.content
      || response.data?.response_text
      || response.data?.error
      || 'No response'

    const reply = {
      role: 'assistant',
      content: assistantContent,
      time: formatTime(),
      similarWindows: null,
      context: buildContext() ? '已结合当前分析窗口上下文' : null,
    }

    // If similarity-related, also run semantic search
    if (isSimilarityQuery) {
      try {
        const searchRes = await axios.post('/api/analysis/search/semantic', {
          session_id: props.sessionId,
          query: text,
          top_k: 5,
        })
        if (searchRes.data?.results?.length) {
          reply.similarWindows = searchRes.data.results
        }
      } catch (e) {
        console.warn('[Chat] Semantic search failed:', e.message)
      }
    }

    messages.value.push(reply)
    emit('message', reply)

    // Auto-play TTS if available
    if (response.data?.response?.audio_base64) {
      playAudio(response.data.response.audio_base64)
    }
  } catch (error) {
    messages.value.push({
      role: 'assistant',
      content: `Error: ${formatError(error)}`,
      time: formatTime(),
    })
  } finally {
    isLoading.value = false
    scrollToBottom()
  }
}

const buildContext = () => {
  if (!props.summaries.length) return ''
  return props.summaries
    .slice(-10)
    .map(s => `Window ${s.window_id + 1} (${formatTimestamp(s.start_time)}-${formatTimestamp(s.end_time)}): ${s.summary}`)
    .join('\n')
}

const formatTimestamp = (seconds) => {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

const toggleRecording = async () => {
  if (isRecording.value) {
    stopRecording()
  } else {
    startRecording()
  }
}

const startRecording = async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    mediaRecorder = new MediaRecorder(stream)
    audioChunks = []

    mediaRecorder.ondataavailable = (e) => {
      audioChunks.push(e.data)
    }

    mediaRecorder.onstop = async () => {
      const audioBlob = new Blob(audioChunks, { type: 'audio/webm' })
      stream.getTracks().forEach(t => t.stop())
      await transcribeAudio(audioBlob)
    }

    mediaRecorder.start()
    isRecording.value = true
  } catch (e) {
    console.error('[Chat] Microphone access failed:', e)
  }
}

const stopRecording = () => {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop()
  }
  isRecording.value = false
}

const transcribeAudio = async (audioBlob) => {
  try {
    const formData = new FormData()
    formData.append('audio', audioBlob, 'recording.webm')

    const res = await axios.post('/api/voice/asr/transcribe', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    if (res.data?.text) {
      inputText.value = res.data.text
      // Auto-send voice input
      sendMessage()
    }
  } catch (e) {
    console.error('[Chat] Transcription failed:', e)
  }
}

const playAudio = (base64Audio) => {
  try {
    const audio = new Audio(`data:audio/wav;base64,${base64Audio}`)
    audio.play()
  } catch (e) {
    console.warn('[Chat] Audio playback failed:', e)
  }
}

watch(messages, scrollToBottom, { deep: true })
</script>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chat-messages::-webkit-scrollbar { width: 3px; }
.chat-messages::-webkit-scrollbar-track { background: transparent; }
.chat-messages::-webkit-scrollbar-thumb { background: var(--bg-elevated); border-radius: 2px; }

/* Empty state */
.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
}

.chat-empty-icon { font-size: 32px; opacity: 0.3; }
.chat-empty-text { font-size: 13px; color: var(--text-secondary); }
.chat-empty-hint { font-size: 11px; color: var(--text-tertiary); }

.chat-hints {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
  justify-content: center;
}

.chat-hint-chip {
  padding: 5px 12px;
  border-radius: 100px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  font-size: 11px;
  cursor: pointer;
  font-family: var(--font-display);
  transition: all 0.15s;
}

.chat-hint-chip:hover {
  border-color: var(--accent-primary);
  color: var(--accent-primary);
  background: var(--accent-glow);
}

/* Messages */
.chat-msg {
  max-width: 88%;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  font-size: 12px;
  line-height: 1.6;
}

.chat-msg.user {
  align-self: flex-end;
  background: var(--accent-glow);
  border: 1px solid rgba(240, 160, 48, 0.15);
  color: var(--text-primary);
  border-bottom-right-radius: 4px;
}

.chat-msg.assistant {
  align-self: flex-start;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  border-bottom-left-radius: 4px;
}

.chat-msg-content { white-space: pre-wrap; word-break: break-word; }

.chat-msg-time {
  font-size: 9px;
  color: var(--text-tertiary);
  margin-top: 4px;
  font-family: var(--font-mono);
}

.chat-msg-context {
  margin-top: 6px;
  padding: 6px 8px;
  background: rgba(0, 0, 0, 0.15);
  border-radius: 6px;
  font-size: 10px;
  color: var(--text-tertiary);
  border-left: 2px solid var(--accent-primary);
}

/* Similarity results */
.chat-similar {
  margin-top: 8px;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.chat-similar-item {
  padding: 4px 8px;
  background: var(--bg-elevated);
  border-radius: 6px;
  font-size: 10px;
  color: #a29bfe;
  cursor: pointer;
  border: 1px solid rgba(162, 155, 254, 0.2);
  font-family: var(--font-mono);
  transition: all 0.15s;
}

.chat-similar-item:hover {
  border-color: #a29bfe;
  background: rgba(162, 155, 254, 0.1);
}

.sim-score {
  color: var(--text-tertiary);
  margin-left: 4px;
}

/* Typing animation */
.typing {
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 4px 0;
}

.typing .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-tertiary);
  animation: typing-bounce 1.2s ease-in-out infinite;
}

.typing .dot:nth-child(2) { animation-delay: 0.2s; }
.typing .dot:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing-bounce {
  0%, 100% { opacity: 0.3; transform: translateY(0); }
  50% { opacity: 1; transform: translateY(-3px); }
}

/* Input bar */
.chat-input-bar {
  padding: 10px 14px;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  gap: 8px;
}

.chat-input {
  flex: 1;
  padding: 8px 14px;
  border-radius: 100px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
  font-family: var(--font-display);
}

.chat-input:focus { border-color: var(--accent-primary); }
.chat-input::placeholder { color: var(--text-tertiary); }

.chat-voice-btn {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  color: var(--text-tertiary);
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.chat-voice-btn:hover {
  border-color: var(--accent-primary);
  color: var(--accent-primary);
  background: var(--accent-glow);
}

.chat-voice-btn.recording {
  background: rgba(231, 76, 60, 0.15);
  border-color: #e74c3c;
  color: #e74c3c;
  animation: voice-pulse 1s ease-in-out infinite;
}

@keyframes voice-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(231, 76, 60, 0.2); }
  50% { box-shadow: 0 0 0 6px rgba(231, 76, 60, 0); }
}

.chat-send-btn {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--accent-primary);
  border: none;
  color: var(--bg-primary);
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.chat-send-btn:hover { filter: brightness(1.1); }
.chat-send-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.chat-empty-icon { font-size: 48px; }
.chat-empty-text { font-size: 22px; }
.chat-empty-hint { font-size: 18px; }
.chat-hint-chip { font-size: 18px; padding: 10px 18px; }
.chat-msg {
  font-size: 22px;
  line-height: 1.7;
  padding: 14px 18px;
  max-width: 94%;
}
.chat-msg-time { font-size: 15px; }
.chat-msg-context,
.chat-similar-item {
  font-size: 18px;
}
.chat-input {
  font-size: 22px;
  padding: 14px 18px;
}
.chat-voice-btn,
.chat-send-btn {
  width: 50px;
  height: 50px;
  font-size: 22px;
}
</style>
