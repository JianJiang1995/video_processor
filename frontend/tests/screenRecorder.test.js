import test from 'node:test'
import assert from 'node:assert/strict'
import { ScreenRecorder } from '../src/utils/screenRecorder.js'

function fixture({ appendError = false } = {}) {
  const calls = []
  const states = []
  const begins = []
  const captures = []
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
    beginRecording: async options => {
      begins.push(options)
      return { id: 'one', path: '/local/screen.webm', sourceId: 'window:1:0' }
    },
    appendRecording: async (id, buffer) => {
      calls.push(['append', id, new TextDecoder().decode(buffer)])
      if (appendError) throw new Error('disk_failed')
    },
    finishRecording: async (id, reason) => { calls.push(['finish', id, reason]); return { path: '/local/screen.webm' } },
  }
  const mediaDevices = { getUserMedia: async constraints => {
    captures.push(constraints)
    assert.equal(constraints.audio, false)
    assert.equal(constraints.video.mandatory.chromeMediaSourceId, 'window:1:0')
    return { getTracks: () => [track], getVideoTracks: () => [track] }
  } }
  return { recorder: new ScreenRecorder(api, { Recorder, mediaDevices, onState: s => states.push(s) }),
    calls, states, track, api, mediaDevices, begins, captures }
}

test('default recording uses 40fps for capture and file metadata', async () => {
  const { recorder, begins, captures, states } = fixture()
  await recorder.start('session')
  assert.equal(begins[0].fps, 40)
  assert.equal(captures[0].video.mandatory.maxFrameRate, 40)
  assert.equal(states.at(-1).fps, 40)
  await recorder.stop()
})

test('each new recording uses its selected frame rate without changing dimensions', async () => {
  const { recorder, begins, captures } = fixture()
  for (const fps of [1, 25, 40, 60]) {
    await recorder.start('session', false, fps)
    const constraints = captures.at(-1).video.mandatory
    assert.equal(constraints.maxFrameRate, fps)
    assert.equal(constraints.maxWidth, 1920)
    assert.equal(constraints.maxHeight, 1080)
    assert.equal(begins.at(-1).fps, fps)
    await recorder.stop()
  }
})

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

test('retains automatic mode even when the begin response omits it', async () => {
  const { recorder, states } = fixture()
  await recorder.start('session', true)
  assert.equal(recorder.job.automatic, true)
  assert.equal(states.at(-1).automatic, true)
  await recorder.stop('preview_overload')
  await recorder.start('session')
  assert.equal(recorder.job.automatic, false)
  assert.equal(states.at(-1).automatic, false)
  await recorder.stop()
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
