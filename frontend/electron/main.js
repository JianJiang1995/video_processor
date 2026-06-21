const { app, BrowserWindow, ipcMain, dialog } = require('electron')
const Store = require('electron-store')
const path = require('path')
const fs = require('fs')
const FrameCache = require('./frameCache')

// ============= Load Project Configuration =============
// Try to load config.json from project root (video_stream_app/)
let projectConfig = {}
const configPaths = [
  path.join(__dirname, '../../config.json'),  // Development: electron/ -> frontend/ -> video_stream_app/
  path.join(__dirname, '../../../config.json'),  // Production: packed app
  path.join(process.cwd(), 'config.json')  // Current working directory
]

for (const configPath of configPaths) {
  try {
    if (fs.existsSync(configPath)) {
      const configContent = fs.readFileSync(configPath, 'utf-8')
      projectConfig = JSON.parse(configContent)
      console.log(`[Config] Loaded from: ${configPath}`)
      break
    }
  } catch (e) {
    console.warn(`[Config] Failed to load ${configPath}:`, e.message)
  }
}

// Extract electron config with defaults
const electronConfig = projectConfig.electron || {}
const gpuConfig = electronConfig.gpu || { enabled: true, device_id: 0 }
const perfConfig = electronConfig.performance || {}

// ============= GPU Configuration =============
if (gpuConfig.enabled !== false) {
  const gpuDeviceId = String(gpuConfig.device_id || 0)
  
  // Set environment variables for GPU selection
  process.env.CUDA_VISIBLE_DEVICES = gpuDeviceId
  process.env.DRI_PRIME = gpuDeviceId  // Linux: use specific GPU for rendering
  
  console.log(`[GPU] Using GPU device: ${gpuDeviceId}`)
  
  // Enable hardware acceleration (use safe defaults)
  app.commandLine.appendSwitch('ignore-gpu-blocklist')
  
  // Note: 'use-gl=desktop' can cause GPU init errors on some systems, use 'egl' or omit
  // app.commandLine.appendSwitch('use-gl', 'desktop')
  
  if (gpuConfig.enable_gpu_rasterization !== false) {
    app.commandLine.appendSwitch('enable-gpu-rasterization')
  }
  
  if (gpuConfig.enable_zero_copy !== false) {
    app.commandLine.appendSwitch('enable-zero-copy')
  }
  
  if (gpuConfig.disable_frame_rate_limit !== false) {
    app.commandLine.appendSwitch('disable-frame-rate-limit')
  }
  
  if (gpuConfig.enable_vaapi !== false) {
    // VAAPI for Linux hardware video decode/encode
    app.commandLine.appendSwitch('enable-features', 'VaapiVideoDecoder,VaapiVideoEncoder')
  }
} else {
  console.log('[GPU] Hardware acceleration disabled by config')
  app.disableHardwareAcceleration()
}

// 初始化配置存储
const store = new Store({
  defaults: {
    backendUrl: 'http://localhost:8001',
    cacheMaxSize: 10 * 1024 * 1024 * 1024, // 10GB
  }
})

// 初始化帧缓存
const cacheDir = path.join(app.getPath('userData'), 'cache')
const frameCache = new FrameCache(cacheDir)

let mainWindow = null

function createWindow() {
  // Build webPreferences from config
  const webPreferences = {
    preload: path.join(__dirname, 'preload.js'),
    contextIsolation: true,
    nodeIntegration: false,
    webSecurity: true,
    offscreen: false,
    // Performance optimizations from config
    backgroundThrottling: perfConfig.background_throttling !== undefined 
      ? perfConfig.background_throttling : false,
    spellcheck: perfConfig.spellcheck !== undefined 
      ? perfConfig.spellcheck : false
  }
  
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 700,
    webPreferences,
    icon: path.join(__dirname, 'icon.png'),
    title: 'Video Analyzer',
    show: false // 等待 ready-to-show 事件
  })

  // 窗口准备好后再显示，避免白屏闪烁
  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
  })

  // 开发模式加载 Vite 服务器，生产模式加载打包文件
  if (process.env.NODE_ENV === 'development' || process.argv.includes('--dev')) {
    const devServerUrl = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5133'
    mainWindow.loadURL(devServerUrl)
    if (process.env.ELECTRON_OPEN_DEVTOOLS === '1') {
      mainWindow.webContents.openDevTools()
    }
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  // 窗口关闭时清理引用
  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

// ============= IPC 处理程序 =============

// 配置相关
ipcMain.handle('get-config', (event, key) => {
  if (key) {
    return store.get(key)
  }
  return store.store
})

ipcMain.handle('set-config', (event, key, value) => {
  store.set(key, value)
  return true
})

ipcMain.handle('get-backend-url', () => {
  return store.get('backendUrl')
})

ipcMain.handle('set-backend-url', (event, url) => {
  store.set('backendUrl', url)
  return true
})

// 帧缓存相关
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

ipcMain.handle('check-cached-frame', async (event, sessionId, filename) => {
  try {
    const exists = await frameCache.hasCachedFrame(sessionId, filename)
    return { success: true, exists }
  } catch (error) {
    return { success: false, error: error.message }
  }
})

ipcMain.handle('download-session', async (event, sessionId) => {
  const backendUrl = store.get('backendUrl')
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

// 直接从后端 session 存储目录读取帧（核心优化：避免 HTTP 传输）
ipcMain.handle('get-local-frame', async (event, sessionId, filename, subfolder = 'frames') => {  try {
    // 后端 sessions 存储目录（相对于项目根目录）
    const sessionsBasePaths = [
      path.join(__dirname, '../../sessions'),  // Development: electron/ -> frontend/ -> video_stream_app/sessions
      path.join(__dirname, '../../../sessions'),  // Alternative path
      '/data2/jj/proj/video_processor/video_stream_app/sessions'  // Absolute fallback
    ]
    
    let sessionsDir = null
    for (const basePath of sessionsBasePaths) {
      if (fs.existsSync(basePath)) {
        sessionsDir = basePath
        break
      }
    }
    
    if (!sessionsDir) {
      return { success: false, error: 'Sessions directory not found' }
    }
    
    // 查找包含 sessionId 的文件夹（格式：YYYYMMDD_HHMMSS_sessionId_videoName）
    const sessionFolders = fs.readdirSync(sessionsDir).filter(folder => {
      return folder.includes(sessionId)
    })
    
    if (sessionFolders.length === 0) {
      return { success: false, error: `No session folder found for ${sessionId}` }
    }
    
    // 使用最新的文件夹（按名称排序，最后一个是最新的）
    sessionFolders.sort()
    const sessionFolder = sessionFolders[sessionFolders.length - 1]
    
    // 构建帧文件路径
    const framePath = path.join(sessionsDir, sessionFolder, subfolder, filename)
    
    if (!fs.existsSync(framePath)) {
      // 尝试不同的子文件夹
      const altSubfolders = subfolder === 'preview' ? ['frames'] : ['preview']
      for (const altSub of altSubfolders) {
        const altPath = path.join(sessionsDir, sessionFolder, altSub, filename)
        if (fs.existsSync(altPath)) {
          const data = fs.readFileSync(altPath)
          return { success: true, data: data, path: altPath }
        }
      }
      return { success: false, error: `Frame not found: ${framePath}` }
    }
    
    // 读取帧文件
    const data = fs.readFileSync(framePath)
    return { success: true, data: data, path: framePath }
    
  } catch (error) {
    return { success: false, error: error.message }
  }
})

// 对话框
ipcMain.handle('show-message-box', async (event, options) => {
  return dialog.showMessageBox(mainWindow, options)
})

ipcMain.handle('show-open-dialog', async (event, options) => {
  return dialog.showOpenDialog(mainWindow, options)
})

// 应用信息
ipcMain.handle('get-app-info', () => {
  return {
    version: app.getVersion(),
    name: app.getName(),
    userDataPath: app.getPath('userData'),
    cachePath: cacheDir
  }
})

// ============= 应用生命周期 =============

app.whenReady().then(() => {
  createWindow()

  app.on('activate', () => {
    // macOS 点击 dock 图标时重新创建窗口
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
})

app.on('window-all-closed', () => {
  // macOS 下通常不会退出应用
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// 退出前清理
app.on('before-quit', async () => {
  // 可以在这里做一些清理工作
  console.log('Video Analyzer is closing...')
})

// 处理未捕获的异常
process.on('uncaughtException', (error) => {
  console.error('Uncaught Exception:', error)
})

process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason)
})
