<template>
  <canvas ref="canvas" class="live-capture-preview"></canvas>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps({ url: { type: String, required: true }, paused: Boolean })
const emit = defineEmits(['frame', 'metrics', 'error'])
const canvas = ref(null)
const metrics = ref(null)
let worker
const updateDisplay = async () => {
  try {
    const display = await window.electronAPI?.getPreviewDisplay?.()
    if (display) worker?.postMessage({ type: 'display', refreshRate: display.refreshRate })
  } catch (_) { /* Older clients use the observed refresh cadence. */ }
}
onMounted(() => {
  try {
    worker = new Worker(new URL('../utils/liveCapture.worker.js', import.meta.url), { type: 'module' })
    worker.onmessage = ({ data }) => {
      if (data.type === 'metrics') {
        metrics.value = data.metrics
        emit('metrics', data.metrics)
      } else if (data.type === 'frame') emit('frame')
      else if (data.type === 'error') emit('error', data.error)
    }
    worker.onerror = event => emit('error', event.message || 'Preview worker failed')
    const target = canvas.value.transferControlToOffscreen()
    worker.postMessage({ type: 'init', canvas: target, url: props.url, paused: props.paused }, [target])
    void updateDisplay()
    window.addEventListener('resize', updateDisplay)
  } catch (error) { emit('error', error.message) }
})
watch(() => props.paused, paused => worker?.postMessage({ type: 'pause', paused }))
onUnmounted(() => { window.removeEventListener('resize', updateDisplay); worker?.terminate(); worker = null })
defineExpose({ metrics })
</script>

<style scoped>
.live-capture-preview { display: block; width: 100%; height: 100%; object-fit: contain; background: #000; }
</style>
