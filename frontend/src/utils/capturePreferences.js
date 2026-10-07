const STORAGE_KEY = 'surgr1_capture_preferences_v1'

export const captureDeviceKey = device =>
  `${device.backend || 'auto'}:${device.device_path || device.device_id}`

export function readCapturePreferences(storage) {
  try {
    const value = JSON.parse(storage.getItem(STORAGE_KEY))
    if (value?.version === 1 && value.devices && typeof value.devices === 'object' && !Array.isArray(value.devices)) {
      return value
    }
  } catch { /* Unavailable storage or older/corrupt preferences: use device defaults. */ }
  return { version: 1, devices: {}, lastDevice: null }
}

const validLayout = value => ['full', 'quad_right'].includes(value)
const choice = (value, supported, fallback) => {
  if (!supported?.length) return fallback || 'auto'
  return supported.includes(value) ? value : supported.includes(fallback) ? fallback : supported[0]
}

export function restoreCaptureSettings(device, preferences, layout = 'full') {
  const saved = preferences.devices[captureDeviceKey(device)]
  const mode = choice(saved?.mode, device.supported_modes, device.default_mode)
  const connection = choice(saved?.connection, device.supported_connections, device.default_connection)
  return {
    mode,
    connection,
    inputLayout: validLayout(saved?.inputLayout) ? saved.inputLayout : validLayout(layout) ? layout : 'full',
    simulatorPath: typeof saved?.simulatorPath === 'string' ? saved.simulatorPath : '',
    // Never start an unconfigured port, or silently substitute settings that
    // this driver no longer supports. Simulators require explicit selection.
    configured: Boolean(saved && device.backend !== 'simulator' && device.available !== false
      && device.hardware_ready !== false
      && saved.mode === mode && saved.connection === connection && validLayout(saved.inputLayout)),
  }
}

export function preferredCaptureDevice(devices, preferences) {
  return devices.find(device => captureDeviceKey(device) === preferences.lastDevice)
    || devices.find(device => device.backend !== 'simulator') || devices[0] || null
}

export function captureDeviceChoices(response) {
  const detected = response.devices || []
  return [...detected, ...(response.unavailable_devices || []).filter(profile =>
    !detected.some(device => captureDeviceKey(device) === captureDeviceKey(profile)))]
    .sort((a, b) => Number(a.backend === 'simulator') - Number(b.backend === 'simulator'))
}

export function rememberCaptureSettings(storage, device, settings) {
  const preferences = readCapturePreferences(storage)
  const key = captureDeviceKey(device)
  preferences.devices[key] = {
    mode: settings.mode,
    connection: settings.connection,
    inputLayout: settings.inputLayout,
    simulatorPath: settings.simulatorPath || '',
  }
  preferences.lastDevice = key
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(preferences))
  } catch { /* Storage restrictions must not interrupt a working capture. */ }
}
