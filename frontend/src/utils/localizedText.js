import { ref, watch } from 'vue'
import axios from 'axios'
import { useI18n } from '../i18n.js'
import { createTextTranslator } from './textTranslation.js'

const revision = ref(0)
const translator = createTextTranslator({
  translate: async (text, target_lang) => {
    const { data } = await axios.post('/api/analysis/translate-summary', { text, target_lang }, { timeout: 55000 })
    if (!data.success) throw Error('Translation failed')
    return data.text
  },
  changed: () => { revision.value++ },
})
const { language, t } = useI18n()
watch(language, () => { translator.clearPending(); translator.retry() })

export function useLocalizedText() {
  const localize = (value, priority = 0) => {
    const text = String(value || '')
    const sourceLanguage = /[\u3400-\u9fff]/.test(text) ? 'zh' : 'en'
    if (!text.trim() || sourceLanguage === language.value || !/[A-Za-z\u3400-\u9fff]/.test(text)) return text
    void revision.value
    const state = translator.request(text, language.value, priority)
    return state.status === 'ready' ? state.text : t(`translation.${state.status === 'failed' ? 'failed' : 'pending'}`)
  }
  return { localize, retryTranslations: () => translator.retry(),
    hasTranslationFailures: () => { void revision.value; return translator.hasFailures() } }
}

export function clearPendingTranslations() { translator.clearPending() }
