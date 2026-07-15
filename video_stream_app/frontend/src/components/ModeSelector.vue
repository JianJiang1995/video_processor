<template>
  <div class="mode-selector">
    <div class="mode-header">
      <div class="logo-large">
        <div class="logo-icon-lg">🏥</div>
        <h1>Surg-R1<span>手术助手</span></h1>
        <p class="subtitle">{{ t('mode.subtitle') }}</p>
      </div>
    </div>

    <div class="mode-cards">
      <!-- Local Video Mode -->
      <div class="mode-card" @click="selectMode('local')">
        <div class="mode-icon">📁</div>
        <h2>{{ t('mode.localVideo') }}</h2>
        <p>{{ t('mode.localVideoEn') }}</p>
        <ul class="mode-features">
          <li>{{ t('mode.localFeatureUpload') }}</li>
          <li>{{ t('mode.localFeatureFormats') }}</li>
          <li>{{ t('mode.localFeaturePlayback') }}</li>
        </ul>
        <div class="mode-action">
          <span>{{ t('mode.chooseMode') }} →</span>
        </div>
      </div>

      <!-- Live Stream Mode -->
      <div class="mode-card" @click="selectMode('stream')">
        <div class="mode-icon">📡</div>
        <h2>{{ t('mode.liveStream') }}</h2>
        <p>{{ t('mode.liveStreamEn') }}</p>
        <ul class="mode-features">
          <li>{{ t('mode.streamFeatureConnect') }}</li>
          <li>{{ t('mode.streamFeatureProtocols') }}</li>
          <li>{{ t('mode.streamFeatureRealtime') }}</li>
        </ul>
        <div class="mode-action">
          <span>{{ t('mode.chooseMode') }} →</span>
        </div>
      </div>
    </div>

    <!-- Recent Sessions -->
    <div class="recent-sessions" v-if="recentSessions.length > 0">
      <div class="sessions-header">
        <h3>{{ t('mode.recentSessions') }}</h3>
        <button 
          class="delete-all-btn"
          @click="deleteAllSessions"
          :title="t('mode.deleteAllTitleAttr')"
        >
          🗑️ {{ t('mode.deleteAll') }}
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
            :title="t('mode.deleteSessionTitleAttr')"
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
              <button class="modal-btn cancel" @click="cancelDelete">{{ t('mode.cancel') }}</button>
              <button class="modal-btn confirm" @click="confirmDelete" :disabled="isDeleting">
                {{ isDeleting ? t('mode.deleting') : t('mode.confirmDelete') }}
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
import { useI18n } from '@/i18n'

const { t } = useI18n()

const emit = defineEmits(['select-mode', 'resume-session'])
const autoOpenStream = import.meta.env.VITE_AUTO_OPEN_STREAM === '1'

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
  deleteModalTitle.value = t('mode.deleteSessionTitle')
  deleteModalMessage.value = t('mode.deleteSessionMessage', { name: session.video_name })
  showDeleteModal.value = true
}

// Delete all sessions
const deleteAllSessions = () => {
  pendingDeleteSession.value = null
  deleteModalTitle.value = t('mode.deleteAllTitle')
  deleteModalMessage.value = t('mode.deleteAllMessage', { count: recentSessions.value.length })
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
    alert(`${t('mode.deleteFailed')}: ${error.response?.data?.detail || error.message}`)
  } finally {
    isDeleting.value = false
    showDeleteModal.value = false
    pendingDeleteSession.value = null
  }
}

onMounted(() => {
  loadSessions()
  if (autoOpenStream) {
    setTimeout(() => {
      selectMode('stream')
    }, 250)
  }
})
</script>

<style scoped>
.mode-selector {
  flex: 1;
  width: 100%;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  padding: 3.2rem 3rem 2.4rem;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.025), transparent 240px),
    #202020;
  overflow-y: auto;
}

.mode-header {
  text-align: center;
  margin-bottom: 2.2rem;
  width: 100%;
}

.logo-large {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
}

.logo-icon-lg {
  width: 72px;
  height: 72px;
  background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 2.5rem;
  margin-bottom: 1rem;
  box-shadow: var(--shadow-glow);
}

.logo-large h1 {
  font-size: 2.2rem;
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
  gap: 1.4rem;
  max-width: 980px;
  width: 100%;
}

.mode-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 1.55rem 1.7rem;
  cursor: pointer;
  transition: all 0.3s ease;
  text-align: center;
  min-height: 330px;
  display: flex;
  flex-direction: column;
}

.mode-card:hover {
  border-color: var(--accent-primary);
  transform: translateY(-4px);
  box-shadow: var(--shadow-glow);
}

.mode-icon {
  font-size: 3rem;
  margin-bottom: 0.8rem;
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
  margin: 0 0 1.25rem 0;
  text-align: left;
  flex: 1;
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
  margin-top: 2rem;
  width: 100%;
  max-width: 980px;
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
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
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
