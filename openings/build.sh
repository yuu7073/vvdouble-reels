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
  ffmpeg -y -loglevel error -framerate 30 -i "$d/f%04d.png" -c:v prores_ks -profile:v 4444 -pix_fmt yuva444p10le -vendor apl0 "mov/開頭$id.mov"
  echo "mov/開頭$id.mov"
done
