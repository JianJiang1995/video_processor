<template>
  <div class="video-wrapper">
    <div class="video-container" ref="containerRef">
      <!-- MJPEG Stream (for live streams) -->
      <img
        v-if="session && mode === 'stream' && isHttpStream"
        ref="streamImgRef"
        :src="streamUrl"
        class="stream-image"
        @load="onStreamLoad"
        @error="onStreamError"
      />
      
      <!-- Video Element (for local files) -->
      <video
        v-else-if="session"
        ref="videoRef"
        :src="`/api/video/stream/${session.session_id}`"
        @timeupdate="onTimeUpdate"
        @loadedmetadata="onLoadedMetadata"
        @play="$emit('play')"
        @pause="$emit('pause')"
        @click="togglePlay"
      ></video>
      
      <!-- Placeholder when no video (Local Mode) -->
      <div v-else class="video-placeholder">
        <div class="upload-zone" @click="triggerUpload" @drop.prevent="onDrop" @dragover.prevent>
          <div class="upload-icon">📁</div>
          <div class="upload-text">拖放视频文件或点击上传</div>
          <div class="upload-hint">支持 MP4, AVI, MOV, MKV</div>
          
          <div style="margin-top: 1.5rem; font-size: 0.9rem; color: var(--text-tertiary);">
            — 或输入本地路径 —
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
              加载
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
        <div class="loading-text">{{ mode === 'stream' ? '连接视频流...' : '加载视频...' }}</div>
      </div>
      
      <!-- Live Indicator -->
      <div v-if="session && mode === 'stream'" class="live-indicator">
        <span class="live-dot"></span>
        LIVE
      </div>
      
      <!-- Current Time Overlay -->
      <div v-if="session" class="time-overlay">
        {{ formatTime(currentTime) }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, computed } from 'vue'

const props = defineProps({
  session: Object,
  currentTime: Number,
  isPlaying: Boolean,
  mode: {
    type: String,
    default: 'local'  // 'local' or 'stream'
  }
})

const emit = defineEmits(['timeupdate', 'play', 'pause', 'seek', 'upload', 'load'])

const videoRef = ref(null)
const streamImgRef = ref(null)
const containerRef = ref(null)
const fileInput = ref(null)
const isLoading = ref(false)
const videoPath = ref('')

// Computed: check if it's an HTTP stream URL
const isHttpStream = computed(() => {
  if (!props.session?.video_path) return false
  const path = props.session.video_path
  return path.startsWith('http://') || path.startsWith('https://')
})

// Computed: get the stream URL directly
const streamUrl = computed(() => {
  if (isHttpStream.value) {
    return props.session.video_path
  }
  return ''
})

const onStreamLoad = () => {
  isLoading.value = false
}

const onStreamError = () => {
  console.error('Stream error')
  isLoading.value = false
}

// Watch for external time changes (seeking)
watch(() => props.currentTime, (newTime) => {
  if (videoRef.value && Math.abs(videoRef.value.currentTime - newTime) > 0.5) {
    videoRef.value.currentTime = newTime
  }
})

// Watch for play/pause state
watch(() => props.isPlaying, (playing) => {
  if (videoRef.value) {
    if (playing) {
      videoRef.value.play()
    } else {
      videoRef.value.pause()
    }
  }
})

const onTimeUpdate = () => {
  if (videoRef.value) {
    emit('timeupdate', videoRef.value.currentTime)
  }
}

const onLoadedMetadata = () => {
  isLoading.value = false
}

const togglePlay = () => {
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
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
}
</script>

<style scoped>
.time-overlay {
  position: absolute;
  top: 1rem;
  right: 1rem;
  font-family: var(--font-mono);
  font-size: 0.9rem;
  background: rgba(0, 0, 0, 0.7);
  padding: 0.35rem 0.75rem;
  border-radius: var(--radius-sm);
  color: var(--accent-primary);
}

.live-indicator {
  position: absolute;
  top: 1rem;
  left: 1rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  font-weight: 600;
  background: rgba(255, 0, 0, 0.8);
  color: white;
  padding: 0.35rem 0.75rem;
  border-radius: var(--radius-sm);
}

.live-dot {
  width: 8px;
  height: 8px;
  background: white;
  border-radius: 50%;
  animation: blink 1s ease-in-out infinite;
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
}
</style>

