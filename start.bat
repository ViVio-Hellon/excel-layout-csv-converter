@echo off
rem  --- keep this file in CP932 (Shift-JIS) with CRLF line endings ---
rem  Everything above the chcp line must stay ASCII. cmd.exe parses a .bat
rem  with the *console* code page, so the page has to be set before the
rem  first non-ASCII byte - including the bytes in these comments.
rem  In CP932 a trail byte can be 0x5C or 0x40, so U+8868 / U+30BD / U+30A1
rem  turn into a literal backslash or at-sign under a different code page.
rem  tests/test_launch_files.py enforces the encoding and this ordering.
chcp 932 >nul 2>&1
rem ===================================================================
rem  エクセル → CSV 変換器 診断起動
rem
rem  普段は Start.vbs を使ってください。こちらは「起動しないとき」に
rem  原因を見るためのもので、コンソールを開いたまま経過を表示します。
rem  (基盤仕様書 2.1「通常起動と原因調査用起動を分ける」)
rem
rem  引数はそのまま start_app.py へ渡します。よく使うのは次の2つ:
rem      start.bat              コンソールを開いたまま起動する
rem      start.bat --check      環境の確認だけして終わる
rem ===================================================================
setlocal

rem  共有フォルダ(\\サーバ\...)は現在地にできないので pushd を使う。
rem  pushd は一時的にドライブ文字を割り当てるため、共有に置いても動く
pushd "%~dp0" || (
    echo [エラー] このフォルダに移動できませんでした: %~dp0
    pause
    exit /b 1
)
title エクセル → CSV 変換器 - 診断起動 (この窓は閉じないでください)

python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [エラー] Python が見つかりません。
    echo.
    echo   https://www.python.org/downloads/ からインストールしてください。
    echo   インストーラの最初の画面で
    echo   「Add python.exe to PATH」に必ずチェックを入れてください。
    echo.
    goto :failed
)

echo ==========================================================
echo   エクセル → CSV 変換器 ── 診断起動
echo ==========================================================
python --version
echo.

python start_app.py --diagnose %*
set "CODE=%ERRORLEVEL%"

echo.
echo ----------------------------------------------------------
echo 終了コード: %CODE%
if "%CODE%"=="1" echo   1 = 実行環境が足りません(Python の版・vendor の有無・書込権限)
if "%CODE%"=="3" echo   3 = すでに起動しています(2つ目は起動しません)
if "%CODE%"=="4" echo   4 = ポートを使えません(別のアプリが使っています)
echo ----------------------------------------------------------
popd
endlocal
pause
exit /b 0

:failed
popd
endlocal
pause
exit /b 1
