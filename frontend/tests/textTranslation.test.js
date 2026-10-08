import test from 'node:test'
import assert from 'node:assert/strict'
import { createTextTranslator } from '../src/utils/textTranslation.js'

const tick = () => new Promise(resolve => setImmediate(resolve))

test('translations are single-flight, deduplicated and prioritize visible content', async () => {
  const calls = []
  const resolvers = []
  const service = createTextTranslator({ translate: (text, target) => {
    calls.push([text, target])
    return new Promise(resolve => resolvers.push(resolve))
  } })
  service.request('old', 'en', 1)
  service.request('current', 'en', 100)
  service.request('current', 'en', 100)
  await tick()
  assert.deepEqual(calls, [['current', 'en']])
  resolvers.shift()('Current result')
  await tick()
  assert.deepEqual(calls, [['current', 'en'], ['old', 'en']])
  resolvers.shift()('Old result')
  await tick()
  assert.deepEqual(service.request('current', 'en'), { status: 'ready', text: 'Current result' })
})

test('late old responses cannot overwrite revised text; clear removes queued work', async () => {
  const resolvers = []
  const service = createTextTranslator({ translate: () => new Promise(resolve => resolvers.push(resolve)) })
  service.request('original', 'en')
  await tick()
  service.request('revision', 'en')
  service.request('unused', 'en')
  service.clearPending()
  service.request('revision', 'en')
  resolvers.shift()('Original English')
  await tick()
  assert.equal(service.request('revision', 'en').status, 'pending')
  resolvers.shift()('Revised English')
  await tick()
  assert.equal(service.request('revision', 'en').text, 'Revised English')
})

test('incomplete translations show a failure, never a rule-generated clinical claim', async () => {
  let calls = 0
  const service = createTextTranslator({ translate: async () => ++calls === 1 ? '仍是中文' : 'No bleeding is seen.' })
  service.request('未见出血', 'en')
  await tick()
  assert.equal(service.request('未见出血', 'en').status, 'failed')
  await tick()
  assert.equal(calls, 1)
  service.retry()
  service.request('未见出血', 'en')
  await tick()
  assert.equal(service.request('未见出血', 'en').text, 'No bleeding is seen.')
})
