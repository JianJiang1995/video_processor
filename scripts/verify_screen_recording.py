#!/usr/bin/env python3
"""Verify recording controls against an isolated Electron replay (no inference)."""
import argparse
import asyncio
import ctypes
import json
import os
from pathlib import Path
import subprocess

from playwright.async_api import async_playwright

CONTROL = "document.querySelector('.screen-recording-control').__vueParentComponent.setupState"
APP = "document.querySelector('#app').__vue_app__._container._vnode.component.setupState"


def close_native_window(window_id, display):
    """Send the same WM_DELETE_WINDOW message as a window-manager close button."""
    class Data(ctypes.Union):
        _fields_ = [('l', ctypes.c_long * 5), ('b', ctypes.c_char * 20)]

    class Client(ctypes.Structure):
        _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong),
                    ('send_event', ctypes.c_int), ('display', ctypes.c_void_p),
                    ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                    ('format', ctypes.c_int), ('data', Data)]

    class Event(ctypes.Union):
        _fields_ = [('client', Client), ('padding', ctypes.c_long * 24)]

    x11 = ctypes.CDLL('libX11.so.6')
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.POINTER(Event)]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    connection = x11.XOpenDisplay(display.encode())
    if not connection:
        raise RuntimeError('test display unavailable')
    try:
        event = Event()
        event.client.type = 33
        event.client.display = connection
        event.client.window = int(window_id)
        event.client.message_type = x11.XInternAtom(connection, b'WM_PROTOCOLS', 0)
        event.client.format = 32
        event.client.data.l[0] = x11.XInternAtom(connection, b'WM_DELETE_WINDOW', 0)
        assert x11.XSendEvent(connection, int(window_id), 0, 0, ctypes.byref(event))
        x11.XSync(connection, 0)
    finally:
        x11.XCloseDisplay(connection)


async def verify(args):
    result = {'cdp': args.cdp, 'passed': False}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(args.cdp)
        page = browser.contexts[0].pages[0]
        if args.spec:
            await page.wait_for_function("document.querySelector('#app')?.__vue_app__")
            assert not await page.evaluate(f'{APP}.isProcessing'), 'Only test an idle app'
            await page.evaluate(f'''async path => {{
              const bundle = await window.electronAPI.loadReplayBundle(path);
              if (!bundle.success) throw Error(bundle.error);
              bundle.spec.auto_play = false;
              await {APP}.openReplayBundle(bundle, 0);
            }}''', str(args.spec.resolve()))
            await page.wait_for_function('document.querySelector(".video-section video")?.readyState >= 2')
        await page.wait_for_selector('[data-testid=screen-recording-toggle]')
        await page.evaluate(f"{CONTROL}.recorder.stop('test_reset')")
        await page.evaluate(f"{APP}.handleSeek(0)")
        await page.evaluate(f"{APP}.setLanguage('en')")
        await page.get_by_test_id('screen-recording-toggle').filter(has_text='Record').wait_for()
        await page.evaluate(f"{APP}.setLanguage('zh')")
        await page.get_by_test_id('screen-recording-toggle').click()
        await page.wait_for_function(f"{CONTROL}.status.state === 'recording'")
        first = await page.evaluate(f"{CONTROL}.status")
        result['capture_settings'] = await page.evaluate(f"{CONTROL}.recorder.stream.getVideoTracks()[0].getSettings()")
        result['window_dimensions'] = await page.evaluate('({width: outerWidth, height: outerHeight, devicePixelRatio})')
        await page.evaluate(f"{APP}.handlePlay()")
        await asyncio.sleep(3)
        window_id = first['sourceId'].split(':')[1]
        subprocess.run(['xdotool', 'windowsize', window_id, '1600', '900'],
                       env={**os.environ, 'DISPLAY': args.display}, check=True)
        await asyncio.sleep(4)
        await page.screenshot(path=str(args.output / 'recording_resized.png'))
        await page.get_by_test_id('screen-recording-toggle').click()
        await page.wait_for_function(f"{CONTROL}.status.state === 'saved'", timeout=30000)
        result['resized_recording'] = await page.evaluate(f"{CONTROL}.status")
        await page.get_by_test_id('screen-recording-toggle').click()
        await page.wait_for_function(f"{CONTROL}.status.state === 'recording'")
        second = await page.evaluate(f"{CONTROL}.status")
        await asyncio.sleep(5)
        close_native_window(second['sourceId'].split(':')[1], args.display)
        sidecar = Path(second['path'] + '.json')
        for _ in range(100):
            if sidecar.exists():
                break
            await asyncio.sleep(.2)
        result['closed_recording'] = json.loads(sidecar.read_text())
        assert result['closed_recording']['reason'] == 'window_closed', result
        for key in ('resized_recording', 'closed_recording'):
            assert result[key]['bytes'] > 0 and not result[key]['warning'], result[key]
            assert result[key]['path'].endswith('.mp4'), result[key]
            assert Path(result[key]['path']).stat().st_mode & 0o777 == 0o600
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                'format=duration,format_name:stream=codec_name,pix_fmt,width,height', '-of', 'json', result[key]['path']],
                check=True, capture_output=True, text=True)
            result[key]['probe'] = json.loads(probe.stdout)
            assert float(result[key]['probe']['format']['duration']) >= 2
            assert 'mp4' in result[key]['probe']['format']['format_name']
            assert result[key]['probe']['streams'][0]['codec_name'] == 'h264'
            assert result[key]['probe']['streams'][0]['pix_fmt'] == 'yuv420p'
            assert result[key]['media']['width'] == result[key]['probe']['streams'][0]['width']
            assert result[key]['media']['height'] == result[key]['probe']['streams'][0]['height']
            assert result[key]['media']['averageFps'] > 0
            assert result[key]['requestedEncoding']['lossless'] is False
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', result[key]['path'],
                            '-f', 'null', '-'], check=True, capture_output=True, timeout=60)
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-ss', '2', '-i', result[key]['path'],
                            '-frames:v', '1', '-f', 'null', '-'], check=True, capture_output=True, timeout=30)
        result['passed'] = True
    (args.output / 'recording_lifecycle.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cdp', default='http://127.0.0.1:9331')
    parser.add_argument('--display', default=':191')
    parser.add_argument('--spec', type=Path, help='Load this local replay in the isolated test app')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    asyncio.run(verify(args))
