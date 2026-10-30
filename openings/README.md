# 開頭動畫

Cowork 畫板（.dc.html）→ 無頭 Chromium 逐格擷取透明 PNG → ProRes 4444（帶 alpha）.mov。

```
cd openings && npm i
node render.js src out 01=01.dc.html 07=07.dc.html ...
ffmpeg -framerate 30 -i out/01/f%04d.png -c:v prores_ks -profile:v 4444 -pix_fmt yuva444p10le 開頭01.mov
```

成品上傳到 GitHub Release（tag `openings`），工具啟動時會自動把缺的下載到 `官網部門\開頭動畫`。
