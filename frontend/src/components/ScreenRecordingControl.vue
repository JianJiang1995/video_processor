<template>
  <div v-if="available" class="screen-recording-control">
    <button class="record-command" :class="{ active: recording }" :disabled="busy || !initialized" @click="toggle"
      :title="recording ? labels.stop : labels.start" data-testid="screen-recording-toggle">
      <Square v-if="recording" :size="13" fill="currentColor" />
      <Circle v-else :size="13" />
      <span>{{ starting ? labels.starting : busy ? labels.saving : recording ? elapsed : labels.start }}</span>
    </button>
    <button class="record-icon" :title="labels.settings" :aria-label="labels.settings"
      :aria-expanded="settingsOpen" @click="settingsOpen = !settingsOpen"><Settings2 :size="15" /></button>
    <button class="record-icon" :title="labels.folder" :aria-label="labels.folder"
      @click="api.openRecordingFolder().catch(showError)"><FolderOpen :size="15" /></button>
    <div v-if="settingsOpen" class="record-settings">
      <label><input type="checkbox" v-model="autoRecord" @change="savePreference">{{ labels.automatic }}</label>
      <label class="record-rate">
        <span>{{ labels.frameRate }}</span>
        <input v-model="frameRate" type="number" :min="MIN_RECORDING_FPS" :max="MAX_RECORDING_FPS" step="1"
          :disabled="busy || recording || !initialized" :aria-label="labels.frameRate"
          data-testid="screen-recording-fps" @change="saveFrameRate">
        <span>fps</span>
      </label>
      <span class="record-format">≤ {{ RECORDING_MAX_WIDTH }} × {{ RECORDING_MAX_HEIGHT }} · {{ frameRate }} fps<br>MP4 (H.264) · {{ RECORDING_BITRATE / 1000000 }} Mbps</span>
    </div>
    <div v-if="message" class="record-message" :class="{ error: status.state === 'error' }" role="status">
      <span>{{ message }}</span>
      <button :aria-label="labels.close" @click="message = ''"><X :size="14" /></button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { Circle, Square, FolderOpen, Settings2, X } from '@lucide/vue'
import { useI18n } from '../i18n.js'
import { ScreenRecorder } from '../utils/screenRecorder.js'
import { previewAllowsRecording } from '../utils/previewPixels.js'
import { DEFAULT_RECORDING_FPS, MIN_RECORDING_FPS, MAX_RECORDING_FPS, normalizeRecordingFps,
  RECORDING_MAX_WIDTH, RECORDING_MAX_HEIGHT, RECORDING_BITRATE } from '../../electron/recordingOptions.mjs'

const props = defineProps({ sessionId: String, analyzing: Boolean, replay: Boolean, previewRequired: Boolean, previewMetrics: Object })
const api = window.electronAPI
const available = Boolean(api?.beginRecording)
const { language } = useI18n()
const labels = computed(() => language.value === 'zh' ? {
  start: '开始录屏', starting: '启动中', stop: '停止录屏', saving: '保存中', folder: '打开录屏文件夹',
  settings: '录屏设置', automatic: '分析时自动录制（资源允许时）', close: '关闭',
  frameRate: '录屏帧率', invalidFrameRate: '录屏帧率须为 1–60 的整数',
  saved: '录屏已保存', disk: '磁盘空间不足，未开始录屏', load: '当前负载较高，已跳过自动录屏',
  previewStop: '实时预览优先，已停止自动录屏',
  failed: '录屏失败', warning: '录屏文件已保留，但索引或格式检查未通过', minimized: '窗口已最小化，录屏已停止', diskStop: '磁盘空间不足，录屏已停止',
  unsupported: '当前桌面运行环境不支持 H.264 MP4 录屏，请更新桌面应用',
} : {
  start: 'Record', starting: 'Starting', stop: 'Stop recording', saving: 'Saving', folder: 'Open recordings',
  settings: 'Recording settings', automatic: 'Auto-record analysis when resources permit', close: 'Close',
  frameRate: 'Recording frame rate', invalidFrameRate: 'Frame rate must be an integer from 1 to 60',
  saved: 'Recording saved', disk: 'Not enough disk space to record', load: 'Auto-recording skipped: high system load',
  previewStop: 'Auto-recording stopped to prioritize live preview',
  failed: 'Recording failed', warning: 'Recording retained; indexing or format verification failed', minimized: 'Recording stopped: window minimized', diskStop: 'Recording stopped: low disk space',
  unsupported: 'This desktop runtime cannot record H.264 MP4. Please update the desktop app.',
})
const autoRecord = ref(false)
const frameRate = ref(DEFAULT_RECORDING_FPS)
const frameRateSaving = ref(false)
const initialized = ref(false)
let savedFrameRate = DEFAULT_RECORDING_FPS
const settingsOpen = ref(false)
const status = ref({ state: 'idle' })
const starting = ref(false)
const message = ref('')
const now = ref(Date.now())
const recording = computed(() => status.value.state === 'recording')
const busy = computed(() => starting.value || frameRateSaving.value || status.value.state === 'saving')
const elapsed = computed(() => {
  const seconds = Math.max(0, Math.floor((now.value - Date.parse(status.value.startedAt)) / 1000))
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
})
const showError = error => { message.value = `${labels.value.failed}: ${error.message}` }
const recorder = available ? new ScreenRecorder(api, { onState: value => {
  status.value = value
  if (value.state === 'recording') message.value = ''
  if (value.state === 'saved') message.value = `${labels.value.saved}: ${value.path}`
  if (value.state === 'saved' && value.warning) message.value = `${labels.value.warning}: ${value.path}`
  if (value.state === 'error') {
    message.value = value.error?.includes('recording_disk') ? labels.value.disk
      : value.error?.includes('recording_load') ? labels.value.load
        : value.error?.includes('recording_mp4_unsupported') ? labels.value.unsupported
        : `${labels.value.failed}: ${value.error}`
  }
  if (value.reason === 'window_minimized') message.value = `${labels.value.minimized}: ${value.path}`
  if (value.reason?.startsWith('disk_')) message.value = `${labels.value.diskStop}: ${value.path}`
  if (value.reason === 'preview_overload') message.value = `${labels.value.previewStop}: ${value.path}`
} }) : null
let stopTimer
let clockTimer
let unsubscribe
let healthySamples = 0
let unhealthySamples = 0
let automaticSuspended = false

async function start(automatic) {
  if (!recorder || !initialized.value || busy.value || recording.value) return
  if (automatic && (automaticSuspended || Number(frameRate.value) !== savedFrameRate || props.previewRequired &&
    (healthySamples < 3 || !previewAllowsRecording(props.previewMetrics)))) return
  starting.value = true
  try { await recorder.start(props.sessionId, automatic, frameRate.value) }
  finally { starting.value = false }
}
async function toggle() {
  clearTimeout(stopTimer)
  if (recording.value) {
    automaticSuspended = true
    await recorder.stop('user')
  }
  else await start(false)
}
async function savePreference() {
  automaticSuspended = false
  try { await api.setConfig('autoRecordAnalysis', autoRecord.value) }
  catch (error) { showError(error) }
  if (autoRecord.value && props.analyzing && !props.replay) await start(true)
}
async function saveFrameRate() {
  const value = normalizeRecordingFps(frameRate.value)
  if (value !== Number(frameRate.value)) {
    frameRate.value = savedFrameRate
    message.value = labels.value.invalidFrameRate
    return
  }
  frameRateSaving.value = true
  try {
    await api.setConfig('recordingFrameRate', value)
    frameRate.value = savedFrameRate = value
  } catch (error) {
    frameRate.value = savedFrameRate
    showError(error)
  } finally { frameRateSaving.value = false }
}
watch(() => props.analyzing, active => {
  clearTimeout(stopTimer)
  if (!initialized.value || props.replay) return
  if (active && autoRecord.value) void start(true)
  else if (!active && recorder?.job?.automatic) {
    stopTimer = setTimeout(() => void recorder.stop('analysis_stopped'), 8000)
  }
})
watch(() => props.sessionId, async (next, previous) => {
  healthySamples = unhealthySamples = 0
  automaticSuspended = false
  if (previous && previous !== next) {
    await recorder?.stop('session_changed')
    if (props.sessionId === next && props.analyzing && autoRecord.value && !props.replay) await start(true)
  }
})
watch(() => props.previewMetrics, metrics => {
  if (!props.previewRequired || !initialized.value || !metrics) return
  if (previewAllowsRecording(metrics)) {
    healthySamples++
    unhealthySamples = 0
  } else {
    healthySamples = 0
    unhealthySamples++
  }
  if (recorder?.job?.automatic && (unhealthySamples >= 3 || metrics.maxGapMs > 200)) {
    automaticSuspended = true
    void recorder.stop('preview_overload')
  } else if (props.analyzing && !props.replay && autoRecord.value) void start(true)
})
onMounted(async () => {
  if (!available) return
  clockTimer = setInterval(() => {
    now.value = Date.now()
    if (props.previewRequired && recorder?.job?.automatic &&
      now.value - (props.previewMetrics?.measuredAt || 0) > 6000) {
      automaticSuspended = true
      void recorder.stop('preview_overload')
    }
  }, 1000)
  unsubscribe = api.onRecordingStop(reason => { void recorder.stop(reason) })
  try {
    frameRate.value = savedFrameRate = normalizeRecordingFps(await api.getConfig('recordingFrameRate'))
    autoRecord.value = (await api.getConfig('autoRecordAnalysis')) === true
  }
  catch (error) { showError(error) }
  initialized.value = true
  if (autoRecord.value && props.analyzing && !props.replay) await start(true)
})
onUnmounted(() => {
  clearInterval(clockTimer)
  clearTimeout(stopTimer)
  unsubscribe?.()
  void recorder?.stop('view_closed')
})
</script>

<style scoped>
.screen-recording-control { display: flex; align-items: center; gap: 3px; position: relative; flex-shrink: 0; }
.record-command, .record-icon { display: inline-flex; align-items: center; justify-content: center; gap: 5px; height: 27px; border: 1px solid var(--border-subtle); border-radius: 4px; background: transparent; color: var(--text-secondary); cursor: pointer; font-size: 12px; }
.record-command { min-width: 84px; padding: 0 8px; font-variant-numeric: tabular-nums; }
.record-command.active { color: #e75a66; border-color: #ad3540; }
.record-command:disabled { opacity: .6; cursor: wait; }
.record-icon { width: 27px; border-color: transparent; }
.record-icon:hover { background: var(--bg-hover); }
.record-settings, .record-message { position: absolute; top: 34px; right: 0; z-index: 10000; padding: 12px; width: 320px; max-width: calc(100vw - 32px); border: 1px solid var(--border-subtle); border-radius: 6px; background: var(--bg-secondary, #232629); box-shadow: 0 4px 20px #0005; color: var(--text-primary); font-size: 12px; }
.record-settings label { display: flex; gap: 8px; align-items: center; }
.record-rate { margin-top: 12px; flex-wrap: wrap; }
.record-rate input { width: 70px; height: 28px; padding: 2px 6px; border: 1px solid var(--border-subtle); border-radius: 4px; background: var(--bg-primary); color: var(--text-primary); font: inherit; }
.record-rate input:disabled { opacity: .55; }
.record-format { display: block; margin: 9px 0 0 24px; color: var(--text-secondary); }
.record-message { top: 102px; display: flex; align-items: flex-start; gap: 8px; width: 420px; overflow-wrap: anywhere; }
.record-message.error { border-color: #ad3540; }
.record-message button { background: none; border: 0; color: inherit; cursor: pointer; padding: 0; }
</style>
