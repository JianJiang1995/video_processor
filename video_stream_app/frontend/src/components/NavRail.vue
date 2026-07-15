<template>
  <div class="nav-rail">
    <div class="nav-logo" @click="$emit('navigate', 'home')" :title="t('nav.home')">SR</div>

    <button
      class="nav-btn"
      :class="{ active: activeView === 'analysis' }"
      @click="$emit('navigate', 'analysis')"
    >
      <span class="nav-icon">&#x1F4F9;</span>
      <span class="nav-tooltip">{{ t('nav.analysis') }}</span>
    </button>

    <button
      class="nav-btn"
      :class="{ active: activeView === 'chat' }"
      @click="$emit('navigate', 'chat')"
    >
      <span class="nav-icon">&#x1F4AC;</span>
      <span class="nav-tooltip">{{ t('nav.chat') }}</span>
    </button>

    <button
      class="nav-btn"
      :class="{ active: activeView === 'overview' }"
      @click="$emit('navigate', 'overview')"
      :disabled="summaryCount === 0"
    >
      <span class="nav-icon">&#x2B1A;</span>
      <span class="nav-tooltip">{{ t('nav.gridOverview') }}</span>
      <span v-if="summaryCount > 0" class="nav-badge">{{ summaryCount }}</span>
    </button>

    <button
      v-if="summaryReady"
      class="nav-btn"
      :class="{ active: activeView === 'report' }"
      @click="$emit('navigate', 'report')"
    >
      <span class="nav-icon">&#x1F4CB;</span>
      <span class="nav-tooltip">{{ t('nav.report') }}</span>
    </button>

    <div class="nav-divider"></div>

    <!-- Analyze Start/Stop -->
    <button
      class="nav-btn analyze"
      :class="{ running: isAnalyzing }"
      @click="$emit('toggleAnalyze')"
      :title="isAnalyzing ? t('nav.stopAnalysis') : t('nav.startAnalysis')"
    >
      <span class="nav-icon">{{ isAnalyzing ? '&#x23F9;' : '&#x25B6;' }}</span>
      <span class="nav-tooltip">{{ isAnalyzing ? t('nav.stopAnalysis') : t('nav.startAnalysis') }}</span>
    </button>

    <div class="nav-spacer"></div>

    <button class="nav-btn" @click="$emit('navigate', 'settings')">
      <span class="nav-icon">&#x2699;</span>
      <span class="nav-tooltip">{{ t('nav.settings') }}</span>
    </button>
  </div>
</template>

<script setup>
import { useI18n } from '@/i18n'

const { t } = useI18n()

defineProps({
  activeView: { type: String, default: 'analysis' },
  isAnalyzing: { type: Boolean, default: false },
  summaryCount: { type: Number, default: 0 },
  summaryReady: { type: Boolean, default: false },
})

defineEmits(['navigate', 'toggleAnalyze'])
</script>

<style scoped>
.nav-rail {
  width: 64px;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 14px 0;
  gap: 6px;
  flex-shrink: 0;
}

.nav-logo {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--accent-primary), #e67e22);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 16px;
  color: var(--bg-primary);
  margin-bottom: 12px;
  cursor: pointer;
  user-select: none;
}

.nav-btn {
  width: 48px;
  height: 48px;
  border-radius: 10px;
  background: transparent;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  transition: all 0.15s;
}

.nav-btn:hover {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.nav-btn.active {
  background: var(--accent-glow);
  color: var(--accent-primary);
}

.nav-btn.active::after {
  content: '';
  position: absolute;
  left: -8px;
  width: 3px;
  height: 18px;
  background: var(--accent-primary);
  border-radius: 0 3px 3px 0;
}

.nav-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.nav-icon {
  font-size: 21px;
  line-height: 1;
}

.nav-tooltip {
  display: none;
  position: absolute;
  left: 60px;
  padding: 6px 12px;
  background: var(--bg-elevated);
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-primary);
  white-space: nowrap;
  z-index: 200;
  pointer-events: none;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}

.nav-btn:hover .nav-tooltip {
  display: block;
}

.nav-badge {
  position: absolute;
  top: 4px;
  right: 2px;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--accent-primary);
  color: var(--bg-primary);
  font-size: 11px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-mono);
}

/* Analyze button */
.nav-btn.analyze {
  background: var(--accent-primary);
  color: var(--bg-primary);
  font-size: 18px;
  margin-top: 4px;
  box-shadow: 0 2px 8px var(--accent-glow);
}

.nav-btn.analyze:hover {
  filter: brightness(1.1);
}

.nav-btn.analyze.running {
  animation: analyze-pulse 2s ease-in-out infinite;
}

@keyframes analyze-pulse {
  0%, 100% { box-shadow: 0 2px 8px var(--accent-glow); }
  50% { box-shadow: 0 2px 16px rgba(240, 160, 48, 0.4); }
}

.nav-divider {
  width: 24px;
  height: 1px;
  background: var(--border-subtle);
  margin: 6px 0;
}

.nav-spacer {
  flex: 1;
}
</style>
