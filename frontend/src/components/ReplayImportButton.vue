<template>
  <div class="replay-import">
    <button class="btn btn-secondary replay-import-button" :disabled="busy" @click="openReplay">
      <FolderOpen :size="16" aria-hidden="true" />
      {{ language === 'zh' ? '导入分析回放' : 'Import Analysis Replay' }}
    </button>
    <p v-if="error" role="alert">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { FolderOpen } from '@lucide/vue'
import { useI18n } from '@/i18n'
import { loadReplayBundle, showOpenDialog } from '../utils/electronBridge.js'

const { language } = useI18n()
const busy = ref(false)
const error = ref('')
const emit = defineEmits(['begin', 'loaded'])

const openReplay = async () => {
  busy.value = true
  error.value = ''
  emit('begin')
  try {
    const choice = await showOpenDialog({
      title: language.value === 'zh' ? '选择分析回放文件' : 'Select Analysis Replay',
      properties: ['openFile'],
      filters: [{ name: 'Analysis Replay', extensions: ['json'] }],
    })
    if (choice.canceled || !choice.filePaths?.length) return
    const specPath = choice.filePaths[0]
    const bundle = await loadReplayBundle(specPath)
    if (!bundle?.success) throw new Error(bundle?.error || 'Invalid replay bundle')
    emit('loaded', bundle)
  } catch (err) {
    error.value = String(err?.message || err)
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.replay-import { margin: 12px 0; text-align: center; }
.replay-import-button { display: inline-flex; align-items: center; gap: 8px; }
.replay-import p { color: var(--accent-danger, #ff6b6b); overflow-wrap: anywhere; }
</style>
