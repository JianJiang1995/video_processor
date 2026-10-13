export const DEFAULT_RECORDING_FPS = 40
export const MIN_RECORDING_FPS = 1
export const MAX_RECORDING_FPS = 60
export const RECORDING_MAX_WIDTH = 1920
export const RECORDING_MAX_HEIGHT = 1080
export const RECORDING_BITRATE = 4500000

export function normalizeRecordingFps(value) {
  const number = typeof value === 'number' ? value
    : typeof value === 'string' && value.trim() ? Number(value) : NaN
  return Number.isInteger(number) && number >= MIN_RECORDING_FPS && number <= MAX_RECORDING_FPS
    ? number : DEFAULT_RECORDING_FPS
}
