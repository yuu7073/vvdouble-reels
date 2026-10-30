// 把 Cowork 開頭畫板（.dc.html）逐格擷取成透明 PNG 序列，再由 ffmpeg 合成 alpha .mov
// 用法：node render.js <src dir> <out dir> <id>=<file> ...
const fs = require('fs'), path = require('path');
const { chromium } = require('playwright');

const [,, SRC, OUT, ...PAIRS] = process.argv;
const FPS = 30, W = 405, H = 720, SCALE = 1080 / 405;
const FONT_DIR = path.join(__dirname, 'node_modules', '@fontsource');

// 每支的特別修正（避免裁字等）
const FIX = {
  '01': `.a-script{padding:.35em .3em;margin:-.35em -.3em}.a-new{padding:.2em .2em;margin:-.2em -.2em}`,
  '07': `.g-type{padding:.2em .15em;margin:-.2em -.15em}`,
  '41': `.ax-w,.ax-w2{padding:.45em .35em;margin:-.45em -.35em}.ax-s{top:540px !important}`,
  '10': `.k-w1,.k-w2{padding:.12em .1em;margin:-.12em -.1em}`,
};

function fontCss() {
  let css = '';
  for (const pkg of fs.readdirSync(FONT_DIR)) {
    const dir = path.join(FONT_DIR, pkg);
    for (const f of fs.readdirSync(dir)) {
      if (/^\d{3}(-italic)?\.css$/.test(f)) {
        css += fs.readFileSync(path.join(dir, f), 'utf8').replace(/url\(\.\/files\//g, `url(file://${dir}/files/`);
      }
    }
  }
  return css;
}

function cycleSec(style) {
  // 主循環秒數：出現最多次的 animation 秒數
  const m = [...style.matchAll(/animation:[^;{}]*?\s([\d.]+)s/g)].map(x => +x[1]);
  const cnt = {}; m.forEach(v => cnt[v] = (cnt[v] || 0) + 1);
  return +Object.entries(cnt).sort((a, b) => b[1] - a[1])[0][0];
}

function buildHtml(src, id) {
  const style = (src.match(/<style>([\s\S]*?)<\/style>/) || [, ''])[1];
  let body = src.slice(src.indexOf('</helmet>') + 9, src.indexOf('</x-dc>'));
  body = body.replace(/<video[\s\S]*?<\/video>/g, '');                 // 影片由 CapCut 放
  body = body.replace(/(<div style="width: 405px; height: 720px;[^"]*?)background: #[0-9a-fA-F]{3,6};/, '$1background: transparent;');
  body = body.replace(/<div style="width: 405px; height: 720px;/, '<div id="root" style="width: 405px; height: 720px;');
  const dur = cycleSec(style);
  const html = `<!doctype html><html><head><meta charset="utf-8"><style>
${fontCss()}
html,body{margin:0;background:transparent;overflow:hidden}
${style}
#root{animation:rootOut ${dur}s linear infinite}
@keyframes rootOut{0%,93%{opacity:1}100%{opacity:0}}
${FIX[id] || ''}
</style></head><body>${body}</body></html>`;
  return { html, dur };
}

(async () => {
  const browser = await chromium.launch();
  for (const pair of PAIRS) {
    const [id, file] = pair.split('=');
    const src = fs.readFileSync(path.join(SRC, file), 'utf8');
    const { html, dur } = buildHtml(src, id);
    const dir = path.join(OUT, id); fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'page.html'), html);
    const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: SCALE });
    await page.goto('file://' + path.resolve(dir, 'page.html'));
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(300);
    const n = Math.round(dur * FPS);
    for (let i = 0; i < n; i++) {
      const t = i / FPS * 1000;
      await page.evaluate(t => { document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; }); }, t);
      await page.screenshot({ path: path.join(dir, `f${String(i).padStart(4, '0')}.png`), omitBackground: true });
    }
    await page.close();
    fs.writeFileSync(path.join(dir, 'meta.json'), JSON.stringify({ id, file, dur, fps: FPS, frames: n }));
    console.log(id, file, dur + 's', n, 'frames');
  }
  await browser.close();
})();
