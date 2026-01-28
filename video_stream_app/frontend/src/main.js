import { createApp } from 'vue'
import { createPinia } from 'pinia'
import axios from 'axios'
import App from './App.vue'
import './styles/main.css'

// 配置 axios 全局默认值
axios.defaults.timeout = 30000  // 30秒全局超时

// 添加请求拦截器 - 调试用
axios.interceptors.request.use(
  config => {
    console.log(`[Axios] ${config.method?.toUpperCase()} ${config.url}`)
    return config
  },
  error => {
    console.error('[Axios] Request error:', error)
    return Promise.reject(error)
  }
)

// 添加响应拦截器 - 调试和错误处理
axios.interceptors.response.use(
  response => {
    console.log(`[Axios] Response ${response.status} from ${response.config.url}`)
    return response
  },
  error => {
    if (axios.isCancel(error)) {
      console.log('[Axios] Request cancelled:', error.message)
    } else if (error.code === 'ECONNABORTED') {
      console.error('[Axios] Request timeout:', error.config?.url)
    } else if (error.code === 'ERR_NETWORK') {
      console.error('[Axios] Network error - backend may be unavailable')
    } else {
      console.error('[Axios] Response error:', error.message, error.config?.url)
    }
    return Promise.reject(error)
  }
)

const app = createApp(App)
app.use(createPinia())
app.mount('#app')




