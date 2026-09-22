#!/usr/bin/env python3
"""Measure the active Electron player and save a short desktop recording."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import time

from playwright.async_api import async_playwright


def cpu_ticks():
    ticks = {}
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():
            continue
        try:
            command = (item / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
            if 'electron/dist/electron' not in command and 'uvicorn backend.main:app' not in command:
                continue
            stat = (item / 'stat').read_text().split()
            ticks[item.name] = {'ticks': int(stat[13]) + int(stat[14]), 'command': command[:180]}
        except (OSError, ValueError):
            pass
    return ticks


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(args.cdp)
        page = browser.contexts[0].pages[0]
        await page.bring_to_front()
        await page.screenshot(path=str(args.output / 'before.png'))
        initial = await page.evaluate('''() => {
          const v=document.querySelector('video');
          if(!v || v.paused) throw new Error('No playing native video');
          const probe=window.playbackProbe={frames:[],last:0,active:true,events:[],backwards:0,lastMedia:null};
          const tick=(t,m)=>{if(!probe.active)return;if(probe.last)probe.frames.push(t-probe.last);
            if(probe.lastMedia!==null && m.mediaTime<probe.lastMedia-.001)probe.backwards++;
            probe.lastMedia=m.mediaTime;
            probe.last=t;v.requestVideoFrameCallback(tick)};
          for(const name of ['seeking','seeked','waiting','stalled','loadedmetadata','emptied']) {
            v.addEventListener(name,()=>{if(probe.active)probe.events.push({event:name,time:v.currentTime,wall:performance.now()})});
          }
          v.requestVideoFrameCallback(tick);
          const q=v.getVideoPlaybackQuality();
          return {source:v.currentSrc,time:v.currentTime,width:v.videoWidth,height:v.videoHeight,
            players:document.querySelectorAll('video').length,total:q.totalVideoFrames,dropped:q.droppedVideoFrames}
        }''')
        recorder = None
        log = (args.output / 'recording.log').open('w')
        if args.record:
            window = subprocess.check_output(['xdotool', 'search', '--onlyvisible', '--name', 'Surg-R1 手术助手'],
                                             env={**os.environ, 'DISPLAY': ':1'}, text=True).splitlines()[0]
            recorder = subprocess.Popen(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'warning',
                '-f', 'x11grab', '-draw_mouse', '0', '-framerate', '25', '-window_id', window, '-i', ':1',
                '-t', str(args.seconds), '-an', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
                '-c:v', 'h264_nvenc', '-gpu', '0', '-preset', 'p4', '-cq', '23', '-b:v', '5M',
                '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(args.output / 'playback_check.mp4')],
                stdout=log, stderr=log)
        start_cpu = cpu_ticks()
        start = time.monotonic()
        await asyncio.sleep(args.seconds)
        elapsed = time.monotonic() - start
        end_cpu = cpu_ticks()
        final = await page.evaluate('''() => {
          window.playbackProbe.active=false;const a=window.playbackProbe.frames.sort((a,b)=>a-b);
          const v=document.querySelector('video'),q=v.getVideoPlaybackQuality();
          return {time:v.currentTime,total:q.totalVideoFrames,dropped:q.droppedVideoFrames,
            callbacks:a.length,p95_ms:a[Math.floor(a.length*.95)],max_ms:a.at(-1),
            gaps_over_100ms:a.filter(x=>x>100).length,frames:a,
            backwards:window.playbackProbe.backwards,events:window.playbackProbe.events,
            source:v.currentSrc,players:document.querySelectorAll('video').length}
        }''')
        if recorder:
            await asyncio.to_thread(recorder.wait, 15)
        log.close()
        await page.screenshot(path=str(args.output / 'after.png'))
        metrics = {'initial': initial, 'final': final, 'wall_seconds': elapsed,
                   'media_seconds': final['time']-initial['time'],
                   'decoded_frames': final['total']-initial['total'],
                   'dropped_frames': final['dropped']-initial['dropped'],
                   'cpu_percent': {pid: {**v, 'percent': round((v['ticks']-start_cpu[pid]['ticks']) /
                       os.sysconf('SC_CLK_TCK') / elapsed * 100, 2)} for pid, v in end_cpu.items() if pid in start_cpu}}
        (args.output / 'metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
        final.pop('frames')
        print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cdp', default='http://127.0.0.1:9222')
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--record', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
