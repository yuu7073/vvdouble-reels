// 渲染前的快速檢查：每支抓 4 格（1x），疊在灰底上拼成對照表，看字有沒有被裁、有沒有缺字（豆腐框）
// 用法：node check.js <src dir> <out dir> <id>=<file> ...   → out/sheet_<id>.png
const fs = require('fs'), path = require('path');
const { chromium } = require('playwright');
const { buildHtml, W, H } = require('./render.js');
const [,, SRC, OUT, ...PAIRS] = process.argv;
(async () => {
  const browser = await chromium.launch();
  fs.mkdirSync(OUT, { recursive: true });
  const report = [];
  for (const pair of PAIRS) {
    const [id, file] = pair.split('=');
    const src = fs.readFileSync(path.join(SRC, file), 'utf8');
    const { html, dur, cut, slots } = buildHtml(src, id);
    const dir = path.join(OUT, id); fs.mkdirSync(dir, { recursive: true });
    // 灰底 + 格子塗綠，方便看字
    const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
    fs.writeFileSync(path.join(dir, 'page.html'), html.replace('html,body{margin:0;background:transparent', 'html,body{margin:0;background:#6e6a66'));
    await page.goto('file://' + path.resolve(dir, 'page.html'));
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(300);
    await page.addStyleTag({ content: '.__slot{visibility:visible !important;background:#3f7a5a !important}' });
    const total = dur * cut;
    const shots = [];
    for (const frac of [0.3, 0.55, 0.8, 0.98]) {
      await page.evaluate(t => { document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; }); }, frac * total * 1000);
      // 偵測被裁的字：文字元素的框超出畫面，或超出有 overflow:hidden 的父層
      const clipped = await page.evaluate(() => {
        const out = [];
        for (const el of document.querySelectorAll('#root *')) {
          if (!el.childNodes.length || ![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
          const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.05) continue;
          const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
          if (r.left < -2 || r.top < -2 || r.right > 407 || r.bottom > 722) { out.push(el.textContent.trim().slice(0, 20) + ' 超出畫面'); continue; }
          let p = el.parentElement;
          while (p && p.id !== 'root') {
            const pc = getComputedStyle(p);
            if (/hidden|clip/.test(pc.overflow) || (pc.clipPath && pc.clipPath !== 'none')) {
              const pr = p.getBoundingClientRect();
              if (r.left < pr.left - 1 || r.right > pr.right + 1 || r.top < pr.top - 1 || r.bottom > pr.bottom + 1) { out.push(el.textContent.trim().slice(0, 20) + ' 被父層裁'); break; }
            }
            p = p.parentElement;
          }
        }
        return out;
      });
      shots.push(path.join(dir, `s${shots.length}.png`));
      await page.screenshot({ path: shots[shots.length - 1] });
      if (clipped.length) report.push(`${id} @${Math.round(frac * 100)}%: ${[...new Set(clipped)].join('；')}`);
    }
    await page.close();
    console.log(id, dur + 's cut', cut, slots ? 'slots' : 'text');
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, 'report.txt'), report.join('\n'));
  console.log(report.length ? '可能被裁：\n' + report.join('\n') : '沒有偵測到被裁的字');
})();
