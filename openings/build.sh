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
  # 介面滑過預覽用：小尺寸帶透明的 webm（瀏覽器能播）
  ffmpeg -y -loglevel error -framerate 30 -i "$d/f%04d.png" -vf "format=rgba,scale=270:480,fps=15" \
    -c:v libvpx-vp9 -pix_fmt yuva420p -b:v 0 -crf 34 -auto-alt-ref 0 "mov/$id.webm"
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
