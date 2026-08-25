@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "MODEL_DIR=D:\loadmode"
set "SERVER_EXE=%MODEL_DIR%\llama-b9711-bin-win-cuda-12.4-x64\llama-server.exe"
set "CONTEXT_SIZE=128000"
set "FIT_MARGIN_MIB=768"

if not exist "%MODEL_DIR%\" (
    echo [错误] 模型目录不存在：%MODEL_DIR%
    pause
    exit /b 1
)

if not exist "%SERVER_EXE%" (
    echo [错误] llama-server.exe 不存在：
    echo %SERVER_EXE%
    pause
    exit /b 1
)

pushd "%MODEL_DIR%"
if errorlevel 1 (
    echo [错误] 无法进入模型目录：%MODEL_DIR%
    pause
    exit /b 1
)

:main_menu
cls
echo ==============================================================
echo 本地模型启动器 - 128K 上下文优化版
echo GPU：RTX 4060 Laptop 8GB   模式：单并发 + Q8 KV + 自动显存拟合
echo ==============================================================
echo.
echo 可用模型（已排除文件名中含 35B 的模型）：
echo --------------------------------------------------------------

set "count=0"
for /f "delims=" %%I in ('dir /b /a-d "*.gguf" 2^>nul') do (
    set "candidate=%%I"
    if /i "!candidate:35B=!"=="!candidate!" (
        set /a count+=1
        set "model_!count!=%%I"
        echo [!count!] %%I
    )
)

if !count! equ 0 (
    echo.
    echo [错误] %MODEL_DIR% 中没有可启动的非 35B GGUF 模型。
    popd
    pause
    exit /b 1
)

echo --------------------------------------------------------------
echo.

:select_loop
set "choice="
set /p "choice=请选择模型（1-!count!，Q=退出）："
set "choice=!choice: =!"

if not defined choice (
    echo [提示] 输入不能为空。
    goto select_loop
)

if /i "!choice!"=="Q" (
    popd
    exit /b 0
)

echo(!choice!| %SystemRoot%\System32\findstr.exe /r /x "[0-9][0-9]*" >nul
if errorlevel 1 (
    echo [提示] 请输入有效的数字。
    goto select_loop
)

set "selected=!model_%choice%!"
if not defined selected (
    echo [提示] 编号超出范围，请输入 1 到 !count!。
    goto select_loop
)

set "MTP_FLAGS="
if /i "!selected!"=="Qwythos-9B-Claude-Mythos-5-1M-MTP-Q5_K_M.gguf" (
    set "MTP_FLAGS=--spec-type draft-mtp --spec-draft-n-max 2"
)

cls
echo ==============================================================
echo 即将启动：!selected!
echo 上下文：!CONTEXT_SIZE!
echo 并发槽：1
echo KV 缓存：Q8_0
echo 显存策略：自动拟合，预留 !FIT_MARGIN_MIB! MiB
if defined MTP_FLAGS echo MTP：已启用原生 MTP 推测解码（草稿长度 2）
echo ==============================================================
echo.
echo 服务地址：http://127.0.0.1:8080
echo 按 Ctrl+C 可停止服务器。
echo.

"%SERVER_EXE%" -m "!selected!" -c !CONTEXT_SIZE! -ngl auto -np 1 -fa on -ctk q8_0 -ctv q8_0 --fit on --fit-target !FIT_MARGIN_MIB! !MTP_FLAGS! --host 127.0.0.1 --port 8080
set "server_exit=!errorlevel!"

echo.
if "!server_exit!"=="0" (
    echo [系统] 服务器已停止。
) else (
    echo [错误] 服务器异常退出，退出码：!server_exit!
    echo 请检查上方日志中的显存不足、模型格式或端口占用信息。
)

popd
pause
exit /b !server_exit!
