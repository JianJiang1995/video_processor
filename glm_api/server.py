#!/usr/bin/env python3
"""
GLM-4.6V-Flash vLLM Server 启动脚本
"""
import os
import sys
import subprocess
import argparse
import signal
from pathlib import Path

# 设置进程名称，方便在 ps/htop 中识别
try:
    import setproctitle
    setproctitle.setproctitle("glm_api [vLLM GLM-4.6V]")
except ImportError:
    pass

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))
from config import load_config, GLMConfig


def build_vllm_command(config: GLMConfig) -> list:
    """构建vLLM启动命令"""
    cmd = [
        sys.executable, "-m", "vllm.entrypoints.openai.api_server",
        "--model", config.model.path,
        "--served-model-name", config.model.served_model_name,
        "--host", config.server.host,
        "--port", str(config.server.port),
        "--gpu-memory-utilization", str(config.vllm.gpu_memory_utilization),
        "--max-model-len", str(config.vllm.max_model_len),
        "--max-num-seqs", str(config.vllm.max_num_seqs),
        "--tensor-parallel-size", str(config.vllm.tensor_parallel_size),
        "--dtype", config.vllm.dtype,
    ]
    
    if config.model.trust_remote_code:
        cmd.append("--trust-remote-code")
    
    if config.vllm.enable_prefix_caching:
        cmd.append("--enable-prefix-caching")
    
    if config.vllm.enforce_eager:
        cmd.append("--enforce-eager")
    
    return cmd


def print_config(config: GLMConfig):
    """打印配置信息"""
    print(f"Model: {config.model.path}")
    print(f"Served Name: {config.model.served_model_name}")
    print(f"GPU: {config.gpu.device_ids}")
    print(f"Server: {config.server.host}:{config.server.port}")
    print(f"GPU Memory: {config.vllm.gpu_memory_utilization}")
    print(f"Max Model Len: {config.vllm.max_model_len}")


def start_server(config_path: str = None):
    """启动vLLM服务器"""
    config = load_config(config_path)
    
    print("=" * 50)
    print_config(config)
    print("=" * 50)
    print()
    
    # 构建命令
    cmd = build_vllm_command(config)
    print("Command:")
    print(" ".join(cmd))
    print()
    print("Starting server...")
    
    # 设置环境变量
    env = os.environ.copy()
    env['CUDA_VISIBLE_DEVICES'] = config.gpu.device_ids
    
    # 创建日志目录
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    
    # 启动服务
    process = None
    shutdown_count = 0
    
    try:
        process = subprocess.Popen(
            cmd,
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr,
            # 创建新的进程组，以便可以终止所有子进程
            preexec_fn=os.setsid
        )
        
        # 处理信号
        def signal_handler(signum, frame):
            nonlocal shutdown_count
            shutdown_count += 1
            
            if process is None or process.poll() is not None:
                # 进程已经退出
                sys.exit(0)
            
            if shutdown_count == 1:
                print("\n正在优雅关闭... (再次 Ctrl+C 强制终止)")
                # 先发送 SIGTERM 给进程组
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
            elif shutdown_count == 2:
                print("\n强制关闭中... (再次 Ctrl+C 立即退出)")
                # 发送 SIGKILL 给进程组
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            else:
                # 第三次直接退出
                print("\n立即退出!")
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                sys.exit(1)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # 等待进程
        exit_code = process.wait()
        if exit_code != 0 and shutdown_count == 0:
            print(f"\n服务异常退出，代码: {exit_code}")
            sys.exit(exit_code)
        
    except KeyboardInterrupt:
        print("\n服务被用户终止")
        if process and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
    except subprocess.CalledProcessError as e:
        print(f"\n服务错误: {e}")
        sys.exit(1)
    finally:
        # 确保进程被清理
        if process and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait(timeout=5)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="GLM-4.6V-Flash vLLM Server")
    parser.add_argument(
        '--config', '-c',
        type=str,
        default=None,
        help='Path to config file (default: config.yaml)'
    )
    args = parser.parse_args()
    
    start_server(args.config)


if __name__ == "__main__":
    main()

