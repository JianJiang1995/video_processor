<template>
  <Teleport to="body">
    <div class="review-overlay" @click.self="$emit('close')">
      <section ref="dialog" class="review-dialog" role="dialog" aria-modal="true" aria-labelledby="review-title">
        <header class="review-header">
          <LanguageSwitch />
          <div class="review-heading">
            <h2 id="review-title">{{ t('review.title') }}</h2>
            <span class="review-range">{{ formatVideoTime(range.start_time) }} - {{ formatVideoTime(range.end_time) }}</span>
          </div>
          <button ref="closeButton" class="review-icon" :title="t('review.close')" :aria-label="t('review.close')" @click="$emit('close')"><X :size="20" /></button>
        </header>
        <div class="review-body">
          <div class="review-media">
            <div class="review-player">
              <VideoPlayer
                v-if="!playbackError"
                :key="playerKey"
                :session="session"
                :mode="mode"
                :currentTime="playbackTime"
                :isPlaying="playing"
                :isPaused="!playing"
                :loopWindow="playerRange"
                :streamEnded="false"
                :windowDuration="windowDuration"
                :reviewMode="true"
                @timeupdate="playbackTime = $event"
                @reviewToggle="playing = !playing"
                @exitLoop="$emit('close')"
                @loopLoadFailed="playbackError = $event || t('review.unavailable')"
              />
              <div v-else class="review-error" role="status">
                <p>{{ playbackError }}</p>
                <button class="review-retry" @click="retry"><RotateCcw :size="16" />{{ t('video.retry') }}</button>
              </div>
            </div>
            <div class="review-controls">
              <button class="review-icon" :disabled="!!playbackError" :title="t(playing ? 'review.pause' : 'review.play')" :aria-label="t(playing ? 'review.pause' : 'review.play')" @click="playing = !playing"><Pause v-if="playing" :size="19" /><Play v-else :size="19" /></button>
              <span class="review-position">{{ formatVideoTime(playbackTime) }}</span>
              <span class="review-loop"><Repeat2 :size="16" />{{ t('review.loop') }}</span>
            </div>
          </div>
          <aside class="review-sidebar">
            <section class="review-analysis">
              <h3>{{ t('overview.analysisSummary') }}</h3>
              <div class="review-analysis-scroll">
                <article v-for="summary in selectedSummaries" :key="summary.window_id" class="review-analysis-record">
                  <div class="review-record-header">
                    <div class="review-record-meta">{{ t('chat.window') }} {{ Number(summary.window_id) + 1 }} · {{ formatVideoTime(summary.start_time) }} - {{ formatVideoTime(summary.end_time) }}</div>
                    <button class="review-reanalyze" :disabled="reviewBusy" :aria-busy="reviewState(summary).status === 'pending'" @click="$emit('reanalyze', summary.window_id)">
                      <RefreshCw :size="14" :class="{ spinning: reviewState(summary).status === 'pending' }" />
                      {{ t(reviewState(summary).status === 'pending' ? 'review.analyzing' : 'review.reanalyze') }}
                    </button>
                  </div>
                  <div class="review-phase">{{ windowPhaseLabel(summary, language) }}</div>
                  <p>{{ formatWindowNarrative(localize(formatWindowNarrative(summary.original_summary || summary.summary), 110)) || t('review.noAnalysis') }}</p>
                  <div v-if="reviewState(summary).error" class="review-result error" role="alert">{{ t(reviewState(summary).error) }}</div>
                  <div v-else-if="reviewState(summary).status === 'done' || summary.others?.manual_review" class="review-result" role="status">{{ t(summary.others?.manual_review?.kind === 'offline_narrative_reconciliation' ? 'review.reconciled' : 'review.updated') }}</div>
                </article>
                <p v-if="!selectedSummaries.length">{{ t('review.noAnalysis') }}</p>
              </div>
            </section>
            <section class="review-chat">
              <h3>{{ t('overview.smartChat') }}</h3>
              <ChatPanel
                :sessionId="session?.session_id || ''"
                :summaries="summaries"
                :reviewContext="chatContext"
                @review="$emit('review', $event)"
                @seekToWindow="openWindow"
              />
            </section>
          </aside>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import LanguageSwitch from './LanguageSwitch.vue'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { X, Pause, Play, Repeat2, RotateCcw, RefreshCw } from '@lucide/vue'
import VideoPlayer from './VideoPlayer.vue'
import ChatPanel from './ChatPanel.vue'
import { formatVideoTime } from '@/utils/chatTimestamps'
import { useI18n } from '@/i18n'
import { formatWindowNarrative, windowPhaseLabel } from '../utils/windowNarrative.js'
import { useLocalizedText } from '../utils/localizedText.js'

const props = defineProps({
  session: Object,
  range: { type: Object, required: true },
  summaries: { type: Array, default: () => [] },
  mode: { type: String, default: 'stream' },
  windowDuration: { type: Number, default: 5 },
  reviewJobs: { type: Object, default: () => ({}) },
  reviewBusy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'review', 'reanalyze'])
const { t, language } = useI18n()
const { localize } = useLocalizedText()
const dialog = ref(null)
const closeButton = ref(null)
const previousFocus = document.activeElement
const playing = ref(true)
const playbackTime = ref(props.range.start_time)
const playbackError = ref('')
const playerKey = ref(0)
const reviewState = summary => props.reviewJobs[`${props.session?.session_id}:${summary.window_id}`] || {}
const selectedSummaries = computed(() => props.summaries
  .filter(s => s.start_time < props.range.end_time && s.end_time > props.range.start_time)
  .sort((a, b) => a.start_time - b.start_time))
const playerRange = computed(() => ({ ...props.range, window_id: props.range.window_id ?? -1 }))
const chatContext = computed(() => ({
  start_time: props.range.start_time, end_time: props.range.end_time,
  ...(props.range.window_id != null ? { window_id: props.range.window_id } : {}),
}))
const retry = () => {
  playbackError.value = ''
  playbackTime.value = props.range.start_time
  playing.value = true
  playerKey.value++
  nextTick(() => closeButton.value?.focus())
}
const openWindow = id => {
  const summary = props.summaries.find(s => s.window_id === id)
  if (summary) emit('review', summary)
}
const onKeydown = event => {
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    emit('close')
  }
  if (event.key === 'Tab') {
    const controls = [...dialog.value.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled)')].filter(el => el.getClientRects().length)
    const first = controls[0]
    const last = controls.at(-1)
    if (event.shiftKey && (document.activeElement === first || !dialog.value.contains(document.activeElement))) { event.preventDefault(); last?.focus() }
    if (!event.shiftKey && (document.activeElement === last || !dialog.value.contains(document.activeElement))) { event.preventDefault(); first?.focus() }
  }
}
watch(() => [props.range.start_time, props.range.end_time], retry)
onMounted(() => {
  document.addEventListener('keydown', onKeydown, true)
  nextTick(() => closeButton.value?.focus())
})
onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown, true)
  if (previousFocus?.isConnected && !document.querySelector('.review-dialog')) previousFocus.focus()
})
</script>

<style scoped>
.review-overlay { position: fixed; inset: 0; z-index: 12000; display: flex; align-items: center; justify-content: center; padding: 20px; background: rgb(0 0 0 / 72%); }
.review-dialog { width: min(1700px, 100%); height: min(940px, 94dvh); display: flex; flex-direction: column; min-width: 0; background: var(--bg-primary); border: 1px solid var(--border-subtle); border-radius: 8px; overflow: hidden; box-shadow: 0 18px 65px rgb(0 0 0 / 45%); color: var(--text-primary); }
.review-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 8px 14px; border-bottom: 1px solid var(--border-subtle); }
.review-heading { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
.review-heading h2 { font-size: 16px; margin: 0; }
.review-range, .review-position { font-size: 14px; font-variant-numeric: tabular-nums; }
.review-range { color: var(--text-secondary); }
.review-body { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(340px, 1fr); flex: 1; min-height: 0; }
.review-media { display: flex; flex-direction: column; min-width: 0; min-height: 0; background: #0a0c0d; }
.review-player { flex: 1; min-height: 0; position: relative; }
.review-player :deep(.video-wrapper), .review-player :deep(.video-container) { height: 100%; min-height: 0; }
.review-controls { display: flex; gap: 12px; align-items: center; padding: 6px 12px; border-top: 1px solid var(--border-subtle); }
.review-position { min-width: 66px; }
.review-loop { margin-left: auto; display: flex; gap: 6px; align-items: center; color: #75cfb2; font-size: 13px; }
.review-icon { display: grid; place-items: center; width: 34px; height: 34px; flex-shrink: 0; border: 1px solid transparent; border-radius: 4px; background: transparent; color: var(--text-primary); cursor: pointer; }
.review-icon:hover { background: var(--bg-tertiary); }
.review-icon:focus-visible { outline: 2px solid #69cbd5; }
.review-icon:disabled { opacity: .4; cursor: default; }
.review-sidebar { min-width: 0; min-height: 0; display: grid; grid-template-rows: minmax(115px, .8fr) minmax(220px, 1.2fr); border-left: 1px solid var(--border-subtle); }
.review-analysis, .review-chat { min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.review-sidebar h3 { flex-shrink: 0; margin: 0; font-size: 14px; padding: 11px 16px; border-bottom: 1px solid var(--border-subtle); }
.review-analysis-scroll { overflow-y: auto; padding: 0 16px 12px; }
.review-analysis-record { padding: 12px 0; border-bottom: 1px solid var(--border-subtle); }
.review-analysis-record:last-child { border-bottom: 0; }
.review-record-meta { font-size: 12px; font-variant-numeric: tabular-nums; color: var(--text-tertiary); }
.review-phase { font-size: 12px; color: var(--text-secondary); margin-top: 8px; }
.review-record-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
.review-reanalyze { display: inline-flex; align-items: center; gap: 5px; padding: 5px 8px; border: 1px solid var(--border-subtle); border-radius: 4px; background: var(--bg-tertiary); color: var(--text-primary); font-size: 12px; cursor: pointer; }
.review-reanalyze:disabled { opacity: .55; cursor: wait; }
.review-result { color: #75cfb2; font-size: 12px; margin-top: 8px; overflow-wrap: anywhere; }
.review-result.error { color: #ff9797; }
.spinning { animation: review-spin 1s linear infinite; }
@keyframes review-spin { to { transform: rotate(360deg); } }
.review-analysis p { font-size: 15px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; margin: 7px 0 0; }
.review-chat { border-top: 1px solid var(--border-subtle); }
.review-chat :deep(.chat-panel) { flex: 1; min-height: 0; }
.review-chat :deep(.chat-msg) { font-size: 14px; padding: 10px 12px; line-height: 1.6; }
.review-chat :deep(.chat-msg-context), .review-chat :deep(.chat-msg-time) { font-size: 11px; }
.review-chat :deep(.chat-empty-icon) { display: none; }
.review-chat :deep(.chat-empty-text) { font-size: 14px; }
.review-chat :deep(.chat-hint-chip) { font-size: 12px; padding: 6px 9px; border-radius: 4px; }
.review-chat :deep(.chat-input) { font-size: 14px; padding: 10px; border-radius: 4px; }
.review-chat :deep(.chat-send-btn), .review-chat :deep(.chat-voice-btn) { width: 34px; height: 34px; flex-shrink: 0; font-size: 16px; }
.review-error { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 20px; gap: 12px; text-align: center; }
.review-retry { display: flex; gap: 8px; align-items: center; background: var(--bg-tertiary); border: 1px solid var(--border-subtle); padding: 8px 12px; color: var(--text-primary); border-radius: 4px; cursor: pointer; }
@media (max-width: 760px) {
  .review-overlay { padding: 6px; }
  .review-dialog { height: 98dvh; }
  .review-body { grid-template-columns: 1fr; grid-template-rows: minmax(140px, 36%) minmax(0, 1fr); }
  .review-sidebar { border-left: 0; grid-template-rows: minmax(75px, .6fr) minmax(140px, 1fr); }
  .review-sidebar h3 { padding: 6px 12px; }
  .review-chat :deep(.chat-messages) { padding: 8px; }
  .review-chat :deep(.chat-input-bar) { padding: 7px; }
}
</style>
