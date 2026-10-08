<template>
  <div class="mode-selector">
    <div class="mode-header">
      <div class="logo-large">
        <ClipboardPlus :size="26" class="home-brand-icon" />
        <h1>Surg-R1 <span>{{ language === 'zh' ? '手术助手' : 'Surgical Assistant' }}</span></h1>
      </div>
      <LanguageSwitch />
    </div>

    <div class="mode-cards">
      <button class="mode-card" @click="selectMode('local')"><FolderOpen :size="19" />{{ t('mode.localVideo') }}</button>
      <button class="mode-card" @click="selectMode('stream')"><Radio :size="19" />{{ t('mode.liveStream') }}</button>
      <ReplayImportButton @begin="cancelAutoOpen" @loaded="$emit('replay', $event)" />
    </div>

    <AnalysisHistory @begin="cancelAutoOpen" @loaded="$emit('replay', $event)" @sessions="handleArchiveSessions" />

    <!-- Recent Sessions -->
    <details class="recent-sessions" v-if="otherSessions.length > 0" :open="!hasHistoryApi">
      <summary>{{ language === 'zh' ? '其他数据库会话' : 'Other Database Sessions' }} <span>{{ otherSessions.length }}</span></summary>
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
          v-for="session in otherSessions"
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
    </details>
    
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
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ClipboardPlus, FolderOpen, Radio } from '@lucide/vue'
import axios from 'axios'
import { useI18n } from '@/i18n'
import ReplayImportButton from './ReplayImportButton.vue'
import AnalysisHistory from './AnalysisHistory.vue'
import LanguageSwitch from './LanguageSwitch.vue'

const { t, language, setLanguage } = useI18n()

const emit = defineEmits(['select-mode', 'resume-session', 'replay'])
const autoOpenStream = import.meta.env.VITE_AUTO_OPEN_STREAM === '1'
let autoOpenTimer = null
const cancelAutoOpen = () => clearTimeout(autoOpenTimer)

const recentSessions = ref([])
const archivedSessions = ref([])
const hasHistoryApi = Boolean(window.electronAPI?.listAnalysisHistory)
const otherSessions = computed(() => recentSessions.value.filter(session => !archivedSessions.value.includes(session.session_id)))
const maybeAutoOpen = () => {
  if (autoOpenStream && !new URLSearchParams(window.location.search).has('replaySpec')) {
    autoOpenTimer = setTimeout(() => selectMode('stream'), 250)
  }
}
const handleArchiveSessions = ids => {
  archivedSessions.value = ids
  cancelAutoOpen()
  if (!ids.length) maybeAutoOpen()
}

// Delete modal state
const showDeleteModal = ref(false)
const deleteModalTitle = ref('')
const deleteModalMessage = ref('')
const isDeleting = ref(false)
const pendingDeleteSession = ref(null)  // null = delete all, session = delete single

const selectMode = (mode) => {
  cancelAutoOpen()
  emit('select-mode', mode)
}

const resumeSession = (session) => {
  cancelAutoOpen()
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
  if (!hasHistoryApi) maybeAutoOpen()
})
onUnmounted(cancelAutoOpen)
</script>

<style scoped>
.mode-selector {
  flex: 1;
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  padding: 28px 32px;
  background: #202020;
  overflow-y: auto;
}

.mode-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
  width: 100%;
  max-width: 1100px;
}

.logo-large {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.home-brand-icon { color: var(--accent-primary); flex-shrink: 0; }
.home-language { display: flex; gap: 2px; flex-shrink: 0; }
.home-language button { background: none; border: 0; border-radius: 4px; padding: 6px 8px; cursor: pointer; color: var(--text-secondary); font-size: 12px; }
.home-language button.active { background: var(--accent-primary); color: #202020; }

.logo-large h1 {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.logo-large h1 span {
  color: var(--accent-primary);
}

.mode-cards {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  max-width: 1100px;
  width: 100%;
}
.mode-cards :deep(.replay-import) { margin: 0; }
.mode-cards :deep(.replay-import-button) { min-height: 40px; font-size: 14px; }

.mode-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 0 16px;
  cursor: pointer;
  height: 40px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--text-primary);
}

.mode-card:hover {
  border-color: var(--accent-primary);
}

.recent-sessions {
  margin-top: 2rem;
  width: 100%;
  max-width: 1100px;
}
.recent-sessions > summary { cursor: pointer; color: var(--text-secondary); font-size: 14px; margin-bottom: 16px; }
.recent-sessions > summary span { margin-left: 8px; color: var(--text-tertiary); }

.sessions-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.sessions-header h3 {
  font-size: 0.9rem;
  text-transform: uppercase;
  letter-spacing: 0;
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
  .mode-selector { padding: 20px 14px; }
  .mode-header { align-items: flex-start; }
  .logo-large h1 { font-size: 20px; }
  .logo-large h1 span { display: block; font-size: 14px; margin-top: 5px; }
}
</style>
