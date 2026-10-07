import test from 'node:test'
import assert from 'node:assert/strict'
import { parsePreviewPacket, previewAllowsRecording } from '../src/utils/previewPixels.js'
import { livePreviewUrl, isLoopbackPreview } from '../src/utils/livePreview.js'

function packet() {
  const buffer = new ArrayBuffer(32 + 3 * 2 * 3)
  const view = new DataView(buffer)
  view.setUint32(0, 0x31505253, true)
  view.setUint32(4, 9, true)
  view.setUint32(8, 3, true)
  view.setUint32(12, 2, true)
  view.setFloat64(16, 1234.5, true)
  view.setFloat32(24, 50, true)
  view.setUint32(28, 18, true)
  return buffer
}

test('raw packets retain resolution, fps, sequence and zero-copy BGR view', () => {
  const buffer = packet()
  const parsed = parsePreviewPacket(buffer)
  assert.equal(parsed.width, 3)
  assert.equal(parsed.height, 2)
  assert.equal(parsed.sequence, 9)
  assert.equal(parsed.sourceFps, 50)
  assert.equal(parsed.readAt, 1234.5)
  assert.equal(parsed.pixels.buffer, buffer)
  assert.equal(parsed.pixels.byteLength, 18)
})

test('malformed or truncated frames are rejected before GPU upload', () => {
  assert.throws(() => parsePreviewPacket(new ArrayBuffer(4)))
  const buffer = packet()
  new DataView(buffer).setUint32(8, 4, true)
  assert.throws(() => parsePreviewPacket(buffer))
  assert.throws(() => parsePreviewPacket(packet().slice(0, -1)))
})

test('I420 payload has full luma resolution and validated even dimensions', () => {
  const buffer = new ArrayBuffer(44)
  const view = new DataView(buffer)
  view.setUint32(0, 0x32505253, true)
  view.setUint32(8, 4, true)
  view.setUint32(12, 2, true)
  view.setUint32(28, 12, true)
  assert.equal(parsePreviewPacket(buffer).format, 'i420')
  assert.equal(parsePreviewPacket(buffer).width, 4)
  view.setUint32(8, 3, true)
  assert.throws(() => parsePreviewPacket(buffer))
})

test('automatic recording requires fresh, healthy display metrics', () => {
  const metrics = { measuredAt: 10000, sourceFps: 50, fps: 49.5, frames: 99, p95PipelineMs: 30, maxGapMs: 60 }
  assert.equal(previewAllowsRecording(metrics, 11000), true)
  assert.equal(previewAllowsRecording(metrics, 20000), false)
  for (const patch of [{ fps: 22 }, { paused: true }, { p95PipelineMs: 350 }, { maxGapMs: 300 }, { frames: 1 }]) {
    assert.equal(previewAllowsRecording({ ...metrics, ...patch }, 11000), false)
  }
  assert.equal(previewAllowsRecording(null), false)
  assert.equal(previewAllowsRecording({ ...metrics, fps: 24, displayRefreshRate: 24 }, 11000), true)
  assert.equal(previewAllowsRecording({ ...metrics, fps: 24, refreshFps: 25, displayRefreshRate: 50 }, 11000), false)
})

test('raw URL is explicit and only local backends qualify', () => {
  assert.equal(new URL(livePreviewUrl('http://127.0.0.1:8001', 'test', true, 'bgr24')).searchParams.get('pixel_format'), 'bgr24')
  for (const base of ['http://localhost:8001', 'http://127.0.0.1:8001', 'http://[::1]:8001']) assert.ok(isLoopbackPreview(base))
  for (const base of ['http://192.168.1.1', 'http://localhost.example.org', '']) assert.equal(isLoopbackPreview(base), false)
})
