# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    打包「视频转白膜」便携版（内置 Python + 依赖 + 模型 + ffmpeg）。

.DESCRIPTION
    产出一个解压即用的目录，对方电脑不需要装 Python / CUDA / ffmpeg。
    需要本机已经装好 uv（或用 pip 也行，自行替换下面的 uv 命令）。

.EXAMPLE
    # 最简：自动找 uv 的 cpython-3.12，模型和 ffmpeg 用默认位置
    powershell -ExecutionPolicy Bypass -File tools/build_portable.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools/build_portable.ps1 `
        -PythonDir "C:\Users\me\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none" `
        -ModelPath "models\depth_anything_v2_large.onnx" `
        -FfmpegPath "E:\tools\ffmpeg\bin\ffmpeg.exe" `
        -OutDir  "build\视频转白膜_便携版"
#>
param(
    [string]$PythonDir = "",
    [string]$ModelPath = "models\depth_anything_v2_large.onnx",
    [string]$FfmpegPath = "",
    [string]$OutDir = "build\视频转白膜_便携版",
    [string]$Mirror = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [switch]$Zip
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail($msg) { Write-Host "[x] $msg" -ForegroundColor Red; exit 1 }

# ---------- 找一个可复制的 Python ----------
if (-not $PythonDir) {
    $uvRoot = Join-Path $env:APPDATA "uv\python"
    if (Test-Path $uvRoot) {
        $cand = Get-ChildItem $uvRoot -Directory |
            Where-Object { $_.Name -like "cpython-3.12*" } |
            Sort-Object Name -Descending | Select-Object -First 1
        if ($cand) { $PythonDir = $cand.FullName }
    }
}
if (-not $PythonDir -or -not (Test-Path (Join-Path $PythonDir "python.exe"))) {
    Fail "找不到可用的 Python 目录，请用 -PythonDir 指定（需要是完整安装，含 tkinter）"
}
Write-Host "[1/5] Python: $PythonDir"

# ---------- 准备输出目录 ----------
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$dstPy = Join-Path $OutDir "python"
robocopy $PythonDir $dstPy /E /NFL /NDL /NJH /NJS /NP | Out-Null
Write-Host "[2/5] python 已复制"

# ---------- 装依赖 ----------
$pyExe = Join-Path $dstPy "python.exe"
$sp = Join-Path $dstPy "Lib\site-packages"
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if ($uv) {
    & $uv pip install --python $pyExe --target $sp --no-cache --index-url $Mirror `
        onnxruntime-directml numpy scipy pillow
} else {
    & $pyExe -m pip install --target $sp -i $Mirror onnxruntime-directml numpy scipy pillow
}
if ($LASTEXITCODE -ne 0) { Fail "依赖安装失败" }
Write-Host "[3/5] 依赖已安装"

# ---------- 模型 / ffmpeg / 脚本 ----------
if (-not (Test-Path $ModelPath)) { Fail "找不到模型：$ModelPath（先跑 tools/export_onnx.py）" }
New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "models") | Out-Null
Copy-Item $ModelPath (Join-Path $OutDir "models\depth_anything_v2_large.onnx") -Force

if (-not $FfmpegPath) {
    $FfmpegPath = @(
        "E:\Hermes\tools\ffmpeg-9.0.1-win32-x64\bin\ffmpeg.exe",
        "ffmpeg\ffmpeg.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($FfmpegPath) {
    New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "ffmpeg") | Out-Null
    Copy-Item $FfmpegPath (Join-Path $OutDir "ffmpeg\ffmpeg.exe") -Force
    Write-Host "      ffmpeg: $FfmpegPath"
} else {
    Write-Host "[!] 没找到 ffmpeg.exe，便携版将依赖目标机器的 PATH" -ForegroundColor Yellow
}

Copy-Item "video2clay.py" $OutDir -Force
Copy-Item "video2clay_gui.py" $OutDir -Force
Copy-Item "使用说明.txt" $OutDir -Force -ErrorAction SilentlyContinue

@"
@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "%~dp0python\pythonw.exe" "%~dp0video2clay_gui.py"
"@ | Set-Content (Join-Path $OutDir "启动白膜转换器.bat") -Encoding UTF8

@"
@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" ( echo 把视频文件拖到这个 bat 上 & pause & exit /b 1 )
"%~dp0python\python.exe" -u "%~dp0video2clay.py" "%~1"
if errorlevel 1 pause
"@ | Set-Content (Join-Path $OutDir "拖视频转换.bat") -Encoding UTF8

Write-Host "[4/5] 资源与脚本就位"

# ---------- 压缩 ----------
if ($Zip) {
    $zipPath = "$OutDir.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    $sevenZip = "C:\Program Files\7-Zip\7z.exe"
    if (Test-Path $sevenZip) {
        & $sevenZip a -tzip -mx=1 -mmt=on $zipPath $OutDir | Out-Null
    } else {
        Compress-Archive -Path $OutDir -DestinationPath $zipPath
    }
    Write-Host ("[5/5] 压缩完成: {0} ({1:N2} GB)" -f $zipPath, ((Get-Item $zipPath).Length / 1GB))
} else {
    $size = (Get-ChildItem $OutDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
    Write-Host ("[5/5] 完成: {0}  ({1:N0} MB)" -f $OutDir, $size)
}