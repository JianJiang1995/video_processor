import test from 'node:test'
import assert from 'node:assert/strict'
import { ScreenRecorder } from '../src/utils/screenRecorder.js'

function fixture({ appendError = false } = {}) {
  const calls = []
  const states = []
  const track = { stopped: false, stop() { this.stopped = true } }
  class Recorder extends EventTarget {
    static isTypeSupported(type) { return type.includes('vp8') }
    start() { this.state = 'recording' }
    stop() {
      this.state = 'inactive'
      queueMicrotask(() => {
        this.ondataavailable({ data: new Blob(['final']) })
        this.dispatchEvent(new Event('stop'))
      })
    }
  }
  const api = {
    beginRecording: async () => ({ id: 'one', path: '/local/screen.webm', sourceId: 'window:1:0' }),
    appendRecording: async (id, buffer) => {
      calls.push(['append', id, new TextDecoder().decode(buffer)])
      if (appendError) throw new Error('disk_failed')
    },
    finishRecording: async (id, reason) => { calls.push(['finish', id, reason]); return { path: '/local/screen.webm' } },
  }
  const mediaDevices = { getUserMedia: async constraints => {
    assert.equal(constraints.audio, false)
    assert.equal(constraints.video.mandatory.chromeMediaSourceId, 'window:1:0')
    return { getTracks: () => [track], getVideoTracks: () => [track] }
  } }
  return { recorder: new ScreenRecorder(api, { Recorder, mediaDevices, onState: s => states.push(s) }),
    calls, states, track, api, mediaDevices }
}

test('stop flushes the final encoder chunk before closing, can record again', async () => {
  const { recorder, calls, states, track } = fixture()
  await recorder.start('session')
  recorder.recorder.ondataavailable({ data: new Blob(['first']) })
  await recorder.stop()
  assert.deepEqual(calls.map(c => c[0]), ['append', 'append', 'finish'])
  assert.deepEqual(calls.slice(0, 2).map(c => c[2]), ['first', 'final'])
  assert.equal(states.at(-1).state, 'saved')
  assert.equal(track.stopped, true)
  assert.equal(recorder.job, null)
  await recorder.start('session')
  await recorder.stop()
  assert.equal(calls.filter(c => c[0] === 'finish').length, 2)
})

test('capture failure finalizes the opened file and reports an error', async () => {
  const { recorder, calls, states, mediaDevices } = fixture()
  mediaDevices.getUserMedia = async () => { throw new Error('capture_failed') }
  await recorder.start('session')
  assert.equal(states.at(-1).state, 'error')
  assert.equal(recorder.job, null)
  assert.deepEqual(calls, [['finish', 'one', 'start_failed']])
})

test('write failure stops recording without throwing into the analysis workflow', async () => {
  const { recorder, calls, states, track } = fixture({ appendError: true })
  await recorder.start('session')
  recorder.recorder.ondataavailable({ data: new Blob(['first']) })
  await new Promise(resolve => setTimeout(resolve, 20))
  assert.equal(track.stopped, true)
  assert.equal(recorder.job, null)
  assert.equal(states.at(-1).state, 'error')
  assert.equal(calls.filter(c => c[0] === 'finish').length, 1)
})

test('leaving the view during capture startup does not resurrect a recorder', async () => {
  const { recorder, calls, states, track, mediaDevices } = fixture()
  let release
  mediaDevices.getUserMedia = () => new Promise(resolve => { release = resolve })
  const starting = recorder.start('session')
  await new Promise(resolve => setTimeout(resolve, 0))
  await recorder.stop('view_closed')
  release({ getTracks: () => [track], getVideoTracks: () => [track] })
  await starting
  assert.equal(track.stopped, true)
  assert.equal(recorder.recorder, null)
  assert.equal(recorder.job, null)
  assert.notEqual(states.at(-1).state, 'recording')
  assert.equal(calls.filter(c => c[0] === 'finish').length, 1)
})

test('closing during file creation cancels a late begin response', async () => {
  const { recorder, api, calls, states } = fixture()
  let release
  api.beginRecording = () => new Promise(resolve => { release = resolve })
  const starting = recorder.start('session')
  await recorder.stop('view_closed')
  release({ id: 'late', sourceId: 'window:1:0' })
  await starting
  assert.equal(recorder.job, null)
  assert.ok(!states.some(s => s.state === 'recording'))
  assert.equal(calls.filter(c => c[0] === 'finish').length, 1)
})
