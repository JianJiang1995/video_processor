<template>
  <div v-if="available" class="screen-recording-control">
    <button class="record-command" :class="{ active: recording }" :disabled="busy" @click="toggle"
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
      <span class="record-format">≤ 1920 × 1080 · 15 fps · WebM</span>
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

const props = defineProps({ sessionId: String, analyzing: Boolean, replay: Boolean })
const api = window.electronAPI
const available = Boolean(api?.beginRecording)
const { language } = useI18n()
const labels = computed(() => language.value === 'zh' ? {
  start: '开始录屏', starting: '启动中', stop: '停止录屏', saving: '保存中', folder: '打开录屏文件夹',
  settings: '录屏设置', automatic: '分析时自动录制（资源允许时）', close: '关闭',
  saved: '录屏已保存', disk: '磁盘空间不足，未开始录屏', load: '当前负载较高，已跳过自动录屏',
  failed: '录屏失败', warning: '录屏已保存，但索引未完成', minimized: '窗口已最小化，录屏已停止', diskStop: '磁盘空间不足，录屏已停止',
} : {
  start: 'Record', starting: 'Starting', stop: 'Stop recording', saving: 'Saving', folder: 'Open recordings',
  settings: 'Recording settings', automatic: 'Auto-record analysis when resources permit', close: 'Close',
  saved: 'Recording saved', disk: 'Not enough disk space to record', load: 'Auto-recording skipped: high system load',
  failed: 'Recording failed', warning: 'Recording saved without a complete index', minimized: 'Recording stopped: window minimized', diskStop: 'Recording stopped: low disk space',
})
const autoRecord = ref(false)
const settingsOpen = ref(false)
const status = ref({ state: 'idle' })
const starting = ref(false)
const message = ref('')
const now = ref(Date.now())
const recording = computed(() => status.value.state === 'recording')
const busy = computed(() => starting.value || status.value.state === 'saving')
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
        : `${labels.value.failed}: ${value.error}`
  }
  if (value.reason === 'window_minimized') message.value = `${labels.value.minimized}: ${value.path}`
  if (value.reason?.startsWith('disk_')) message.value = `${labels.value.diskStop}: ${value.path}`
} }) : null
let stopTimer
let clockTimer
let unsubscribe
let initialized = false

async function start(automatic) {
  if (!recorder || busy.value || recording.value) return
  starting.value = true
  try { await recorder.start(props.sessionId, automatic) }
  finally { starting.value = false }
}
async function toggle() {
  clearTimeout(stopTimer)
  if (recording.value) await recorder.stop('user')
  else await start(false)
}
async function savePreference() {
  try { await api.setConfig('autoRecordAnalysis', autoRecord.value) }
  catch (error) { showError(error) }
  if (autoRecord.value && props.analyzing && !props.replay) await start(true)
}
watch(() => props.analyzing, active => {
  clearTimeout(stopTimer)
  if (!initialized || props.replay) return
  if (active && autoRecord.value) void start(true)
  else if (!active && recorder?.job?.automatic) {
    stopTimer = setTimeout(() => void recorder.stop('analysis_stopped'), 8000)
  }
})
watch(() => props.sessionId, async (next, previous) => {
  if (previous && previous !== next) {
    await recorder?.stop('session_changed')
    if (props.sessionId === next && props.analyzing && autoRecord.value && !props.replay) await start(true)
  }
})
onMounted(async () => {
  if (!available) return
  clockTimer = setInterval(() => { now.value = Date.now() }, 1000)
  unsubscribe = api.onRecordingStop(reason => { void recorder.stop(reason) })
  try { autoRecord.value = (await api.getConfig('autoRecordAnalysis')) === true }
  catch (error) { showError(error) }
  initialized = true
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
.record-format { display: block; margin: 9px 0 0 24px; color: var(--text-secondary); }
.record-message { top: 102px; display: flex; align-items: flex-start; gap: 8px; width: 420px; overflow-wrap: anywhere; }
.record-message.error { border-color: #ad3540; }
.record-message button { background: none; border: 0; color: inherit; cursor: pointer; padding: 0; }
</style>
