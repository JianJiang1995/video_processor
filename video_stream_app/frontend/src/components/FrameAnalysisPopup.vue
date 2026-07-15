<template>
  <Transition name="popup">
    <div v-if="visible && frameData" class="frame-analysis-popup" :style="popupStyle" :class="{ 'with-image': frameData.has_saved_frame }">
      <div class="popup-header">
        <span class="popup-title">🔬 {{ t('frame.title') }}</span>
        <span class="popup-time">{{ formatTime(frameData.timestamp) }}</span>
        <button class="popup-close-btn" @click="$emit('close')" :title="t('frame.close')">×</button>
      </div>
      
      <div class="popup-body">
        <!-- Saved Frame Image -->
        <div v-if="frameData.has_saved_frame && frameData.image_base64" class="frame-image-container">
          <img 
            :src="`data:image/jpeg;base64,${frameData.image_base64}`" 
            :alt="t('frame.alt')"
            class="frame-image"
          />
        </div>
        
        <div class="popup-content">
          <!-- Surgical Phase -->
          <div class="analysis-item" v-if="frameData.surgical_phase">
            <div class="item-label">
              <span class="item-icon">📋</span>
              {{ t('frame.phase') }}
            </div>
            <div class="item-value phase">{{ frameData.surgical_phase }}</div>
          </div>
          
          <!-- Surgical Action -->
          <div class="analysis-item" v-if="frameData.surgical_action">
            <div class="item-label">
              <span class="item-icon">✂️</span>
              {{ t('frame.action') }}
            </div>
            <div class="item-value action">{{ frameData.surgical_action }}</div>
          </div>
          
          <!-- Tool Localization -->
          <div class="analysis-item" v-if="frameData.tool_localization">
            <div class="item-label">
              <span class="item-icon">🎯</span>
              {{ t('frame.toolLocalization') }}
            </div>
            <div class="item-value tools">{{ truncate(frameData.tool_localization, 150) }}</div>
          </div>
          
          <!-- Window Summary -->
          <div class="analysis-item window-summary" v-if="frameData.window_summary">
            <div class="item-label">
              <span class="item-icon">📝</span>
              {{ t('frame.windowSummary') }}
            </div>
            <div class="item-value summary">{{ truncate(frameData.window_summary, 200) }}</div>
          </div>
          
          <!-- Loading State -->
          <div v-if="isLoading" class="loading-state">
            <div class="loader-small"></div>
            <span>{{ t('frame.loading') }}</span>
          </div>
          
          <!-- Empty State -->
          <div v-if="!isLoading && !hasData" class="empty-state">
            <span>{{ t('frame.empty') }}</span>
          </div>
        </div>
      </div>
      
      <div class="popup-footer" v-if="frameData.window_id !== undefined && frameData.window_id !== null">
        <span class="window-badge">{{ t('app.windowPrefix') }} {{ frameData.window_id + 1 }}</span>
        <span v-if="frameData.window_start !== undefined" class="window-time">
          {{ formatTime(frameData.window_start) }} - {{ formatTime(frameData.window_end) }}
        </span>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '@/i18n'

const { t } = useI18n()

defineEmits(['close'])

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  frameData: {
    type: Object,
    default: null
  },
  isLoading: {
    type: Boolean,
    default: false
  },
  position: {
    type: Object,
    default: () => ({ x: 0, y: 0 })
  }
})

const hasData = computed(() => {
  if (!props.frameData) return false
  return props.frameData.surgical_phase || 
         props.frameData.surgical_action || 
         props.frameData.tool_localization ||
         props.frameData.window_summary ||
         props.frameData.has_saved_frame
})

const popupStyle = computed(() => {
  // Position popup near the mouse/drag position
  return {
    left: `${props.position.x}px`,
    top: `${props.position.y}px`
  }
})

const formatTime = (seconds) => {
  if (seconds === undefined || seconds === null) return '--:--'
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
}

const truncate = (text, length) => {
  if (!text) return ''
  if (text.length <= length) return text
  return text.substring(0, length) + '...'
}
</script>

<style scoped>
.frame-analysis-popup {
  position: fixed;
  z-index: 1000;
  min-width: 280px;
  max-width: 400px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-medium);
  border-radius: var(--radius-lg);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  overflow: hidden;
  transform: translateY(-100%) translateX(-50%);
  margin-top: -16px;
}

.frame-analysis-popup.with-image {
  max-width: 480px;
}

.popup-body {
  display: flex;
  flex-direction: column;
}

.frame-image-container {
  width: 100%;
  max-height: 200px;
  overflow: hidden;
  background: #000;
}

.frame-image {
  width: 100%;
  height: auto;
  object-fit: contain;
  max-height: 200px;
}

.popup-header {
  position: relative;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 2.5rem 0.75rem 1rem;  /* Extra padding-right for close button */
  background: var(--bg-tertiary);
  border-bottom: 1px solid var(--border-subtle);
}

.popup-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-primary);
}

.popup-time {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: var(--accent-primary);
  background: var(--bg-primary);
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
}

.popup-close-btn {
  position: absolute;
  right: 0.5rem;
  top: 50%;
  transform: translateY(-50%);
  width: 24px;
  height: 24px;
  border: none;
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-secondary);
  border-radius: 50%;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}

.popup-close-btn:hover {
  background: rgba(255, 100, 100, 0.3);
  color: #ff6b6b;
}


.popup-content {
  padding: 0.75rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.analysis-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.item-label {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.75rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.item-icon {
  font-size: 0.9rem;
}

.item-value {
  font-size: 0.85rem;
  line-height: 1.4;
  color: var(--text-primary);
  padding: 0.4rem 0.6rem;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  border-left: 2px solid var(--border-medium);
}

.item-value.phase {
  border-left-color: #4ecdc4;
  color: #4ecdc4;
}

.item-value.action {
  border-left-color: #ff6b6b;
}

.item-value.tools {
  border-left-color: #ffd93d;
  font-family: var(--font-mono);
  font-size: 0.75rem;
}

.item-value.summary {
  border-left-color: #9b59b6;
  font-size: 0.8rem;
  color: var(--text-secondary);
}

.window-summary {
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--border-subtle);
}

.loading-state {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem;
  color: var(--text-secondary);
  font-size: 0.85rem;
}

.loader-small {
  width: 16px;
  height: 16px;
  border: 2px solid var(--bg-primary);
  border-top-color: var(--accent-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.empty-state {
  padding: 1rem;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 0.85rem;
}

.popup-footer {
  padding: 0.5rem 1rem;
  background: var(--bg-tertiary);
  border-top: 1px solid var(--border-subtle);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.window-badge {
  font-size: 0.7rem;
  padding: 0.2rem 0.5rem;
  background: var(--accent-primary);
  color: var(--bg-primary);
  border-radius: var(--radius-sm);
  font-weight: 600;
}

.window-time {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: var(--text-tertiary);
}

/* Transition */
.popup-enter-active,
.popup-leave-active {
  transition: all 0.2s ease;
}

.popup-enter-from,
.popup-leave-to {
  opacity: 0;
  transform: translateY(-90%) translateX(-50%) scale(0.95);
}
</style>


