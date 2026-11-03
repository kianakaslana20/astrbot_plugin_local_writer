@echo off
setlocal
title Diagnose - local AI novel workbench
chcp 65001 >nul 2>&1
set "BASE=E:\AI-NSFW-Writer"

echo ================ files ================
if exist "%BASE%\koboldcpp\koboldcpp.exe" (echo [OK]   koboldcpp.exe) else (echo [MISS] koboldcpp.exe)
if exist "%BASE%\models\Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf" (echo [OK]   model gguf) else (echo [MISS] model gguf)
if exist "%BASE%\SillyTavern\server.js" (echo [OK]   server.js) else (echo [MISS] server.js)
if exist "%BASE%\SillyTavern\node_modules" (echo [OK]   node_modules) else (echo [MISS] node_modules)
if exist "%BASE%\SillyTavern\data\default-user\characters\*.png" (echo [OK]   character cards) else (echo [MISS] character cards)

echo.
echo ================ ports ================
netstat -ano | findstr /C:":5001" | findstr /C:"LISTENING" >nul && (echo [UP]   koboldcpp 5001) || (echo [DOWN] koboldcpp 5001)
netstat -ano | findstr /C:":8000" | findstr /C:"LISTENING" >nul && (echo [UP]   sillytavern 8000) || (echo [DOWN] sillytavern 8000)

echo.
echo ================ api probe ================
curl.exe -s --max-time 6 http://127.0.0.1:5001/api/v1/model
echo.
curl.exe -s -o NUL -w "sillytavern http code: %%{http_code}" --max-time 6 http://127.0.0.1:8000/
echo.

echo.
echo ================ node ================
where node
echo.
echo ================ gpu ================
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader
echo.
pause
