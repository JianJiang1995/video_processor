<template>
  <div class="cached-history-player" @click="$emit('toggle')">
    <img v-if="imageUrl" :src="imageUrl" alt="" @error="imageUrl = ''; missing = true; $emit('pause')" />
    <p v-else role="status">{{ t(missing ? 'video.historyMissing' : 'video.loadingPlayback') }}</p>
    <span class="history-label">{{ t('video.cachedReview') }}</span>
  </div>
</template>

<script setup>
import { ref, watch, onUnmounted } from 'vue'
import axios from 'axios'
import { apiUrl } from '@/utils/electronBridge'
import { useI18n } from '@/i18n'

const props = defineProps({ sessionId: String, currentTime: Number, playing: Boolean,
  seekNonce: Number, start: Number, end: Number })
const emit = defineEmits(['timeupdate', 'pause', 'toggle'])
const { t } = useI18n()
const imageUrl = ref('')
const missing = ref(false)
const chunks = new Map()
let controller = new AbortController()
let generation = 0
let position = props.currentTime
let lastTick = performance.now()
let rendering = false
let shownUrl = ''

async function framesAt(time) {
  const key = Math.floor(time / 5)
  if (!chunks.has(key)) {
    const request = axios.get(`/api/analysis/frames-batch/${props.sessionId}`, {
      params: { start: key * 5, end: (key + 1) * 5, max_frames: 300, use_preview: true },
      timeout: 8000, signal: controller.signal,
    }).then(({ data }) => (data.frames || []).filter(f => Number.isFinite(f.timestamp)))
    chunks.set(key, request)
    // Never retain a complete operation's decoded frames in the renderer.
    while (chunks.size > 4) chunks.delete(chunks.keys().next().value)
    request.catch(() => { if (chunks.get(key) === request) chunks.delete(key) })
  }
  return chunks.get(key)
}

async function render(time) {
  if (rendering) return false
  rendering = true
  const ownGeneration = generation
  try {
    const frames = await framesAt(time)
    if (ownGeneration !== generation) return false
    const nearest = frames.reduce((best, frame) =>
      !best || Math.abs(frame.timestamp - time) < Math.abs(best.timestamp - time) ? frame : best, null)
    // A missing cache interval must not masquerade as a frozen live image.
    if (!nearest || Math.abs(nearest.timestamp - time) > 1) {
      imageUrl.value = ''; missing.value = true; emit('pause')
      return false
    }
    const url = apiUrl(nearest.url)
    if (url !== shownUrl) {
      const img = new Image()
      img.src = url
      await img.decode()
      if (ownGeneration !== generation) return false
      imageUrl.value = url
      shownUrl = url
    }
    missing.value = false
    // Prefetch just one neighbouring chunk, independent of capture/analysis.
    if (time % 5 > 3 && (Math.floor(time / 5) + 1) * 5 < props.end) {
      framesAt((Math.floor(time / 5) + 1) * 5).catch(() => {})
    }
    return true
  } catch (error) {
    if (ownGeneration === generation && !axios.isCancel(error)) {
      imageUrl.value = ''; missing.value = true; emit('pause')
    }
    return false
  } finally {
    if (ownGeneration === generation) rendering = false
  }
}

function seek() {
  generation++
  controller.abort()
  controller = new AbortController()
  chunks.clear()
  rendering = false
  imageUrl.value = ''; shownUrl = ''; missing.value = false
  position = Math.min(props.end, Math.max(props.start, props.currentTime))
  lastTick = performance.now()
  render(position)
}
watch(() => props.seekNonce, seek)
watch(() => props.playing, () => { lastTick = performance.now() })
watch(() => props.sessionId, seek)
seek()
const timer = setInterval(async () => {
  const now = performance.now()
  const delta = Math.min(.2, (now - lastTick) / 1000)
  lastTick = now
  if (!props.playing || rendering || missing.value) return
  const target = Math.min(props.end, Math.max(props.start, position + delta))
  if (await render(target)) {
    position = target
    emit('timeupdate', target)
    if (target >= props.end) emit('pause')
  }
}, 66)
onUnmounted(() => { generation++; clearInterval(timer); controller.abort(); chunks.clear() })
</script>

<style scoped>
.cached-history-player { position: relative; flex: 1; min-height: 0; width: 100%; display: flex; align-items: center; justify-content: center; background: #111; color: #eee; overflow: hidden; }
.cached-history-player img { width: 100%; height: 100%; object-fit: contain; min-height: 0; }
.history-label { position: absolute; top: 10px; right: 10px; padding: 4px 8px; background: #333; font-size: 13px; border-radius: 4px; }
</style>
