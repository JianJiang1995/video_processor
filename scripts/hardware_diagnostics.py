#!/usr/bin/env python3
"""Read-only GPU/DeckLink diagnostics; signal probes never take over a busy card."""
import argparse
import csv
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT.parent / '.venv/bin/python'
LABELS = {'PASS': '正常', 'WARN': '待确认', 'FAIL': '异常', 'SKIP': '未测试'}


def command(args, timeout=10):
    try:
        result = subprocess.run([str(a) for a in args], capture_output=True,
                                text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, '', f'检测超时（{timeout} 秒）'
    except OSError as exc:
        return 127, '', str(exc)


def device_busy(nodes):
    if not shutil.which('fuser'):
        return None, '缺少 fuser，无法确认设备是否被占用；跳过信号测试。'
    code, out, err = command(['fuser', *nodes])
    if code == 0 and out.strip():
        return True, f'设备被进程占用，PID: {out.strip()}。请停止软件采集或独立预览后重测。'
    if code == 1 and not out and not err:
        return False, '当前未发现设备占用。'
    return None, f'无法确认设备占用状态：{out} {err}'


def signal_result(attempts):
    good = next((a for a in attempts if a.get('frames', 0) >= 3), None)
    if good:
        detail = (f"{good['connection'].upper()} / {good['mode']}："
                  f"{good.get('width')}×{good.get('height')}，{good.get('fps')} fps；"
                  f"连续收到 {good['frames']} 帧。")
        if good.get('near_black'):
            return 'WARN', detail + ' 输入画面接近全黑，请确认视频源画面。'
        return 'PASS', detail
    if any(a.get('busy') for a in attempts):
        return 'SKIP', '采集卡正在被占用或占用状态无法确认；未尝试抢占。停止采集后重新检测。'
    errors = [a.get('last_error') for a in attempts if a.get('last_error')]
    if errors:
        return 'FAIL', '采集管线未通过检测：' + '; '.join(dict.fromkeys(errors))
    return 'WARN', '未收到有效输入帧，不等于采集卡损坏。检查视频源已开启、线接 IN、SDI/HDMI 接口及输出制式；可用 --mode 指定源制式重测。'


def summarize(checks):
    if any(c['state'] == 'FAIL' for c in checks):
        return '发现异常，请查看标为“异常”的项目及处理建议。'
    if any(c['state'] == 'WARN' for c in checks):
        return '存在待确认项目，请查看具体检测结果；无输入帧不代表采集卡损坏。'
    if any(c['state'] == 'SKIP' and c['key'] == 'capture_signal' for c in checks):
        return 'GPU 运算、采集卡驱动及插件检查通过；输入信号尚未验证。'
    return 'GPU 运算及采集信号检查通过。'


def signal_worker(device, connection, mode):
    spec = importlib.util.spec_from_file_location('diagnostic_decklink', ROOT / 'backend/services/decklink_capture.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cap = module.DeckLinkCapture(module.build_decklink_uri(device, mode, connection))
    frames = 0
    status = {}
    try:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and cap.isOpened():
            ok, frame = cap.read(timeout=0.5)
            status = cap.status() or {}
            if ok and frame is not None:
                frames += 1
                # Evaluate actual pixels, but never save images in diagnostic reports.
                status['near_black'] = float(frame.mean()) < 8 and float(frame.std()) < 8
                if frames >= 3:
                    break
        status = {**(cap.status() or {}), **status, 'frames': frames,
                  'connection': connection, 'mode': mode}
        print(json.dumps(status, ensure_ascii=False))
    finally:
        cap.release()


CUDA_TEST = '''import json, torch
results=[]
for i in range(torch.cuda.device_count()):
    try:
        with torch.cuda.device(i):
            a=torch.ones((32,32),device=f"cuda:{i}")
            value=float((a @ a)[0,0].item())
            torch.cuda.synchronize(i)
            if value != 32: raise RuntimeError(f"unexpected result: {value}")
        results.append({"index":i,"ok":True,"name":torch.cuda.get_device_name(i)})
    except Exception as e:
        results.append({"index":i,"ok":False,"error":str(e)})
print(json.dumps(results))
'''


def collect(args):
    checks = []

    def add(key, state, title, detail):
        checks.append(dict(key=key, state=state, title=title, detail=detail))
        print(f'[{LABELS[state]}] {title}\n  {detail}', flush=True)

    print('SurgR1 GPU / 采集卡快速诊断\n检测期间不修改驱动、不重启服务、不保存采集画面。', flush=True)
    print(f'系统：{platform.system()} {platform.release()}\n', flush=True)
    code, pci, err = command(['lspci', '-nnk'])
    if code:
        add('pci', 'WARN', 'PCIe 硬件枚举', err or '无法运行 lspci')
    else:
        lines = [line for line in pci.splitlines() if line and not line[0].isspace()
                 and any(s in line.lower() for s in ('nvidia', 'blackmagic', 'bdbd:'))]
        add('pci', 'PASS' if lines else 'WARN', 'PCIe 硬件枚举', '\n  '.join(lines) or '没有发现 NVIDIA / Blackmagic 设备。')

    code, gpu, err = command(['nvidia-smi', '--query-gpu=index,name,driver_version,memory.total,memory.used,temperature.gpu', '--format=csv,noheader,nounits'])
    gpu_rows = list(csv.reader(gpu.splitlines())) if code == 0 else []
    if not gpu_rows:
        add('gpu_driver', 'FAIL', 'GPU 驱动与显存', err or gpu or '未发现可用 NVIDIA GPU；请检查 PCIe 枚举、驱动及内核模块。')
    else:
        for row in gpu_rows:
            if len(row) != 6:
                add('gpu_driver', 'WARN', 'GPU 状态解析', ','.join(row))
                continue
            index, name, driver, total, used, temp = [s.strip() for s in row]
            add(f'gpu_{index}', 'PASS', f'GPU {index}：{name}', f'驱动 {driver}；显存 {used}/{total} MiB；温度 {temp} °C。')
        code, out, err = command([PYTHON if PYTHON.exists() else sys.executable, '-c', CUDA_TEST], timeout=45)
        try:
            cuda = json.loads(out) if code == 0 else []
        except ValueError:
            cuda = []
        if not cuda:
            add('cuda', 'FAIL', 'CUDA 实际运算', err or out or '项目 Python 未发现可用 CUDA GPU。')
        else:
            for result in cuda:
                add(f"cuda_{result['index']}", 'PASS' if result['ok'] else 'FAIL',
                    f"GPU {result['index']} CUDA 实际运算", '小矩阵运算及同步通过。' if result['ok'] else result['error'])
            if len(cuda) != len(gpu_rows):
                add('cuda_visibility', 'WARN', 'CUDA GPU 可见性', f'nvidia-smi 显示 {len(gpu_rows)} 块，但项目 CUDA 只看到 {len(cuda)} 块；检查 CUDA_VISIBLE_DEVICES。')

    selected = Path(f'/dev/blackmagic/io{args.device}')
    if not selected.exists():
        present = 'bdbd:' in pci.lower() or 'blackmagic' in pci.lower()
        add('capture_node', 'FAIL', '采集卡驱动节点',
            f'{selected} 不存在。' + ('PCIe 已识别采集卡，优先检查 Blackmagic 驱动/内核模块。' if present else 'PCIe 未确认采集卡，检查插槽、供电及系统枚举。'))
    else:
        accessible = os.access(selected, os.R_OK | os.W_OK)
        add('capture_node', 'PASS' if accessible else 'FAIL', '采集卡驱动与权限',
            f'{selected} 存在，当前用户' + ('可读写。' if accessible else '无读写权限，请检查设备权限。'))
    code, out, err = command(['BlackmagicFirmwareUpdater', 'status'])
    selected_lines = [line for line in out.splitlines() if str(selected) + ' ' in line or str(selected) + '\t' in line]
    firmware_ok = code == 0 and selected_lines and selected_lines[0].split()[-1] == 'OK'
    add('capture_firmware', 'PASS' if firmware_ok else 'WARN', 'Blackmagic 固件/驱动状态', out or err)
    code, out, err = command(['gst-inspect-1.0', 'decklinkvideosrc'])
    plugin_ok = code == 0
    add('capture_plugin', 'PASS' if plugin_ok else 'FAIL', 'GStreamer 采集插件', 'decklinkvideosrc 可用。' if plugin_ok else err or out)

    attempts = []
    if args.no_signal:
        add('capture_signal', 'SKIP', '采集输入信号', '快速检查未打开采集卡输入，不影响正在采集的视频。需要时可在检测窗口按 S 测试输入信号。')
    elif not selected.exists() or not os.access(selected, os.R_OK | os.W_OK) or not plugin_ok:
        add('capture_signal', 'SKIP', '采集输入信号', '请先解决上述驱动/权限/插件问题。')
    else:
        busy, detail = device_busy([selected])
        if busy is not False:
            add('capture_signal', 'SKIP', '采集输入信号', detail)
        else:
            connections = ['sdi', 'hdmi'] if args.connection == 'auto' else [args.connection]
            modes = ['auto', '1080p50'] if args.mode == 'auto' else [args.mode]
            for connection in connections:
                for mode in modes:
                    busy, detail = device_busy([selected])
                    if busy is not False:
                        attempts.append({'busy': True})
                        break
                    print(f'  正在检测 {connection.upper()} / {mode} …', flush=True)
                    code, out, err = command([PYTHON if PYTHON.exists() else sys.executable, __file__,
                        '--signal-worker', '--device', args.device, '--connection', connection, '--mode', mode], timeout=15)
                    try:
                        result = json.loads(out) if code == 0 else {'last_error': err or out or f'采集进程退出 {code}'}
                    except ValueError:
                        result = {'last_error': err or out or '采集进程未返回有效结果'}
                    attempts.append(result)
                    if result.get('frames', 0) >= 3:
                        break
                if attempts[-1].get('frames', 0) >= 3 or attempts[-1].get('busy'):
                    break
            state, detail = signal_result(attempts)
            add('capture_signal', state, '采集输入信号', detail)

    usb = sorted(str(p) for p in Path('/dev').glob('video*'))
    add('v4l2', 'SKIP', '其他视频设备', ', '.join(usb) + '（本工具未打开这些设备）' if usb else '未发现 /dev/video*；DeckLink 使用 /dev/blackmagic，这是正常现象。')
    return {'time': dt.datetime.now().astimezone().isoformat(), 'summary': summarize(checks),
            'system': {'kernel': platform.release(), 'python': str(PYTHON if PYTHON.exists() else sys.executable)},
            'checks': checks, 'signal_attempts': attempts,
            'limits': '快速诊断仅验证当前状态，不代表长时间采集稳定性或模型推理性能。未保存视频或图片。'}


def save_report(report, directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = 'hardware_' + dt.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    body = f"SurgR1 GPU / 采集卡诊断\n{report['time']}\n\n结论：{report['summary']}\n\n"
    body += '\n\n'.join(f"[{LABELS[c['state']]}] {c['title']}\n{c['detail']}" for c in report['checks'])
    body += '\n\n' + report['limits'] + '\n'
    for suffix, content in [('.txt', body), ('.json', json.dumps(report, ensure_ascii=False, indent=2))]:
        target = directory / (name + suffix)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(content)
    return directory / (name + '.txt')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', type=int, default=0)
    parser.add_argument('--connection', choices=['auto', 'sdi', 'hdmi'], default='auto')
    parser.add_argument('--mode', default='auto')
    parser.add_argument('--no-signal', action='store_true')
    parser.add_argument('--signal-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'logs/hardware_diagnostics')
    args = parser.parse_args()
    if args.device < 0:
        parser.error('--device must be nonnegative')
    if args.signal_worker:
        signal_worker(args.device, args.connection, args.mode)
        return 0
    # One diagnostic instance at a time, including across desktop double-clicks.
    import fcntl
    lock_dir = Path(os.environ.get('XDG_RUNTIME_DIR', str(Path.home() / '.cache'))) / 'surgr1'
    lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (lock_dir / 'hardware-diagnostics.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('已有硬件诊断正在运行，请等待该窗口完成。')
            return 2
        report = collect(args)
        file = save_report(report, args.output_dir)
        print(f"\n结论：{report['summary']}\n{report['limits']}\n\n报告已保存：{file}", flush=True)
        return 1 if any(c['state'] == 'FAIL' for c in report['checks']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
