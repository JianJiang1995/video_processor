<template>
  <div class="report-layer">
    <div class="report-shell">
      <header class="report-header">
        <button class="report-back" @click="$emit('back')">{{ t('report.back') }}</button>
        <div>
          <h1>{{ t('report.title') }}</h1>
          <p>{{ session?.video_name || session?.session_id || '' }}</p>
        </div>
        <button
          v-if="!initialReport"
          class="report-generate"
          :disabled="loading || !session?.session_id"
          @click="generateReport(true)"
        >
          {{ loading ? t('report.generating') : t('report.regenerate') }}
        </button>
      </header>

      <main class="report-main">
        <section class="report-panel report-summary-panel">
          <div class="report-panel-head">
            <span>{{ t('report.markdown') }}</span>
            <span v-if="report" class="report-source" :class="report.source">
              {{ reportSourceLabel }}
            </span>
          </div>
          <div v-if="loading" class="report-empty">{{ t('report.generatingHint') }}</div>
          <div v-else-if="error" class="report-error">{{ error }}</div>
          <article
            v-else-if="report?.markdown"
            class="report-markdown"
            v-html="renderedMarkdown"
          ></article>
          <div v-else class="report-empty">{{ t('report.empty') }}</div>
        </section>

        <aside class="report-panel report-events-panel">
          <div class="report-panel-head">
            <span>{{ t('report.keyNodes') }}</span>
            <span v-if="report?.event_count != null">{{ report.event_count }}</span>
          </div>
          <div v-if="!reportEvents.length" class="report-empty small">{{ t('report.noEvents') }}</div>
          <div
            v-for="event in reportEvents"
            :key="event.id || `${event.start_time}-${event.title}`"
            class="report-event"
            :class="[`severity-${event.severity || 'normal'}`]"
          >
            <div class="report-event-top">
              <span>{{ event.title }}</span>
              <time>{{ formatRange(event) }}</time>
            </div>
            <p>{{ event.summary }}</p>
          </div>
        </aside>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import axios from 'axios'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { useI18n } from '@/i18n'

const props = defineProps({
  session: { type: Object, default: null },
  language: { type: String, default: 'zh' },
  initialReport: { type: Object, default: null },
})

defineEmits(['back'])

const { t } = useI18n()
const loading = ref(false)
const error = ref('')
const report = ref(null)

const reportEvents = computed(() => Array.isArray(report.value?.events) ? report.value.events : [])
const renderedMarkdown = computed(() => {
  const source = String(report.value?.markdown || '')
  return source ? DOMPurify.sanitize(marked.parse(source, { gfm: true })) : ''
})

const reportSourceLabel = computed(() => {
  if (!report.value) return ''
  if (report.value.source === 'llm') {
    return report.value.model ? `${t('report.model')} ${report.value.model}` : t('report.modelGenerated')
  }
  if (report.value.source === 'offline_replay') return t('report.offlineLoaded')
  return t('report.fallbackGenerated')
})

function formatTime(seconds) {
  const value = Math.max(0, Math.round(Number(seconds || 0)))
  const m = Math.floor(value / 60)
  const s = value % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatRange(event) {
  return `${formatTime(event?.start_time)}-${formatTime(event?.end_time)}`
}

async function generateReport(force = false) {
  const sid = props.session?.session_id
  if (!sid) return
  loading.value = true
  error.value = ''
  try {
    const res = await axios.post(`/api/analysis/clinical-summary/${sid}`, {
      language: props.language || 'zh',
      force,
      max_windows: 260,
      max_events: 40,
      video_title: props.session?.video_name || sid,
    }, { timeout: 90000 })
    report.value = res.data
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message || t('report.failed')
  } finally {
    loading.value = false
  }
}

function loadInitialReport() {
  if (!props.initialReport?.markdown) return false
  report.value = props.initialReport
  error.value = ''
  loading.value = false
  return true
}

onMounted(() => {
  if (!loadInitialReport()) generateReport(false)
})
watch(() => props.session?.session_id, () => {
  report.value = null
  if (!loadInitialReport()) generateReport(false)
})
watch(() => props.initialReport, () => loadInitialReport(), { deep: false })
</script>

<style scoped>
.report-layer {
  position: fixed;
  inset: 0;
  z-index: 70;
  background: var(--bg-primary);
  color: var(--text-primary);
  display: flex;
  padding: 24px;
}

.report-shell {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.report-header {
  display: flex;
  align-items: center;
  gap: 18px;
  min-height: 72px;
}

.report-header h1 {
  margin: 0;
  font-size: 28px;
}

.report-header p {
  margin: 6px 0 0;
  color: var(--text-tertiary);
  font-size: 17px;
}

.report-back,
.report-generate {
  border: 1px solid var(--border-subtle);
  background: var(--bg-secondary);
  color: var(--text-primary);
  border-radius: 8px;
  padding: 12px 16px;
  font-size: 17px;
  cursor: pointer;
}

.report-generate {
  margin-left: auto;
  background: var(--accent-primary);
  color: var(--bg-primary);
  font-weight: 700;
}

.report-generate:disabled {
  opacity: 0.55;
  cursor: wait;
}

.report-main {
  min-height: 0;
  flex: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 420px;
  gap: 18px;
}

.report-panel {
  min-height: 0;
  border: 1px solid var(--border-subtle);
  background: var(--bg-secondary);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
}

.report-panel-head {
  height: 54px;
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 18px;
  font-size: 18px;
  font-weight: 700;
}

.report-source {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary);
}

.report-source.deterministic_fallback {
  color: var(--warning);
}

.report-markdown {
  flex: 1;
  overflow: auto;
  margin: 0;
  padding: 22px 28px;
  word-break: break-word;
  font-family: inherit;
  line-height: 1.72;
  font-size: 18px;
}

.report-markdown :deep(h1) {
  margin: 0 0 24px;
  font-size: 30px;
  line-height: 1.28;
}

.report-markdown :deep(h2) {
  margin: 30px 0 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-subtle);
  font-size: 22px;
  line-height: 1.35;
}

.report-markdown :deep(p) {
  margin: 10px 0;
  color: var(--text-secondary);
}

.report-markdown :deep(ul) {
  margin: 8px 0 18px;
  padding-left: 26px;
}

.report-markdown :deep(li) {
  margin: 8px 0;
  color: var(--text-secondary);
}

.report-markdown :deep(strong) {
  color: var(--text-primary);
}

.report-empty,
.report-error {
  padding: 24px;
  color: var(--text-tertiary);
  font-size: 17px;
}

.report-error {
  color: #ff7777;
}

.report-events-panel {
  overflow: hidden;
}

.report-events-panel .report-panel-head + * {
  margin-top: 0;
}

.report-event {
  margin: 12px 14px 0;
  padding: 14px;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  background: var(--bg-tertiary);
  box-shadow: inset 4px 0 0 var(--border-medium);
}

.report-event.severity-critical {
  border-color: rgba(255, 72, 72, 0.78);
  box-shadow: inset 4px 0 0 rgba(255, 72, 72, 0.95);
}

.report-event.severity-resolved {
  border-color: rgba(32, 200, 120, 0.76);
  box-shadow: inset 4px 0 0 rgba(32, 200, 120, 0.9);
}

.report-event.severity-safety {
  border-color: rgba(240, 170, 55, 0.76);
  box-shadow: inset 4px 0 0 rgba(240, 170, 55, 0.9);
}

.report-event-top {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  font-size: 16px;
  font-weight: 700;
}

.report-event time {
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  font-weight: 500;
}

.report-event p {
  margin: 10px 0 0;
  line-height: 1.55;
  color: var(--text-secondary);
  font-size: 15px;
}

@media (max-width: 1200px) {
  .report-main {
    grid-template-columns: 1fr;
  }
}
</style>
