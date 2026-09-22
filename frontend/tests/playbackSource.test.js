import test from 'node:test'
import assert from 'node:assert/strict'
import { selectPlaybackPreview } from '../src/utils/playbackSource.js'

test('ready proxy is chosen before playback and is not polled afterwards', async () => {
  let calls = 0
  assert.equal(await selectPlaybackPreview(async () => { calls++; return { status: 'ready' } }), true)
  assert.equal(calls, 1)
})

test('allows a cache probe to finish without mounting the original first', async () => {
  let calls = 0
  assert.equal(await selectPlaybackPreview(async () => ({ status: ++calls === 1 ? 'preparing' : 'ready' }),
    { pollMs: 1 }), true)
  assert.equal(calls, 2)
})

test('cold cache falls back once and cancels outstanding work', async () => {
  let fetchSignal
  assert.equal(await selectPlaybackPreview(async signal => {
    fetchSignal = signal
    return { status: 'preparing' }
  }, { timeoutMs: 15, pollMs: 2 }), false)
  assert.equal(fetchSignal.aborted, true)
})

test('session changes abort old source selection', async () => {
  const controller = new AbortController()
  controller.abort()
  let calls = 0
  assert.equal(await selectPlaybackPreview(async () => { calls++; return { status: 'ready' } },
    { signal: controller.signal }), false)
  assert.equal(calls, 0)
})

test('failed proxy metadata does not block original playback', async () => {
  assert.equal(await selectPlaybackPreview(async () => { throw new Error('offline') }), false)
})
