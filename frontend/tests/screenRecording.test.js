import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import recording from '../electron/screenRecording.cjs'
import { normalizeRecordingFps } from '../electron/recordingOptions.mjs'

const { RecordingFile, recordingAdmission } = recording
const plenty = { diskBytes: 10 * 1024 ** 3, memoryBytes: 10 * 1024 ** 3, cpuLoad: 0.1 }

test('recording fps accepts 1-60 integers and defaults invalid preferences to 40', () => {
  for (const value of [undefined, null, '', 'abc', 0, -1, 61, 29.97, Infinity, NaN, true, {}, []]) {
    assert.equal(normalizeRecordingFps(value), 40)
  }
  for (const value of [1, 15, 25, 40, 50, 60]) {
    assert.equal(normalizeRecordingFps(value), value)
    assert.equal(normalizeRecordingFps(String(value)), value)
  }
})

test('file metadata preserves the requested fps across recordings, including default', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'screen-recording-fps-'))
  try {
    const recorder = new RecordingFile(directory, { capacity: async () => plenty, index: false })
    for (const [fps, expected] of [[undefined, 40], [25, 25], ['50', 50], [500, 40]]) {
      const job = await recorder.begin({ fps })
      assert.equal(job.fps, expected)
      const result = await recorder.finish(job.id)
      assert.equal(result.fps, expected)
      assert.equal(JSON.parse(await fs.readFile(`${job.path}.json`)).fps, expected)
    }
  } finally { await fs.rm(directory, { recursive: true, force: true }) }
})

test('auto admission protects memory and load, all recordings protect disk', () => {
  assert.equal(recordingAdmission(plenty, true), null)
  assert.equal(recordingAdmission({ ...plenty, cpuLoad: 0.9 }, true), 'load')
  assert.equal(recordingAdmission({ ...plenty, cpuLoad: 0.9 }, false), null)
  assert.equal(recordingAdmission({ ...plenty, memoryBytes: 1 }, true), 'load')
  assert.equal(recordingAdmission({ ...plenty, diskBytes: 1 }, false), 'disk')
})

test('chunks are serialized, isolated to the active recording, and drained on finish', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'screen-recording-'))
  try {
    const recorder = new RecordingFile(directory, { capacity: async () => plenty, index: false })
    const job = await recorder.begin({ sessionId: '../../unsafe/session' })
    assert.ok(job.path.startsWith(directory))
    assert.equal(path.dirname(path.dirname(job.path)), directory)
    await assert.rejects(recorder.begin(), /recording_busy/)
    await assert.rejects(recorder.append('wrong', new ArrayBuffer(1)), /not_active/)
    await assert.rejects(recorder.append(job.id, 'bad'), /invalid_chunk/)
    const a = recorder.append(job.id, new TextEncoder().encode('first').buffer)
    const b = recorder.append(job.id, new TextEncoder().encode('last').buffer)
    const result = await recorder.finish(job.id)
    await Promise.all([a, b])
    assert.equal(await fs.readFile(job.path, 'utf8'), 'firstlast')
    assert.equal(result.bytes, 9)
    assert.equal(JSON.parse(await fs.readFile(`${job.path}.json`)).audio, false)
    await assert.rejects(recorder.append(job.id, new ArrayBuffer(1)), /not_active/)
    const next = await recorder.begin()
    assert.notEqual(next.path, job.path)
    await recorder.finish(next.id)
  } finally { await fs.rm(directory, { recursive: true, force: true }) }
})
