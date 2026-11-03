@echo off
chcp 65001 >nul
title KoboldCpp 14B (quality, ~3.5 tok/s)
cd /d "E:\AI-NSFW-Writer\koboldcpp"
if not exist "koboldcpp.exe" (
  echo [ERROR] koboldcpp.exe not found in this folder.
  pause
  exit /b 1
)
set "MODEL=E:\AI-NSFW-Writer\models\Qwen2.5-14B-Instruct-abliterated.Q4_K_M.gguf"
if not exist "%MODEL%" (
  echo [ERROR] 14B model not found: %MODEL%
  pause
  exit /b 1
)
echo Loading 14B (about 60-90 seconds) ...
echo   speed ~3.5 tok/s, 600 chars need ~2 minutes.
koboldcpp.exe --model "%MODEL%" --contextsize 4096 --gpulayers 23 --usecublas --flashattention --quantkv 1 --host 127.0.0.1 --port 5001 --skiplauncher
echo.
echo [KoboldCpp 14B exited] code=%ERRORLEVEL%
echo If it reports out-of-memory, lower --gpulayers from 23 to 20 or 18.
pause
