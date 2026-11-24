#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
video2clay —— 任意视频 -> 深度视频(depth pass) + 白膜视频(clay render)

流程:
  1. ffmpeg 抽帧 (无损 PNG)
  2. DepthAnything V2 (Large) 逐帧估计相对深度 (near = 亮)
  3. 时间中值滤波去闪烁 + 全局分位数归一化 (整段视频统一尺度)
  4. 深度 -> 伪法线 -> Lambert/Blinn 白膜着色 (+ 简易 AO)
  5. ffmpeg 编码两路 mp4 (保留原音轨)

用法:
  python video2clay.py input.mp4 [-o outdir] [--relief 2.0] [--smooth 5]
                        [--max-frames N] [--start S] [--duration D]
                        [--scale 1.0] [--model large|base|small]
                        [--depth-mode gray|color]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "models")
ONNX_MODEL = os.path.join(MODEL_DIR, "depth_anything_v2_large.onnx")


def _find_ffmpeg():
    for c in (os.path.join(BASE_DIR, "ffmpeg", "ffmpeg.exe"),
              os.path.join(BASE_DIR, "bin", "ffmpeg.exe"),
              shutil.which("ffmpeg")):
        if c and os.path.isfile(c):
            return c
    return "ffmpeg"


FFMPEG = _find_ffmpeg()


def log(msg):
    print(f"[v2c] {msg}", flush=True)


def run(cmd, capture=True):
    p = subprocess.run(cmd, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "ignore")[-3000:]
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{err}")
    return p


def probe(path):
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", path],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    txt = p.stderr.decode("utf-8", "ignore")
    dur = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", txt)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    w = h = 0
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", txt, re.S)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    fps = 25.0
    m = re.search(r"([\d.]+)\s*fps", txt)
    if m:
        fps = float(m.group(1))
    has_audio = "Audio:" in txt
    if not w:
        raise RuntimeError("cannot probe video: " + txt[-500:])
    return dict(width=w, height=h, fps=fps, duration=dur, has_audio=has_audio)


def extract_frames(src, outdir, fps=None, start=None, duration=None):
    os.makedirs(outdir, exist_ok=True)
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-i", src]
    if fps:
        cmd += ["-vf", f"fps={fps}", "-fps_mode", "vfr"]
    else:
        cmd += ["-fps_mode", "passthrough"]
    cmd += [os.path.join(outdir, "%06d.png")]
    run(cmd)
    files = sorted(f for f in os.listdir(outdir) if f.endswith(".png"))
    return [os.path.join(outdir, f) for f in files]


# ---------------------------------------------------------------- depth
class DepthEngine:
    """Depth Anything V2 (ONNX 推理, 优先 DirectML, 自动回退 CPU)。"""

    MEAN = np.array([0.485, 0.456, 0.406], np.float32)
    STD = np.array([0.229, 0.224, 0.225], np.float32)

    def __init__(self, path=None):
        import onnxruntime as ort
        path = path or os.environ.get("V2C_MODEL") or ONNX_MODEL
        if not os.path.isfile(path):
            raise RuntimeError("找不到深度模型: %s" % path)
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.log_severity_level = 3
        avail = ort.get_available_providers()
        provs = [x for x in ("DmlExecutionProvider", "CUDAExecutionProvider",
                             "CPUExecutionProvider") if x in avail]
        self.sess = ort.InferenceSession(path, so, providers=provs)
        self.provider = self.sess.get_providers()[0]
        shp = self.sess.get_inputs()[0].shape
        self.res = int(shp[2]) if isinstance(shp[2], int) else 756
        log("depth model: %s  res=%d  backend=%s"
            % (os.path.basename(path), self.res, self.provider))

    def __call__(self, img):
        im = img.convert("RGB").resize((self.res, self.res), Image.BICUBIC)
        a = np.asarray(im, np.float32) / 255.0
        a = (a - self.MEAN) / self.STD
        x = np.ascontiguousarray(a.transpose(2, 0, 1)[None])
        y = self.sess.run(["depth"], {"pixel_values": x})[0]
        return y[0]


def infer_depth(frames, engine, depth_dir):
    os.makedirs(depth_dir, exist_ok=True)
    out = []
    t0 = time.time()
    W = H = None
    for i, fr in enumerate(frames):
        with Image.open(fr) as im:
            im = im.convert("RGB")
            w, h = im.size
            W, H = w, h
            pred = engine(im)
        arr = np.asarray(
            Image.fromarray(pred, mode="F").resize((w, h), Image.BICUBIC),
            np.float32)
        stem = os.path.splitext(os.path.basename(fr))[0]
        dst = os.path.join(depth_dir, stem + ".npy")
        np.save(dst, arr.astype(np.float16))
        out.append(dst)
        done = i + 1
        el = time.time() - t0
        log("  depth %d/%d  (%.1f frames/s)" % (done, len(frames),
                                                done / max(el, 1e-6)))
    return out, (W, H)


def global_range(depth_files, sample=25):
    lo, hi = [], []
    step = max(1, len(depth_files) // sample)
    for f in depth_files[::step]:
        d = np.load(f).astype(np.float32)
        v = d[d > 0]
        if v.size:
            lo.append(np.percentile(v, 2))
            hi.append(np.percentile(v, 98))
    return float(np.median(lo)), float(np.median(hi))


# ---------------------------------------------------------------- clay
def _box(x, r):
    return ndi.uniform_filter(x, size=2 * r + 1, mode="nearest")


def guided_filter(guide, src, radius=12, eps=1e-4):
    """边缘保持平滑: 用 guide(0..1) 引导 src."""
    mI = _box(guide, radius)
    mp = _box(src, radius)
    mIp = _box(guide * src, radius)
    varI = _box(guide * guide, radius) - mI * mI
    cov = mIp - mI * mp
    a = cov / (varI + eps)
    b = mp - a * mI
    return _box(a, radius) * guide + _box(b, radius)


def smooth_depth_guided(d, rgb=None, radius=12, eps=1e-4):
    if rgb is None:
        return ndi.median_filter(d, size=3, mode="nearest")
    g = rgb.astype(np.float32) / 255.0
    lum = 0.299 * g[..., 0] + 0.587 * g[..., 1] + 0.114 * g[..., 2]
    return guided_filter(lum, d, radius, eps)


def edge_mask(d, t0=0.02, t1=0.16, sigma=1.2):
    gy, gx = np.gradient(ndi.gaussian_filter(d, 1.0, mode="nearest"))
    g = np.sqrt(gx * gx + gy * gy)
    m = np.clip((g - t0) / max(t1 - t0, 1e-6), 0.0, 1.0)
    return ndi.gaussian_filter(m, sigma, mode="nearest")


def normals_from_disp(d, relief=2.0, sigma=2.0, guide_rgb=None, clamp_edges=True,
                      edge_t0=0.02, edge_t1=0.16, normal_scale=0.5):
    """d: disparity (near larger). 归一化图像坐标下求梯度, relief 控制起伏.
    normal_scale<1 时在低分辨率上求法线再上采样, 抑制单目深度噪声. """
    H, W = d.shape
    z = smooth_depth_guided(d, guide_rgb, radius=16, eps=1e-3)
    z = ndi.gaussian_filter(z, sigma, mode="nearest")

    if normal_scale and normal_scale < 0.999:
        nh = max(64, int(round(H * normal_scale)))
        nw = max(64, int(round(W * normal_scale)))
        zs = np.asarray(Image.fromarray(z.astype(np.float32), mode="F").resize((nw, nh), Image.BILINEAR))
        gy, gx = np.gradient(zs)
        gx = gx * (nw * 0.5) * relief
        gy = gy * (nh * 0.5) * relief
        nz = np.ones_like(zs)
        inv = 1.0 / np.sqrt(gx * gx + gy * gy + 1.0)
        nx = -gx * inv
        ny = -gy * inv
        nz = nz * inv
        nx = np.asarray(Image.fromarray(nx.astype(np.float32), mode="F").resize((W, H), Image.BILINEAR))
        ny = np.asarray(Image.fromarray(ny.astype(np.float32), mode="F").resize((W, H), Image.BILINEAR))
        nz = np.asarray(Image.fromarray(nz.astype(np.float32), mode="F").resize((W, H), Image.BILINEAR))
        n = np.stack([nx, ny, nz], axis=-1)
        v = np.linalg.norm(n, axis=-1, keepdims=True)
        n = n / np.maximum(v, 1e-6)
    else:
        gy, gx = np.gradient(z)
        gx = gx * (W * 0.5) * relief
        gy = gy * (H * 0.5) * relief
        nz = np.ones_like(z)
        inv = 1.0 / np.sqrt(gx * gx + gy * gy + 1.0)
        n = np.stack([-gx * inv, -gy * inv, nz * inv], axis=-1)

    m = edge_mask(d, edge_t0, edge_t1)
    if clamp_edges:
        w = (m * 0.92)[..., None]
        n = n * (1.0 - w) + np.array([0.0, 0.0, 1.0], np.float32) * w
        v = np.linalg.norm(n, axis=-1, keepdims=True)
        n = n / np.maximum(v, 1e-6)
    return n, m


def shade_clay(d_norm, relief=1.8, sigma=2.5, albedo=0.92, lo=0.62, hi=1.0,
               key=0.78, fill=0.22, ao_k=1.2, exposure=1.0, gamma=0.95,
               guide_rgb=None, clamp_edges=True, edge_shade=0.0, flatten=0.55,
               key_dir=(-0.42, -0.66, 0.62), fill_dir=(0.55, 0.18, 0.82),
               contrast=1.0, normal_scale=0.5, return_linear=False):
    """把归一化视差画成白色石膏像: 表面亮度恒定落在 [lo, hi]*albedo, 只靠方向光塑形。"""
    n, m = normals_from_disp(d_norm, relief=relief, sigma=sigma,
                             guide_rgb=guide_rgb, clamp_edges=clamp_edges,
                             normal_scale=normal_scale)

    Lk = np.array(key_dir, np.float32)
    Lk /= np.linalg.norm(Lk)
    Lf = np.array(fill_dir, np.float32)
    Lf /= np.linalg.norm(Lf)

    dk = np.clip(n @ Lk, 0.0, 1.0)
    dk = np.clip((dk + 0.10) / 1.10, 0.0, 1.0)
    df = np.clip(n @ Lf, 0.0, 1.0)
    t = np.clip(key * dk + fill * df, 0.0, 1.0)

    blur = ndi.gaussian_filter(d_norm, 14, mode="nearest")
    # 凹陷遮蔽只在深度连续处生效, 避免物体轮廓生成黑环
    occl = np.clip((blur - d_norm) * ao_k * 40.0, 0.0, 1.0) * (1.0 - m)
    ao = 1.0 - 0.55 * occl

    shade = albedo * ((lo + (hi - lo) * t) * ao)
    shade = shade * (1.0 - edge_shade * m)

    if flatten > 0.0:
        far = np.clip((0.16 - d_norm) / 0.16, 0.0, 1.0) ** 0.6
        hh = d_norm.shape[0]
        ramp = np.linspace(0.86, 0.94, hh, dtype=np.float32)[:, None]
        fw = flatten * far
        shade = (1.0 - fw) * shade + fw * ramp

    if return_linear:
        return shade.astype(np.float32)

    shade = np.power(np.clip(shade * exposure, 0.0, 4.0), gamma)
    if contrast != 1.0:
        shade = np.clip((shade - 0.5) * contrast + 0.5, 0.0, 1.0)
    return np.clip(shade * 255.0, 0, 255).astype(np.uint8)


def depth_to_gray(d_norm):
    return (np.clip(d_norm, 0.0, 1.0) * 255.0).astype(np.uint8)


def depth_to_color(d_norm):
    try:
        from matplotlib import cm
        rgb = (cm.inferno(np.clip(d_norm, 0, 1))[..., :3] * 255).astype(np.uint8)
        return rgb
    except Exception:
        g = depth_to_gray(d_norm)
        return np.stack([g, g, g], axis=-1)


# ---------------------------------------------------------------- encode
def encode(frames_dir, pattern, dst, fps, src=None, audio=True):
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-framerate", f"{fps}", "-i", os.path.join(frames_dir, pattern)]
    if src and audio:
        cmd += ["-i", src, "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "copy", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "15",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", dst]
    run(cmd)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("-o", "--outdir", default=None)
    ap.add_argument("--model", default="large", help="兼容旧参数, 已忽略")
    ap.add_argument("--relief", type=float, default=1.8, help="白膜起伏强度")
    ap.add_argument("--sigma", type=float, default=2.5, help="法线平滑")
    ap.add_argument("--exposure", type=float, default=1.0, help="白膜亮度系数(自动曝光之上再乘)")
    ap.add_argument("--gamma", type=float, default=0.95, help="白膜灰阶 gamma")
    ap.add_argument("--target", type=float, default=0.80, help="自动曝光目标: 亮部 p88 落点")
    ap.add_argument("--auto-exp", action="store_true", help="开启自动曝光(默认关)")
    ap.add_argument("--edge-shade", type=float, default=0.0, help="轮廓压暗")
    ap.add_argument("--no-guide", action="store_true", help="关闭 RGB 引导滤波")
    ap.add_argument("--smooth", type=int, default=5, help="时间中值窗口(帧), 1=关闭")
    ap.add_argument("--ao", type=float, default=1.2)
    ap.add_argument("--flatten-bg", type=float, default=0.55,
                    help="远景背景压平强度 0~1")
    ap.add_argument("--albedo", type=float, default=0.92)
    ap.add_argument("--lo", type=float, default=0.62, help="white-model darkest brightness")
    ap.add_argument("--hi", type=float, default=1.0, help="white-model brightest brightness")
    ap.add_argument("--depth-mode", default="gray", choices=["gray", "color"])
    ap.add_argument("--fps", type=float, default=None, help="抽帧帧率, 默认原帧率")
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--scale", type=float, default=1.0, help="输出缩放, 1.0=原尺寸")
    ap.add_argument("--keep-frames", action="store_true")
    args = ap.parse_args()

    src = os.path.abspath(args.video)
    if not os.path.isfile(src):
        sys.exit(f"no such file: {src}")
    info = probe(src)
    name = os.path.splitext(os.path.basename(src))[0]
    outdir = args.outdir or os.path.join(os.path.dirname(src), name + "_clay")
    os.makedirs(outdir, exist_ok=True)
    work = os.path.join(outdir, "_work")
    fdir = os.path.join(work, "frames")
    ddir = os.path.join(work, "depth")
    cdir = os.path.join(work, "clay")
    pdir = os.path.join(work, "depth_png")
    for d in (fdir, ddir, cdir, pdir):
        os.makedirs(d, exist_ok=True)

    log(f"source: {src}  {info['width']}x{info['height']} @ {info['fps']:.3f}fps  "
        f"dur={info['duration']:.2f}s audio={info['has_audio']}")

    frames = extract_frames(src, fdir, fps=args.fps, start=args.start, duration=args.duration)
    if args.max_frames:
        frames = frames[:args.max_frames]
    if not frames:
        sys.exit("no frames extracted")
    log(f"frames: {len(frames)}")

    engine = DepthEngine()
    depth_files, (W, H) = infer_depth(frames, engine, ddir)

    lo, hi = global_range(depth_files)
    log(f"depth range: p2={lo:.3f} p98={hi:.3f}")

    expo = args.exposure
    if args.auto_exp:
        ntot = len(depth_files)
        picks = sorted({int(x) for x in np.linspace(0, ntot - 1, min(24, ntot))})
        vals = []
        for j in picks:
            dj = np.load(depth_files[j]).astype(np.float32)
            dnj = np.clip((dj - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
            gj = None
            if not args.no_guide:
                with Image.open(frames[j]) as im:
                    gj = np.asarray(im.convert("RGB"))
            sh = shade_clay(dnj, relief=args.relief, sigma=args.sigma,
                            albedo=args.albedo, lo=args.lo, hi=args.hi, ao_k=args.ao,
                            guide_rgb=gj,
                            edge_shade=args.edge_shade,
                            flatten=args.flatten_bg, return_linear=True)
            vals.append(float(np.percentile(sh, 88)))
        base = float(np.median(vals)) if vals else 1.0
        expo = float(np.clip(args.target / max(base, 1e-3), 0.45, 2.2)) * args.exposure
        log(f"auto exposure: p88={base:.3f} -> blend x{expo:.2f}")

    ow, oh = int(round(W * args.scale)), int(round(H * args.scale))
    if (ow, oh) != (W, H):
        ow -= ow % 2
        oh -= oh % 2

    half = max(0, args.smooth // 2)
    N = len(depth_files)
    t0 = time.time()
    for i in range(N):
        idx = list(range(max(0, i - half), min(N, i + half + 1)))
        if args.smooth > 1 and len(idx) > 1:
            stack = np.stack([np.load(depth_files[j]).astype(np.float32) for j in idx], 0)
            med = np.median(stack, axis=0)
            cur = np.load(depth_files[i]).astype(np.float32)
            # 运动感知: 相邻帧变化越大越少用时间中值, 避免快速运动重影
            span = max(hi - lo, 1e-6)
            diff = float(np.mean(np.abs(med - cur)))
            w = float(np.clip(1.0 - diff / (0.015 * span), 0.0, 1.0))
            d = (1.0 - w) * cur + w * med
        else:
            d = np.load(depth_files[i]).astype(np.float32)

        dn = np.clip((d - lo) / max(hi - lo, 1e-6), 0.0, 1.0)

        if args.depth_mode == "color":
            dimg = Image.fromarray(depth_to_color(dn))
        else:
            dimg = Image.fromarray(depth_to_gray(dn), mode="L")
        if (ow, oh) != (W, H):
            dimg = dimg.resize((ow, oh), Image.LANCZOS)
        dimg.save(os.path.join(pdir, "%06d.png" % (i + 1)))

        guide = None
        if not args.no_guide:
            with Image.open(frames[i]) as im:
                guide = np.asarray(im.convert("RGB"))
        clay = shade_clay(dn, relief=args.relief, sigma=args.sigma,
                          albedo=args.albedo, lo=args.lo, hi=args.hi, ao_k=args.ao, exposure=expo,
                          gamma=args.gamma, guide_rgb=guide,
                          edge_shade=args.edge_shade,
                          flatten=args.flatten_bg)
        cimg = Image.fromarray(clay, mode="L")
        if (ow, oh) != (W, H):
            cimg = cimg.resize((ow, oh), Image.LANCZOS)
        cimg.save(os.path.join(cdir, "%06d.png" % (i + 1)))

        if (i + 1) % 25 == 0 or i == N - 1:
            el = time.time() - t0
            log(f"  render {i+1}/{N} ({(i+1)/max(el,1e-6):.1f} f/s)")

    ofps = args.fps or info["fps"]
    depth_mp4 = os.path.join(outdir, f"{name}_depth.mp4")
    clay_mp4 = os.path.join(outdir, f"{name}_clay.mp4")
    encode(pdir, "%06d.png", depth_mp4, ofps, src, info["has_audio"])
    encode(cdir, "%06d.png", clay_mp4, ofps, src, info["has_audio"])
    log(f"depth -> {depth_mp4}")
    log(f"clay  -> {clay_mp4}")

    mid = N // 2
    tiles = []
    for p in (frames[mid], os.path.join(pdir, "%06d.png" % (mid + 1)),
              os.path.join(cdir, "%06d.png" % (mid + 1))):
        im = Image.open(p).convert("RGB")
        if (ow, oh) != (W, H):
            im = im.resize((ow, oh), Image.LANCZOS)
        tiles.append(im)
    canvas = Image.new("RGB", (ow * 3 + 16, oh), (24, 24, 24))
    for k, im in enumerate(tiles):
        canvas.paste(im, (k * (ow + 8), 0))
    prev = os.path.join(outdir, f"{name}_preview.png")
    canvas.save(prev)
    log(f"preview -> {prev}")

    if not args.keep_frames:
        shutil.rmtree(work, ignore_errors=True)
    log("done")


if __name__ == "__main__":
    main()