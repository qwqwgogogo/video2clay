# -*- coding: utf-8 -*-
"""下载 Depth Anything V2 Large 原版权重（默认走 ModelScope，国内直连）。

用法：
    python tools/fetch_model.py                 # 下到 models/DA2-Large
    python tools/fetch_model.py --out D:\\models\\DA2-Large
    python tools/fetch_model.py --source hf     # 改从 huggingface 下（需能访问）

下完后再导出 ONNX：
    python tools/export_onnx.py --model models/DA2-Large --size 756 --out models/depth_anything_v2_large.onnx

脚本支持断点续传（.part 文件），网络断了重跑即可。
"""
import argparse
import os
import sys
import time
import urllib.request

MODEL_ID = "depth-anything/Depth-Anything-V2-Large-hf"
FILES = ["config.json", "preprocessor_config.json", "model.safetensors"]

SOURCES = {
    "modelscope": "https://modelscope.cn/models/{id}/resolve/master/{name}",
    "hf": "https://huggingface.co/{id}/resolve/main/{name}",
    "hf-mirror": "https://hf-mirror.com/{id}/resolve/main/{name}",
}


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def download(url, dst, resume=True):
    tmp = dst + ".part"
    start = os.path.getsize(tmp) if resume and os.path.exists(tmp) else 0
    req = urllib.request.Request(url, headers={"User-Agent": "video2clay/fetch_model"})
    if start:
        req.add_header("Range", "bytes=%d-" % start)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        if start and "416" in str(e):
            os.replace(tmp, dst)
            print("  already complete")
            return
        raise

    total = int(resp.headers.get("Content-Length", 0)) + start
    mode = "ab" if start else "wb"
    t0 = time.time()
    got = start
    with open(tmp, mode) as f:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                pct = got * 100.0 / total
                speed = (got - start) / max(time.time() - t0, 1e-6)
                sys.stdout.write("\r  %5.1f%%  %s / %s  %s/s   "
                                 % (pct, human(got), human(total), human(speed)))
                sys.stdout.flush()
    sys.stdout.write("\n")
    os.replace(tmp, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("models", "DA2-Large"))
    ap.add_argument("--source", default="modelscope", choices=list(SOURCES))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    print("source: %s" % args.source)
    for name in FILES:
        dst = os.path.join(args.out, name)
        if os.path.exists(dst):
            print("[skip] %s (%s)" % (name, human(os.path.getsize(dst))))
            continue
        url = SOURCES[args.source].format(id=MODEL_ID, name=name)
        print("[get ] %s\n       %s" % (name, url))
        try:
            download(url, dst)
        except Exception as e:
            print("  失败: %s" % e)
            print("  可以换源重试：--source hf-mirror 或 --source hf")
            return 1
    print("done ->", os.path.abspath(args.out))
    print("下一步：python tools/export_onnx.py --model %s --size 756 "
          "--out models/depth_anything_v2_large.onnx" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())