# -*- coding: utf-8 -*-
"""从 Depth Anything V2 权重导出**固定输入尺寸**的 ONNX 模型。

为什么固定尺寸：
    Depth Anything V2 里有多处 `int(patch_h * patch_size)` 之类的写法，
    用动态 shape 导出时 torch 的 tracer 会把它冻成常量，导致无论输入多大
    都按首次 trace 的尺寸算；同时动态 shape 在 DirectML 上大量算子会回退
    CPU，反而慢 5 倍以上。所以这里直接导出静态尺寸。

用法：
    python tools/export_onnx.py --model D:\\models\\DA2-Large --size 756 \
        --out models/depth_anything_v2_large.onnx

    --size  输入正方形边长，推荐 756（速度/质量平衡）；1036 更清晰但慢约 3 倍
    --attn  sdpa（默认，省显存，能导 1036）或 eager（1036 会爆显存）
"""
import argparse
import os

import torch
from transformers import AutoModelForDepthEstimation


def load_model(src, attn):
    try:
        m = AutoModelForDepthEstimation.from_pretrained(src, attn_implementation=attn)
    except (TypeError, ValueError):
        m = AutoModelForDepthEstimation.from_pretrained(src)
    return m.eval()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="HF 目录或 ModelScope 下载的目录")
    ap.add_argument("--size", type=int, default=756, help="输入正方形边长")
    ap.add_argument("--out", default=None, help="输出 onnx 路径")
    ap.add_argument("--attn", default="sdpa", choices=["sdpa", "eager"])
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    dev = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    out = args.out or "depth_anything_v2_large_%d.onnx" % args.size

    print("loading %s (attn=%s, device=%s)" % (args.model, args.attn, dev))
    m = load_model(args.model, args.attn).to(dev)

    dummy = torch.randn(1, 3, args.size, args.size, device=dev)
    with torch.no_grad():
        out_dict = m(pixel_values=dummy)
    print("torch output:", tuple(out_dict.predicted_depth.shape))

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    torch.onnx.export(
        m, (dummy,), out,
        input_names=["pixel_values"], output_names=["depth"],
        opset_version=args.opset,
        do_constant_folding=True,
        dynamo=False,
    )
    print("exported %s  %.1f MB" % (out, os.path.getsize(out) / 1e6))


if __name__ == "__main__":
    main()