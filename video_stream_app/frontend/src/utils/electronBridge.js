/**
 * Electron API 封装
 * 提供统一的接口，在浏览器和 Electron 环境中都能正常工作
 */

// 检测是否在 Electron 环境中运行
export const isElectron = () => {
  return !!(window.electronAPI && window.electronAPI.isElectron)
}

// ============= 配置管理 =============

/**
 * 获取后端 URL
 * - Electron 环境：从配置文件读取
 * - 浏览器环境：返回默认值（通过 Vite proxy 代理）
 */
export const getBackendUrl = async () => {
  if (isElectron()) {
    const url = await window.electronAPI.getBackendUrl()
    return url || 'http://localhost:8001'
  }
  // 浏览器环境下使用相对路径，通过 Vite proxy 代理
  return ''
}

/**
 * 设置后端 URL（仅 Electron 环境有效）
 */
export const setBackendUrl = async (url) => {
  if (isElectron()) {
    return await window.electronAPI.setBackendUrl(url)
  }
  console.warn('setBackendUrl is only available in Electron environment')
  return false
}

/**
 * 获取配置项
 */
export const getConfig = async (key) => {
  if (isElectron()) {
    return await window.electronAPI.getConfig(key)
  }
  // 浏览器环境使用 localStorage
  const value = localStorage.getItem(`config_${key}`)
  try {
    return value ? JSON.parse(value) : null
  } catch {
    return value
  }
}

/**
 * 设置配置项
 */
export const setConfig = async (key, value) => {
  if (isElectron()) {
    return await window.electronAPI.setConfig(key, value)
  }
  // 浏览器环境使用 localStorage
  localStorage.setItem(`config_${key}`, JSON.stringify(value))
  return true
}

// ============= 帧缓存操作 =============

/**
 * 缓存帧数据
 * @param {string} sessionId - 会话 ID
 * @param {string} filename - 帧文件名
 * @param {Blob|ArrayBuffer|string} data - 图像数据
 */
export const cacheFrame = async (sessionId, filename, data) => {
  if (!isElectron()) {
    // 浏览器环境不支持本地缓存
    return { success: false, error: 'Cache not available in browser' }
  }
  
  // 转换为普通数组以便 IPC 传输（ArrayBuffer 通过 IPC 传输后格式会变化）
  let arrayData
  if (data instanceof Blob) {
    const ab = await data.arrayBuffer()
    arrayData = Array.from(new Uint8Array(ab))
  } else if (data instanceof ArrayBuffer) {
    arrayData = Array.from(new Uint8Array(data))
  } else if (ArrayBuffer.isView(data)) {
    arrayData = Array.from(new Uint8Array(data.buffer))
  } else if (Array.isArray(data)) {
    arrayData = data
  } else {
    console.warn('[cacheFrame] Unsupported data type:', typeof data)
    return { success: false, error: 'Unsupported data type' }
  }
  
  return await window.electronAPI.cacheFrame(sessionId, filename, arrayData)
}

/**
 * 获取缓存的帧
 * @param {string} sessionId - 会话 ID
 * @param {string} filename - 帧文件名
 * @returns {Blob|null}
 */
export const getCachedFrame = async (sessionId, filename) => {
  if (!isElectron()) {
    return { success: false, data: null }
  }
  
  const result = await window.electronAPI.getCachedFrame(sessionId, filename)
  
  if (result.success && result.data) {
    // 将 Buffer 数据转换为 Blob
    const uint8Array = new Uint8Array(result.data.data || result.data)
    const blob = new Blob([uint8Array], { type: 'image/jpeg' })
    return { success: true, data: blob }
  }
  
  return result
}

/**
 * 直接从后端 session 目录读取帧（不经过 HTTP）
 * 这是 Electron 环境下的首选方式，避免网络传输
 * @param {string} sessionId - 会话 ID
 * @param {string} filename - 帧文件名
 * @param {string} subfolder - 子文件夹（'frames' 或 'preview'）
 * @returns {Blob|null}
 */
export const getLocalFrame = async (sessionId, filename, subfolder = 'frames') => {  if (!isElectron()) {
    return { success: false, data: null, error: 'Not in Electron environment' }
  }
  
  try {
    const result = await window.electronAPI.getLocalFrame(sessionId, filename, subfolder)    
    if (result.success && result.data) {
      // 将 Buffer 数据转换为 Blob
      const uint8Array = new Uint8Array(result.data.data || result.data)
      const blob = new Blob([uint8Array], { type: 'image/jpeg' })
      return { success: true, data: blob, path: result.path }
    }
    
    return result
  } catch (e) {    return { success: false, data: null, error: e.message }
  }
}

/**
 * 检查帧是否已缓存
 * @param {string} sessionId - 会话 ID
 * @param {string} filename - 帧文件名
 * @returns {boolean}
 */
export const checkCachedFrame = async (sessionId, filename) => {
  if (!isElectron()) {
    return { success: true, exists: false }
  }
  return await window.electronAPI.checkCachedFrame(sessionId, filename)
}

/**
 * 下载整个会话的帧到本地缓存
 * @param {string} sessionId - 会话 ID
 */
export const downloadSession = async (sessionId) => {
  if (!isElectron()) {
    return { success: false, error: 'Download not available in browser' }
  }
  return await window.electronAPI.downloadSession(sessionId)
}

/**
 * 获取缓存统计信息
 */
export const getCacheStats = async () => {
  if (!isElectron()) {
    return { success: false, stats: null }
  }
  return await window.electronAPI.getCacheStats()
}

/**
 * 清除缓存
 * @param {string} sessionId - 可选，指定会话 ID。不提供则清除所有缓存
 */
export const clearCache = async (sessionId = null) => {
  if (!isElectron()) {
    return { success: false, error: 'Cache not available in browser' }
  }
  return await window.electronAPI.clearCache(sessionId)
}

// ============= 对话框 =============

/**
 * 显示消息对话框
 */
export const showMessageBox = async (options) => {
  if (isElectron()) {
    return await window.electronAPI.showMessageBox(options)
  }
  // 浏览器环境使用 alert/confirm
  if (options.type === 'question' || options.buttons?.length > 1) {
    const result = window.confirm(options.message)
    return { response: result ? 0 : 1 }
  }
  window.alert(options.message)
  return { response: 0 }
}

/**
 * 显示文件选择对话框
 */
export const showOpenDialog = async (options) => {
  if (isElectron()) {
    return await window.electronAPI.showOpenDialog(options)
  }
  // 浏览器环境不支持
  return { canceled: true, filePaths: [] }
}

// ============= 应用信息 =============

/**
 * 获取应用信息
 */
export const getAppInfo = async () => {
  if (isElectron()) {
    return await window.electronAPI.getAppInfo()
  }
  return {
    version: '1.0.0',
    name: 'Video Analyzer (Browser)',
    userDataPath: null,
    cachePath: null
  }
}

// ============= 事件监听 =============

/**
 * 监听下载进度
 * @param {function} callback - 进度回调函数
 * @returns {function} - 取消订阅函数
 */
export const onDownloadProgress = (callback) => {
  if (isElectron()) {
    return window.electronAPI.onDownloadProgress(callback)
  }
  return () => {} // 空的取消函数
}

// ============= 图像加载辅助 =============

/**
 * 加载帧图像，优先使用缓存
 * @param {string} sessionId - 会话 ID
 * @param {string} filename - 帧文件名
 * @param {string} backendUrl - 后端 URL（可选，用于回退）
 * @returns {string} - 图像 URL（可以是 blob: 或 http:）
 */
export const loadFrameImage = async (sessionId, filename, backendUrl = '') => {
  // 尝试从缓存加载
  if (isElectron()) {
    const cached = await getCachedFrame(sessionId, filename)
    if (cached.success && cached.data) {
      return URL.createObjectURL(cached.data)
    }
  }
  
  // 回退到网络加载
  const baseUrl = backendUrl || await getBackendUrl()
  return `${baseUrl}/sessions/${sessionId}/frames/${filename}`
}

/**
 * 释放通过 loadFrameImage 创建的 blob URL
 */
export const revokeFrameUrl = (url) => {
  if (url && url.startsWith('blob:')) {
    URL.revokeObjectURL(url)
  }
}

// 导出默认对象
export default {
  isElectron,
  getBackendUrl,
  setBackendUrl,
  getConfig,
  setConfig,
  cacheFrame,
  getCachedFrame,
  getLocalFrame,
  checkCachedFrame,
  downloadSession,
  getCacheStats,
  clearCache,
  showMessageBox,
  showOpenDialog,
  getAppInfo,
  onDownloadProgress,
  loadFrameImage,
  revokeFrameUrl
}
