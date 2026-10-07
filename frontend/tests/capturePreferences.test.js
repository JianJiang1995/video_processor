import test from 'node:test'
import assert from 'node:assert/strict'
import {
  captureDeviceChoices, captureDeviceKey, preferredCaptureDevice, readCapturePreferences,
  rememberCaptureSettings, restoreCaptureSettings,
} from '../src/utils/capturePreferences.js'

const card = {
  backend: 'decklink', device_id: 0, device_path: '/dev/blackmagic/io0',
  default_mode: 'auto', default_connection: 'hdmi',
  supported_modes: ['auto', '1080p50'], supported_connections: ['hdmi', 'sdi', 'auto'],
}
const settings = { mode: 'auto', connection: 'sdi', inputLayout: 'full' }
const storage = (initial = null) => {
  let value = initial
  return { getItem: () => value, setItem: (_key, next) => { value = next } }
}

test('first launch does not auto-connect an unconfirmed HDMI port', () => {
  const restored = restoreCaptureSettings(card, readCapturePreferences(storage()))
  assert.equal(restored.connection, 'hdmi')
  assert.equal(restored.mode, 'auto')
  assert.equal(restored.configured, false)
})

test('successful SDI auto settings survive a fresh UI mount', () => {
  const saved = storage()
  rememberCaptureSettings(saved, card, settings)
  const restored = restoreCaptureSettings(card, readCapturePreferences(saved), 'quad_right')
  assert.deepEqual(restored, { ...settings, simulatorPath: '', configured: true })
})

test('settings are isolated per backend and physical device', () => {
  const saved = storage()
  const second = { ...card, device_id: 1, device_path: '/dev/blackmagic/io1' }
  const simulator = { backend: 'simulator', device_id: 0 }
  rememberCaptureSettings(saved, card, settings)
  rememberCaptureSettings(saved, second, { ...settings, connection: 'hdmi', mode: '1080p50', inputLayout: 'quad_right' })
  const preferences = readCapturePreferences(saved)
  assert.equal(restoreCaptureSettings(card, preferences).connection, 'sdi')
  assert.equal(restoreCaptureSettings(second, preferences).mode, '1080p50')
  assert.equal(restoreCaptureSettings(simulator, preferences).configured, false)
  assert.equal(preferredCaptureDevice([simulator, card, second], preferences), second)
})

test('stale driver settings fall back but never automatically start a different input', () => {
  const saved = storage()
  rememberCaptureSettings(saved, card, settings)
  const changed = { ...card, supported_connections: ['hdmi'] }
  const restored = restoreCaptureSettings(changed, readCapturePreferences(saved))
  assert.equal(restored.connection, 'hdmi')
  assert.equal(restored.configured, false)
})

test('unplugged preferred device does not confer auto-start on a replacement', () => {
  const saved = storage()
  rememberCaptureSettings(saved, card, settings)
  const replacement = { ...card, device_id: 1, device_path: '/dev/blackmagic/io1' }
  const preferences = readCapturePreferences(saved)
  assert.equal(preferredCaptureDevice([replacement], preferences), replacement)
  assert.notEqual(captureDeviceKey(replacement), preferences.lastDevice)
  assert.equal(restoreCaptureSettings(replacement, preferences).configured, false)
  assert.equal(preferredCaptureDevice([], preferences), null)
})

test('simulator path is restored for manual use, without auto-opening a file as a live input', () => {
  const saved = storage()
  const simulator = { backend: 'simulator', device_id: 0, default_mode: 'auto', default_connection: 'auto' }
  rememberCaptureSettings(saved, simulator, { ...settings, connection: 'auto', simulatorPath: '/videos/test.mp4' })
  const restored = restoreCaptureSettings(simulator, readCapturePreferences(saved))
  assert.equal(restored.simulatorPath, '/videos/test.mp4')
  assert.equal(restored.configured, false)
})

test('malformed or inaccessible storage cannot break device selection or successful capture', () => {
  for (const value of ['{', 'null', '[]', '{"version":1,"devices":[]}']) {
    assert.equal(restoreCaptureSettings(card, readCapturePreferences(storage(value))).configured, false)
  }
  const unavailable = { getItem() { throw Error('denied') }, setItem() { throw Error('denied') } }
  assert.equal(restoreCaptureSettings(card, readCapturePreferences(unavailable)).configured, false)
  assert.doesNotThrow(() => rememberCaptureSettings(unavailable, card, settings))
})

test('missing card retains SDI/50fps settings but can never auto-connect', () => {
  const saved = storage()
  rememberCaptureSettings(saved, card, { ...settings, mode: '1080p50' })
  const offline = { ...card, available: false, hardware_ready: false }
  const choices = captureDeviceChoices({ devices: [{ backend: 'simulator', device_id: -1 }], unavailable_devices: [offline] })
  const preferences = readCapturePreferences(saved)
  assert.equal(preferredCaptureDevice(choices, preferences), offline)
  const restored = restoreCaptureSettings(offline, preferences)
  assert.equal(restored.connection, 'sdi')
  assert.equal(restored.mode, '1080p50')
  assert.equal(restored.configured, false)
  assert.equal(restoreCaptureSettings(card, preferences).configured, true)
})

test('detected card replaces offline profile, simulator still works when explicitly selected', () => {
  const simulator = { backend: 'simulator', device_id: -1 }
  const offline = { ...card, available: false }
  const choices = captureDeviceChoices({ devices: [simulator, card], unavailable_devices: [offline] })
  assert.deepEqual(choices, [card, simulator])
  assert.equal(preferredCaptureDevice(choices, readCapturePreferences(storage())), card)
  assert.equal(preferredCaptureDevice(choices, { lastDevice: captureDeviceKey(simulator) }), simulator)
})
