import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_PANEL_TEXT_SIZE, MIN_PANEL_TEXT_SIZE, MAX_PANEL_TEXT_SIZE,
  PANEL_TEXT_SIZE_KEY, normalizePanelTextSize, loadPanelTextSize, savePanelTextSize,
} from '../src/utils/panelTextSize.js'

test('right-panel font defaults to 20px and accepts the full integer slider range', () => {
  assert.equal(DEFAULT_PANEL_TEXT_SIZE, 20)
  for (let size = MIN_PANEL_TEXT_SIZE; size <= MAX_PANEL_TEXT_SIZE; size++) {
    assert.equal(normalizePanelTextSize(size), size)
    assert.equal(normalizePanelTextSize(String(size)), size)
  }
  for (const value of [null, undefined, '', ' ', 'invalid', 15, 29, -2, 20.5, Infinity, NaN, true, [], {}]) {
    assert.equal(normalizePanelTextSize(value), 20)
  }
})

test('selection and reset survive a new panel without touching layout or other settings', () => {
  const settings = new Map([['surg_right_panel_width_v3', '550']])
  const storage = { getItem: key => settings.get(key), setItem: (key, value) => settings.set(key, value) }
  assert.equal(loadPanelTextSize(storage), 20)
  assert.equal(savePanelTextSize(26, storage), true)
  assert.equal(loadPanelTextSize(storage), 26)
  assert.equal(settings.get(PANEL_TEXT_SIZE_KEY), '26')
  savePanelTextSize(DEFAULT_PANEL_TEXT_SIZE, storage)
  assert.equal(loadPanelTextSize(storage), 20)
  assert.equal(settings.get('surg_right_panel_width_v3'), '550')
})

test('corrupt or unavailable preference storage cannot break the panel', () => {
  assert.equal(loadPanelTextSize({ getItem: () => '999' }), 20)
  const blocked = { getItem() { throw Error('blocked') }, setItem() { throw Error('blocked') } }
  assert.equal(loadPanelTextSize(blocked), 20)
  assert.equal(savePanelTextSize(24, blocked), false)
})
