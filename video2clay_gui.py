# -*- coding: utf-8 -*-
"""视频 → 白膜 + 深度 的图形界面（tkinter，零额外依赖）。

用法：双击 白膜转换器.bat，或
  E:\\Comfy-Desktop\\...\\Scripts\\python.exe video2clay_gui.py
"""
import os
import re
import sys
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "video2clay.py")
PY = sys.executable
# 以 pythonw 启动时，子进程改用 python.exe，方便捕获报错输出
if os.path.basename(PY).lower() == "pythonw.exe":
    _console = os.path.join(os.path.dirname(PY), "python.exe")
    if os.path.exists(_console):
        PY = _console

UI_FONT = ("Microsoft YaHei UI", 10)
UI_FONT_B = ("Microsoft YaHei UI", 10, "bold")
TITLE_FONT = ("Microsoft YaHei UI", 15, "bold")

BG = "#f6f7f9"
CARD = "#ffffff"
ACCENT = "#2f6fed"
MUTED = "#6b7280"

RE_DEPTH = re.compile(r"depth\s+(\d+)/(\d+)")
RE_RENDER = re.compile(r"render\s+(\d+)/(\d+)")
RE_FRAMES = re.compile(r"frames:\s*(\d+)")


class Tip:
    """悬停提示。"""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, font=("Microsoft YaHei UI", 8),
                 bg="#fffbeb", fg="#78350f", relief="solid", bd=1,
                 padx=8, pady=3).pack()

    def hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.proc = None
        self.q = queue.Queue()
        self.total = 0
        self.busy = False
        self.last_dir = None
        self.preview_img = None

        root.title("视频 → 白膜 + 深度")
        root.geometry("820x800")
        root.minsize(720, 620)
        root.configure(bg=BG)
        try:
            root.iconbitmap(default="")
        except Exception:
            pass

        self._build()
        self.root.after(100, self._drain)

    # ---------------------------------------------------------------- UI
    def _card(self, parent, pad=12):
        f = tk.Frame(parent, bg=CARD, highlightbackground="#e5e7eb",
                     highlightthickness=1)
        f.pack(fill="x", padx=14, pady=(8, 0))
        inner = tk.Frame(f, bg=CARD)
        inner.pack(fill="x", padx=pad, pady=pad)
        return inner

    def _build(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 2))
        tk.Label(head, text="视频 → 白膜 + 深度", font=TITLE_FONT, bg=BG,
                 fg="#111827").pack(anchor="w")
        tk.Label(head, text="选一个视频，点开始；产出 白膜.mp4 / 深度.mp4 / 对比图",
                 font=UI_FONT, bg=BG, fg=MUTED).pack(anchor="w", pady=(2, 0))

        # --- 文件
        c = self._card(self.root)
        row = tk.Frame(c, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text="视频", width=6, anchor="w", font=UI_FONT_B,
                 bg=CARD).pack(side="left")
        self.var_video = tk.StringVar()
        ent = tk.Entry(row, textvariable=self.var_video, font=UI_FONT,
                       relief="flat", bg="#f3f4f6", fg="#111827")
        ent.pack(side="left", fill="x", expand=True, ipady=6, padx=(4, 8))
        ttk.Button(row, text="选择视频…", command=self.pick_video).pack(side="left")

        row2 = tk.Frame(c, bg=CARD)
        row2.pack(fill="x", pady=(10, 0))
        tk.Label(row2, text="输出到", width=6, anchor="w", font=UI_FONT_B,
                 bg=CARD).pack(side="left")
        self.var_out = tk.StringVar()
        ent2 = tk.Entry(row2, textvariable=self.var_out, font=UI_FONT,
                        relief="flat", bg="#f3f4f6", fg="#111827")
        ent2.pack(side="left", fill="x", expand=True, ipady=6, padx=(4, 8))
        ttk.Button(row2, text="选择目录…", command=self.pick_out).pack(side="left")

        # --- 参数
        c2 = self._card(self.root)
        hdr = tk.Frame(c2, bg=CARD)
        hdr.pack(fill="x")
        tk.Label(hdr, text="效果调节", font=UI_FONT_B, bg=CARD).pack(side="left")
        tk.Label(hdr, text="  调到满意为止，可反复重跑", font=("Microsoft YaHei UI", 8),
                 bg=CARD, fg="#9ca3af").pack(side="left")
        self.sl_relief = self._slider(c2, "立体强度", 0.6, 3.4, 1.8,
                                      "立体强度：越大浮雕感越强，过大容易出黑边和噪点")
        self.sl_lo = self._slider(c2, "明暗对比", 0.45, 0.85, 0.62,
                                  "明暗对比：数值越小暗部越深、越立体；越大越白越平")
        self.sl_ao = self._slider(c2, "接触阴影", 0.0, 3.0, 1.2,
                                  "接触阴影：物体转折和凹陷处的暗部，0 = 关闭")
        self.sl_flat = self._slider(c2, "背景压平", 0.0, 1.0, 0.55,
                                    "背景压平：把远处杂乱的背景压成干净的浅灰")

        opt = tk.Frame(c2, bg=CARD)
        opt.pack(fill="x", pady=(8, 0))
        self.var_color = tk.BooleanVar(value=False)
        tk.Checkbutton(opt, text="深度用彩色（inferno）", variable=self.var_color,
                       font=UI_FONT, bg=CARD, activebackground=CARD,
                       selectcolor=CARD, anchor="w").pack(side="left")

        tk.Label(opt, text="   只转前", font=UI_FONT, bg=CARD,
                 fg=MUTED).pack(side="left")
        self.var_dur = tk.StringVar(value="")
        tk.Entry(opt, textvariable=self.var_dur, width=5, font=UI_FONT,
                 relief="flat", bg="#f3f4f6").pack(side="left", ipady=3, padx=4)
        tk.Label(opt, text="秒（留空 = 整段）", font=UI_FONT, bg=CARD,
                 fg=MUTED).pack(side="left")

        # --- 按钮
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=14, pady=(10, 0))
        self.btn_start = ttk.Button(bar, text="开始转换", command=self.start)
        self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(bar, text="取消", command=self.cancel,
                                     state="disabled")
        self.btn_cancel.pack(side="left", padx=(8, 0))
        self.btn_open = ttk.Button(bar, text="打开输出目录",
                                   command=self.open_outdir, state="disabled")
        self.btn_open.pack(side="left", padx=(8, 0))
        self.btn_play = ttk.Button(bar, text="播放白膜",
                                   command=self.play_clay, state="disabled")
        self.btn_play.pack(side="left", padx=(8, 0))

        # --- 进度
        p = self._card(self.root)
        self.pb = ttk.Progressbar(p, mode="determinate", maximum=100)
        self.pb.pack(fill="x")
        self.var_status = tk.StringVar(value="等待开始")
        tk.Label(p, textvariable=self.var_status, font=UI_FONT, bg=CARD,
                 fg=MUTED, anchor="w").pack(fill="x", pady=(8, 0))

        # --- 预览
        self.preview = tk.Label(
            self.root, bg=BG, fg="#9ca3af", font=UI_FONT,
            text="转换完成后，这里显示 原图 / 白膜 / 深度 三联图")
        self.preview.pack(fill="both", expand=True, padx=14, pady=(8, 12))

    def _slider(self, parent, label, lo, hi, val, hint):
        wrap = tk.Frame(parent, bg=CARD)
        wrap.pack(fill="x", pady=(4, 0))
        tk.Label(wrap, text=label, width=7, anchor="w", font=UI_FONT,
                 bg=CARD).pack(side="left")
        num = tk.StringVar(value=f"{val:g}")
        var = tk.DoubleVar(value=val)
        tk.Label(wrap, textvariable=num, width=4, anchor="e", font=UI_FONT,
                 bg=CARD, fg=ACCENT).pack(side="right")
        sc = tk.Scale(wrap, from_=lo, to=hi, variable=var, orient="horizontal",
                      showvalue=False, sliderlength=16, width=7, bd=0,
                      highlightthickness=0, troughcolor="#e5e7eb", bg=CARD,
                      activebackground=ACCENT, relief="flat",
                      command=lambda v: num.set(f"{float(v):.2f}"))
        sc.pack(side="left", fill="x", expand=True, padx=(2, 8))
        Tip(sc, hint)
        wrap.var = var
        return wrap

    # ---------------------------------------------------------------- 交互
    def pick_video(self):
        path = filedialog.askopenfilename(
            title="选择视频",
            filetypes=[("视频", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"),
                       ("所有文件", "*.*")])
        if not path:
            return
        self.var_video.set(path)
        name = os.path.splitext(os.path.basename(path))[0]
        self.var_out.set(os.path.join(os.path.dirname(path), name + "_clay"))

    def pick_out(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.var_out.set(d)

    def open_outdir(self):
        d = self.var_out.get().strip()
        if d and os.path.isdir(d):
            os.startfile(d)

    def play_clay(self):
        d = self.var_out.get().strip()
        name = self._name()
        f = os.path.join(d, name + "_clay.mp4")
        if os.path.isfile(f):
            os.startfile(f)

    def _name(self):
        return os.path.splitext(os.path.basename(self.var_video.get().strip()))[0]

    def cancel(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self._set_status("已取消")
        self._finish(False)

    # ---------------------------------------------------------------- 运行
    def start(self):
        if self.busy:
            return
        video = self.var_video.get().strip().strip('"')
        if not video or not os.path.isfile(video):
            messagebox.showwarning("没找到视频", "请先选择一个存在的视频文件。")
            return
        out = self.var_out.get().strip() or os.path.join(
            os.path.dirname(video), self._name() + "_clay")

        cmd = [PY, "-u", SCRIPT, video, "-o", out,
               "--relief", f"{self.sl_relief.var.get():.2f}",
               "--lo", f"{self.sl_lo.var.get():.2f}",
               "--ao", f"{self.sl_ao.var.get():.2f}",
               "--flatten-bg", f"{self.sl_flat.var.get():.2f}"]
        if self.var_color.get():
            cmd += ["--depth-mode", "color"]
        dur = self.var_dur.get().strip()
        if dur:
            try:
                cmd += ["--duration", str(float(dur))]
            except ValueError:
                messagebox.showwarning("时长不是数字", "「只转前 N 秒」要填数字。")
                return

        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.busy = True
        self.total = 0
        self.pb["value"] = 0
        self.btn_start["state"] = "disabled"
        self.btn_cancel["state"] = "normal"
        self.btn_open["state"] = "disabled"
        self.btn_play["state"] = "disabled"
        self.preview.configure(
            image="", text="转换完成后，这里显示 原图 / 白膜 / 深度 三联图")
        self._set_status("准备中…（首次运行要加载 1.3GB 模型，等 10–30 秒）")

        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=env, cwd=HERE)
        except Exception as e:
            self._finish(False)
            messagebox.showerror("启动失败", str(e))
            return

        threading.Thread(target=self._reader, args=(self.proc, out),
                         daemon=True).start()

    def _reader(self, proc, out):
        try:
            for line in proc.stdout:
                self.q.put(("log", line.rstrip()))
        finally:
            code = proc.wait()
            self.q.put(("done", (code, out)))

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._on_log(payload)
                else:
                    self._on_done(*payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _on_log(self, line):
        m = RE_FRAMES.search(line)
        if m:
            self.total = int(m.group(1))
        m = RE_DEPTH.search(line)
        if m and self.total:
            cur, tot = int(m.group(1)), int(m.group(2))
            self.total = tot
            self.pb["value"] = 65.0 * cur / max(tot, 1)
            self._set_status(f"正在算深度  {cur}/{tot}")
            return
        m = RE_RENDER.search(line)
        if m:
            cur, tot = int(m.group(1)), int(m.group(2))
            self.pb["value"] = 65.0 + 34.0 * cur / max(tot, 1)
            self._set_status(f"正在画白膜  {cur}/{tot}")
            return
        if "depth range" in line:
            self._set_status("深度范围已定，开始渲染…")
        elif "Error" in line or "Traceback" in line:
            self._set_status(line.strip()[:120])

    def _on_done(self, code, out):
        if code == 0:
            self.pb["value"] = 100
            self._set_status("完成 ✓  白膜 / 深度 / 对比图已输出")
            self._show_preview(out)
            self.btn_open["state"] = "normal"
            self.btn_play["state"] = "normal"
            self.last_dir = out
        elif self.busy:
            self._set_status(f"出错了（退出码 {code}），把窗口信息发我看看")
        self._finish(code == 0)

    def _finish(self, ok):
        self.busy = False
        self.proc = None
        self.btn_start["state"] = "normal"
        self.btn_cancel["state"] = "disabled"
        if ok:
            self.btn_open["state"] = "normal"
            self.btn_play["state"] = "normal"

    def _show_preview(self, out):
        name = self._name()
        p = os.path.join(out, name + "_preview.png")
        if not os.path.isfile(p):
            return
        try:
            im = Image.open(p)
            self.root.update_idletasks()
            w = max(320, self.preview.winfo_width() - 8)
            h = max(90, self.preview.winfo_height() - 8)
            ratio = min(1.0, w / im.width, h / im.height)
            im = im.resize((int(im.width * ratio), int(im.height * ratio)),
                           Image.LANCZOS)
            self.preview_img = ImageTk.PhotoImage(im)
            self.preview.configure(image=self.preview_img)
        except Exception:
            pass

    def _set_status(self, text):
        self.var_status.set(text)

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno("还在转换", "现在退出会中断转换，确定吗？"):
                return
            self.cancel()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()