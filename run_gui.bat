@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 没找到 python，请先安装 Python 3.10+ 并加入 PATH
  pause
  exit /b 1
)
start "" pythonw video2clay_gui.py
if errorlevel 1 python video2clay_gui.py