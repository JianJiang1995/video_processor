// Preview transport only. Model sampling and stored analysis frames are separate.
export function livePreviewUrl(base, sessionId, websocket = false, pixelFormat = 'jpeg') {
  const endpoint = websocket ? 'ws-display' : 'display-mjpeg'
  const url = new URL(`/api/video/${endpoint}/${encodeURIComponent(sessionId)}`, base)
  url.searchParams.set('fps', '0')
  url.searchParams.set('max_width', '0')
  url.searchParams.set('quality', '78')
  if (websocket) {
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
    url.searchParams.set('ack', 'true')
    if (pixelFormat !== 'jpeg') url.searchParams.set('pixel_format', pixelFormat)
  }
  return url.toString()
}

export function isLoopbackPreview(base) {
  try { return ['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname) }
  catch { return false }
}

export function captureModeDetails(mode) {
  const match = /^(\d+)([pi])(\d+)$/.exec(mode)
  if (!match) return null
  const rate = ({ 2398: 23.976, 2997: 29.97, 5994: 59.94 })[match[3]] || Number(match[3])
  return {
    scan: match[2] === 'i' ? 'interlaced' : 'progressive',
    frames: match[2] === 'i' ? rate / 2 : rate,
    fields: match[2] === 'i' ? rate : null,
  }
}
