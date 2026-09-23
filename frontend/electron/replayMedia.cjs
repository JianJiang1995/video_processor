const fs = require('node:fs')
const path = require('node:path')
const { Readable } = require('node:stream')

// Electron's file:// fetch omits byte-range headers. Serve a bounded stream so
// Chromium can seek without reading the entire surgical recording into memory.
async function serveReplayMedia(request, videoPath) {
  const { size } = await fs.promises.stat(videoPath)
  const headers = new Headers({
    'Accept-Ranges': 'bytes',
    'Content-Type': ({ '.webm': 'video/webm', '.mov': 'video/quicktime' })[path.extname(videoPath).toLowerCase()] || 'video/mp4',
    'Access-Control-Allow-Origin': '*',
    'Cross-Origin-Resource-Policy': 'cross-origin',
  })
  if (!['GET', 'HEAD'].includes(request.method)) {
    headers.set('Allow', 'GET, HEAD')
    return new Response(null, { status: 405, headers })
  }

  let start = 0
  let end = size - 1
  const range = request.method === 'GET' ? request.headers.get('range') : null
  let status = 200
  if (range) {
    const match = /^bytes=(\d*)-(\d*)$/.exec(range.trim())
    if (match && (match[1] || match[2])) {
      start = match[1] ? Number(match[1]) : Math.max(0, size - Number(match[2]))
      end = match[1] && match[2] ? Math.min(size - 1, Number(match[2])) : size - 1
    }
    if (!match || !(match[1] || match[2]) || !Number.isSafeInteger(start)
        || !Number.isSafeInteger(end) || start < 0 || start >= size || end < start) {
      headers.set('Content-Range', `bytes */${size}`)
      return new Response(null, { status: 416, headers })
    }
    status = 206
    headers.set('Content-Range', `bytes ${start}-${end}/${size}`)
  }
  headers.set('Content-Length', String(Math.max(0, end - start + 1)))
  const body = request.method === 'HEAD' || !size
    ? null : Readable.toWeb(fs.createReadStream(videoPath, { start, end }))
  return new Response(body, { status, headers })
}

module.exports = { serveReplayMedia }
