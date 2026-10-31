#!/usr/bin/env bash
# 把 src/*.dc.html 全部渲成 mov/開頭XX.mov
set -e
cd "$(dirname "$0")"
args=""
for f in src/*.dc.html; do id=$(basename "$f" .dc.html); args="$args $id=$id.dc.html"; done
node render.js src out $args
mkdir -p mov
for d in out/*/; do
  id=$(basename "$d")
  # 先把色彩乘上透明度（premultiplied，CapCut 讀 ProRes 4444 的慣例，否則邊緣會有白邊），再用 lanczos 縮到 1080x1920
  ffmpeg -y -loglevel error -framerate 30 -i "$d/f%04d.png" \
    -vf "format=rgba,premultiply=inplace=1,scale=1080:1920:flags=lanczos,format=yuva444p10le" \
    -c:v prores_ks -profile:v 4444 -pix_fmt yuva444p10le -vendor apl0 -bits_per_mb 1000 "mov/$id.mov"
  # 介面滑過預覽用：小尺寸帶透明的 webm（瀏覽器能播）＋ 60% 處一張透明 PNG（縮圖／靜態預覽）
  # 多格影片類用 p 序列（格子是純綠），先去背再縮，滑過時首支影片會從格子透出來
  n=$(ls "$d"/f*.png | wc -l); idx=$(printf %04d $((n * 6 / 10)))
  if [ -f "$d/p0000.png" ] && [ -f "$d/t0000.png" ]; then
    # 去綠 → 疊上層字 → 縮
    fc="[0]format=rgba,colorkey=0x00FF00:0.4:0.1[a];[1]format=rgba[b];[a][b]overlay=format=auto,format=rgba,scale=270:480"
    ffmpeg -y -loglevel error -framerate 30 -i "$d/p%04d.png" -framerate 30 -i "$d/t%04d.png" -filter_complex "$fc,fps=15" \
      -c:v libvpx-vp9 -pix_fmt yuva420p -b:v 0 -crf 34 -auto-alt-ref 0 "mov/$id.webm"
    ffmpeg -y -loglevel error -i "$d/p$idx.png" -i "$d/t$idx.png" -filter_complex "$fc" -frames:v 1 "mov/$id.png"
  else
    if [ -f "$d/p0000.png" ]; then seq="p"; key="colorkey=0x00FF00:0.4:0.1,"; else seq="f"; key=""; fi
    ffmpeg -y -loglevel error -framerate 30 -i "$d/${seq}%04d.png" -vf "format=rgba,${key}scale=270:480,fps=15" \
      -c:v libvpx-vp9 -pix_fmt yuva420p -b:v 0 -crf 34 -auto-alt-ref 0 "mov/$id.webm"
    ffmpeg -y -loglevel error -i "$d/${seq}${idx}.png" -vf "format=rgba,${key}scale=270:480" -frames:v 1 "mov/$id.png"
  fi
  # 多格影片類：上層文字 + 格子軌跡
  if [ -f "$d/slots.json" ]; then
    cp "$d/slots.json" "mov/$id.json"
    if ls "$d"/t0000.png >/dev/null 2>&1; then
      ffmpeg -y -loglevel error -framerate 30 -i "$d/t%04d.png" \
        -vf "format=rgba,premultiply=inplace=1,scale=1080:1920:flags=lanczos,format=yuva444p10le" \
        -c:v prores_ks -profile:v 4444 -pix_fmt yuva444p10le -vendor apl0 -bits_per_mb 1000 "mov/${id}_top.mov"
    fi
  fi
  echo "mov/$id.mov"
done
