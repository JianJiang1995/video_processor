<template>
  <div class="mode-selector">
    <div class="mode-header">
      <div class="logo-large">
        <div class="logo-icon-lg">🎬</div>
        <h1>Video<span>Analyzer</span></h1>
        <p class="subtitle">Real-time Surgical Video Analysis</p>
      </div>
    </div>

    <div class="mode-cards">
      <!-- Local Video Mode -->
      <div class="mode-card" @click="selectMode('local')">
        <div class="mode-icon">📁</div>
        <h2>本地视频</h2>
        <p>Local Video</p>
        <ul class="mode-features">
          <li>上传本地视频文件</li>
          <li>支持 MP4, AVI, MOV, MKV</li>
          <li>可暂停、回放、拖动进度</li>
        </ul>
        <div class="mode-action">
          <span>选择此模式 →</span>
        </div>
      </div>

      <!-- Live Stream Mode -->
      <div class="mode-card" @click="selectMode('stream')">
        <div class="mode-icon">📡</div>
        <h2>实时视频流</h2>
        <p>Live Stream</p>
        <ul class="mode-features">
          <li>连接手术室视频流</li>
          <li>支持 RTSP, HTTP, WebRTC</li>
          <li>实时分析与总结</li>
        </ul>
        <div class="mode-action">
          <span>选择此模式 →</span>
        </div>
      </div>
    </div>

    <!-- Recent Sessions -->
    <div class="recent-sessions" v-if="recentSessions.length > 0">
      <h3>最近的会话</h3>
      <div class="session-list">
        <div 
          v-for="session in recentSessions" 
          :key="session.session_id"
          class="session-item"
          @click="resumeSession(session)"
        >
          <span class="session-name">{{ session.video_name }}</span>
          <span class="session-status" :class="session.status">{{ session.status }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'

const emit = defineEmits(['select-mode', 'resume-session'])

const recentSessions = ref([])

const selectMode = (mode) => {
  emit('select-mode', mode)
}

const resumeSession = (session) => {
  emit('resume-session', session)
}

onMounted(async () => {
  try {
    const response = await axios.get('/api/video/sessions?limit=5')
    recentSessions.value = response.data
  } catch (error) {
    console.error('Failed to load sessions:', error)
  }
})
</script>

<style scoped>
.mode-selector {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  background: linear-gradient(135deg, var(--bg-primary) 0%, var(--bg-secondary) 100%);
}

.mode-header {
  text-align: center;
  margin-bottom: 3rem;
}

.logo-large {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
}

.logo-icon-lg {
  width: 80px;
  height: 80px;
  background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
  border-radius: var(--radius-lg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 2.5rem;
  margin-bottom: 1rem;
  box-shadow: var(--shadow-glow);
}

.logo-large h1 {
  font-size: 2.5rem;
  font-weight: 700;
  letter-spacing: -0.03em;
  margin: 0;
}

.logo-large h1 span {
  color: var(--accent-primary);
}

.subtitle {
  color: var(--text-secondary);
  font-size: 1.1rem;
  margin-top: 0.5rem;
}

.mode-cards {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 2rem;
  max-width: 800px;
  width: 100%;
}

.mode-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-xl);
  padding: 2rem;
  cursor: pointer;
  transition: all 0.3s ease;
  text-align: center;
}

.mode-card:hover {
  border-color: var(--accent-primary);
  transform: translateY(-4px);
  box-shadow: var(--shadow-glow);
}

.mode-icon {
  font-size: 3.5rem;
  margin-bottom: 1rem;
}

.mode-card h2 {
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0 0 0.25rem 0;
}

.mode-card > p {
  color: var(--text-tertiary);
  font-size: 0.9rem;
  margin: 0 0 1.5rem 0;
}

.mode-features {
  list-style: none;
  padding: 0;
  margin: 0 0 1.5rem 0;
  text-align: left;
}

.mode-features li {
  padding: 0.5rem 0;
  padding-left: 1.5rem;
  position: relative;
  color: var(--text-secondary);
  font-size: 0.9rem;
}

.mode-features li::before {
  content: '✓';
  position: absolute;
  left: 0;
  color: var(--accent-primary);
}

.mode-action {
  color: var(--accent-primary);
  font-weight: 500;
  font-size: 0.95rem;
}

.mode-card:hover .mode-action {
  text-decoration: underline;
}

.recent-sessions {
  margin-top: 3rem;
  width: 100%;
  max-width: 600px;
}

.recent-sessions h3 {
  font-size: 0.9rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--text-tertiary);
  margin-bottom: 1rem;
}

.session-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.session-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1rem;
  background: var(--bg-tertiary);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.2s ease;
}

.session-item:hover {
  background: var(--bg-elevated);
}

.session-name {
  font-size: 0.9rem;
}

.session-status {
  font-size: 0.75rem;
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  color: var(--text-tertiary);
}

.session-status.completed {
  background: rgba(0, 212, 170, 0.2);
  color: var(--accent-primary);
}

.session-status.processing {
  background: rgba(253, 203, 110, 0.2);
  color: var(--warning);
}

@media (max-width: 640px) {
  .mode-cards {
    grid-template-columns: 1fr;
  }
}
</style>




