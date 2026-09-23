import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import media from '../electron/replayMedia.cjs'

test('replay media supplies byte ranges for Chromium seeking', async (t) => {
  const dir = await mkdtemp(join(tmpdir(), 'replay-media-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const file = join(dir, 'video.mp4')
  await writeFile(file, '0123456789')
  const request = (range, method = 'GET') => new Request('https://replay/video.mp4', {
    method, headers: range ? { Range: range } : {},
  })
  for (const [range, status, text, contentRange] of [
    [null, 200, '0123456789', null],
    ['bytes=3-5', 206, '345', 'bytes 3-5/10'],
    ['bytes=7-', 206, '789', 'bytes 7-9/10'],
    ['bytes=-3', 206, '789', 'bytes 7-9/10'],
    ['bytes=8-100', 206, '89', 'bytes 8-9/10'],
  ]) {
    const result = await media.serveReplayMedia(request(range), file)
    assert.equal(result.status, status)
    assert.equal(result.headers.get('content-range'), contentRange)
    assert.equal(result.headers.get('content-length'), String(text.length))
    assert.equal(result.headers.get('accept-ranges'), 'bytes')
    assert.equal(await result.text(), text)
  }
  for (const range of ['bytes=10-', 'bytes=5-3', 'bytes=-0', 'bytes=-', 'bytes=1-2,4-5']) {
    const result = await media.serveReplayMedia(request(range), file)
    assert.equal(result.status, 416)
    assert.equal(result.headers.get('content-range'), 'bytes */10')
  }
  const head = await media.serveReplayMedia(request(null, 'HEAD'), file)
  assert.equal(head.headers.get('content-length'), '10')
  assert.equal(await head.text(), '')
  const denied = await media.serveReplayMedia(request(null, 'POST'), file)
  assert.equal(denied.status, 405)
})
