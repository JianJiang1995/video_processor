const fs = require('fs').promises
const fsSync = require('fs')
const path = require('path')
const https = require('https')
const http = require('http')

class FrameCache {
  constructor(cacheDir) {
    this.cacheDir = cacheDir
    this.maxSize = 10 * 1024 * 1024 * 1024 // 10GB 默认上限
    this.ensureCacheDir()
  }

  /**
   * 确保缓存目录存在
   */
  ensureCacheDir() {
    if (!fsSync.existsSync(this.cacheDir)) {
      fsSync.mkdirSync(this.cacheDir, { recursive: true })
    }
  }

  /**
   * 获取会话的缓存目录路径
   */
  getSessionDir(sessionId) {
    return path.join(this.cacheDir, sessionId)
  }

  /**
   * 获取帧文件的完整路径
   */
  getFramePath(sessionId, filename) {
    return path.join(this.getSessionDir(sessionId), filename)
  }

  /**
   * 缓存单个帧
   * @param {string} sessionId - 会话 ID
   * @param {string} filename - 帧文件名
   * @param {Buffer|ArrayBuffer|string} data - 图像数据（Buffer、ArrayBuffer 或 base64 字符串）
   */
  async cacheFrame(sessionId, filename, data) {
    const sessionDir = this.getSessionDir(sessionId)
    
    // 确保会话目录存在
    if (!fsSync.existsSync(sessionDir)) {
      await fs.mkdir(sessionDir, { recursive: true })
    }

    const framePath = this.getFramePath(sessionId, filename)
    
    // 处理不同类型的数据
    let buffer
    if (Buffer.isBuffer(data)) {
      buffer = data
    } else if (Array.isArray(data)) {
      // 从 IPC 传递的数组（最常见的情况）
      buffer = Buffer.from(data)
    } else if (data instanceof ArrayBuffer) {
      buffer = Buffer.from(data)
    } else if (typeof data === 'string') {
      // 假设是 base64 编码的字符串
      const base64Data = data.replace(/^data:image\/\w+;base64,/, '')
      buffer = Buffer.from(base64Data, 'base64')
    } else if (data && data.type === 'Buffer' && Array.isArray(data.data)) {
      // 从 IPC 传递的 Buffer 对象（旧格式）
      buffer = Buffer.from(data.data)
    } else {
      console.error('[FrameCache] Unsupported data type:', typeof data, data)
      throw new Error('Unsupported data type for caching')
    }

    await fs.writeFile(framePath, buffer)
    
    // 更新访问时间（用于 LRU）
    const now = new Date()
    await fs.utimes(framePath, now, now)
    
    return framePath
  }

  /**
   * 读取缓存的帧
   * @param {string} sessionId - 会话 ID
   * @param {string} filename - 帧文件名
   * @returns {Buffer|null} - 图像数据或 null（如果不存在）
   */
  async getCachedFrame(sessionId, filename) {
    const framePath = this.getFramePath(sessionId, filename)
    
    try {
      const data = await fs.readFile(framePath)
      
      // 更新访问时间（用于 LRU）
      const now = new Date()
      await fs.utimes(framePath, now, now)
      
      return data
    } catch (error) {
      if (error.code === 'ENOENT') {
        return null
      }
      throw error
    }
  }

  /**
   * 检查帧是否已缓存
   * @param {string} sessionId - 会话 ID
   * @param {string} filename - 帧文件名
   * @returns {boolean}
   */
  async hasCachedFrame(sessionId, filename) {
    const framePath = this.getFramePath(sessionId, filename)
    try {
      await fs.access(framePath)
      return true
    } catch {
      return false
    }
  }

  /**
   * 获取缓存帧的文件路径（用于 file:// 协议加载）
   * @param {string} sessionId - 会话 ID
   * @param {string} filename - 帧文件名
   * @returns {string|null}
   */
  async getCachedFramePath(sessionId, filename) {
    const framePath = this.getFramePath(sessionId, filename)
    try {
      await fs.access(framePath)
      return framePath
    } catch {
      return null
    }
  }

  /**
   * 下载整个会话的所有帧
   * @param {string} sessionId - 会话 ID
   * @param {string} backendUrl - 后端服务器 URL
   * @param {function} onProgress - 进度回调
   * @returns {object} - 下载结果
   */
  async downloadSession(sessionId, backendUrl, onProgress = null) {
    const sessionDir = this.getSessionDir(sessionId)
    
    // 确保会话目录存在
    if (!fsSync.existsSync(sessionDir)) {
      await fs.mkdir(sessionDir, { recursive: true })
    }

    // 获取会话的帧列表
    const framesUrl = `${backendUrl}/sessions/${sessionId}/frames`
    const frameList = await this.fetchJson(framesUrl)
    
    if (!frameList || !Array.isArray(frameList.frames)) {
      throw new Error('Failed to get frame list from server')
    }

    const frames = frameList.frames
    const total = frames.length
    let downloaded = 0
    let skipped = 0
    let failed = 0

    for (const frameInfo of frames) {
      const filename = typeof frameInfo === 'string' ? frameInfo : frameInfo.filename
      
      // 检查是否已缓存
      if (await this.hasCachedFrame(sessionId, filename)) {
        skipped++
        downloaded++
        if (onProgress) {
          onProgress({ current: downloaded, total, skipped, failed })
        }
        continue
      }

      // 下载帧
      try {
        const frameUrl = `${backendUrl}/sessions/${sessionId}/frames/${filename}`
        const data = await this.fetchBinary(frameUrl)
        await this.cacheFrame(sessionId, filename, data)
        downloaded++
      } catch (error) {
        console.error(`Failed to download frame ${filename}:`, error)
        failed++
        downloaded++
      }

      if (onProgress) {
        onProgress({ current: downloaded, total, skipped, failed })
      }
    }

    return { total, downloaded: downloaded - skipped - failed, skipped, failed }
  }

  /**
   * 获取缓存统计信息
   */
  async getStats() {
    const stats = {
      totalSize: 0,
      sessionCount: 0,
      frameCount: 0,
      sessions: []
    }

    try {
      const entries = await fs.readdir(this.cacheDir, { withFileTypes: true })
      
      for (const entry of entries) {
        if (entry.isDirectory()) {
          const sessionDir = path.join(this.cacheDir, entry.name)
          const sessionStats = await this.getSessionStats(entry.name, sessionDir)
          stats.sessions.push(sessionStats)
          stats.totalSize += sessionStats.size
          stats.frameCount += sessionStats.frameCount
          stats.sessionCount++
        }
      }
    } catch (error) {
      if (error.code !== 'ENOENT') {
        throw error
      }
    }

    return stats
  }

  /**
   * 获取单个会话的统计信息
   */
  async getSessionStats(sessionId, sessionDir) {
    const stats = {
      sessionId,
      size: 0,
      frameCount: 0,
      oldestAccess: null,
      newestAccess: null
    }

    try {
      const files = await fs.readdir(sessionDir)
      
      for (const file of files) {
        const filePath = path.join(sessionDir, file)
        const fileStat = await fs.stat(filePath)
        
        if (fileStat.isFile()) {
          stats.size += fileStat.size
          stats.frameCount++
          
          const accessTime = fileStat.atime
          if (!stats.oldestAccess || accessTime < stats.oldestAccess) {
            stats.oldestAccess = accessTime
          }
          if (!stats.newestAccess || accessTime > stats.newestAccess) {
            stats.newestAccess = accessTime
          }
        }
      }
    } catch (error) {
      console.error(`Error getting stats for session ${sessionId}:`, error)
    }

    return stats
  }

  /**
   * 清理缓存
   * @param {string} sessionId - 可选，指定要清理的会话。如果不提供，执行 LRU 清理
   */
  async clearCache(sessionId = null) {
    if (sessionId) {
      // 清理指定会话
      const sessionDir = this.getSessionDir(sessionId)
      await this.removeDirectory(sessionDir)
    } else {
      // LRU 清理：删除最旧的会话直到缓存大小低于上限
      await this.lruCleanup()
    }
  }

  /**
   * LRU 清理策略
   */
  async lruCleanup() {
    const stats = await this.getStats()
    
    if (stats.totalSize <= this.maxSize) {
      return // 不需要清理
    }

    // 按最后访问时间排序（最旧的在前）
    const sortedSessions = stats.sessions.sort((a, b) => {
      const aTime = a.oldestAccess ? a.oldestAccess.getTime() : 0
      const bTime = b.oldestAccess ? b.oldestAccess.getTime() : 0
      return aTime - bTime
    })

    let currentSize = stats.totalSize
    
    for (const session of sortedSessions) {
      if (currentSize <= this.maxSize * 0.8) {
        break // 清理到 80% 以下
      }
      
      await this.clearCache(session.sessionId)
      currentSize -= session.size
      console.log(`LRU cleanup: removed session ${session.sessionId}`)
    }
  }

  /**
   * 递归删除目录
   */
  async removeDirectory(dirPath) {
    try {
      const entries = await fs.readdir(dirPath, { withFileTypes: true })
      
      for (const entry of entries) {
        const fullPath = path.join(dirPath, entry.name)
        if (entry.isDirectory()) {
          await this.removeDirectory(fullPath)
        } else {
          await fs.unlink(fullPath)
        }
      }
      
      await fs.rmdir(dirPath)
    } catch (error) {
      if (error.code !== 'ENOENT') {
        throw error
      }
    }
  }

  /**
   * HTTP/HTTPS JSON 请求
   */
  fetchJson(url) {
    return new Promise((resolve, reject) => {
      const client = url.startsWith('https') ? https : http
      
      client.get(url, (res) => {
        let data = ''
        res.on('data', chunk => data += chunk)
        res.on('end', () => {
          try {
            resolve(JSON.parse(data))
          } catch (e) {
            reject(new Error('Failed to parse JSON response'))
          }
        })
      }).on('error', reject)
    })
  }

  /**
   * HTTP/HTTPS 二进制请求
   */
  fetchBinary(url) {
    return new Promise((resolve, reject) => {
      const client = url.startsWith('https') ? https : http
      
      client.get(url, (res) => {
        const chunks = []
        res.on('data', chunk => chunks.push(chunk))
        res.on('end', () => {
          resolve(Buffer.concat(chunks))
        })
      }).on('error', reject)
    })
  }
}

module.exports = FrameCache
