<template>
  <span class="message-text"><template v-for="(part, index) in parts" :key="index"><a
    v-if="part.reference"
    class="chat-timestamp"
    :href="`#review-${part.reference.start_time}-${part.reference.end_time}`"
    :title="t('review.openTimestamp', { time: part.text })"
    @click.prevent.stop="$emit('review', part.reference)"
  >{{ part.text }}</a><template v-else>{{ part.text }}</template></template></span>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '@/i18n'
import { tokenizeChatTimestamps } from '@/utils/chatTimestamps'

const props = defineProps({ text: { type: String, default: '' }, summaries: { type: Array, default: () => [] } })
defineEmits(['review'])
const { t } = useI18n()
const parts = computed(() => tokenizeChatTimestamps(props.text, props.summaries))
</script>

<style scoped>
.message-text { white-space: pre-wrap; overflow-wrap: anywhere; }
.chat-timestamp { color: #69cbd5; text-decoration: underline; text-underline-offset: 3px; font-variant-numeric: tabular-nums; }
.chat-timestamp:hover { color: #adf0f4; }
.chat-timestamp:focus-visible { outline: 2px solid #69cbd5; outline-offset: 3px; border-radius: 2px; }
</style>
