import test from 'node:test'
import assert from 'node:assert/strict'
import { livePreviewUrl, captureModeDetails } from '../src/utils/livePreview.js'

test('live preview follows the source rate and resolution on both transports', () => {
  for (const websocket of [false, true]) {
    const url = new URL(livePreviewUrl('http://127.0.0.1:8001', 'capture', websocket))
    assert.equal(url.searchParams.get('fps'), '0')
    assert.equal(url.searchParams.get('max_width'), '0')
    assert.equal(url.searchParams.get('quality'), '78')
    assert.equal(url.protocol, websocket ? 'ws:' : 'http:')
    assert.equal(url.searchParams.get('ack'), websocket ? 'true' : null)
  }
})

test('secure preview preserves transport security and escapes session identifiers', () => {
  const url = new URL(livePreviewUrl('https://example.test', 'a/b', true))
  assert.equal(url.protocol, 'wss:')
  assert.equal(url.pathname, '/api/video/ws-display/a%2Fb')
})

test('1080p50 and 1080i50 are not presented as the same frame rate', () => {
  assert.deepEqual(captureModeDetails('1080p50'), { scan: 'progressive', frames: 50, fields: null })
  assert.deepEqual(captureModeDetails('1080i50'), { scan: 'interlaced', frames: 25, fields: 50 })
  assert.deepEqual(captureModeDetails('1080i5994'), { scan: 'interlaced', frames: 29.97, fields: 59.94 })
  assert.equal(captureModeDetails('2160p5994').frames, 59.94)
  assert.equal(captureModeDetails('1080p2398').frames, 23.976)
  assert.equal(captureModeDetails('auto'), null)
})
