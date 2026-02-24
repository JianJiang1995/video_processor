# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Video Analyzer Backend.
Compiles the FastAPI backend into a standalone executable.

Usage:
    cd video_stream_app
    pyinstaller build_backend.spec
"""
import os
import sys
from pathlib import Path

block_cipher = None

# Paths
APP_DIR = os.path.abspath('.')
BACKEND_DIR = os.path.join(APP_DIR, 'backend')

# Collect all backend Python files
backend_datas = []

# Include backend package
backend_datas.append((BACKEND_DIR, 'backend'))

# Include config.json
if os.path.exists(os.path.join(APP_DIR, 'config.json')):
    backend_datas.append((os.path.join(APP_DIR, 'config.json'), '.'))

# Include prompts directory if exists
prompts_dir = os.path.join(APP_DIR, 'prompts')
if os.path.exists(prompts_dir):
    backend_datas.append((prompts_dir, 'prompts'))

# Include .env from project root if exists
project_root = os.path.dirname(APP_DIR)
env_file = os.path.join(project_root, '.env')
if os.path.exists(env_file):
    backend_datas.append((env_file, '.'))

a = Analysis(
    ['backend_entry.py'],
    pathex=[APP_DIR],
    binaries=[],
    datas=backend_datas,
    hiddenimports=[
        # FastAPI & Uvicorn
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'starlette',
        'starlette.routing',
        'starlette.middleware',
        'starlette.middleware.cors',
        'pydantic',
        'pydantic_settings',
        'pydantic.deprecated.decorator',
        # Database
        'sqlalchemy',
        'sqlalchemy.dialects.mysql',
        'sqlalchemy.dialects.mysql.pymysql',
        'pymysql',
        # HTTP clients
        'httpx',
        'aiohttp',
        'aiofiles',
        'websockets',
        # Image processing
        'cv2',
        'PIL',
        'numpy',
        # WebRTC
        'aiortc',
        # Other
        'dotenv',
        'openai',
        'google.genai',
        'multipart',
        'json',
        # Backend modules
        'backend',
        'backend.main',
        'backend.config',
        'backend.database',
        'backend.database.models',
        'backend.database.crud',
        'backend.routers',
        'backend.routers.video',
        'backend.routers.analysis',
        'backend.routers.model',
        'backend.routers.webrtc',
        'backend.routers.voice',
        'backend.middleware',
        'backend.middleware.api_logger',
        'backend.services.video_processor',
        'backend.services.gpt_summarizer',
        'backend.services.sam2_service',
        'backend.services.tts_service',
        'backend.services.model_service',
        'backend.services.surgr1_client',
        'backend.services.sam3_client',
        'backend.services.glm_client',
        'backend.services.vlm_factory',
        'backend.services.gemini_client',
        'backend.services.tts_cosyvoice_client',
        'backend.services.mysql_service',
        'backend.services.frame_storage_service',
        'backend.services.frame_capture_service',
        'backend.services.video_export_service',
        'backend.services.conversation_service',
        'backend.services.summary_compressor',
        'backend.services.asr_funasr_client',
        'backend.services.temporal_analyze',
        'backend.services.async_task_queue',
        'backend.services.frame_buffer_service',
        'backend.services.sam3_consistency',
        'backend.services.glm_multimodal_verifier',
        'backend.services.analysis_logger',
        'backend.services.chat_audio_notifier',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'torch',       # Exclude heavy ML libs (they run as separate services)
        'torchvision',
        'transformers',
        'vllm',
        'ms_swift',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='video-analyzer-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Keep console for log output
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='video-analyzer-backend',
)
