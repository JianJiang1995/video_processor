/**
 * AnalysisQueue - 视频分析请求队列管理器
 * 
 * 功能：
 * 1. 批量收集帧后一起发送（提高GPU利用率）
 * 2. 控制并发请求数量
 * 3. 返回主页时立即清空队列并取消pending请求
 * 4. 支持优先级和超时控制
 */

import axios from 'axios'

class AnalysisQueue {
  constructor(options = {}) {
    // 配置选项
    this.batchSize = options.batchSize || 5          // 每批处理的帧数
    this.batchInterval = options.batchInterval || 1000  // 批量发送间隔(ms)
    this.maxConcurrent = options.maxConcurrent || 2   // 最大并发请求数
    this.timeout = options.timeout || 30000           // 请求超时(ms)
    
    // 内部状态
    this.queue = []                    // 待处理的帧队列
    this.pendingRequests = new Map()   // 正在进行的请求 (id -> AbortController)
    this.isProcessing = false          // 是否正在处理
    this.sessionId = null              // 当前会话ID
    this.batchTimer = null             // 批量发送定时器
    this.currentBatchId = 0            // 当前批次ID
    
    // 回调函数
    this.onResult = options.onResult || (() => {})
    this.onError = options.onError || (() => {})
    this.onBatchStart = options.onBatchStart || (() => {})
    this.onBatchComplete = options.onBatchComplete || (() => {})
  }

  /**
   * 初始化队列（开始新会话时调用）
   */
  init(sessionId) {
    this.clear()  // 清空之前的状态
    this.sessionId = sessionId
    this.isProcessing = true
    console.log(`[AnalysisQueue] Initialized for session: ${sessionId}`)
  }

  /**
   * 添加帧到队列
   * @param {Object} frame - 帧数据 { image, frameIdx, timestamp }
   */
  addFrame(frame) {
    if (!this.isProcessing || !this.sessionId) {
      console.warn('[AnalysisQueue] Queue not initialized, ignoring frame')
      return
    }

    this.queue.push({
      ...frame,
      addedAt: Date.now()
    })

    // 如果队列达到批量大小，立即处理
    if (this.queue.length >= this.batchSize) {
      this.processBatch()
    } else {
      // 否则启动定时器等待更多帧
      this.scheduleBatch()
    }
  }

  /**
   * 批量添加帧
   * @param {Array} frames - 帧数组
   */
  addFrames(frames) {
    frames.forEach(frame => this.addFrame(frame))
  }

  /**
   * 调度批量处理
   */
  scheduleBatch() {
    if (this.batchTimer) return  // 已有定时器

    this.batchTimer = setTimeout(() => {
      this.batchTimer = null
      if (this.queue.length > 0) {
        this.processBatch()
      }
    }, this.batchInterval)
  }

  /**
   * 处理当前批次
   */
  async processBatch() {
    if (!this.isProcessing || this.queue.length === 0) return
    
    // 检查并发限制
    if (this.pendingRequests.size >= this.maxConcurrent) {
      console.log(`[AnalysisQueue] Max concurrent reached (${this.maxConcurrent}), waiting...`)
      return
    }

    // 取出一批帧
    const batch = this.queue.splice(0, this.batchSize)
    if (batch.length === 0) return

    const batchId = ++this.currentBatchId
    const abortController = new AbortController()
    
    // 记录这个请求
    this.pendingRequests.set(batchId, abortController)
    
    console.log(`[AnalysisQueue] Processing batch #${batchId} with ${batch.length} frames`)
    this.onBatchStart({ batchId, frameCount: batch.length })

    try {
      // 发送批量分析请求
      const response = await axios.post(
        '/api/analysis/analyze-frames-batch',
        {
          session_id: this.sessionId,
          frames: batch.map(f => ({
            frame_idx: f.frameIdx,
            timestamp: f.timestamp,
            // 注意：图片数据需要base64编码
            image_base64: f.imageBase64 || null
          }))
        },
        {
          signal: abortController.signal,
          timeout: this.timeout
        }
      )

      // 处理结果
      if (response.data && response.data.results) {
        response.data.results.forEach(result => {
          this.onResult(result)
        })
      }

      this.onBatchComplete({ 
        batchId, 
        success: true, 
        frameCount: batch.length,
        results: response.data.results 
      })

    } catch (error) {
      if (axios.isCancel(error)) {
        console.log(`[AnalysisQueue] Batch #${batchId} cancelled`)
      } else {
        console.error(`[AnalysisQueue] Batch #${batchId} failed:`, error)
        this.onError({ batchId, error, frames: batch })
      }
      
      this.onBatchComplete({ 
        batchId, 
        success: false, 
        error: error.message 
      })

    } finally {
      this.pendingRequests.delete(batchId)
      
      // 如果还有待处理的帧，继续处理
      if (this.queue.length > 0 && this.isProcessing) {
        this.processBatch()
      }
    }
  }

  /**
   * 清空队列并取消所有请求（返回主页时调用）
   */
  clear() {
    console.log(`[AnalysisQueue] Clearing queue and cancelling ${this.pendingRequests.size} requests`)
    
    // 停止处理
    this.isProcessing = false
    
    // 清空定时器
    if (this.batchTimer) {
      clearTimeout(this.batchTimer)
      this.batchTimer = null
    }
    
    // 取消所有pending请求
    this.pendingRequests.forEach((controller, batchId) => {
      console.log(`[AnalysisQueue] Cancelling batch #${batchId}`)
      controller.abort()
    })
    this.pendingRequests.clear()
    
    // 清空队列
    const droppedCount = this.queue.length
    this.queue = []
    
    // 重置状态
    this.sessionId = null
    this.currentBatchId = 0
    
    console.log(`[AnalysisQueue] Cleared. Dropped ${droppedCount} queued frames`)
    
    return droppedCount
  }

  /**
   * 暂停处理（保留队列内容）
   */
  pause() {
    this.isProcessing = false
    if (this.batchTimer) {
      clearTimeout(this.batchTimer)
      this.batchTimer = null
    }
    console.log('[AnalysisQueue] Paused')
  }

  /**
   * 恢复处理
   */
  resume() {
    if (!this.sessionId) {
      console.warn('[AnalysisQueue] Cannot resume: no session')
      return
    }
    this.isProcessing = true
    if (this.queue.length > 0) {
      this.processBatch()
    }
    console.log('[AnalysisQueue] Resumed')
  }

  /**
   * 获取队列状态
   */
  getStatus() {
    return {
      isProcessing: this.isProcessing,
      sessionId: this.sessionId,
      queueLength: this.queue.length,
      pendingRequests: this.pendingRequests.size,
      batchSize: this.batchSize
    }
  }
}

// 创建单例实例
const analysisQueue = new AnalysisQueue({
  batchSize: 5,           // 每批5帧
  batchInterval: 1000,    // 最多等待1秒
  maxConcurrent: 2,       // 最多2个并发请求
  timeout: 60000          // 60秒超时
})

export { AnalysisQueue, analysisQueue }
export default analysisQueue


