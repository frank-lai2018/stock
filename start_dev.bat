@echo off
chcp 65001 >nul
REM ==========================================================================
REM  twstock 一鍵啟動前後端（開發模式）
REM   後端 FastAPI/uvicorn  -> http://localhost:8000 （/docs 有 API 文件）
REM   前端 Vue/Vite         -> http://localhost:5173 （dev proxy /api -> :8000）
REM  各自開一個視窗執行、即時看 log；關掉該視窗即停止該服務。
REM  路徑用 %~dp0 自動定位（bat 放在專案根目錄，換機不用改）。
REM  首次使用前端需先安裝相依：cd stockselect\frontend 然後 npm install
REM ==========================================================================
set ROOT=%~dp0

echo 啟動後端 (uvicorn :8000) ...
start "twstock backend :8000" cmd /k "cd /d %ROOT%stockselect\backend && .venv\Scripts\python -m uvicorn app.main:app --reload --port 8000"

echo 啟動前端 (vite :5173) ...
start "twstock frontend :5173" cmd /k "cd /d %ROOT%stockselect\frontend && npm run dev"

echo 等待服務啟動後開啟瀏覽器 ...
timeout /t 5 /nobreak >nul
start "" http://localhost:5173

echo.
echo 前後端已在各自視窗啟動。關閉那兩個視窗即可停止服務。
echo （本視窗可直接關閉）
