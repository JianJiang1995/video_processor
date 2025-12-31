#!/bin/bash
# 显示各GPU进程对应的服务名称
# 通过PID关联查找父进程命令

echo "======================================================================"
echo "  GPU 进程与服务对应关系"
echo "======================================================================"
echo ""

# 函数：获取进程的服务名称
get_service_name() {
    local pid=$1
    local cmdline=""
    
    # 读取进程命令行
    if [ -f "/proc/$pid/cmdline" ]; then
        cmdline=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null)
    fi
    
    # 如果是 VLLM::EngineCore，查找父进程
    if echo "$cmdline" | grep -q "VLLM::EngineCore\|^$"; then
        # 获取父进程PID
        local ppid=$(ps -o ppid= -p $pid 2>/dev/null | xargs)
        if [ -n "$ppid" ] && [ "$ppid" != "1" ]; then
            # 递归查找父进程
            get_service_name "$ppid"
            return
        fi
    fi
    
    # 解析服务名称
    if echo "$cmdline" | grep -q "SurgR1_api"; then
        port=$(echo "$cmdline" | grep -oP 'port[=\s:]+\K\d+' | head -1 || echo "9003")
        echo "SurgR1_api (port: ${port:-9003})"
    elif echo "$cmdline" | grep -q "glm_api\|GLM-4"; then
        port=$(echo "$cmdline" | grep -oP -- '--port[=\s]+\K\d+' | head -1 || echo "8000")
        model=$(echo "$cmdline" | grep -oP -- '--served-model-name[=\s]+\K[^\s]+' | head -1 || echo "GLM")
        echo "glm_api [$model] (port: ${port:-8000})"
    elif echo "$cmdline" | grep -q "tts_api\|CosyVoice\|cosyvoice"; then
        port=$(echo "$cmdline" | grep -oP 'port[=\s:]+\K\d+' | head -1 || echo "50000")
        echo "tts_api [CosyVoice] (port: ${port:-50000})"
    elif echo "$cmdline" | grep -q "vllm.entrypoints.openai.api_server"; then
        port=$(echo "$cmdline" | grep -oP -- '--port[=\s]+\K\d+' | head -1 || echo "?")
        model=$(echo "$cmdline" | grep -oP -- '--served-model-name[=\s]+\K[^\s]+' | head -1)
        if [ -z "$model" ]; then
            model=$(echo "$cmdline" | grep -oP -- '--model[=\s]+\K[^\s]+' | head -1)
            model=$(basename "$model" 2>/dev/null || echo "vLLM")
        fi
        echo "vLLM [$model] (port: ${port:-?})"
    elif echo "$cmdline" | grep -q "vllm\|LLM"; then
        echo "vLLM service (orphaned?)"
    else
        # 截取命令的关键部分
        local short_cmd=$(echo "$cmdline" | cut -c1-40)
        if [ -n "$short_cmd" ]; then
            echo "$short_cmd..."
        else
            echo "(无法识别的进程)"
        fi
    fi
}

# 获取 nvidia-smi 中的 GPU 进程信息
nvidia_output=$(nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory --format=csv,noheader 2>/dev/null)

if [ -z "$nvidia_output" ]; then
    echo "没有检测到 GPU 进程"
    exit 0
fi

echo "GPU | PID      | Memory     | Service"
echo "----|----------|------------|------------------------------------------"

# 处理每个 GPU 进程
while IFS=',' read -r pid gpu_uuid memory; do
    # 去除空格
    pid=$(echo "$pid" | xargs)
    memory=$(echo "$memory" | xargs)
    
    # 获取 GPU 编号
    gpu_id=$(nvidia-smi --query-gpu=index,uuid --format=csv,noheader | grep "$gpu_uuid" | cut -d',' -f1 | xargs)
    
    # 获取服务名称
    service_name=$(get_service_name "$pid")
    
    printf "%-3s | %-8s | %-10s | %s\n" "$gpu_id" "$pid" "$memory" "$service_name"
done <<< "$nvidia_output"

echo ""
echo "======================================================================"
echo ""

# 显示所有 vLLM 相关进程的详细信息
echo "vLLM 相关主进程详情:"
echo "----------------------------------------------------------------------"
ps -eo pid,ppid,start,etime,%mem,args 2>/dev/null | grep -E "vllm\.entrypoints|SurgR1_api|tts_api.*server" | grep -v grep | while read line; do
    echo "  $line"
done

echo ""
echo "======================================================================"
echo ""

# 额外显示各服务的端口占用
echo "当前监听的 API 服务端口:"
echo "----------------------------------------------------------------------"
ss -tlnp 2>/dev/null | grep -E 'LISTEN' | while read line; do
    port=$(echo "$line" | grep -oP ':\K\d+(?=\s)' | head -1)
    case $port in
        8000) echo "  Port $port -> glm_api (GLM-4.6V-Flash)" ;;
        8001) echo "  Port $port -> glm_api (alternate)" ;;
        9001) echo "  Port $port -> rtc_simulator" ;;
        9003) echo "  Port $port -> SurgR1_api (Surgical Analysis)" ;;
        50000) echo "  Port $port -> tts_api (CosyVoice TTS)" ;;
        50001) echo "  Port $port -> tts_api (alternate)" ;;
    esac
done

echo ""
