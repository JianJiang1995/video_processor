import test from 'node:test'
import assert from 'node:assert/strict'
import { nextTick } from 'vue'
import { messages, useI18n } from '../src/i18n.js'

test('English covers every Chinese interface key and keeps placeholders', () => {
  assert.deepEqual(Object.keys(messages.en).sort(), Object.keys(messages.zh).sort())
  for (const [key, value] of Object.entries(messages.en)) {
    if (key !== 'lang.zh') assert.doesNotMatch(value, /[\u3400-\u9fff]/, key)
    assert.deepEqual(value.match(/\{\w+\}/g)?.sort() || [], messages.zh[key].match(/\{\w+\}/g)?.sort() || [], key)
  }
})

test('switching is persistent, validates values and works with denied storage', async () => {
  const saved = new Map()
  globalThis.localStorage = { setItem: (key, value) => saved.set(key, value) }
  globalThis.document = { documentElement: {} }
  const { language, setLanguage, t } = useI18n()
  setLanguage('en')
  await nextTick()
  assert.equal(language.value, 'en')
  assert.equal(saved.get('surg_ui_language'), 'en')
  assert.equal(document.documentElement.lang, 'en')
  assert.equal(document.title, 'Surg-R1 Surgical Assistant')
  assert.equal(t('app.windowRange', { start: 2, end: 4 }), 'Windows 2-4')
  setLanguage('fr')
  assert.equal(language.value, 'en')
  globalThis.localStorage.setItem = () => { throw Error('Storage denied') }
  assert.doesNotThrow(() => setLanguage('zh'))
  await nextTick()
  assert.equal(document.documentElement.lang, 'zh-CN')
  delete globalThis.localStorage
  delete globalThis.document
})
