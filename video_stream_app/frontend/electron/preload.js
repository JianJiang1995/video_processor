const { contextBridge, ipcRenderer } = require('electron')

// 通过 contextBridge 暴露安全的 API 给渲染进程
contextBridge.exposeInMainWorld('electronAPI', {
  // 标识当前运行在 Electron 环境
  isElectron: true,
  
  // ============= 配置管理 =============
  getConfig: (key) => ipcRenderer.invoke('get-config', key),
  setConfig: (key, value) => ipcRenderer.invoke('set-config', key, value),
  getBackendUrl: () => ipcRenderer.invoke('get-backend-url'),
  setBackendUrl: (url) => ipcRenderer.invoke('set-backend-url', url),
  
  // ============= 帧缓存操作 =============
  cacheFrame: (sessionId, filename, data) => 
    ipcRenderer.invoke('cache-frame', sessionId, filename, data),
  
  getCachedFrame: (sessionId, filename) => 
    ipcRenderer.invoke('get-cached-frame', sessionId, filename),
  
  // 直接从后端 session 目录读取帧（不经过 HTTP，核心优化）
  getLocalFrame: (sessionId, filename, subfolder) =>
    ipcRenderer.invoke('get-local-frame', sessionId, filename, subfolder),
  
  checkCachedFrame: (sessionId, filename) =>
    ipcRenderer.invoke('check-cached-frame', sessionId, filename),
  
  downloadSession: (sessionId) => 
    ipcRenderer.invoke('download-session', sessionId),
  
  getCacheStats: () => 
    ipcRenderer.invoke('get-cache-stats'),
  
  clearCache: (sessionId) => 
    ipcRenderer.invoke('clear-cache', sessionId),
  
  // ============= 对话框 =============
  showMessageBox: (options) => 
    ipcRenderer.invoke('show-message-box', options),
  
  showOpenDialog: (options) => 
    ipcRenderer.invoke('show-open-dialog', options),
  
  // ============= 应用信息 =============
  getAppInfo: () => ipcRenderer.invoke('get-app-info'),
  
  // ============= 事件监听 =============
  onDownloadProgress: (callback) => {
    const listener = (event, progress) => callback(progress)
    ipcRenderer.on('download-progress', listener)
    // 返回取消订阅函数
    return () => ipcRenderer.removeListener('download-progress', listener)
  }
})

// 打印加载成功信息（调试用）
console.log('Electron preload script loaded successfully')
