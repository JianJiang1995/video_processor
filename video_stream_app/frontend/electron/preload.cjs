const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  // 标识 Electron 环境
  isElectron: true,
  
  // ============= 配置管理 =============
  getConfig: (key) => ipcRenderer.invoke('get-config', key),
  setConfig: (key, value) => ipcRenderer.invoke('set-config', key, value),
  getBackendUrl: () => ipcRenderer.invoke('get-backend-url'),
  setBackendUrl: (url) => ipcRenderer.invoke('set-backend-url', url),
  
  // ============= 服务管理 (全部服务) =============
  getAllServiceStatuses: () => ipcRenderer.invoke('get-all-service-statuses'),
  getServiceStatus: (key) => ipcRenderer.invoke('get-service-status', key),
  checkServiceHealth: (key) => ipcRenderer.invoke('check-service-health', key),
  startService: (key) => ipcRenderer.invoke('start-service', key),
  stopService: (key) => ipcRenderer.invoke('stop-service', key),
  restartService: (key) => ipcRenderer.invoke('restart-service', key),
  startAllServices: () => ipcRenderer.invoke('start-all-services'),
  
  // 监听单个服务状态变化
  onServiceStatusChanged: (callback) => {
    const listener = (event, data) => callback(data)
    ipcRenderer.on('service-status-changed', listener)
    return () => ipcRenderer.removeListener('service-status-changed', listener)
  },
  
  // ============= 后端兼容接口 =============
  getBackendStatus: () => ipcRenderer.invoke('get-backend-status'),
  restartBackend: () => ipcRenderer.invoke('restart-backend'),
  onBackendStatus: (callback) => {
    const listener = (event, status) => callback(status)
    ipcRenderer.on('backend-status', listener)
    return () => ipcRenderer.removeListener('backend-status', listener)
  },
  
  // ============= 帧缓存操作 =============
  cacheFrame: (sessionId, filename, data) => 
    ipcRenderer.invoke('cache-frame', sessionId, filename, data),
  
  getCachedFrame: (sessionId, filename) => 
    ipcRenderer.invoke('get-cached-frame', sessionId, filename),
  
  getLocalFrame: (sessionId, filename, subfolder) =>
    ipcRenderer.invoke('get-local-frame', sessionId, filename, subfolder),
  
  getSessionsStoragePath: () => ipcRenderer.invoke('get-sessions-storage-path'),
  setSessionsStoragePath: (path) => ipcRenderer.invoke('set-sessions-storage-path', path),
  
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
    return () => ipcRenderer.removeListener('download-progress', listener)
  }
})

console.log('Electron preload script loaded successfully')
