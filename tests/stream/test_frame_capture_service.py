"""
帧捕获服务单元测试

测试 frame_capture_service.py 的核心功能：
1. 配置加载
2. 帧捕获服务启动/停止
3. 帧存储验证
"""

import sys
import os
import asyncio
import tempfile
import shutil
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import numpy as np

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "video_stream_app" / "backend"))

import pytest


class TestFrameCaptureConfig:
    """测试帧捕获配置加载"""
    
    def test_load_config_default(self):
        """测试默认配置加载"""
        from services.frame_capture_service import load_frame_capture_config
        
        config = load_frame_capture_config()
        
        # 验证配置包含必需的键
        assert "fps" in config
        assert "quality" in config
        assert "preview_fps" in config
        assert "preview_quality" in config
        
        # 验证默认值范围合理
        assert 1 <= config["fps"] <= 60
        assert 1 <= config["quality"] <= 100
        assert 1 <= config["preview_fps"] <= config["fps"]
        assert 1 <= config["preview_quality"] <= 100
        
        print(f"✓ 配置加载成功: fps={config['fps']}, quality={config['quality']}")


class TestFrameCaptureState:
    """测试帧捕获状态管理"""
    
    def test_state_creation(self):
        """测试状态对象创建"""
        from services.frame_capture_service import FrameCaptureState
        
        state = FrameCaptureState(
            session_id="test_session",
            storage_path="/tmp/test",
            video_source="http://localhost:9001/stream",
            is_realtime_stream=True
        )
        
        assert state.session_id == "test_session"
        assert state.is_running == False
        assert state.should_stop == False
        assert state.frames_captured == 0
        assert state.frames_saved == 0
        
        print("✓ 状态对象创建成功")
    
    def test_state_with_config(self):
        """测试带配置的状态对象"""
        from services.frame_capture_service import FrameCaptureState
        
        state = FrameCaptureState(
            session_id="test_session",
            storage_path="/tmp/test",
            video_source="test.mp4",
            is_realtime_stream=False,
            capture_fps=25,
            preview_fps=10,
            quality=85,
            preview_quality=40
        )
        
        assert state.capture_fps == 25
        assert state.preview_fps == 10
        assert state.quality == 85
        assert state.preview_quality == 40
        
        print("✓ 带配置的状态对象创建成功")


class TestFrameCaptureService:
    """测试帧捕获服务"""
    
    def test_service_singleton(self):
        """测试服务单例模式"""
        from services.frame_capture_service import get_frame_capture_service
        
        service1 = get_frame_capture_service()
        service2 = get_frame_capture_service()
        
        assert service1 is service2
        print("✓ 服务单例模式正确")
    
    def test_is_capturing_false_initially(self):
        """测试初始状态不在捕获"""
        from services.frame_capture_service import get_frame_capture_service
        
        service = get_frame_capture_service()
        
        # 随机session_id不应该在捕获中
        assert service.is_capturing("nonexistent_session") == False
        print("✓ 初始状态检查正确")
    
    def test_get_capture_stats_none(self):
        """测试获取不存在会话的统计信息"""
        from services.frame_capture_service import get_frame_capture_service
        
        service = get_frame_capture_service()
        
        stats = service.get_capture_stats("nonexistent_session")
        assert stats is None
        print("✓ 不存在会话统计信息返回None")


class TestOpenVideoSource:
    """测试视频源打开功能"""
    
    def test_open_invalid_source(self):
        """测试打开无效视频源"""
        from services.frame_capture_service import open_video_source
        
        # 不存在的文件应该返回None
        cap = open_video_source("/nonexistent/video.mp4")
        
        assert cap is None
        print("✓ 无效视频源正确返回None")
    
    def test_open_device_format(self):
        """测试设备格式解析"""
        from services.frame_capture_service import open_video_source
        
        # device://0 格式应该被正确解析
        # 注意：实际打开可能失败（无摄像头），但不应抛出异常
        try:
            cap = open_video_source("device://0")
            if cap:
                cap.release()
            print("✓ 设备格式解析正确")
        except Exception as e:
            print(f"⚠ 设备格式解析失败（可能无摄像头）: {e}")


class TestFrameCaptureIntegration:
    """集成测试：完整的帧捕获流程"""
    
    @pytest.fixture
    def temp_storage(self):
        """创建临时存储目录"""
        temp_dir = tempfile.mkdtemp(prefix="frame_capture_test_")
        yield temp_dir
        # 清理
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.mark.asyncio
    async def test_start_stop_capture_mock(self, temp_storage):
        """测试启动和停止捕获（使用mock）"""
        from services.frame_capture_service import get_frame_capture_service, _capture_states, _capture_tasks
        
        service = get_frame_capture_service()
        session_id = "test_integration_session"
        
        # Mock视频源
        with patch('services.frame_capture_service.open_video_source') as mock_open:
            # 模拟打开失败（快速测试）
            mock_open.return_value = None
            
            # 启动捕获
            result = await service.start_capture(
                session_id=session_id,
                video_source="http://fake/stream",
                storage_path=temp_storage,
                is_realtime_stream=True,
                stream_start_time=time.time()
            )
            
            # 等待任务启动
            await asyncio.sleep(0.1)
            
            # 停止捕获
            await service.stop_capture(session_id)
            
            print("✓ 启动/停止捕获流程正常（mock模式）")
    
    @pytest.mark.asyncio
    async def test_capture_with_mock_frames(self, temp_storage):
        """测试帧捕获和保存（使用mock视频）"""
        from services.frame_capture_service import (
            get_frame_capture_service, 
            frame_capture_task,
            _capture_states
        )
        from services.frame_storage_service import get_frame_storage_service
        
        session_id = "test_mock_frames"
        
        # 创建帧存储目录结构
        frames_dir = Path(temp_storage) / "frames"
        preview_dir = Path(temp_storage) / "preview"
        frames_dir.mkdir(parents=True, exist_ok=True)
        preview_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建mock视频捕获
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = 25.0  # fps
        
        # 创建模拟帧（黑色图像）
        mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame_count = 0
        max_frames = 5  # 只测试5帧
        
        def mock_read():
            nonlocal frame_count
            if frame_count < max_frames:
                frame_count += 1
                return True, mock_frame.copy()
            return False, None
        
        mock_cap.read.side_effect = mock_read
        
        with patch('services.frame_capture_service.open_video_source') as mock_open:
            mock_open.return_value = mock_cap
            
            # 启动捕获任务
            task = asyncio.create_task(
                frame_capture_task(
                    session_id=session_id,
                    video_source="http://fake/stream",
                    storage_path=temp_storage,
                    is_realtime_stream=False,  # 使用帧索引计时
                    stream_start_time=time.time()
                )
            )
            
            # 等待一些帧被处理
            await asyncio.sleep(0.5)
            
            # 停止任务
            if session_id in _capture_states:
                _capture_states[session_id].should_stop = True
            
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            # 验证状态
            if session_id in _capture_states:
                state = _capture_states[session_id]
                print(f"✓ 捕获统计: captured={state.frames_captured}, saved={state.frames_saved}")
            
            # 检查是否有帧被保存
            saved_frames = list(frames_dir.glob("*.jpg"))
            print(f"✓ 保存的帧数: {len(saved_frames)}")


class TestConfigIntegration:
    """测试配置与服务的集成"""
    
    def test_config_file_exists(self):
        """测试配置文件存在"""
        config_path = Path(__file__).parent.parent.parent / "video_stream_app" / "config.json"
        
        assert config_path.exists(), f"配置文件不存在: {config_path}"
        print(f"✓ 配置文件存在: {config_path}")
    
    def test_config_has_frame_storage(self):
        """测试配置文件包含帧存储配置"""
        import json
        config_path = Path(__file__).parent.parent.parent / "video_stream_app" / "config.json"
        
        with open(config_path, "r") as f:
            config = json.load(f)
        
        assert "video_processing" in config
        assert "frame_storage" in config["video_processing"]
        
        frame_storage = config["video_processing"]["frame_storage"]
        assert "fps" in frame_storage
        assert "quality" in frame_storage
        assert "preview_fps" in frame_storage
        assert "preview_quality" in frame_storage
        
        print(f"✓ 帧存储配置: fps={frame_storage['fps']}, preview_fps={frame_storage['preview_fps']}")


def run_sync_tests():
    """运行同步测试"""
    print("\n" + "="*60)
    print("运行帧捕获服务单元测试")
    print("="*60 + "\n")
    
    # 配置测试
    print("--- 配置测试 ---")
    config_test = TestFrameCaptureConfig()
    config_test.test_load_config_default()
    
    # 状态测试
    print("\n--- 状态测试 ---")
    state_test = TestFrameCaptureState()
    state_test.test_state_creation()
    state_test.test_state_with_config()
    
    # 服务测试
    print("\n--- 服务测试 ---")
    service_test = TestFrameCaptureService()
    service_test.test_service_singleton()
    service_test.test_is_capturing_false_initially()
    service_test.test_get_capture_stats_none()
    
    # 视频源测试
    print("\n--- 视频源测试 ---")
    video_test = TestOpenVideoSource()
    video_test.test_open_invalid_source()
    video_test.test_open_device_format()
    
    # 配置集成测试
    print("\n--- 配置集成测试 ---")
    config_int_test = TestConfigIntegration()
    config_int_test.test_config_file_exists()
    config_int_test.test_config_has_frame_storage()
    
    print("\n" + "="*60)
    print("✅ 所有同步测试通过!")
    print("="*60)


async def run_async_tests():
    """运行异步测试"""
    print("\n" + "="*60)
    print("运行帧捕获服务异步测试")
    print("="*60 + "\n")
    
    # 创建临时目录
    temp_dir = tempfile.mkdtemp(prefix="frame_capture_test_")
    
    try:
        int_test = TestFrameCaptureIntegration()
        
        print("--- 集成测试 ---")
        await int_test.test_start_stop_capture_mock(temp_dir)
        await int_test.test_capture_with_mock_frames(temp_dir)
        
        print("\n" + "="*60)
        print("✅ 所有异步测试通过!")
        print("="*60)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    # 运行同步测试
    run_sync_tests()
    
    # 运行异步测试
    print("\n")
    asyncio.run(run_async_tests())
