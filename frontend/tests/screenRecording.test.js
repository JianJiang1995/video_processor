import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import recording from '../electron/screenRecording.cjs'
import { normalizeRecordingFps } from '../electron/recordingOptions.mjs'

const { RecordingFile, recordingAdmission, recordingMediaInfo } = recording
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
      assert.equal(result.format, 'mp4')
      assert.equal(result.codec, 'h264')
      assert.deepEqual(result.requestedEncoding, { maxWidth: 1920, maxHeight: 1080,
        fps: expected, videoBitrate: 4500000, lossless: false })
      assert.equal(result.media, null)
      assert.match(result.path, /\.mp4$/)
      assert.equal(JSON.parse(await fs.readFile(`${job.path}.json`)).fps, expected)
    }
  } finally { await fs.rm(directory, { recursive: true, force: true }) }
})

test('MP4 metadata describes the actual media, not the requested frame rate', () => {
  const probe = { format: { format_name: 'mov,mp4,m4a,3gp,3g2,mj2', duration: '8.14' },
    streams: [{ codec_type: 'video', codec_name: 'h264', pix_fmt: 'yuv420p', width: 1920,
      height: 1052, avg_frame_rate: '37500/1001', bit_rate: '4310012' }] }
  assert.deepEqual(recordingMediaInfo(probe), { width: 1920, height: 1052,
    averageFps: 37500 / 1001, videoBitrate: 4310012, durationSeconds: 8.14,
    codec: 'h264', pixelFormat: 'yuv420p', container: 'mp4' })
  probe.streams[0].avg_frame_rate = '0/0'
  probe.streams[0].bit_rate = 'N/A'
  assert.equal(recordingMediaInfo(probe).averageFps, null)
  assert.equal(recordingMediaInfo(probe).videoBitrate, null)
  assert.throws(() => recordingMediaInfo({ ...probe, streams: [] }), /invalid_mp4/)
  assert.throws(() => recordingMediaInfo({ ...probe, format: { format_name: 'matroska,webm' } }), /invalid_mp4/)
  probe.streams[0].codec_name = 'vp8'
  assert.throws(() => recordingMediaInfo(probe), /invalid_mp4/)
})

test('failed MP4 indexing preserves all original data and reports a warning', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'screen-recording-recovery-'))
  try {
    const recorder = new RecordingFile(directory, { capacity: async () => plenty })
    const job = await recorder.begin()
    const input = new TextEncoder().encode('incomplete recording data')
    await recorder.append(job.id, input.buffer)
    const result = await recorder.finish(job.id, 'renderer_crash')
    assert.match(result.warning, /index:/)
    assert.deepEqual(new Uint8Array(await fs.readFile(result.path)), input)
    assert.equal(result.bytes, input.length)
    assert.equal((await fs.stat(result.path)).mode & 0o777, 0o600)
    assert.equal(JSON.parse(await fs.readFile(`${result.path}.json`)).reason, 'renderer_crash')
    assert.equal(recorder.active, null)
    await assert.rejects(fs.stat(result.path.replace(/\.mp4$/, '.indexed.mp4')), { code: 'ENOENT' })
  } finally { await fs.rm(directory, { recursive: true, force: true }) }
})

test('auto admission protects memory and load, all recordings protect disk', () => {
  assert.equal(recordingAdmission(plenty, true), null)
  assert.equal(recordingAdmission({ ...plenty, cpuLoad: 0.9 }, true), 'load')
  assert.equal(recordingAdmission({ ...plenty, cpuLoad: 0.9 }, false), null)
  assert.equal(recordingAdmission({ ...plenty, memoryBytes: 1 }, true), 'load')
  assert.equal(recordingAdmission({ ...plenty, diskBytes: 1 }, false), 'disk')
})

test('a cancelled capture with no chunks is never reported as a valid saved video', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'screen-recording-empty-'))
  try {
    const recorder = new RecordingFile(directory, { capacity: async () => plenty })
    const job = await recorder.begin()
    const result = await recorder.finish(job.id, 'start_cancelled')
    assert.equal(result.bytes, 0)
    assert.equal(result.media, null)
    assert.equal(result.warning, 'recording_empty')
    assert.equal(JSON.parse(await fs.readFile(`${job.path}.json`)).warning, 'recording_empty')
    assert.equal(recorder.active, null)
  } finally { await fs.rm(directory, { recursive: true, force: true }) }
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
