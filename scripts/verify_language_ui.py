#!/usr/bin/env python3
"""Verify language switching against the actual Electron app and local translator."""
import argparse
import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = 'document.querySelector("#app").__vue_app__._container._vnode.component.setupState'
HAN = re.compile(r'[\u3400-\u9fff]')


async def verify(args):
    args.output.mkdir(parents=True, exist_ok=True)
    result = {'passed': False, 'errors': [], 'views': {}, 'translations': [], 'analysis_requests': []}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp)
        page = browser.contexts[0].pages[0]
        await page.add_init_script("sessionStorage.setItem('surgr1_capture_auto_connect_started', '1')")
        await page.evaluate("sessionStorage.setItem('surgr1_capture_auto_connect_started', '1')")
        page.on('pageerror', lambda error: result['errors'].append(str(error)))
        page.on('request', lambda r: result['analysis_requests'].append(r.url)
                if r.method == 'POST' and any(s in r.url for s in
                    ['/api/analysis/start', '/api/video/connect', '/api/analysis/event-nodes']) else None)
        assert not await page.evaluate(f'{ROOT}.isProcessing'), 'Only test an idle app'
        await page.evaluate(f'''async path => {{
          const bundle = await window.electronAPI.loadReplayBundle(path);
          if (!bundle.success) throw Error(bundle.error);
          bundle.spec.auto_play = false;
          await {ROOT}.openReplayBundle(bundle, 420);
        }}''', str(args.spec.resolve()))
        await page.locator('.latest-text').first.wait_for()
        await page.wait_for_function('document.querySelector(".video-section video")?.readyState >= 2')
        await page.locator('.video-section video').first.evaluate('v => v.pause()')
        await page.evaluate(f'{ROOT}.handleSeek(420)')
        await page.wait_for_function('Math.abs(document.querySelector(".video-section video").currentTime - 420) < .1')
        await page.evaluate(f'{ROOT}.setLanguage("zh")')
        original = await page.evaluate(f'JSON.stringify({ROOT}.summaries)')
        before = await page.locator('.video-section video').first.evaluate('v => v.currentTime')
        await page.get_by_role('button', name='English', exact=True).first.click()
        await page.wait_for_function('document.documentElement.lang === "en"')
        await page.wait_for_function('''[...document.querySelectorAll('.latest-text')].every(e =>
            !/Translating|Translation unavailable|[\u3400-\u9fff]/.test(e.innerText))''', timeout=120000)
        after = await page.locator('.video-section video').first.evaluate('v => v.currentTime')
        assert abs(after - before) < .1, (before, after)
        assert original == await page.evaluate(f'JSON.stringify({ROOT}.summaries)')
        result['playback_unchanged'] = [before, after]
        result['original_analysis_unchanged'] = True
        result['translations'] = await page.locator('.latest-text').all_inner_texts()

        async def snapshot(name, root):
            # File/session names are user data, not interface labels.
            strings = await page.locator(root).evaluate('''e => {
              const walker = document.createTreeWalker(e, NodeFilter.SHOW_TEXT), texts = [];
              while (walker.nextNode()) {
                const n=walker.currentNode, p=n.parentElement;
                if (!n.textContent.trim() || !p?.getClientRects().length ||
                    p.closest('.language-switch,.session-name,.history-name,.report-header p,.session-info')) continue;
                texts.push(n.textContent.trim());
              }
              return texts;
            }''')
            untranslated = [text for text in strings if HAN.search(text)]
            assert not untranslated, (name, untranslated[:10])
            result['views'][name] = {'untranslated': untranslated, 'text_nodes': len(strings)}
            await page.screenshot(path=str(args.output / f'{name}.png'))

        await snapshot('analysis_en', '.app-container')
        await page.locator('.rp-tab').nth(1).click()
        await snapshot('chat_en', '.right-panel')
        await page.locator('.rp-tab').first.click()
        await page.get_by_role('button', name=re.compile('Grid View')).click()
        await page.locator('.overview-container').wait_for()
        await snapshot('overview_en', '.overview-container')
        await page.locator('.window-card').first.click()
        await page.locator('.review-dialog').wait_for()
        await snapshot('review_en', '.review-dialog')
        await page.get_by_role('button', name='Close review', exact=True).click()
        await page.locator('.overview-header .back-btn').click()
        await page.get_by_role('button', name='Video Summary', exact=True).click()
        await page.locator('.report-markdown').wait_for()
        await page.wait_for_function('''!document.querySelector('.report-main').innerText.includes('Translating...')''', timeout=180000)
        await snapshot('report_en', '.report-layer')
        assert 'Translation unavailable' not in await page.locator('.report-markdown').inner_text()
        await page.locator('.report-back').click()
        await page.reload()
        await page.locator('.latest-text').first.wait_for()
        assert await page.evaluate(f'{ROOT}.language') == 'en'
        assert await page.title() == 'Surg-R1 Surgical Assistant'
        result['reload_and_import_preserve_language'] = True
        cdp = await page.context.new_cdp_session(page)
        try:
            for width, height in [(1920, 1080), (1280, 720), (390, 844)]:
                await cdp.send('Emulation.setDeviceMetricsOverride', dict(width=width, height=height, deviceScaleFactor=1, mobile=False))
                await page.wait_for_timeout(600)
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), width
                await page.screenshot(path=str(args.output / f'english_{width}.png'))
        finally:
            await cdp.send('Emulation.clearDeviceMetricsOverride')
        await page.evaluate(f'{ROOT}.goHome()')
        await page.locator('.mode-selector').wait_for()
        await snapshot('home_en', '.mode-selector')
        await page.get_by_role('button', name='Live Stream', exact=True).click()
        await page.locator('.stream-input-panel').wait_for()
        await page.locator('.device-card', has_text='DeckLink Mini Recorder 4K').click()
        await page.locator('#capture-connection').select_option('sdi')
        await page.locator('#capture-mode').select_option('1080p50')
        for language in ('zh', 'en'):
            await page.get_by_role('button', name='中文' if language == 'zh' else 'English', exact=True).click()
            assert await page.locator('#capture-connection').input_value() == 'sdi'
            assert await page.locator('#capture-mode').input_value() == '1080p50'
            label = await page.locator('#capture-mode option:checked').inner_text()
            assert '1920x1080' in label and '50' in label, label
            assert await page.locator('#capture-mode').is_enabled()
        result['capture_selection_retained'] = {'connection': 'sdi', 'mode': '1080p50', 'resolution': '1920x1080'}
        await snapshot('input_en', '.stream-input-panel')
        await page.evaluate(f'''async path => {{
          const b = await window.electronAPI.loadReplayBundle(path);
          b.spec.auto_play = false;
          await {ROOT}.openReplayBundle(b, 420);
        }}''', str(args.spec.resolve()))
        await page.locator('.latest-text').first.wait_for()
        await page.get_by_role('button', name='中文', exact=True).first.click()
        assert await page.evaluate(f'{ROOT}.language') == 'zh'
        assert HAN.search(await page.locator('.latest-text').first.inner_text())
        await page.get_by_role('button', name='English', exact=True).first.click()
        await page.wait_for_timeout(700)
        await page.wait_for_function(f'''{ROOT}.localizedSummaries.every(s => !/Translating\.\.\./.test(s.summary))''', timeout=180000)
        result['narrative_audit'] = await page.evaluate(f'''() => {{
          const summaries={ROOT}.localizedSummaries;
          return {{total:summaries.length,
            failed:summaries.filter(s=>s.summary.includes('Translation unavailable')).length,
            filler:summaries.filter(s=>/in the field of view|it can be seen that|tractioned|current anatomical location/i.test(s.summary))
              .map(s=>({{window_id:s.window_id, text:s.summary}}))}};
        }}''')
        assert result['narrative_audit']['failed'] == 0, result['narrative_audit']
        assert not result['narrative_audit']['filler'], result['narrative_audit']
        await page.screenshot(path=str(args.output / 'final_en.png'))
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
