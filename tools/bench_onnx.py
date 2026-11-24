# -*- coding: utf-8 -*-
"""测 ONNX 模型在各后端上的推理速度。

用法：
    python tools/bench_onnx.py --model models/depth_anything_v2_large.onnx
    python tools/bench_onnx.py --model ... --size 756 --n 5 --provider DmlExecutionProvider
"""
import argparse
import time

import numpy as np
import onnxruntime as ort


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--size", type=int, default=None, help="默认从模型输入形状读")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--provider", default=None)
    args = ap.parse_args()

    print("available providers:", ort.get_available_providers())

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.log_severity_level = 3

    providers = [args.provider] if args.provider else None
    sess = ort.InferenceSession(args.model, so, providers=providers)
    print("session providers:", sess.get_providers())

    inp = sess.get_inputs()[0]
    size = args.size or int(inp.shape[2])
    print("input:", inp.name, inp.shape, "-> size", size)

    x = np.random.RandomState(0).randn(1, 3, size, size).astype(np.float32)
    sess.run(["depth"], {inp.name: x})  # warmup

    t0 = time.time()
    for _ in range(args.n):
        y = sess.run(["depth"], {inp.name: x})[0]
    dt = (time.time() - t0) / args.n
    print("%d: %.3f s/frame  (%.2f fps)  out %s  mean %.2f"
          % (size, dt, 1.0 / dt, y.shape, float(y.mean())))


if __name__ == "__main__":
    main()