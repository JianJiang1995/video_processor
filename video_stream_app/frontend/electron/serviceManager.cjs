/**
 * Service Manager for Standalone Electron App
 * 
 * Manages all backend services lifecycle:
 * - FastAPI Backend (port 8001)
 * - SurgR1 vLLM (port 9003)
 * - GLM/Qwen vLLM (port 8000)
 * - SAM3 (port 9004) [optional]
 * - TTS CosyVoice (port 50000) [optional]
 * - ASR FunASR (port 8765) [optional]
 * - Gemini: cloud API, no process needed
 */
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const http = require('http')

// ============= Service Definitions =============

/**
 * Build service definitions from config.json + runtime paths.
 * @param {string} resourcesPath - app resources path (packaged) or project root (dev)
 * @param {object} appConfig - parsed config.json
 * @param {boolean} isPackaged - whether running as packaged app
 */
function buildServiceDefinitions(resourcesPath, appConfig, isPackaged) {
  const services = appConfig.services || {}

  // Helper: resolve executable path
  // In packaged mode: resources/<service>/executable
  // In dev mode: null (user starts manually)
  function resolveExe(serviceDirName, exeName, fallbackScript) {
    if (isPackaged) {
      const winExe = process.platform === 'win32' ? `${exeName}.exe` : exeName
      return path.join(resourcesPath, serviceDirName, winExe)
    }
    return null // dev mode
  }

  // Helper: resolve Python script path (for services that run via python)
  function resolvePythonScript(serviceDirName, scriptName) {
    if (isPackaged) {
      // In packaged mode, Python services are compiled via PyInstaller
      const exeName = serviceDirName.replace(/_/g, '-')
      const winExe = process.platform === 'win32' ? `${exeName}.exe` : exeName
      return path.join(resourcesPath, serviceDirName, winExe)
    }
    return null
  }

  const defs = {
    backend: {
      name: 'FastAPI Backend',
      key: 'backend',
      port: services.backend?.port || 8001,
      host: '127.0.0.1',
      healthPath: '/api/health',
      healthCheck: 'http',
      required: true,
      enabled: true,
      exe: resolveExe('backend', 'video-analyzer-backend'),
      args: [],
      env: {},
      startupTimeout: 30000,
      cwd: isPackaged ? path.join(resourcesPath, 'backend') : null,
    },

    surgr1: {
      name: 'SurgR1 (Surgical Analysis)',
      key: 'surgr1',
      port: services.surgr1?.port || 9003,
      host: '127.0.0.1',
      healthPath: '/health',
      healthCheck: 'http',
      required: services.surgr1?.required !== false,
      enabled: services.surgr1?.enabled !== false,
      exe: resolveExe('surgr1', 'surgr1-server'),
      args: [],
      env: {
        CUDA_VISIBLE_DEVICES: process.env.SURGR1_GPU || process.env.CUDA_VISIBLE_DEVICES || '0',
      },
      startupTimeout: 120000, // vLLM model loading takes time
      cwd: isPackaged ? path.join(resourcesPath, 'surgr1') : null,
    },

    // GLM/Qwen 不打包 — 由 Gemini 云端 API 替代（通过 vlm_factory 切换 provider）
    // 如需本地 GLM，用户可手动启动 glm_api/start.sh

    sam3: {
      name: 'SAM3 (Segmentation)',
      key: 'sam3',
      port: services.sam3?.port || 9004,
      host: '127.0.0.1',
      healthPath: '/health',
      healthCheck: 'http',
      required: services.sam3?.required === true,
      enabled: services.sam3?.enabled !== false,
      exe: resolveExe('sam3', 'sam3-server'),
      args: [],
      env: {},
      startupTimeout: 60000,
      cwd: isPackaged ? path.join(resourcesPath, 'sam3') : null,
    },

    tts: {
      name: 'TTS CosyVoice',
      key: 'tts',
      port: services.tts?.port || 50000,
      host: '127.0.0.1',
      healthPath: '/health',
      healthCheck: 'http',
      required: services.tts?.required === true,
      enabled: services.tts?.enabled !== false,
      exe: resolveExe('tts', 'tts-server'),
      args: [],
      env: {},
      startupTimeout: 60000,
      cwd: isPackaged ? path.join(resourcesPath, 'tts') : null,
    },

    asr: {
      name: 'ASR FunASR',
      key: 'asr',
      port: services.asr?.port || 8765,
      host: '127.0.0.1',
      healthPath: '/',
      healthCheck: 'tcp', // WebSocket service, just check TCP
      required: services.asr?.required === true,
      enabled: services.asr?.enabled !== false,
      exe: resolveExe('asr', 'asr-server'),
      args: [],
      env: {},
      startupTimeout: 30000,
      cwd: isPackaged ? path.join(resourcesPath, 'asr') : null,
    },
  }

  return defs
}


// ============= Service Manager Class =============

class ServiceManager {
  constructor() {
    this.services = {}       // service definitions
    this.processes = {}      // running child processes { key: ChildProcess }
    this.statuses = {}       // { key: { running, starting, error, pid } }
    this.statusListeners = [] // callbacks for status changes
    this.isPackaged = false
  }

  /**
   * Initialize with config
   */
  init(resourcesPath, appConfig, isPackaged) {
    this.isPackaged = isPackaged
    this.services = buildServiceDefinitions(resourcesPath, appConfig, isPackaged)

    // Initialize statuses
    for (const [key, svc] of Object.entries(this.services)) {
      this.statuses[key] = {
        running: false,
        starting: false,
        error: null,
        pid: null,
        enabled: svc.enabled,
        required: svc.required,
        name: svc.name,
        port: svc.port,
      }
    }
  }

  /**
   * Register a status change listener
   */
  onStatusChange(callback) {
    this.statusListeners.push(callback)
    return () => {
      this.statusListeners = this.statusListeners.filter(cb => cb !== callback)
    }
  }

  _notifyStatus(key) {
    const status = this.statuses[key]
    for (const cb of this.statusListeners) {
      try { cb(key, status) } catch (e) { /* ignore */ }
    }
  }

  /**
   * Get all service statuses
   */
  getAllStatuses() {
    return { ...this.statuses }
  }

  /**
   * HTTP health check
   */
  _httpHealthCheck(host, port, path, timeout = 3000) {
    return new Promise((resolve) => {
      const url = `http://${host}:${port}${path}`
      const req = http.get(url, { timeout }, (res) => {
        let data = ''
        res.on('data', (chunk) => { data += chunk })
        res.on('end', () => {
          resolve(res.statusCode >= 200 && res.statusCode < 500)
        })
      })
      req.on('error', () => resolve(false))
      req.on('timeout', () => { req.destroy(); resolve(false) })
    })
  }

  /**
   * TCP health check (just try to connect)
   */
  _tcpHealthCheck(host, port, timeout = 2000) {
    const net = require('net')
    return new Promise((resolve) => {
      const socket = new net.Socket()
      socket.setTimeout(timeout)
      socket.on('connect', () => { socket.destroy(); resolve(true) })
      socket.on('error', () => { socket.destroy(); resolve(false) })
      socket.on('timeout', () => { socket.destroy(); resolve(false) })
      socket.connect(port, host)
    })
  }

  /**
   * Check if a service is healthy
   */
  async checkHealth(key) {
    const svc = this.services[key]
    if (!svc) return false

    if (svc.healthCheck === 'tcp') {
      return this._tcpHealthCheck(svc.host, svc.port)
    }
    return this._httpHealthCheck(svc.host, svc.port, svc.healthPath)
  }

  /**
   * Start a single service
   */
  async startService(key) {
    const svc = this.services[key]
    if (!svc) throw new Error(`Unknown service: ${key}`)
    if (!svc.enabled) {
      console.log(`[ServiceManager] ${svc.name} is disabled, skipping`)
      return false
    }

    // Check if already running
    const alreadyRunning = await this.checkHealth(key)
    if (alreadyRunning) {
      console.log(`[ServiceManager] ${svc.name} already running on port ${svc.port}`)
      this.statuses[key] = { ...this.statuses[key], running: true, starting: false, error: null }
      this._notifyStatus(key)
      return true
    }

    // In dev mode, no exe to spawn
    if (!svc.exe) {
      console.log(`[ServiceManager] ${svc.name}: dev mode, no embedded executable`)
      this.statuses[key] = { ...this.statuses[key], running: false, starting: false, error: 'dev-mode' }
      this._notifyStatus(key)
      return false
    }

    // Check exe exists
    if (!fs.existsSync(svc.exe)) {
      const msg = `Executable not found: ${svc.exe}`
      console.error(`[ServiceManager] ${svc.name}: ${msg}`)
      this.statuses[key] = { ...this.statuses[key], running: false, starting: false, error: msg }
      this._notifyStatus(key)
      return false
    }

    // Update status: starting
    this.statuses[key] = { ...this.statuses[key], starting: true, error: null }
    this._notifyStatus(key)

    console.log(`[ServiceManager] Starting ${svc.name}: ${svc.exe}`)

    return new Promise((resolve) => {
      const proc = spawn(svc.exe, [
        `--port=${svc.port}`,
        `--host=${svc.host}`,
        ...svc.args,
      ], {
        cwd: svc.cwd || path.dirname(svc.exe),
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, ...svc.env },
        detached: process.platform !== 'win32',
      })

      this.processes[key] = proc

      proc.stdout.on('data', (data) => {
        const msg = data.toString().trim()
        if (msg) console.log(`[${svc.name}] ${msg}`)
      })

      proc.stderr.on('data', (data) => {
        const msg = data.toString().trim()
        if (msg) console.error(`[${svc.name} ERR] ${msg}`)
      })

      proc.on('error', (err) => {
        console.error(`[ServiceManager] ${svc.name} process error:`, err)
        this.processes[key] = null
        this.statuses[key] = { ...this.statuses[key], running: false, starting: false, error: err.message, pid: null }
        this._notifyStatus(key)
      })

      proc.on('exit', (code, signal) => {
        console.log(`[ServiceManager] ${svc.name} exited (code=${code}, signal=${signal})`)
        this.processes[key] = null
        this.statuses[key] = { ...this.statuses[key], running: false, starting: false, pid: null }
        this._notifyStatus(key)
      })

      // Poll health
      const startTime = Date.now()
      const pollInterval = setInterval(async () => {
        const healthy = await this.checkHealth(key)
        if (healthy) {
          clearInterval(pollInterval)
          console.log(`[ServiceManager] ${svc.name} ready (${Date.now() - startTime}ms)`)
          this.statuses[key] = {
            ...this.statuses[key],
            running: true,
            starting: false,
            error: null,
            pid: proc.pid,
          }
          this._notifyStatus(key)
          resolve(true)
        } else if (Date.now() - startTime > svc.startupTimeout) {
          clearInterval(pollInterval)
          const msg = `Startup timeout (${svc.startupTimeout}ms)`
          console.error(`[ServiceManager] ${svc.name}: ${msg}`)
          this.statuses[key] = { ...this.statuses[key], starting: false, error: msg }
          this._notifyStatus(key)
          resolve(false)
        } else if (!this.processes[key]) {
          // Process died during startup
          clearInterval(pollInterval)
          this.statuses[key] = { ...this.statuses[key], starting: false, error: 'Process exited during startup' }
          this._notifyStatus(key)
          resolve(false)
        }
      }, 1500)
    })
  }

  /**
   * Stop a single service
   */
  stopService(key) {
    const proc = this.processes[key]
    if (!proc) return

    const svc = this.services[key]
    console.log(`[ServiceManager] Stopping ${svc?.name || key}...`)

    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/pid', String(proc.pid), '/f', '/t'])
      } else {
        // Kill process group
        process.kill(-proc.pid, 'SIGTERM')
        // Force kill after 5s
        setTimeout(() => {
          try { process.kill(-proc.pid, 'SIGKILL') } catch (e) { /* ignore */ }
        }, 5000)
      }
    } catch (err) {
      try { proc.kill('SIGKILL') } catch (e) { /* ignore */ }
    }

    this.processes[key] = null
    this.statuses[key] = { ...this.statuses[key], running: false, starting: false, pid: null }
    this._notifyStatus(key)
  }

  /**
   * Start all enabled services (in dependency order)
   */
  async startAll() {
    // Start backend first, then AI services in parallel
    console.log('[ServiceManager] Starting all services...')

    // Phase 1: Backend (required for everything)
    await this.startService('backend')

    // Phase 2: Core AI services
    const coreServices = ['surgr1']
    const corePromises = coreServices
      .filter(k => this.services[k]?.enabled)
      .map(k => this.startService(k))
    await Promise.allSettled(corePromises)

    // Phase 3: Optional services in parallel
    const optionalServices = ['sam3', 'tts', 'asr']
    const optPromises = optionalServices
      .filter(k => this.services[k]?.enabled)
      .map(k => this.startService(k))
    await Promise.allSettled(optPromises)

    console.log('[ServiceManager] All services started')
    return this.getAllStatuses()
  }

  /**
   * Stop all services
   */
  stopAll() {
    console.log('[ServiceManager] Stopping all services...')
    // Stop in reverse order: optional → core → backend
    const order = ['asr', 'tts', 'sam3', 'surgr1', 'backend']
    for (const key of order) {
      this.stopService(key)
    }
  }

  /**
   * Restart a single service
   */
  async restartService(key) {
    this.stopService(key)
    await new Promise(resolve => setTimeout(resolve, 2000))
    return this.startService(key)
  }
}

module.exports = { ServiceManager, buildServiceDefinitions }
