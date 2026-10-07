export function parsePreviewPacket(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 32) throw new Error('Invalid preview header')
  const view = new DataView(buffer)
  const magic = view.getUint32(0, true)
  if (![0x31505253, 0x32505253].includes(magic)) throw new Error('Unsupported preview format')
  const format = magic === 0x32505253 ? 'i420' : 'bgr24'
  const width = view.getUint32(8, true)
  const height = view.getUint32(12, true)
  const size = view.getUint32(28, true)
  const expected = width * height * (format === 'i420' ? 1.5 : 3)
  if (!width || !height || width > 8192 || height > 8192 ||
    format === 'i420' && (width % 2 || height % 2) || size !== expected || size + 32 !== buffer.byteLength) {
    throw new Error('Invalid preview dimensions')
  }
  return {
    sequence: view.getUint32(4, true), width, height, format,
    readAt: view.getFloat64(16, true), sourceFps: view.getFloat32(24, true),
    pixels: new Uint8Array(buffer, 32, size),
  }
}

export function previewAllowsRecording(metrics, now = Date.now()) {
  return Boolean(metrics && !metrics.paused && now - metrics.measuredAt < 5000 &&
    metrics.frames >= 20 && metrics.fps >= Math.min(metrics.sourceFps, metrics.displayRefreshRate || metrics.sourceFps) * 0.9 &&
    metrics.p95PipelineMs < 100 && metrics.maxGapMs < 200)
}
