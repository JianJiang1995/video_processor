#!/usr/bin/env python3
"""Exercise font controls and layout in an idle Electron replay, without analysis."""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


ROOT_STATE = 'document.querySelector("#app").__vue_app__._container._vnode.component.setupState'


async def verify(args):
    args.output.mkdir(parents=True, exist_ok=True)
    result = {'passed': False, 'errors': [], 'analysis_requests': [], 'layouts': []}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp)
        page = browser.contexts[0].pages[0]
        page.on('pageerror', lambda error: result['errors'].append(str(error)))
        page.on('request', lambda r: result['analysis_requests'].append(r.url)
                if r.method == 'POST' and any(s in r.url for s in
                    ['/api/analysis/start', '/api/video/connect', '/api/analysis/start-surgr1']) else None)
        await page.evaluate(f'''async path => {{
            const bundle = await window.electronAPI.loadReplayBundle(path);
            if (!bundle.success) throw Error(bundle.error);
            await {ROOT_STATE}.openReplayBundle(bundle, 420);
        }}''', str(args.spec.resolve()))
        await page.locator('.latest-text').first.wait_for()
        await page.wait_for_function('document.querySelector(".video-section video")?.readyState >= 2')
        slider = page.get_by_test_id('right-panel-text-size')
        await page.get_by_role('button', name='恢复默认字号', exact=True).click()
        assert await slider.input_value() == '20'
        assert await page.locator('.chapter-text').first.evaluate('e => getComputedStyle(e).fontSize') == '20px'
        assert await page.locator('.latest-text').first.evaluate('e => getComputedStyle(e).fontSize') == '22px'
        await page.get_by_role('button', name='增大右侧字号', exact=True).click()
        assert await slider.input_value() == '21'
        await slider.focus()
        await slider.press('End')
        assert await slider.input_value() == '28'
        assert await page.get_by_role('button', name='增大右侧字号', exact=True).is_disabled()
        assert await page.locator('.chapter-text').first.evaluate('e => getComputedStyle(e).fontSize') == '28px'
        await page.reload()
        await page.locator('.latest-text').first.wait_for()
        assert await slider.input_value() == '28'
        result['persistence'] = True
        await page.screenshot(path=str(args.output / 'maximum_font.png'))
        await slider.focus()
        await slider.press('Home')
        assert await slider.input_value() == '16'
        assert await page.get_by_role('button', name='减小右侧字号', exact=True).is_disabled()
        await page.get_by_role('button', name='恢复默认字号', exact=True).click()
        await page.locator('.rp-tab').nth(1).click()
        assert await page.locator('.right-panel .chat-input').evaluate('e => getComputedStyle(e).fontSize') == '24px'
        await page.get_by_role('button', name='增大右侧字号', exact=True).click()
        assert await page.locator('.right-panel .chat-input').evaluate('e => getComputedStyle(e).fontSize') == '25px'
        await page.get_by_role('button', name='恢复默认字号', exact=True).click()
        await page.locator('.rp-tab').first.click()
        cdp = await page.context.new_cdp_session(page)
        try:
            for width, height in [(1920, 1080), (1280, 720), (390, 844)]:
                await cdp.send('Emulation.setDeviceMetricsOverride', {
                    'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': False})
                await page.wait_for_function(f'{ROOT_STATE}.viewportWidth === {width} && {ROOT_STATE}.viewportHeight === {height}')
                await page.evaluate(f'{ROOT_STATE}.resetMainLayout()')
                await page.wait_for_timeout(450)
                sizes = await page.evaluate('''() => {
                    const rect = selector => {
                        const e = document.querySelector(selector), r = e.getBoundingClientRect();
                        return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,
                            scrollWidth:e.scrollWidth,clientWidth:e.clientWidth};
                    };
                    return {width:innerWidth,height:innerHeight,scroll:document.documentElement.scrollWidth,
                        panel:rect('.right-panel'),video:rect('.video-section'),events:rect('.bottom-card-strip'),
                        controls:rect('.reading-controls'),eventCard:rect('.event-node-card'),
                        titleFont:getComputedStyle(document.querySelector('.event-node-title')).fontSize,
                        summaryFont:getComputedStyle(document.querySelector('.event-node-summary')).fontSize};
                }''')
                result['layouts'].append(sizes)
                assert sizes['scroll'] <= width + 1, sizes
                assert sizes['controls']['scrollWidth'] <= sizes['controls']['clientWidth'] + 1, sizes
                assert sizes['panel']['x'] >= 0 and sizes['panel']['x'] + sizes['panel']['width'] <= width + 1, sizes
                assert sizes['titleFont'] == '18px' and sizes['summaryFont'] == '16px', sizes
                if width > 1024:
                    assert sizes['events']['bottom'] <= height + 1, sizes
                    assert sizes['video']['width'] > width * .55, sizes
                else:
                    await page.locator('.reading-controls').scroll_into_view_if_needed()
                    await page.get_by_role('button', name='增大右侧字号', exact=True).click()
                    assert await slider.input_value() == '21'
                    await page.get_by_role('button', name='恢复默认字号', exact=True).click()
                await page.screenshot(path=str(args.output / f'layout_{width}.png'))
            # Existing drag handles must remain functional on desktop.
            await cdp.send('Emulation.clearDeviceMetricsOverride')
            await page.wait_for_function(f'{ROOT_STATE}.viewportWidth === innerWidth && {ROOT_STATE}.viewportHeight === innerHeight')
            await page.evaluate(f'{ROOT_STATE}.resetMainLayout()')
            handle = page.locator('.right-panel-resize-handle')
            box = await handle.bounding_box()
            before = await page.locator('.right-panel').evaluate('e=>e.getBoundingClientRect().width')
            await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + 100)
            await page.mouse.down()
            await page.mouse.move(box['x'] - 50, box['y'] + 100, steps=5)
            await page.mouse.up()
            after = await page.locator('.right-panel').evaluate('e=>e.getBoundingClientRect().width')
            assert after > before + 20, (before, after)
            result['drag_widths'] = [before, after]
        finally:
            await cdp.send('Emulation.clearDeviceMetricsOverride')
            await page.wait_for_function(f'{ROOT_STATE}.viewportWidth === innerWidth && {ROOT_STATE}.viewportHeight === innerHeight')
            await page.evaluate(f'{ROOT_STATE}.resetMainLayout()')
            await page.get_by_role('button', name='恢复默认字号', exact=True).click()
            await page.evaluate('document.querySelector(".right-panel").__vueParentComponent.setupState.resetLatestPaneHeight()')
            await page.locator('.video-section video').first.evaluate('video => video.pause()')
        await page.screenshot(path=str(args.output / 'final.png'))
        result['state'] = await page.evaluate(f'''() => {{const a={ROOT_STATE};return {{
            replay:a.replayMode,recording:document.querySelector('.screen-recording-control')?.__vueParentComponent.setupState.recording,
            playing:a.isPlaying,processing:a.isProcessing}}}}''')
        assert not result['errors'], result['errors']
        assert not result['analysis_requests'], result['analysis_requests']
        result['passed'] = True
    (args.output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cdp', default='http://127.0.0.1:9222')
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(verify(parser.parse_args()))
