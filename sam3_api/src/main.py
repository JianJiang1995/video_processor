"""
SAM3 FastAPI 服务 - 独立的图像分割API服务
"""
import os
from pathlib import Path
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# 导入模型和配置
from sam3_model import get_model, SAM3Model
from config_loader import get_visualization_config, get_server_config


class BBox(BaseModel):
    """Bounding Box 输入格式"""
    x1: int = Field(..., description="左上角 x 坐标")
    y1: int = Field(..., description="左上角 y 坐标")
    x2: int = Field(..., description="右下角 x 坐标")
    y2: int = Field(..., description="右下角 y 坐标")
    label: str = Field(default="object", description="物体标签")


class ColorConfig(BaseModel):
    """颜色配置"""
    label: str = Field(..., description="工具标签")
    color: List[int] = Field(..., description="BGR颜色 [B, G, R]")


class SegmentRequest(BaseModel):
    """分割请求"""
    image_input_path: str = Field(..., description="输入图片的绝对路径")
    bboxes: List[BBox] = Field(..., description="Bounding boxes 列表")
    output_dir: Optional[str] = Field(None, description="输出目录（可选，默认使用图片同目录）")
    alpha: Optional[float] = Field(None, ge=0.0, le=1.0, description="mask透明度 (0.0-1.0, 默认从配置读取)")
    contour_thickness: Optional[int] = Field(None, ge=0, le=10, description="mask边缘粗细 (默认从配置读取)")
    colors: Optional[List[ColorConfig]] = Field(None, description="自定义颜色配置")
    return_base64: Optional[bool] = Field(None, description="是否返回 base64 编码的图片 (默认从配置读取)")


class MaskInfo(BaseModel):
    """Mask 信息"""
    obj_id: int
    label: str
    area: int


class SegmentResponse(BaseModel):
    """分割响应"""
    success: bool
    output_path: str = Field(..., description="带mask的结果图片路径")
    num_objects: int = Field(..., description="检测到的物体数量")
    masks: List[MaskInfo] = Field(..., description="各mask的详细信息")
    message: str = ""
    image_base64: Optional[str] = Field(None, description="Base64 编码的结果图片 (仅当 return_base64=true 时返回)")
    image_format: Optional[str] = Field(None, description="图片格式 (如 'png')")


class ConfigResponse(BaseModel):
    """配置响应"""
    visualization: dict
    server: dict


# 输出目录（用于图片下载）
OUTPUT_BASE_DIR = Path(__file__).parent.parent / "output"


# 启动时预加载模型
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    print("=" * 60)
    print("SAM3 FastAPI 服务启动中...")
    print("正在加载模型（首次加载可能需要较长时间）...")
    
    # 显示配置信息
    viz_config = get_visualization_config()
    print(f"默认配置: alpha={viz_config.get('alpha')}, "
          f"contour_thickness={viz_config.get('contour_thickness')}, "
          f"return_base64={viz_config.get('return_base64')}")
    print("=" * 60)
    
    try:
        model = get_model()
        print("=" * 60)
        print("✅ SAM3 模型加载成功！服务就绪。")
        print("=" * 60)
    except Exception as e:
        print("=" * 60)
        print(f"⚠️ 警告: SAM3 模型加载失败: {e}")
        print("服务将在收到请求时尝试重新加载模型")
        print("=" * 60)
    
    # 创建输出目录
    OUTPUT_BASE_DIR.mkdir(parents=True, exist_ok=True)
    
    yield
    
    # 关闭时清理
    print("SAM3 服务关闭中...")


# 创建FastAPI应用
app = FastAPI(
    title="SAM3 分割服务",
    description="使用 SAM3 模型进行图像分割的独立 API 服务",
    version="1.1.0",
    lifespan=lifespan
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    """服务信息"""
    return {
        "service": "SAM3 Segmentation API",
        "version": "1.1.0",
        "status": "running",
        "endpoints": {
            "POST /sam3": "使用 bounding boxes 进行图像分割",
            "GET /config": "获取当前配置",
            "GET /download/{filename}": "下载结果图片"
        }
    }


@app.get("/health")
def health_check():
    """健康检查端点"""
    return {"status": "healthy"}


@app.get("/config", response_model=ConfigResponse)
def get_config():
    """获取当前配置"""
    return ConfigResponse(
        visualization=get_visualization_config(),
        server=get_server_config()
    )


@app.get("/download/{filename}")
def download_image(filename: str):
    """
    下载结果图片
    
    其他机器可以通过这个端点获取分割后的图片
    
    Args:
        filename: 图片文件名 (如 "image_masked.png")
    """
    # 在输出目录中查找文件
    file_path = OUTPUT_BASE_DIR / filename
    
    if not file_path.exists():
        # 也检查文件名是否带有完整路径的情况
        raise HTTPException(
            status_code=404,
            detail=f"文件不存在: {filename}. 请确保使用正确的文件名。"
        )
    
    return FileResponse(
        path=str(file_path),
        media_type="image/png",
        filename=filename
    )


@app.post("/sam3", response_model=SegmentResponse)
def segment_image(request: SegmentRequest):
    """
    使用 SAM3 进行图像分割
    
    - 输入：图片路径和 bounding boxes
    - 输出：带 mask 可视化的结果图片路径，可选返回 base64 编码
    
    可选参数:
    - alpha: mask透明度 (0.0-1.0, 默认从配置读取)
    - contour_thickness: 边缘粗细 (默认从配置读取)
    - colors: 自定义颜色 [{"label": "grasper", "color": [0, 255, 0]}, ...]
    - return_base64: 是否返回 base64 编码的图片 (默认从配置读取)
    
    示例请求:
    ```
    curl -X POST http://127.0.0.1:8000/sam3 \\
         -H "Content-Type: application/json" \\
         -d '{
               "image_input_path": "/path/to/image.png",
               "bboxes": [{"x1": 100, "y1": 50, "x2": 300, "y2": 200, "label": "forceps"}],
               "alpha": 0.4,
               "contour_thickness": 2,
               "return_base64": true
             }'
    ```
    """
    # 验证输入图片
    if not os.path.exists(request.image_input_path):
        raise HTTPException(
            status_code=400,
            detail=f"图片文件不存在: {request.image_input_path}"
        )
    
    # 验证bboxes
    if not request.bboxes:
        raise HTTPException(
            status_code=400,
            detail="bboxes 列表不能为空"
        )
    
    # 转换bboxes格式
    bboxes = [
        {
            "x1": bbox.x1,
            "y1": bbox.y1,
            "x2": bbox.x2,
            "y2": bbox.y2,
            "label": bbox.label
        }
        for bbox in request.bboxes
    ]
    
    try:
        # 解析颜色配置
        colors_dict = None
        if request.colors:
            colors_dict = {
                c.label: tuple(c.color) for c in request.colors
            }
        
        # 获取模型并执行分割
        model = get_model()
        result = model.segment_with_bboxes(
            image_path=request.image_input_path,
            bboxes=bboxes,
            output_dir=request.output_dir,
            alpha=request.alpha,
            contour_thickness=request.contour_thickness,
            colors=colors_dict,
            return_base64=request.return_base64
        )
        
        return SegmentResponse(
            success=True,
            output_path=result["output_path"],
            num_objects=result["num_objects"],
            masks=[MaskInfo(**m) for m in result["masks"]],
            message="分割成功",
            image_base64=result.get("image_base64"),
            image_format=result.get("image_format")
        )
        
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"分割失败: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    
    server_config = get_server_config()
    port = int(os.environ.get("PORT", server_config.get("port", 8000)))
    host = os.environ.get("HOST", server_config.get("host", "0.0.0.0"))
    
    print(f"启动 SAM3 FastAPI 服务: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
