<template>
  <div class="language-switch" role="group" :aria-label="t('lang.label')">
    <Languages :size="15" aria-hidden="true" />
    <button type="button" class="language-option" :class="{ active: language === 'zh' }"
      :aria-pressed="language === 'zh'" title="切换到中文" @click="setLanguage('zh')">中文</button>
    <button type="button" class="language-option" :class="{ active: language === 'en' }"
      :aria-pressed="language === 'en'" title="Switch to English" @click="setLanguage('en')">English</button>
    <button v-if="hasTranslationFailures()" type="button" class="language-option"
      :title="t('translation.retry')" :aria-label="t('translation.retry')" @click="retryTranslations"><RotateCcw :size="14" /></button>
  </div>
</template>

<script setup>
import { Languages, RotateCcw } from '@lucide/vue'
import { useI18n } from '../i18n.js'
import { useLocalizedText } from '../utils/localizedText.js'
const { t, language, setLanguage } = useI18n()
const { hasTranslationFailures, retryTranslations } = useLocalizedText()
</script>

<style scoped>
.language-switch { display: inline-flex; align-items: center; gap: 3px; padding: 3px; flex-shrink: 0; border: 1px solid var(--border-subtle); border-radius: 4px; color: var(--text-secondary); }
.language-switch > svg { margin: 0 4px; }
.language-option { min-height: 28px; padding: 4px 8px; font: inherit; font-size: 12px; border: 0; border-radius: 3px; background: transparent; color: inherit; cursor: pointer; white-space: nowrap; }
.language-option.active { background: var(--bg-tertiary); color: var(--text-primary); font-weight: 700; }
.language-option:focus-visible { outline: 2px solid var(--accent-primary); outline-offset: 1px; }
</style>
