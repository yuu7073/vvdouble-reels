// 把 Cowork 開頭畫板（.dc.html）逐格擷取成透明 PNG 序列，再由 ffmpeg 合成 alpha .mov
// 用法：node render.js <src dir> <out dir> <id>=<file> ...
//
// 兩種模式：
//   文字類（1 支滿版影片＋疊字）：影片拿掉、底透明，只渲字與圖形 → XX.mov
//   多格影片類（多支影片／影片在動）：影片位置改成「格子」，每格每一格的位置／縮放／旋轉／透明度取樣成 slots.json
//     → XX.mov（底層：背景色＋框線等）、XX_top.mov（上層：壓在影片上的字）、XX.json（格子軌跡）
//     草稿裡由 CapCut 原生放影片在格子上，字在最上層
const fs = require('fs'), path = require('path');
const { chromium } = require('playwright');

const [,, SRC, OUT, ...PAIRS] = process.argv;
const FPS = 30, W = 405, H = 720, SCALE = 2160 / 405;   // 先渲 2 倍（2160x3840），ffmpeg 再縮回 1080，邊緣更乾淨
const FONT_DIR = path.join(__dirname, 'node_modules', '@fontsource');

// 每支的特別修正（避免裁字、色塊不要滑入等）
const FIX = {
  '10': `.k-w1,.k-w2{padding:.12em .1em;margin:-.12em -.1em}`,
  '19': `.x-panel,.x-vid{animation:none !important}`,                 // 大色塊從第 0 格就定住，不滑入
  '30': `.al-top,.al-bot{animation:none !important}`,               // 上下黑邊從第 0 格就定住
  '41': `.ax-s{top:540px !important}`,
  '03': `.c-top,.c-bot{animation:none !important}`,                 // 上下兩格影片一開始就在位，不滑入
  '14': `.r-grid{animation:none !important;opacity:1 !important}`,  // 九宮格第 0 格就在，不淡入
  '25': `.ae-top,.ae-bot{animation:none !important}`,
  '48': `.bf-strip{animation:none !important;transform:rotate(-4deg) !important}`,  // 底片條定住，不掉下來
};

// 多格影片類：哪些元素要渲到「上層」（壓在影片上面的字、壓暗、裝飾）
const TOP = {
  '02': '.b-flank,.b-end',
  '03': '.c-w1,.c-w2,.c-mono',
  '13': '.q-txt',
  '14': '.r-title,.r-tag',
  '16': '.t-txt,.t-ser,.t-flash',
  '25': '.ae-w1,.ae-w2,.ae-b1,.ae-b2,.ae-st',
  '39': '.av-w0,.av-w1,.av-w2,.av-w3,.av-w4,.av-w5,.av-w6,.tw,div[style*="top: 70px"],div[style*="inset: 0; background: rgba"]',
  '47': '.bd-t,.bd-s',
  '48': '',
  '15': '',
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

function widenClip(style) {
  // 文字用 clip-path:inset() 做擦入時，裁切框只有行高大小，草寫字的上下伸部會被切掉：
  // 把 0 的邊往外推（上下 0.6em、左右 0.4em），100% 的邊照舊，動畫不變、字不會被裁
  return style.replace(/inset\(([^)]*)\)/g, (m, inner) => {
    const v = inner.trim().split(/\s+/);
    if (v.length !== 4 || !v.every(x => x === '0' || x === '100%')) return m;
    const out = v.map((x, i) => x === '0' ? (i % 2 === 0 ? '-0.6em' : '-0.4em') : x);
    return `inset(${out.join(' ')})`;
  });
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

function isSlotsMode(body) {
  const vids = body.match(/<video[^>]*>/g) || [];
  const imgs = (body.match(/<img[^>]*>/g) || []).filter(x => /width:\s*\d{3,}px|class="[^"]*(card|layer)/.test(x));
  if (vids.length + imgs.length > 1) return true;
  if (vids.length === 1 && !/inset:\s*0|-vid"|class="[a-z]-vid/.test(vids[0])) return true;
  return false;
}

function buildHtml(src, id) {
  const style = widenClip((src.match(/<style>([\s\S]*?)<\/style>/) || [, ''])[1]);
  let body = src.slice(src.indexOf('</helmet>') + 9, src.indexOf('</x-dc>'));
  const slots = id === '19' ? false : isSlotsMode(body);   // 19 上方字卡：影片在下半，直接用底下那支滿版就好
  let nslot = 0;
  const slotMeta = [];
  if (slots) {
    // 影片／大圖換成同樣 class、同樣 style 的格子（版面與動畫完全一樣），之後逐格取樣位置
    body = body.replace(/<(video|img)([^>]*?)\/?>(?:<\/video>)?/g, (m, tag, attrs) => {
      if (tag === 'img' && !/width:\s*\d{3,}px|class="[^"]*(card|layer)/.test(m)) return m;
      const cls = (attrs.match(/class="([^"]*)"/) || [, ''])[1];
      const st = (attrs.match(/style="([^"]*)"/) || [, ''])[1];
      const pos = (st.match(/object-position:\s*([^;"]+)/) || [, '50% 50%'])[1].trim();
      slotMeta.push({ i: nslot, cls, objpos: pos });
      return `<div class="${cls} __slot" data-slot="${nslot++}" style="${st}"></div>`;
    });
  } else {
    body = body.replace(/<video[\s\S]*?<\/video>/g, '');                 // 影片由 CapCut 放
    body = body.replace(/(<div style="width: 405px; height: 720px;[^"]*?)background:\s*#[0-9a-fA-F]{3,8}\s*;?/, '$1background: transparent;');
  }
  body = body.replace(/<div style="width: 405px; height: 720px;/, '<div id="root" style="width: 405px; height: 720px;');
  const dur = cycleSec(style);
  const cut = cutoffFrac(style, dur);
  const html = `<!doctype html><html><head><meta charset="utf-8"><style>
${fontCss()}
html,body{margin:0;background:transparent;overflow:hidden}
${style}
/* 不做整體淡出：畫面上的色塊、壓暗要一路留到最後一格，跟著剪接點硬切（設計本身有淡出的字，照它自己的時間走） */
${FIX[id] || ''}
.__slot{visibility:hidden !important}
body.top-pass #root{background:transparent !important}
body.top-pass #root *{visibility:hidden !important}
body.top-pass #root .__top,body.top-pass #root .__top *{visibility:visible !important}
</style></head><body>${body}</body></html>`;
  return { html, dur, cut, slots, slotMeta, top: TOP[id] || '' };
}

(async () => {
  const browser = await chromium.launch();
  for (const pair of PAIRS) {
    const [id, file] = pair.split('=');
    const src = fs.readFileSync(path.join(SRC, file), 'utf8');
    const { html, dur, cut, slots, slotMeta, top } = buildHtml(src, id);
    const dir = path.join(OUT, id); fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'page.html'), html);
    const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: SCALE });
    await page.goto('file://' + path.resolve(dir, 'page.html'));
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(300);
    if (slots && top) await page.evaluate(sel => { document.querySelectorAll(sel).forEach(e => e.classList.add('__top')); }, top);
    const n = Math.max(FPS * 2, Math.floor(dur * cut * FPS));   // 停在退場開始前（至少 2 秒）
    const frames = [];
    for (let i = 0; i < n; i++) {
      const t = i / FPS * 1000;
      await page.evaluate(t => { document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; }); }, t);
      await page.screenshot({ path: path.join(dir, `f${String(i).padStart(4, '0')}.png`), omitBackground: true });
      if (!slots) continue;
      if (top) {
        await page.evaluate(() => document.body.classList.add('top-pass'));
        await page.screenshot({ path: path.join(dir, `t${String(i).padStart(4, '0')}.png`), omitBackground: true });
        await page.evaluate(() => document.body.classList.remove('top-pass'));
      }
      // 取樣每個格子：中心點、未旋轉前的寬高、縮放、旋轉、透明度（含所有祖先）
      const smp = await page.evaluate(() => {
        const root = document.getElementById('root').getBoundingClientRect();
        return [...document.querySelectorAll('.__slot')].map(el => {
          let m = new DOMMatrix(), op = 1, e = el, vis = true;
          while (e && e.id !== 'root') {
            const cs = getComputedStyle(e);
            if (cs.display === 'none') vis = false;
            op *= parseFloat(cs.opacity);
            const br = cs.filter.match(/brightness\(([\d.]+)\)/); if (br) op *= Math.min(1, parseFloat(br[1]));
            if (cs.transform && cs.transform !== 'none') m = new DOMMatrix(cs.transform).multiply(m);
            e = e.parentElement;
          }
          const r = el.getBoundingClientRect();
          const rot = Math.atan2(m.b, m.a) * 180 / Math.PI;
          const sc = Math.hypot(m.a, m.b) || 1;
          return { cx: +(r.left + r.width / 2 - root.left).toFixed(2), cy: +(r.top + r.height / 2 - root.top).toFixed(2),
                   w0: el.offsetWidth, h0: el.offsetHeight, s: +sc.toFixed(4), rot: +rot.toFixed(2), op: vis ? +Math.max(0, Math.min(1, op)).toFixed(3) : 0 };
        });
      });
      frames.push(smp);
    }
    await page.close();
    const meta = { id, file, dur, cut, fps: FPS, frames: n, slots: slots ? slotMeta : null };
    fs.writeFileSync(path.join(dir, 'meta.json'), JSON.stringify(meta));
    if (slots) fs.writeFileSync(path.join(dir, 'slots.json'), JSON.stringify({ id, fps: FPS, frames: n, w: W, h: H, hasTop: !!top, slots: slotMeta, samples: frames }));
    console.log(id, file, dur + 's', 'cut', cut, n, 'frames', slots ? `slots ${slotMeta.length}` : '');
  }
  await browser.close();
})();
