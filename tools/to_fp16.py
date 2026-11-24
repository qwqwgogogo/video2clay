# -*- coding: utf-8 -*-
"""把 fp32 的 ONNX 转成 fp16（体积减半）。

⚠️ 实测结论：在这套模型 + DirectML 上，fp16 **并不更快**（0.997 s/帧 vs fp32 的
0.442 s/帧），精度也有损失（max|diff| 18.7 / 值域 500）。所以默认流程不用 fp16，
这个脚本仅作为对比实验保留。

用法：
    python tools/to_fp16.py --model models/depth_anything_v2_large.onnx \
        --out models/depth_anything_v2_large_fp16.onnx
"""
import argparse
import os


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import onnx
    from onnxruntime.transformers.float16 import convert_float_to_float16

    out = args.out or args.model.replace(".onnx", "_fp16.onnx")
    m = onnx.load(args.model)
    m16 = convert_float_to_float16(m, keep_io_types=True)
    onnx.save(m16, out)
    print("saved %s  %.1f MB  (src %.1f MB)"
          % (out, os.path.getsize(out) / 1e6, os.path.getsize(args.model) / 1e6))


if __name__ == "__main__":
    main()