export const PANEL_TEXT_SIZE_KEY = 'surg_right_panel_text_size_v1'
export const DEFAULT_PANEL_TEXT_SIZE = 20
export const MIN_PANEL_TEXT_SIZE = 16
export const MAX_PANEL_TEXT_SIZE = 28

export function normalizePanelTextSize(value) {
  const number = typeof value === 'number' ? value
    : typeof value === 'string' && value.trim() ? Number(value) : NaN
  return Number.isInteger(number) && number >= MIN_PANEL_TEXT_SIZE && number <= MAX_PANEL_TEXT_SIZE
    ? number : DEFAULT_PANEL_TEXT_SIZE
}

export function loadPanelTextSize(storage) {
  try {
    return normalizePanelTextSize((storage ?? globalThis.localStorage)?.getItem(PANEL_TEXT_SIZE_KEY))
  } catch {
    return DEFAULT_PANEL_TEXT_SIZE
  }
}

export function savePanelTextSize(value, storage) {
  try {
    const target = storage ?? globalThis.localStorage
    if (!target) return false
    target.setItem(PANEL_TEXT_SIZE_KEY, String(normalizePanelTextSize(value)))
    return true
  } catch {
    return false
  }
}
