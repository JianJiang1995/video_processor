const { app, BrowserWindow, ipcMain, dialog } = require('electron')
const Store = require('electron-store')
const path = require('path')
const fs = require('fs').promises
const fsSync = require('fs')
const FrameCache = require('./frameCache.cjs')

// 初始化配置存储
const store = new Store({
  defaults: {
    backendUrl: 'http://localhost:8001',
    cacheMaxSize: 10 * 1024 * 1024 * 1024, // 10GB
    // 后端 session 存储目录（Electron 直接读取帧文件）
    sessionsStoragePath: '/data2/jj/proj/video_processor/video_stream_app/sessions'
  }
})

// 初始化帧缓存
const cacheDir = path.join(app.getPath('userData'), 'cache')
const frameCache = new FrameCache(cacheDir)

let mainWindow = null

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 700,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true
    },
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
    const devServerUrl = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5174'
    mainWindow.loadURL(devServerUrl)
    mainWindow.webContents.openDevTools()
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

// 查找包含 sessionId 的 session 目录（返回最新的）
async function findSessionDir(sessionsPath, sessionId) {
  try {
    // 先尝试直接路径
    const directPath = path.join(sessionsPath, sessionId)
    if (fsSync.existsSync(directPath)) {
      return directPath
    }
    
    // 搜索包含 sessionId 的目录（处理短 ID 情况）
    // 返回最新的目录（按目录名排序，因为格式是 YYYYMMDD_HHMMSS_sessionId_xxx）
    const entries = fsSync.readdirSync(sessionsPath, { withFileTypes: true })
    const matchingDirs = entries
      .filter(entry => entry.isDirectory() && entry.name.includes(sessionId))
      .map(entry => entry.name)
      .sort()  // 按字母排序，时间戳格式保证最新的在最后
    
    if (matchingDirs.length > 0) {
      // 返回最新的目录（最后一个）
      return path.join(sessionsPath, matchingDirs[matchingDirs.length - 1])
    }
    
    return null
  } catch (error) {
    console.error('Error finding session dir:', error)
    return null
  }
}

// 直接从后端 session 目录读取帧（不经过 HTTP）
ipcMain.handle('get-local-frame', async (event, sessionId, filename, subfolder = 'frames') => {  
  try {
    const sessionsPath = store.get('sessionsStoragePath')
    
    // 1. 先尝试直接路径
    const directPath = path.join(sessionsPath, sessionId, subfolder, filename)
    if (fsSync.existsSync(directPath)) {
      const data = await fs.readFile(directPath)
      return { success: true, data, path: directPath }
    }
    
    // 2. 查找包含 sessionId 的目录（处理短 ID 如 dd4f34e6）
    const sessionDir = await findSessionDir(sessionsPath, sessionId)
    
    if (sessionDir) {
      const framePath = path.join(sessionDir, subfolder, filename)
      
      if (fsSync.existsSync(framePath)) {
        const data = await fs.readFile(framePath)
        return { success: true, data, path: framePath }
      }
      
      // 3. 尝试 frames 子文件夹作为 fallback
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

// 获取 session 存储路径配置
ipcMain.handle('get-sessions-storage-path', () => {
  return store.get('sessionsStoragePath')
})

ipcMain.handle('set-sessions-storage-path', (event, path) => {
  store.set('sessionsStoragePath', path)
  return true
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
