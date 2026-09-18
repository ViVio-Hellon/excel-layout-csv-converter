@echo off
rem ===================================================================
rem  停止の入口(基盤仕様書 2.8)
rem
rem  まずアプリ自身へ正常終了を頼み、応答が無いときだけ控えたPIDを使います。
rem  変換中なら、止めてよいか聞きます。**python.exe をまとめて落とすような
rem  ことはしません** ── 同じPCで動く別のツールを巻き添えにしないためです。
rem ===================================================================
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul || set "PY=py -3"

%PY% process_manager.py stop
echo.
pause
endlocal
