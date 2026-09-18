@echo off
rem  --- keep this file in CP932 (Shift-JIS) with CRLF line endings ---
rem  Keep everything above the chcp line ASCII; see start.bat for why.
chcp 932 >nul 2>&1
rem ===================================================================
rem  エクセル → CSV 変換器 停止
rem
rem  このアプリだけを止めます。同じPCで動く他の Python アプリは
rem  影響を受けません(基盤仕様書 2.8)。
rem
rem      stop.bat            止める(変換中なら聞きます)
rem      stop.bat --status   状態を見るだけ
rem      stop.bat --force    変換中でも聞かずに止める
rem ===================================================================
setlocal

rem  共有フォルダに置かれていても動くよう pushd を使う(start.bat と同じ)
pushd "%~dp0" || (
    echo [エラー] このフォルダに移動できませんでした: %~dp0
    pause
    exit /b 1
)
title エクセル → CSV 変換器 - 停止

python --version >nul 2>&1
if errorlevel 1 (
    echo [エラー] Python が見つかりません。
    goto :failed
)

python process_manager.py %*
if errorlevel 1 (
    echo.
    echo 止められなかったものがあります。上のメッセージを確認してください。
    echo 変換中のときは、中断してよければ次を実行してください:
    echo     stop.bat --force
    echo.
    goto :failed
)
popd
endlocal
exit /b 0

:failed
pause
popd
endlocal
exit /b 1
