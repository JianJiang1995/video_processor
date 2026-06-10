const { app, BrowserWindow, ipcMain, dialog, screen } = require('electron')
const Store = require('electron-store')
const path = require('path')
const fs = require('fs').promises
const fsSync = require('fs')
const FrameCache = require('./frameCache.cjs')
const { ServiceManager } = require('./serviceManager.cjs')

// ============= Load config.json =============

function loadAppConfig() {
  // Try multiple locations
  const candidates = [
    // Packaged: resources/backend/config.json
    path.join(process.resourcesPath || '', 'backend', 'config.json'),
    // Dev: video_stream_app/config.json
    path.join(__dirname, '..', '..', 'config.json'),
    // Fallback
    path.join(__dirname, '..', 'config.json'),
  ]
  for (const p of candidates) {
    try {
      if (fsSync.existsSync(p)) {
        const data = JSON.parse(fsSync.readFileSync(p, 'utf-8'))
        console.log(`[Config] Loaded from: ${p}`)
        return data
      }
    } catch (e) { /* skip */ }
  }
  console.warn('[Config] No config.json found, using defaults')
  return {}
}

const appConfig = loadAppConfig()

// ============= Display / GPU Compatibility =============
// Keep GPU acceleration for the local desktop. Only force software rendering
// when DISPLAY explicitly points at a remote X server or when manually disabled.
const displayEnv = process.env.DISPLAY || ''
const isRemoteDisplay = displayEnv.startsWith('localhost:') ||
                        displayEnv.includes(':') && !displayEnv.startsWith(':0') && !displayEnv.startsWith(':1')
const forceSoftwareRender = isRemoteDisplay || process.env.ELECTRON_DISABLE_GPU === '1'

if (forceSoftwareRender) {
  console.log(`[GPU] Software rendering enabled (DISPLAY=${displayEnv})`)
  app.disableHardwareAcceleration()
} else if (appConfig.electron?.gpu?.enabled !== false) {
  const gpuConfig = appConfig.electron?.gpu || {}
  app.commandLine.appendSwitch('ignore-gpu-blocklist')
  if (gpuConfig.enable_gpu_rasterization !== false) {
    app.commandLine.appendSwitch('enable-gpu-rasterization')
  }
  if (gpuConfig.enable_zero_copy !== false) {
    app.commandLine.appendSwitch('enable-zero-copy')
  }
  if (gpuConfig.disable_frame_rate_limit) {
    app.commandLine.appendSwitch('disable-frame-rate-limit')
  }
  if (gpuConfig.enable_vaapi) {
    app.commandLine.appendSwitch('enable-features', 'VaapiVideoDecoder,VaapiVideoEncoder')
  }
}

// Sandbox conflicts with many server environments
app.commandLine.appendSwitch('no-sandbox')

// ============= Service Manager =============

const serviceManager = new ServiceManager()

// ============= App Configuration =============

const BACKEND_PORT = appConfig.services?.backend?.port || 8001
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`

const store = new Store({
  defaults: {
    backendUrl: BACKEND_URL,
    cacheMaxSize: 10 * 1024 * 1024 * 1024, // 10GB
    sessionsStoragePath: '',
  }
})

const cacheDir = path.join(app.getPath('userData'), 'cache')
const frameCache = new FrameCache(cacheDir)

let mainWindow = null

function getInitialWindowBounds() {
  const { workAreaSize } = screen.getPrimaryDisplay()
  const width = Math.min(Math.max(Math.round(workAreaSize.width * 0.94), 1680), workAreaSize.width)
  const height = Math.min(Math.max(Math.round(workAreaSize.height * 0.94), 980), workAreaSize.height)

  return { width, height }
}

function createWindow() {
  const initialBounds = getInitialWindowBounds()

  mainWindow = new BrowserWindow({
    width: initialBounds.width,
    height: initialBounds.height,
    minWidth: Math.min(1360, initialBounds.width),
    minHeight: Math.min(860, initialBounds.height),
    resizable: true,
    maximizable: true,
    backgroundColor: '#242424',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
      backgroundThrottling: appConfig.electron?.performance?.background_throttling !== false,
      spellcheck: appConfig.electron?.performance?.spellcheck !== false
    },
    icon: path.join(__dirname, 'icon.png'),
    title: 'Video Analyzer',
    show: false
  })

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    mainWindow.maximize()
  })

  // Force repaint on resize to avoid compositor stale regions during live video playback.
  let resizeDebounce = null
  mainWindow.on('resize', () => {
    if (resizeDebounce) clearTimeout(resizeDebounce)
    resizeDebounce = setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.invalidate()
      }
    }, 100)
  })

  if (process.env.NODE_ENV === 'development' || process.argv.includes('--dev')) {
    const devServerUrl = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5133'
    mainWindow.loadURL(devServerUrl)
    if (process.env.ELECTRON_OPEN_DEVTOOLS === '1') {
      mainWindow.webContents.openDevTools({ mode: 'detach' })
    }
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

// ============= IPC: Config =============

ipcMain.handle('get-config', (event, key) => {
  if (key) return store.get(key)
  return store.store
})

ipcMain.handle('set-config', (event, key, value) => {
  store.set(key, value)
  return true
})

ipcMain.handle('get-backend-url', () => {
  if (app.isPackaged) return BACKEND_URL
  return store.get('backendUrl')
})

ipcMain.handle('set-backend-url', (event, url) => {
  store.set('backendUrl', url)
  return true
})

// ============= IPC: Service Management =============

ipcMain.handle('get-all-service-statuses', () => {
  return serviceManager.getAllStatuses()
})

ipcMain.handle('get-service-status', async (event, key) => {
  const statuses = serviceManager.getAllStatuses()
  return statuses[key] || null
})

ipcMain.handle('check-service-health', async (event, key) => {
  const healthy = await serviceManager.checkHealth(key)
  return { key, healthy }
})

ipcMain.handle('start-service', async (event, key) => {
  try {
    const result = await serviceManager.startService(key)
    return { success: result, statuses: serviceManager.getAllStatuses() }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

ipcMain.handle('stop-service', (event, key) => {
  serviceManager.stopService(key)
  return { success: true, statuses: serviceManager.getAllStatuses() }
})

ipcMain.handle('restart-service', async (event, key) => {
  try {
    const result = await serviceManager.restartService(key)
    return { success: result, statuses: serviceManager.getAllStatuses() }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

ipcMain.handle('start-all-services', async () => {
  try {
    const statuses = await serviceManager.startAll()
    return { success: true, statuses }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

// Legacy compatibility
ipcMain.handle('get-backend-status', async () => {
  const healthy = await serviceManager.checkHealth('backend')
  return {
    running: healthy,
    embedded: app.isPackaged,
    url: BACKEND_URL,
    pid: serviceManager.statuses.backend?.pid || null
  }
})

ipcMain.handle('restart-backend', async () => {
  try {
    await serviceManager.restartService('backend')
    return { success: true }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

// ============= IPC: Frame Cache =============

ipcMain.handle('cache-frame', async (event, sessionId, filename, data) => {
  try {
    await frameCache.cacheFrame(sessionId, filename, data)
    return { success: true }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

ipcMain.handle('get-cached-frame', async (event, sessionId, filename) => {
  try {
    const data = await frameCache.getCachedFrame(sessionId, filename)
    return { success: true, data }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

async function findSessionDir(sessionsPath, sessionId) {
  try {
    const directPath = path.join(sessionsPath, sessionId)
    if (fsSync.existsSync(directPath)) return directPath

    const entries = fsSync.readdirSync(sessionsPath, { withFileTypes: true })
    const matchingDirs = entries
      .filter(entry => entry.isDirectory() && entry.name.includes(sessionId))
      .map(entry => entry.name)
      .sort()

    if (matchingDirs.length > 0) {
      return path.join(sessionsPath, matchingDirs[matchingDirs.length - 1])
    }
    return null
  } catch (error) {
    console.error('Error finding session dir:', error)
    return null
  }
}

ipcMain.handle('get-local-frame', async (event, sessionId, filename, subfolder = 'frames') => {
  try {
    let sessionsPath = store.get('sessionsStoragePath')

    if (!sessionsPath && app.isPackaged) {
      sessionsPath = path.join(process.resourcesPath, 'backend', 'sessions')
    }

    if (!sessionsPath) {
      return { success: false, error: 'Sessions storage path not configured' }
    }

    const directPath = path.join(sessionsPath, sessionId, subfolder, filename)
    if (fsSync.existsSync(directPath)) {
      const data = await fs.readFile(directPath)
      return { success: true, data, path: directPath }
    }

    const sessionDir = await findSessionDir(sessionsPath, sessionId)
    if (sessionDir) {
      const framePath = path.join(sessionDir, subfolder, filename)
      if (fsSync.existsSync(framePath)) {
        const data = await fs.readFile(framePath)
        return { success: true, data, path: framePath }
      }
      if (subfolder !== 'frames') {
        const fallbackPath = path.join(sessionDir, 'frames', filename)
        if (fsSync.existsSync(fallbackPath)) {
          const data = await fs.readFile(fallbackPath)
          return { success: true, data, path: fallbackPath }
        }
      }
    }

    return { success: false, error: `Frame not found: ${sessionId}/${subfolder}/${filename}` }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

ipcMain.handle('get-sessions-storage-path', () => store.get('sessionsStoragePath'))
ipcMain.handle('set-sessions-storage-path', (event, p) => { store.set('sessionsStoragePath', p); return true })

ipcMain.handle('check-cached-frame', async (event, sessionId, filename) => {
  try {
    const exists = await frameCache.hasCachedFrame(sessionId, filename)
    return { success: true, exists }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

ipcMain.handle('download-session', async (event, sessionId) => {
  const backendUrl = app.isPackaged ? BACKEND_URL : store.get('backendUrl')
  try {
    const result = await frameCache.downloadSession(sessionId, backendUrl)
    return { success: true, ...result }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

ipcMain.handle('get-cache-stats', async () => {
  try {
    const stats = await frameCache.getStats()
    return { success: true, stats }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

ipcMain.handle('clear-cache', async (event, sessionId) => {
  try {
    await frameCache.clearCache(sessionId)
    return { success: true }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

// ============= IPC: Dialogs & App Info =============

ipcMain.handle('show-message-box', async (event, options) => {
  return dialog.showMessageBox(mainWindow, options)
})

ipcMain.handle('show-open-dialog', async (event, options) => {
  return dialog.showOpenDialog(mainWindow, options)
})

ipcMain.handle('get-app-info', () => {
  return {
    version: app.getVersion(),
    name: app.getName(),
    userDataPath: app.getPath('userData'),
    cachePath: cacheDir,
    embedded: app.isPackaged,
    services: serviceManager.getAllStatuses(),
  }
})

// ============= App Lifecycle =============

app.whenReady().then(async () => {
  // Initialize service manager
  const resourcesPath = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', '..')
  serviceManager.init(resourcesPath, appConfig, app.isPackaged)

  // Forward service status changes to renderer
  serviceManager.onStatusChange((key, status) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('service-status-changed', { key, status })
      // Legacy compatibility
      if (key === 'backend') {
        mainWindow.webContents.send('backend-status', {
          running: status.running,
          starting: status.starting,
          error: status.error,
        })
      }
    }
  })

  createWindow()

  // Start all services if packaged
  if (app.isPackaged) {
    try {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('backend-status', { running: false, starting: true })
      }
      await serviceManager.startAll()
    } catch (err) {
      console.error('[App] Failed to start services:', err)
      if (mainWindow && !mainWindow.isDestroyed()) {
        dialog.showErrorBox(
          '服务启动失败',
          `无法启动内置服务：${err.message}\n\n请检查日志或联系技术支持。`
        )
      }
    }
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', () => {
  console.log('Video Analyzer is closing...')
  serviceManager.stopAll()
})

app.on('will-quit', () => {
  serviceManager.stopAll()
})

process.on('uncaughtException', (error) => {
  console.error('Uncaught Exception:', error)
})

process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason)
})
