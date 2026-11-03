@echo off
setlocal
title KoboldCpp - model server (port 5001)
chcp 65001 >nul 2>&1
cd /d "E:\AI-NSFW-Writer\koboldcpp"

if not exist "koboldcpp.exe" (
  echo [ERROR] koboldcpp.exe not found in %CD%
  pause
  exit /b 1
)
if not exist "E:\AI-NSFW-Writer\models\Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf" (
  echo [ERROR] model file not found.
  pause
  exit /b 1
)

echo Loading model, please wait 20-60 seconds...
echo Keep this window open while you are writing.
echo.

koboldcpp.exe --model "E:\AI-NSFW-Writer\models\Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf" --contextsize 8192 --gpulayers 26 --usecublas --flashattention --quantkv 1 --host 127.0.0.1 --port 5001 --skiplauncher

echo.
echo [KoboldCpp exited] code=%ERRORLEVEL%
echo If the model fails to load, lower --gpulayers 26 to 22 (slower but uses less VRAM)
pause
