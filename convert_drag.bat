@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo 用法：把视频文件直接拖到这个 bat 图标上
  pause
  exit /b 1
)
python -u video2clay.py "%~1"
if errorlevel 1 pause