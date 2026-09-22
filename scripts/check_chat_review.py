"""Exercise actual Electron chat citations and isolated clip review on saved results."""
import argparse
import asyncio
import json
from pathlib import Path

import httpx
from playwright.async_api import async_playwright


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient() as client:
        for _ in range(60):
            try:
                response = await client.get('http://127.0.0.1:8001/openapi.json')
                if 'ReviewContext' in response.json().get('components', {}).get('schemas', {}):
                    break
            except Exception:
                pass
            await asyncio.sleep(1)
        else:
            raise RuntimeError('Updated backend did not start')
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://127.0.0.1:9222')
        page = browser.contexts[0].pages[0]
        await page.wait_for_function("() => !!document.querySelector('#app')?.__vue_app__")
        errors, mutations = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def record_mutation(req):
            if req.method != 'POST':
                return
            if '/api/video/control/' in req.url:
                body = req.post_data_json or {}
                if body.get('action') != 'position':
                    mutations.append({'url': req.url, 'body': body})
            elif any(part in req.url for part in ['/seek', '/playback-state', '/pause', '/resume']):
                mutations.append(req.url)
        page.on('request', record_mutation)
        await page.evaluate('''async sid => {
          const app = document.querySelector('#app').__vue_app__;
          const state = (app._instance || app._container._vnode.component).setupState;
          if (state.currentSession?.session_id !== sid) {
            const session = await (await fetch(`/api/video/session/${sid}`)).json();
            state.handleResumeSession(session);
          }
          state.currentView = 'main'; state.rightPanelTab = 'chat';
          state.reviewRange = null;
        }''', args.session)
        await page.wait_for_function('''() => {
          const a=document.querySelector('#app').__vue_app__;
          return (a._instance || a._container._vnode.component).setupState.summaries.length > 0;
        }''')

        async def send_chat(container, message):
            field = container.locator('.chat-input').first
            if await field.input_value():
                raise RuntimeError('Input contains a draft; not replacing it')
            await field.fill(message)
            async with page.expect_response(lambda r: '/api/voice/chat/' in r.url and r.url.endswith('/send'), timeout=90000) as pending:
                await container.locator('.chat-send-btn').first.click()
            result = await (await pending.value).json()
            if not result.get('success'):
                raise RuntimeError(str(result))
            return result

        main_chat = page.locator('.tab-chat')
        main_answer = await send_chat(main_chat, '请根据记录回顾6:25-6:40的情况，在回答中保留6:25-6:40这个视频时间范围。')
        link = main_chat.locator('.chat-msg.assistant').last.locator('a.chat-timestamp').first
        await link.wait_for()
        before = await page.locator('.video-section video').evaluate('(v)=>({t:v.currentTime,paused:v.paused,src:v.currentSrc})')
        before_wall = asyncio.get_running_loop().time()
        mutations.clear()
        await link.click()
        modal = page.locator('.review-dialog')
        await modal.wait_for()
        await modal.locator('video').wait_for()
        await page.wait_for_function('''() => {
          const v=document.querySelector('.review-dialog video');
          return v && v.readyState >= 2 && v.videoWidth > 0;
        }''', timeout=30000)
        times = []
        for _ in range(19):
            times.append(await modal.locator('video').evaluate('(v)=>v.currentTime'))
            await asyncio.sleep(1)
        assert any(b < a - 1 for a, b in zip(times, times[1:])), f'No loop observed: {times}'
        await modal.get_by_role('button', name='暂停片段', exact=True).click()
        paused_at = await modal.locator('video').evaluate('(v)=>v.currentTime')
        await asyncio.sleep(1)
        paused_after = await modal.locator('video').evaluate('(v)=>v.currentTime')
        assert abs(paused_at - paused_after) < .1, (paused_at, paused_after)
        await modal.get_by_role('button', name='播放片段', exact=True).click()
        clip_answer = await send_chat(modal, '这个片段记录了哪些情况？请带上对应的时间戳，不要推测未记录的动作。')
        await page.screenshot(path=str(args.output / 'desktop_review.png'))
        await modal.locator('.chat-msg.assistant a.chat-timestamp').first.click()
        await page.wait_for_function("() => document.querySelector('.review-range').textContent.trim() === '6:25 - 6:30'")
        assert await page.locator('.review-dialog').count() == 1
        assert await modal.locator('.review-analysis-record').count() == 1
        await modal.locator('.review-header button').focus()
        await page.keyboard.press('Escape')
        await modal.wait_for(state='detached')
        after = await page.locator('.video-section video').evaluate('(v)=>({t:v.currentTime,paused:v.paused,src:v.currentSrc})')
        elapsed = asyncio.get_running_loop().time() - before_wall
        assert before['src'] == after['src']
        assert before['paused'] == after['paused'], (before, after)
        assert after['t'] >= before['t'] - .2, (before, after)
        assert after['t'] - before['t'] <= elapsed + 2, (before, after, elapsed)
        assert not mutations, mutations

        await page.locator('.bcs-overview-btn').click()
        await page.locator('.window-card').first.click()
        await page.locator('.review-dialog').wait_for()
        assert await page.locator('.review-analysis-record').count() >= 1
        assert await page.locator('.review-chat .chat-input').is_visible()
        await page.screenshot(path=str(args.output / 'overview_review.png'))
        await page.keyboard.press('Escape')
        await page.locator('.review-dialog').wait_for(state='detached')
        await page.locator('.window-card').last.click()
        last_video = page.locator('.review-dialog video')
        await page.wait_for_function("() => document.querySelector('.review-dialog video')?.readyState >= 2")
        end_times = []
        for _ in range(18):
            end_times.append(await last_video.evaluate('(v)=>({t:v.currentTime,paused:v.paused})'))
            await asyncio.sleep(.5)
        assert any(b['t'] < a['t'] - 1 for a, b in zip(end_times, end_times[1:])), end_times
        assert sum(t['paused'] for t in end_times) <= 1, end_times
        await page.locator('.review-dialog .review-header button').click()
        await page.locator('.overview-layer .back-btn').click()
        await link.click()
        original_viewport = page.viewport_size
        await page.set_viewport_size({'width': 390, 'height': 844})
        await asyncio.sleep(1)
        await page.screenshot(path=str(args.output / 'mobile_review.png'))
        layout = await page.locator('.review-dialog').evaluate('''el => {
          const input=el.querySelector('.chat-input').getBoundingClientRect();
          const rect=el.getBoundingClientRect();
          return {overflow:el.scrollWidth>el.clientWidth+1, inputVisible:input.x>=rect.x && input.right<=rect.right && input.bottom<=rect.bottom};
        }''')
        await page.set_viewport_size(original_viewport or {'width': 1360, 'height': 838})
        assert not layout['overflow'] and layout['inputVisible'], layout
        assert not errors, errors
        result = dict(main_answer=main_answer, clip_answer=clip_answer, loop_times=times,
                      eof_loop_times=end_times,
                      main_before=before, main_after=after, review_elapsed=elapsed,
                      playback_mutations=mutations, mobile=layout, page_errors=errors)
        (args.output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
