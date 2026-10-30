// 把 Cowork 開頭畫板（.dc.html）逐格擷取成透明 PNG 序列，再由 ffmpeg 合成 alpha .mov
// 用法：node render.js <src dir> <out dir> <id>=<file> ...
const fs = require('fs'), path = require('path');
const { chromium } = require('playwright');

const [,, SRC, OUT, ...PAIRS] = process.argv;
const FPS = 30, W = 405, H = 720, SCALE = 2160 / 405;   // 先渲 2 倍（2160x3840），ffmpeg 再縮回 1080，邊緣更乾淨
const FONT_DIR = path.join(__dirname, 'node_modules', '@fontsource');

// 每支的特別修正（避免裁字等）
const FIX = {
  '01': `.a-script{padding:.35em .3em;margin:-.35em -.3em}.a-new{padding:.2em .2em;margin:-.2em -.2em}`,
  '05': `.e-l2,.e-l4{padding:.3em .25em;margin:-.3em -.25em}`,
  '07': `.g-type{padding:.2em .15em;margin:-.2em -.15em}`,
  '10': `.k-w1,.k-w2{padding:.12em .1em;margin:-.12em -.1em}`,
  '19': `.x-panel,.x-vid{animation:none !important}`,                 // 大色塊從第 0 格就定住，不滑入
  '30': `.al-top,.al-bot{animation:none !important}`,               // 上下黑邊從第 0 格就定住
  '41': `.ax-w,.ax-w2{padding:.45em .35em;margin:-.45em -.35em}.ax-s{top:540px !important}`,
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

function cutoffFrac(style, dur) {
  // 不做任何退場：找出主循環動畫裡「最後一段變化」開始的百分比，渲到那裡就停（之後的淡出／滑出都不要）
  let cut = 1;
  const used = new Set();
  for (const m of style.matchAll(/animation:\s*([\w-]+)\s+([\d.]+)s/g)) if (+m[2] === dur) used.add(m[1]);
  for (const m of style.matchAll(/@keyframes\s+([\w-]+)\s*\{([\s\S]*?)\}\s*\}/g)) {
    if (!used.has(m[1])) continue;
    const stops = [...(m[2] + '}').matchAll(/([\d.%,\s]+)\{([^}]*)\}/g)].map(x => ({
      pct: x[1].split(',').map(v => parseFloat(v)), decl: x[2].replace(/\s/g, '') }));
    if (stops.length < 2) continue;
    const last = stops[stops.length - 1], prev = stops[stops.length - 2];
    if (!last.pct.includes(100) || last.decl === prev.decl) continue;
    // 只算「退場」：最後一段變成看不見（透明、滑出畫面、縮到 0、整個裁掉）
    if (!/opacity:0(?![.\d])|translate[XY]?\(-?100%|scale[XY]?\(0\)|inset\(100%|inset\(0 100%|polygon\(0 0,0 0/.test(last.decl)) continue;
    cut = Math.min(cut, Math.max(...prev.pct) / 100);
  }
  return cut;
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
  body = body.replace(/(<div style="width: 405px; height: 720px;[^"]*?)background:\s*#[0-9a-fA-F]{3,8}\s*;?/, '$1background: transparent;');
  body = body.replace(/<div style="width: 405px; height: 720px;/, '<div id="root" style="width: 405px; height: 720px;');
  const dur = cycleSec(style);
  const cut = cutoffFrac(style, dur);
  const html = `<!doctype html><html><head><meta charset="utf-8"><style>
${fontCss()}
html,body{margin:0;background:transparent;overflow:hidden}
${style}
/* 不做整體淡出：畫面上的色塊、壓暗要一路留到最後一格，跟著剪接點硬切（設計本身有淡出的字，照它自己的時間走） */
${FIX[id] || ''}
</style></head><body>${body}</body></html>`;
  return { html, dur, cut };
}

(async () => {
  const browser = await chromium.launch();
  for (const pair of PAIRS) {
    const [id, file] = pair.split('=');
    const src = fs.readFileSync(path.join(SRC, file), 'utf8');
    const { html, dur, cut } = buildHtml(src, id);
    const dir = path.join(OUT, id); fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'page.html'), html);
    const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: SCALE });
    await page.goto('file://' + path.resolve(dir, 'page.html'));
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(300);
    const n = Math.max(FPS * 2, Math.floor(dur * cut * FPS));   // 停在退場開始前（至少 2 秒）
    for (let i = 0; i < n; i++) {
      const t = i / FPS * 1000;
      await page.evaluate(t => { document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; }); }, t);
      await page.screenshot({ path: path.join(dir, `f${String(i).padStart(4, '0')}.png`), omitBackground: true });
    }
    await page.close();
    fs.writeFileSync(path.join(dir, 'meta.json'), JSON.stringify({ id, file, dur, cut, fps: FPS, frames: n }));
    console.log(id, file, dur + 's', 'cut', cut, n, 'frames');
  }
  await browser.close();
})();
