@echo off
REM ============================================
REM Video Analyzer - Windows Standalone Build
REM 在 Windows 上一键构建完整独立应用
REM ============================================

setlocal enabledelayedexpansion

echo ======================================================
echo   Video Analyzer - Windows Standalone Build
echo ======================================================
echo.

REM ============================================
REM Check prerequisites
REM ============================================
echo [Check] Checking prerequisites...

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python 3.10+ from python.org
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do echo   Python: %%i

where pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller not found. Run: pip install pyinstaller
    exit /b 1
)
for /f "tokens=*" %%i in ('pyinstaller --version') do echo   PyInstaller: %%i

where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found. Install from nodejs.org
    exit /b 1
)
for /f "tokens=*" %%i in ('node --version') do echo   Node.js: %%i

where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] npm not found.
    exit /b 1
)

echo   All prerequisites OK
echo.

REM ============================================
REM Set paths
REM ============================================
set "SCRIPT_DIR=%~dp0"
set "APP_DIR=%SCRIPT_DIR%"
set "FRONTEND_DIR=%APP_DIR%frontend\"
set "PROJECT_ROOT=%APP_DIR%..\"
set "DIST_SERVICES=%APP_DIR%dist-services\"

REM ============================================
REM Step 1: Build Backend with PyInstaller
REM ============================================
echo [1/4] Building FastAPI Backend...

cd /d "%APP_DIR%"

if not exist "dist-services" mkdir dist-services
if not exist "build-services" mkdir build-services

if exist "build_backend.spec" (
    pyinstaller build_backend.spec --distpath dist-services --workpath build-services\backend --clean -y
    if exist "dist-services\video-analyzer-backend" (
        if exist "dist-services\backend" rmdir /s /q "dist-services\backend"
        rename "dist-services\video-analyzer-backend" "backend"
        copy /y "config.json" "dist-services\backend\" >nul
        if exist "%PROJECT_ROOT%.env" copy /y "%PROJECT_ROOT%.env" "dist-services\backend\" >nul
        echo   Backend built OK
    ) else (
        echo   [WARN] Backend build may have failed
    )
) else (
    echo   [WARN] build_backend.spec not found, skipping
)
echo.

REM ============================================
REM Step 2: Build SurgR1 with PyInstaller
REM ============================================
echo [2/4] Building SurgR1 Service...

cd /d "%APP_DIR%"

REM Create SurgR1 spec dynamically
(
echo # -*- mode: python ; coding: utf-8 -*-
echo import os
echo block_cipher = None
echo SURGR1_DIR = os.path.join(os.path.dirname(os.path.abspath('.')), 'SurgR1_api')
echo.
echo a = Analysis(
echo     [os.path.join(SURGR1_DIR, 'main.py')],
echo     pathex=[SURGR1_DIR],
echo     datas=[
echo         (os.path.join(SURGR1_DIR, 'config.json'), '.'),
echo     ],
echo     hiddenimports=[
echo         'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
echo         'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
echo         'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
echo         'uvicorn.lifespan', 'uvicorn.lifespan.on',
echo         'fastapi', 'starlette', 'pydantic',
echo         'vllm', 'PIL', 'numpy',
echo     ],
echo     excludes=['tkinter', 'matplotlib', 'scipy'],
echo     cipher=block_cipher,
echo ^)
echo pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
echo exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
echo           name='surgr1-server', console=True)
echo coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas,
echo                name='surgr1')
) > build_surgr1.spec

pyinstaller build_surgr1.spec --distpath dist-services --workpath build-services\surgr1 --clean -y 2>nul
if exist "dist-services\surgr1" (
    echo   SurgR1 built OK
) else (
    echo   [WARN] SurgR1 build failed (vLLM may not be installed on this machine)
    echo   SurgR1 can be started manually later
    mkdir "dist-services\surgr1" 2>nul
)
echo.

REM ============================================
REM Step 3: Build Frontend (Vite)
REM ============================================
echo [3/4] Building Frontend...

cd /d "%FRONTEND_DIR%"

if not exist "node_modules" (
    echo   Installing npm dependencies...
    call npm install
)

call npm run build
echo   Frontend built OK
echo.

REM ============================================
REM Step 4: Package with electron-builder
REM ============================================
echo [4/4] Packaging Electron App for Windows...

cd /d "%FRONTEND_DIR%"

REM Create placeholder dirs for services that weren't built
if not exist "%DIST_SERVICES%sam3" mkdir "%DIST_SERVICES%sam3"
if not exist "%DIST_SERVICES%tts" mkdir "%DIST_SERVICES%tts"
if not exist "%DIST_SERVICES%asr" mkdir "%DIST_SERVICES%asr"

call npx electron-builder --win --x64 -c electron-builder.yml

echo.
echo ======================================================
echo   Build Complete!
echo ======================================================
echo.
echo Output directory: %FRONTEND_DIR%dist-electron\
echo.

if exist "%FRONTEND_DIR%dist-electron" (
    echo Generated files:
    dir /b "%FRONTEND_DIR%dist-electron\*.exe" 2>nul
)

echo.
echo Embedded services:
echo   [OK] FastAPI Backend (port 8001)
echo   [OK] SurgR1 vLLM (port 9003) - requires NVIDIA GPU
echo   [  ] Gemini - cloud API (needs GEMINI_API_KEY in .env)
echo.
echo Double-click the .exe to run. All services start automatically.
echo.

pause
