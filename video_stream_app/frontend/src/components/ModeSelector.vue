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
      <div class="sessions-header">
        <h3>最近的会话</h3>
        <button 
          class="delete-all-btn"
          @click="deleteAllSessions"
          title="删除所有会话"
        >
          🗑️ 删除全部
        </button>
      </div>
      <div class="session-list">
        <div 
          v-for="session in recentSessions" 
          :key="session.session_id"
          class="session-item"
        >
          <div class="session-info" @click="resumeSession(session)">
            <span class="session-name">{{ session.video_name }}</span>
            <span class="session-status" :class="session.status">{{ session.status }}</span>
          </div>
          <button 
            class="delete-btn"
            @click.stop="deleteSession(session)"
            title="删除此会话"
          >
            ✕
          </button>
        </div>
      </div>
    </div>
    
    <!-- Delete Confirmation Modal -->
    <Teleport to="body">
      <Transition name="modal-fade">
        <div v-if="showDeleteModal" class="modal-overlay" @click.self="cancelDelete">
          <div class="modal-content">
            <div class="modal-icon">⚠️</div>
            <div class="modal-title">{{ deleteModalTitle }}</div>
            <div class="modal-message">{{ deleteModalMessage }}</div>
            <div class="modal-actions">
              <button class="modal-btn cancel" @click="cancelDelete">取消</button>
              <button class="modal-btn confirm" @click="confirmDelete" :disabled="isDeleting">
                {{ isDeleting ? '删除中...' : '确认删除' }}
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'

const emit = defineEmits(['select-mode', 'resume-session'])

const recentSessions = ref([])

// Delete modal state
const showDeleteModal = ref(false)
const deleteModalTitle = ref('')
const deleteModalMessage = ref('')
const isDeleting = ref(false)
const pendingDeleteSession = ref(null)  // null = delete all, session = delete single

const selectMode = (mode) => {
  emit('select-mode', mode)
}

const resumeSession = (session) => {
  emit('resume-session', session)
}

const loadSessions = async () => {
  try {
    const response = await axios.get('/api/video/sessions?limit=10')
    recentSessions.value = response.data
  } catch (error) {
    console.error('Failed to load sessions:', error)
  }
}

// Delete single session
const deleteSession = (session) => {
  pendingDeleteSession.value = session
  deleteModalTitle.value = '删除会话'
  deleteModalMessage.value = `确定要删除会话 "${session.video_name}" 吗？\n\n这将删除该会话的所有分析数据和保存的帧图片，此操作不可撤销。`
  showDeleteModal.value = true
}

// Delete all sessions
const deleteAllSessions = () => {
  pendingDeleteSession.value = null
  deleteModalTitle.value = '删除所有会话'
  deleteModalMessage.value = `确定要删除所有 ${recentSessions.value.length} 个会话吗？\n\n这将删除所有分析数据和保存的帧图片，此操作不可撤销！`
  showDeleteModal.value = true
}

// Cancel delete
const cancelDelete = () => {
  showDeleteModal.value = false
  pendingDeleteSession.value = null
}

// Confirm delete
const confirmDelete = async () => {
  isDeleting.value = true
  
  try {
    if (pendingDeleteSession.value) {
      // Delete single session
      await axios.delete(`/api/video/session/${pendingDeleteSession.value.session_id}`)
    } else {
      // Delete all sessions
      await axios.delete('/api/video/sessions/all')
    }
    
    // Refresh session list
    await loadSessions()
    
  } catch (error) {
    console.error('Failed to delete session(s):', error)
    alert('删除失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    isDeleting.value = false
    showDeleteModal.value = false
    pendingDeleteSession.value = null
  }
}

onMounted(loadSessions)
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

.sessions-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.sessions-header h3 {
  font-size: 0.9rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--text-tertiary);
  margin: 0;
}

.delete-all-btn {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.4rem 0.75rem;
  background: rgba(255, 107, 107, 0.1);
  border: 1px solid rgba(255, 107, 107, 0.3);
  border-radius: var(--radius-sm);
  color: #ff6b6b;
  font-size: 0.75rem;
  cursor: pointer;
  transition: all 0.2s ease;
}

.delete-all-btn:hover {
  background: rgba(255, 107, 107, 0.2);
  border-color: #ff6b6b;
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
  transition: all 0.2s ease;
}

.session-item:hover {
  background: var(--bg-elevated);
}

.session-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex: 1;
  cursor: pointer;
  gap: 1rem;
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

.delete-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  background: transparent;
  border: none;
  border-radius: 50%;
  color: var(--text-tertiary);
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s ease;
  margin-left: 0.5rem;
  opacity: 0.5;
}

.session-item:hover .delete-btn {
  opacity: 1;
}

.delete-btn:hover {
  background: rgba(255, 107, 107, 0.2);
  color: #ff6b6b;
}

/* Delete Confirmation Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.modal-content {
  background: linear-gradient(145deg, var(--bg-elevated, #1a1a2e) 0%, var(--bg-primary, #12122a) 100%);
  border: 2px solid rgba(255, 107, 107, 0.5);
  border-radius: var(--radius-lg, 16px);
  padding: 2rem;
  max-width: 400px;
  width: 90%;
  text-align: center;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}

.modal-icon {
  font-size: 3rem;
  margin-bottom: 1rem;
}

.modal-title {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--text-primary, #fff);
  margin-bottom: 0.75rem;
}

.modal-message {
  font-size: 0.9rem;
  color: var(--text-secondary, #aaa);
  line-height: 1.6;
  white-space: pre-line;
  margin-bottom: 1.5rem;
}

.modal-actions {
  display: flex;
  gap: 1rem;
  justify-content: center;
}

.modal-btn {
  padding: 0.6rem 1.5rem;
  border-radius: var(--radius-md, 8px);
  font-size: 0.9rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
}

.modal-btn.cancel {
  background: var(--bg-tertiary, rgba(255, 255, 255, 0.1));
  border: 1px solid var(--border-subtle, rgba(255, 255, 255, 0.1));
  color: var(--text-secondary, #aaa);
}

.modal-btn.cancel:hover {
  background: var(--bg-elevated);
}

.modal-btn.confirm {
  background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
  border: none;
  color: white;
}

.modal-btn.confirm:hover:not(:disabled) {
  background: linear-gradient(135deg, #c0392b 0%, #a93226 100%);
  transform: translateY(-1px);
}

.modal-btn.confirm:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* Modal animation */
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.2s ease;
}

.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}

.modal-fade-enter-active .modal-content,
.modal-fade-leave-active .modal-content {
  transition: transform 0.2s ease;
}

.modal-fade-enter-from .modal-content,
.modal-fade-leave-to .modal-content {
  transform: scale(0.9);
}

@media (max-width: 640px) {
  .mode-cards {
    grid-template-columns: 1fr;
  }
}
</style>




