<template>
  <section v-if="available" class="analysis-history" aria-labelledby="analysis-history-title" data-testid="analysis-history">
    <header class="history-header">
      <h2 id="analysis-history-title">{{ labels.title }} <span>{{ entries.length }}</span></h2>
      <div class="history-tools">
        <label class="history-search"><Search :size="15" /><input v-model="query" type="search" :placeholder="labels.search" :aria-label="labels.search" /></label>
        <button class="icon-button" :title="labels.refresh" :aria-label="labels.refresh" :disabled="loading || !!opening" @click="refresh"><RefreshCw :size="16" /></button>
      </div>
    </header>
    <p v-if="error" class="history-error" role="alert">{{ error }}</p>
    <p v-if="truncated" class="history-status">{{ labels.limited }}</p>
    <p v-if="loading && !entries.length" class="history-status" role="status">{{ labels.loading }}</p>
    <p v-else-if="!filtered.length" class="history-status">{{ query ? labels.noMatch : labels.empty }}</p>
    <div v-else class="history-list" :aria-busy="loading || !!opening">
      <article v-for="entry in visibleEntries" :key="entry.sessionId" class="history-row" :data-session-id="entry.sessionId">
        <FileVideo :size="22" class="history-video-icon" aria-hidden="true" />
        <div class="history-details">
          <button class="history-name" :title="entry.title" :disabled="!!opening || !entry.available" @click="open(entry)">{{ entry.sourceName || entry.title }}</button>
          <div class="history-meta">
            <time>{{ formatDate(entry.updatedAt) }}</time><span>{{ formatDuration(entry.duration) }}</span>
            <span v-if="entry.windowCount != null">{{ entry.windowCount }} {{ labels.windows }}</span>
            <span class="history-session">{{ entry.sessionId }}</span>
            <span v-if="!entry.available" class="missing">{{ labels.missing }}</span>
          </div>
        </div>
        <div class="history-actions">
          <button class="history-open" :disabled="!!opening || !entry.available" @click="open(entry)"><Play :size="14" />{{ opening === entry.sessionId ? labels.opening : labels.open }}</button>
          <button class="icon-button" :title="entry.reportAvailable ? labels.report : labels.noReport" :aria-label="labels.report" :disabled="!!opening || !entry.available || !entry.reportAvailable" @click="open(entry, true)"><FileText :size="17" /></button>
        </div>
      </article>
    </div>
    <nav v-if="pages > 1" class="history-pagination" :aria-label="labels.pages">
      <button class="icon-button" :disabled="page === 1" :title="labels.previous" :aria-label="labels.previous" @click="page--"><ChevronLeft :size="17" /></button>
      <span>{{ page }} / {{ pages }}</span>
      <button class="icon-button" :disabled="page === pages" :title="labels.next" :aria-label="labels.next" @click="page++"><ChevronRight :size="17" /></button>
    </nav>
  </section>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { Search, RefreshCw, FileVideo, FileText, Play, ChevronLeft, ChevronRight } from '@lucide/vue'
import { useI18n } from '../i18n.js'
import { loadReplayBundle } from '../utils/electronBridge.js'

const emit = defineEmits(['begin', 'loaded', 'sessions'])
const { language } = useI18n()
const available = Boolean(window.electronAPI?.listAnalysisHistory)
const entries = ref([])
const query = ref('')
const page = ref(1)
const loading = ref(false)
const opening = ref('')
const error = ref('')
const truncated = ref(false)
let disposed = false
const labels = computed(() => language.value === 'zh' ? {
  title: '历史分析记录', search: '搜索视频或会话', refresh: '刷新记录', loading: '正在读取历史分析',
  empty: '暂无已归档分析', noMatch: '没有匹配的分析记录', windows: '个窗口', missing: '回放文件缺失',
  open: '打开分析', opening: '正在打开', report: '查看报告', noReport: '尚无报告',
  previous: '上一页', next: '下一页', pages: '历史记录分页', failed: '无法打开分析', loadFailed: '无法读取历史记录',
  limited: '部分深层目录未扫描，可手动导入所需回放。',
} : {
  title: 'Analysis History', search: 'Search video or session', refresh: 'Refresh history', loading: 'Loading analysis history',
  empty: 'No archived analyses', noMatch: 'No matching analyses', windows: 'windows', missing: 'Replay files missing',
  open: 'Open analysis', opening: 'Opening', report: 'View report', noReport: 'No report yet',
  previous: 'Previous page', next: 'Next page', pages: 'History pages', failed: 'Cannot open analysis', loadFailed: 'Cannot load history',
  limited: 'Some deeply nested folders were not scanned. Import the replay to add it.',
})
const filtered = computed(() => {
  const term = query.value.trim().toLocaleLowerCase()
  return entries.value.filter(entry => `${entry.title} ${entry.sourceName || ''} ${entry.sessionId}`.toLocaleLowerCase().includes(term))
})
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / 8)))
const visibleEntries = computed(() => filtered.value.slice((page.value - 1) * 8, page.value * 8))
watch(query, () => { page.value = 1; emit('begin') })
function formatDuration(value) {
  const seconds = Math.floor(Number(value) || 0)
  return seconds >= 3600
    ? `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
    : `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}
function formatDate(value) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(language.value === 'zh' ? 'zh-CN' : 'en-GB', { hour12: false })
}
async function refresh() {
  if (!available || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await window.electronAPI.listAnalysisHistory()
    if (!result.success) throw new Error(result.error || labels.value.loadFailed)
    if (disposed) return
    entries.value = result.entries
    truncated.value = result.truncated
    page.value = Math.min(page.value, pages.value)
    emit('sessions', entries.value.map(entry => entry.sessionId))
  } catch (err) { if (!disposed) error.value = `${labels.value.loadFailed}: ${err.message}` }
  finally { loading.value = false }
}
async function open(entry, report = false) {
  if (opening.value) return
  opening.value = entry.sessionId
  error.value = ''
  emit('begin')
  try {
    const bundle = await loadReplayBundle(entry.specPath)
    if (!bundle?.success) throw new Error(bundle?.error || labels.value.failed)
    if (!disposed) emit('loaded', { ...bundle, initialView: report ? 'report' : 'analysis', spec: { ...bundle.spec, auto_play: false } })
  } catch (err) {
    await refresh()
    if (!disposed) error.value = `${labels.value.failed}: ${err.message}`
  } finally { opening.value = '' }
}
onMounted(refresh)
onUnmounted(() => { disposed = true })
</script>

<style scoped>
.analysis-history { width: 100%; max-width: 1100px; margin-top: 24px; letter-spacing: 0; }
.history-header, .history-tools, .history-actions, .history-meta, .history-pagination { display: flex; align-items: center; gap: 12px; }
.history-header { justify-content: space-between; flex-wrap: wrap; margin-bottom: 12px; }
h2 { margin: 0; font-size: 19px; }
h2 span { color: var(--text-tertiary); font-size: 13px; margin-left: 8px; font-weight: 400; }
.history-search { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border: 1px solid var(--border-subtle); border-radius: 4px; color: var(--text-secondary); }
.history-search input { background: none; border: 0; outline: none; color: var(--text-primary); width: 200px; min-width: 0; font-size: 13px; }
.history-search:focus-within { border-color: var(--accent-primary); }
.history-row { display: grid; grid-template-columns: 24px minmax(0, 1fr) auto; align-items: center; gap: 14px; min-height: 76px; padding: 12px 4px; border-bottom: 1px solid var(--border-subtle); }
.history-row:first-child { border-top: 1px solid var(--border-subtle); }
.history-video-icon { color: #7faeba; }
.history-name { display: block; background: none; border: 0; padding: 0; color: var(--text-primary); text-align: left; font-size: 15px; cursor: pointer; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-name:hover { color: var(--accent-primary); }
.history-meta { margin-top: 7px; flex-wrap: wrap; row-gap: 4px; font-size: 12px; color: var(--text-secondary); font-variant-numeric: tabular-nums; }
.history-session { color: var(--text-tertiary); }
.missing, .history-error { color: #ed8990; }
.history-error { overflow-wrap: anywhere; }
.history-open, .icon-button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 32px; cursor: pointer; border: 1px solid var(--border-subtle); border-radius: 4px; background: transparent; color: var(--text-primary); font-size: 12px; }
.history-open { padding: 0 10px; min-width: 104px; }
.icon-button { width: 32px; flex-shrink: 0; }
button:hover:not(:disabled) { background: var(--bg-hover, #303030); }
button:disabled { opacity: .45; cursor: default; }
.history-status { color: var(--text-secondary); font-size: 14px; padding: 20px 0; }
.history-pagination { justify-content: flex-end; margin-top: 12px; font-size: 13px; font-variant-numeric: tabular-nums; }
@media (max-width: 600px) {
  .history-header { align-items: flex-start; }
  .history-tools { width: 100%; }
  .history-search { flex: 1; min-width: 0; }
  .history-search input { width: 100%; }
  .history-row { grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
  .history-video-icon { display: none; }
  .history-actions { gap: 4px; }
  .history-open { min-width: 0; padding: 0 6px; }
  .history-session { display: none; }
}
</style>
