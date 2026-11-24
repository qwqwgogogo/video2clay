# -*- coding: utf-8 -*-
"""校验导出的 ONNX 与原始 torch 模型输出是否一致（导出后必做）。

用法：
    python tools/verify_onnx.py --model models/depth_anything_v2_large.onnx \
        --hf D:\\models\\DA2-Large --image D:\\samples\\frame.png

不给 --image 时用随机张量。判定标准：corr 应 ≥ 0.9999，max|diff| 相对
深度值域应该在千分之一量级以内。
"""
import argparse

import numpy as np
import onnxruntime as ort


def preprocess(path, size):
    from PIL import Image

    im = Image.open(path).convert("RGB").resize((size, size), Image.BICUBIC)
    a = np.asarray(im, dtype=np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], np.float32)
    std = np.array([0.229, 0.224, 0.225], np.float32)
    a = (a - mean) / std
    return np.transpose(a, (2, 0, 1))[None].astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--hf", default=None, help="原始 torch 权重目录，用于比对")
    ap.add_argument("--image", default=None)
    args = ap.parse_args()

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(args.model, so,
                                providers=["DmlExecutionProvider", "CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    size = int(inp.shape[2])
    print("onnx input:", inp.shape, "providers:", sess.get_providers())

    x = preprocess(args.image, size) if args.image else \
        np.random.RandomState(1).randn(1, 3, size, size).astype(np.float32)

    got = sess.run(["depth"], {inp.name: x})[0]
    print("onnx out:", got.shape, "range [%.1f, %.1f]" % (got.min(), got.max()))

    if not args.hf:
        print("(no --hf given, skip numeric comparison)")
        return

    import torch
    from transformers import AutoModelForDepthEstimation

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    m = AutoModelForDepthEstimation.from_pretrained(args.hf).eval().to(dev)
    with torch.no_grad():
        ref = m(pixel_values=torch.from_numpy(x).to(dev)).predicted_depth.cpu().numpy()

    a, b = ref.ravel(), got.ravel()
    print("torch out:", ref.shape)
    print("max|diff| = %.5f   corr = %.6f"
          % (np.abs(a - b).max(), np.corrcoef(a, b)[0, 1]))


if __name__ == "__main__":
    main()