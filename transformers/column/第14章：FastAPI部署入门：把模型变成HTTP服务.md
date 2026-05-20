# 第14章：FastAPI 部署入门：把模型变成 HTTP 服务

## 1 项目背景

### 业务场景

算法团队训练好客服工单分类模型后，产品经理催着上线："下周一就是双十一大促了，客服工单量会翻三倍，必须在大促前把自动分派功能上线！"

后端工程师小李的任务是将模型集成到现有的 Spring Boot 客服系统中。经过讨论，团队决定采用 Python 微服务方案——用 FastAPI 写一个独立的推理服务，暴露 HTTP 接口，Java 后端通过 HTTP 调用。

然而上线第二天就出了问题：大促期间并发工单量暴增，推理服务的 CPU 使用率达 95%，P95 延迟从 50ms 飙升到 3 秒，甚至有几个请求直接超时报 504。排查发现服务默认用单 worker 模式，同时只处理一个请求，其他请求在排队。

更糟糕的是，Docker 镜像打包了完整的 PyTorch（2GB），镜像拉取耗时 15 分钟，滚动更新期间服务几乎不可用。

### 痛点放大

从"能推理"到"能上线"，是 ML 工程师最容易被忽视的一公里：

1. **并发处理能力**：notebook 里一次推理一个样本没问题，但生产环境每秒可能有几十个请求同时到达。单线程同步推理会导致请求排队——前一个请求处理 100ms，第 10 个请求就要等 1 秒。
2. **服务稳定性**：模型加载失败、输入异常、超时——线上任何问题都应该有明确的错误码和降级策略，而不是抛一个 Python traceback。
3. **镜像体积**：完整的 PyTorch + Transformers 超过 2GB，加上模型文件动辄 3-5GB，每次部署都像搬家一样沉重。

```
Notebook 推理:  import → predict → print  (1 人用, 没问题)
       ↓ 工程化
生产服务:   并发处理 + 超时控制 + 错误处理 + 健康检查 + Docker 瘦身
```

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周一早上 9:30，War Room。大促第一天，监控大屏上推理服务的延迟曲线一路飙升。运维小王满头大汗。

---

**小胖**（看着红色告警）:"这曲线比我的体重曲线还夸张。咋回事？模型昨天不是好好的吗？"

**小李**:"昨天压测的时候 QPS 才 5，今天双十一直接 50 QPS。我们的 FastAPI 服务是单 worker 同步推理，请求一个接一个排队，前面的没处理完后面的就得等。"

**小胖**:"那你不能多开几个窗口吗？食堂中午人多了也是临时多开两个窗口，不就不用排长队了。"

**小白**:"FastAPI 确实支持多 worker——启动时 `uvicorn main:app --workers 4` 就开 4 个进程。但这里有个坑：每个 worker 是独立的进程，会各自加载一份模型到内存。4 个 worker × 400MB 模型 ≈ 1.6GB 内存，如果 GPU 只有一块，显存也会被 4 个 worker 瓜分。"

**大师**（走进来关上门）:"多 worker 是方案之一，但不是最优解。让我把生产级推理服务的架构讲清楚。

**第一层：Uvicorn worker 配置。**

Uvicorn 有两种并发模式：
- `--workers N`：多进程模式，每个进程独立加载模型 → 内存/显存 × N，适合 CPU 推理
- 单 worker + 异步处理：利用 FastAPI 的 `async def` + 线程池，模型作为全局单例加载一次 → 内存只用 1 份，GPU 推理必需

**第二层：请求队列与批处理。**

对 GPU 推理来说，最理想的方式是**动态批处理**——把短时间内到达的多个请求合并为一个 batch 一起推理，充分利用 GPU 并行能力。比如 10 个请求各自推理耗时 10ms × 10 = 100ms，合并为一个 batch 只需要 15ms。

简单的实现是用 `asyncio.Queue` 做请求缓冲——请求进来不是立即推理，而是先放入队列，由一个后台 worker 每隔 50ms 取出一批请求批量推理。

**第三层：Docker 镜像瘦身。**

PyTorch 完整安装包约 2GB，但推理服务不需要所有组件。瘦身三板斧：
1. 用 `python:3.10-slim` 作基础镜像
2. PyTorch 只装 CPU 版（如果不用 GPU）：`pip install torch --index-url ...whl/cpu`
3. 模型文件不进镜像，用 volume 挂载。这样更新模型只需替换 volume，不需要重新构建镜像。

这样镜像可以从 5GB 瘦到 500MB。"

**小白**:"那服务的错误处理和监控呢？"

**大师**:"三个必须实现的能力：
- `/health` 健康检查接口：返回模型是否加载完毕、内存占用等，供 K8s liveness probe 用
- 统一错误响应：输入为空返回 400，模型未就绪返回 503，推理超时返回 504——错误码对调用方有明确语义
- 结构化日志：每条请求记录 trace_id、input_length、latency、predicted_label、error 等字段，方便排查和监控"

**技术映射总结**：
- 多 worker = 食堂多窗口，内存/显存 × N
- 异步 + 批处理 = 拼车出行，多个请求共享一次推理
- Docker 瘦身 = 只带必需品旅行，PyTorch CPU 版 + volume 挂载模型
- /health + 统一错误码 = 服务的"体检报告"和"报修流程"

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install fastapi>=0.110.0 uvicorn[standard]>=0.27.0
pip install pydantic>=2.0.0  # 请求/响应模型
pip install python-multipart  # 表单上传支持（可选）
```

### 3.2 FastAPI 推理服务

#### 目标

将第 13 章的 `ModelInference` 封装为生产级 HTTP 服务。

```python
# main.py
"""FastAPI 推理服务 —— 文本分类"""

import time
import logging
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from inference_package import ModelInference

# ===== 日志配置 =====
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ===== 请求/响应模型 =====
class PredictRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000,
                      description="待分类的文本")
    threshold: float = Field(0.5, ge=0.0, le=1.0,
                             description="置信度阈值，低于此值转人工")


class PredictResponse(BaseModel):
    label: str
    confidence: float
    needs_review: bool
    latency_ms: float
    trace_id: str


class BatchPredictRequest(BaseModel):
    texts: List[str] = Field(..., min_items=1, max_items=100,
                             description="批量文本（最多100条）")


class BatchPredictResponse(BaseModel):
    results: List[PredictResponse]
    total_latency_ms: float


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_path: str
    device: str
    uptime_seconds: float


# ===== 全局模型实例 =====
_model: Optional[ModelInference] = None
_start_time: float = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时加载模型，关闭时清理"""
    global _model, _start_time

    logger.info("正在加载模型...")
    _model = ModelInference("./models/sentiment_minimal")
    _start_time = time.time()
    logger.info("模型加载完成，服务就绪")

    yield  # 应用运行中

    logger.info("服务关闭")
    _model = None


app = FastAPI(
    title="客服工单分类服务",
    description="基于 Transformers 的文本分类推理服务",
    version="1.0.0",
    lifespan=lifespan,
)


# ===== 中间件：请求日志 =====
@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = str(uuid.uuid4())[:8]
    request.state.trace_id = trace_id

    start = time.time()
    response = await call_next(request)
    elapsed = (time.time() - start) * 1000

    logger.info(
        f"trace_id={trace_id} method={request.method} "
        f"path={request.url.path} status={response.status_code} "
        f"latency={elapsed:.1f}ms"
    )
    response.headers["X-Trace-ID"] = trace_id
    return response


# ===== 健康检查 =====
@app.get("/health", response_model=HealthResponse)
async def health():
    global _model, _start_time
    uptime = time.time() - _start_time if _start_time else 0

    return HealthResponse(
        status="healthy" if _model else "not_ready",
        model_loaded=_model is not None,
        model_path=_model.model_path if _model else "",
        device=_model.device if _model else "",
        uptime_seconds=round(uptime, 1),
    )


# ===== 单条预测 =====
@app.post("/predict", response_model=PredictResponse)
async def predict(request: PredictRequest, req: Request):
    """单条文本分类"""
    global _model
    if not _model:
        raise HTTPException(status_code=503, detail="模型未加载完成")

    try:
        result = _model.predict(
            text=request.text,
            confidence_threshold=request.threshold,
        )
    except Exception as e:
        logger.error(f"推理失败: {e}")
        raise HTTPException(status_code=500, detail=f"推理错误: {str(e)}")

    return PredictResponse(
        label=result.label,
        confidence=result.confidence,
        needs_review=result.needs_review,
        latency_ms=result.latency_ms,
        trace_id=getattr(req.state, "trace_id", "unknown"),
    )


# ===== 批量预测 =====
@app.post("/batch_predict", response_model=BatchPredictResponse)
async def batch_predict(request: BatchPredictRequest, req: Request):
    """批量文本分类（最多100条）"""
    global _model
    if not _model:
        raise HTTPException(status_code=503, detail="模型未加载完成")

    start = time.time()
    try:
        results = _model.predict_batch(request.texts)
    except Exception as e:
        logger.error(f"批量推理失败: {e}")
        raise HTTPException(status_code=500, detail=f"推理错误: {str(e)}")

    total_latency = (time.time() - start) * 1000
    trace_id = getattr(req.state, "trace_id", "unknown")

    response_items = [
        PredictResponse(
            label=r.label,
            confidence=r.confidence,
            needs_review=r.needs_review,
            latency_ms=0,
            trace_id=trace_id,
        )
        for r in results
    ]

    return BatchPredictResponse(
        results=response_items,
        total_latency_ms=round(total_latency, 1),
    )


# ===== 全局异常处理 =====
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status_code": exc.status_code},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"未捕获异常: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "服务器内部错误", "status_code": 500},
    )


# ===== 启动 =====
if __name__ == "__main__":
    import uvicorn
    # 单 worker + 异步处理（GPU 推理推荐）
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        workers=1,        # GPU 推理用单 worker
        log_level="info",
    )
```

### 3.3 测试服务

```bash
# 启动服务
python main.py

# 或者用 uvicorn 命令行启动
# uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

```bash
# ===== curl 测试 =====

# 1. 健康检查
curl http://localhost:8000/health
# 响应: {"status":"healthy","model_loaded":true,...}

# 2. 单条预测
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "这个产品质量太差了，我要投诉！"}'
# 响应: {"label":"投诉","confidence":0.8732,"needs_review":false,...}

# 3. 空文本测试（应返回 422 验证错误）
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": ""}'
# 响应: 422 Unprocessable Entity

# 4. 批量预测
curl -X POST http://localhost:8000/batch_predict \
  -H "Content-Type: application/json" \
  -d '{"texts":["产品很好","太差了","什么时候发货"]}'
# 响应: {"results":[...],"total_latency_ms":68.3}

# 5. 压测（用 ab 或 wrk）
# ab -n 100 -c 10 -p data.json -T application/json http://localhost:8000/predict
```

### 3.4 Docker 化

```dockerfile
# Dockerfile
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（减小镜像体积）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（CPU 版本 PyTorch 更小）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --index-url https://download.pytorch.org/whl/cpu

# 复制推理代码
COPY main.py inference_package.py ./

# 模型文件通过 volume 挂载，不进镜像
# docker run -v /path/to/models:/app/models ...

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

# 启动服务
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

```txt
# requirements.txt
fastapi==0.110.0
uvicorn[standard]==0.27.0
pydantic==2.5.0
transformers==4.44.0
torch==2.2.0  # CPU 版通过 --index-url 指定
```

```bash
# 构建镜像
docker build -t ticket-classifier:latest .

# 运行（模型通过 volume 挂载）
docker run -d \
  --name classifier \
  -p 8000:8000 \
  -v $(pwd)/models:/app/models \
  ticket-classifier:latest

# 查看日志
docker logs -f classifier

# 验证
curl http://localhost:8000/health
```

### 3.5 性能压测

```bash
# 安装压测工具
# brew install wrk  (macOS)
# apt install wrk   (Linux)

# write data.json for POST
echo '{"text":"这个产品质量很好推荐给大家"}' > /tmp/data.json

# 压测 30s，10 并发
wrk -t2 -c10 -d30s --latency \
  -s post.lua \
  http://localhost:8000/predict
```

```lua
-- post.lua (wrk POST 脚本)
wrk.method = "POST"
wrk.body   = '{"text":"这个产品质量很好，推荐给大家购买使用"}'
wrk.headers["Content-Type"] = "application/json"
```

### 3.6 测试验证

```python
# test_api.py
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

class TestAPI:
    def test_health(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert "status" in response.json()

    def test_predict_success(self):
        response = client.post(
            "/predict",
            json={"text": "这个产品不错"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "label" in data
        assert "confidence" in data

    def test_predict_empty_text(self):
        response = client.post(
            "/predict",
            json={"text": ""},
        )
        assert response.status_code == 422

    def test_batch_predict(self):
        response = client.post(
            "/batch_predict",
            json={"texts": ["好", "差", "一般"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 3

    def test_trace_id_header(self):
        response = client.post(
            "/predict",
            json={"text": "测试"},
        )
        assert "X-Trace-ID" in response.headers
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **FastAPI** | 异步支持好，自动生成 OpenAPI 文档，Pydantic 验证输入 | async 编程模型对 ML 从业者有学习曲线 |
| **Uvicorn** | 轻量高性能，支持多 worker 和异步 | 多 worker 模式下每个进程独立加载模型，内存占用翻倍 |
| **Docker 化** | 环境一致，可移植，支持 K8s 编排 | 镜像体积优化需要额外工作 |
| **RESTful 设计** | 通用性强，任何语言都能调用 | 大文本/批量推理时 HTTP 请求体可能很大 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 小团队快速部署 | FastAPI + Uvicorn 1 worker + Docker |
| GPU 推理服务 | 单 worker + 异步队列 + 动态 batch |
| CPU 高并发推理 | 多 worker + CPU 版 PyTorch |
| K8s 云原生部署 | FastAPI + Horizontal Pod Autoscaler + liveness/readiness probe |

**不适用场景**：
- 超大模型（>7B）的实时推理：FastAPI 的同步/异步模型不如 vLLM/TGI 等专用框架高效
- 流式 chat 场景：需要额外的 SSE/WebSocket 支持

### 4.3 注意事项

1. **`workers=1` 用于 GPU**：多 worker 会各自加载一份模型到显存，导致 OOM
2. **Pydantic 输入验证**：`min_length=1` 防止空输入，`max_items=100` 防止批量接口被滥用
3. **镜像与模型分离**：模型文件通过 volume 挂载，更新模型不需要重新构建镜像

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 高并发下大量 504 | 同步推理阻塞事件循环 | 将推理放入 `ThreadPoolExecutor` 或使用后台队列 |
| Docker 中 `torch.cuda.is_available()` 返回 False | 容器未映射 GPU | `docker run --gpus all` 或 `nvidia-docker` |
| 请求 422 验证失败 | Pydantic 校验输入格式不符 | 检查请求 Content-Type 是否 `application/json` |

### 4.5 思考题

1. **初级**：在 FastAPI 服务中，如果你在 `lifespan` 中加载模型，在 `predict` 函数中用 `await asyncio.sleep(0.1)` 模拟 IO 操作，响应时间会是多少？如果是 `time.sleep(0.1)` 呢？这两者的区别是什么？
2. **进阶**：设计一个支持**热切换模型版本**的方案——不重启服务即可从 v1 切换到 v2。（提示：考虑用配置中心或环境变量 + 后台定期检查）

（答案将在第15章末尾给出）

### 4.6 第13章思考题答案

**第13章思考题1**：
- 如果 `torch.cuda.is_available()` 返回 False 时强制使用 CUDA，会抛出 `RuntimeError`。自动降级方案：`device = "cuda" if torch.cuda.is_available() else "cpu"`。在 ModelInference 的 `__init__` 中已包含此逻辑。

**第13章思考题2**：
- 灰度方案：(1) 两个 ModelInference 实例分别加载 v1 和 v2；(2) 在推理服务中根据请求的某个哈希值（如 `hash(trace_id) % 100 < 10`）决定走 v2；(3) 记录两个版本的预测结果（标签、置信度）和 trace_id 到日志；(4) 逐步将 10% → 30% → 50% → 100%；(5) 每次提升比例后对比两个版本的准确率和延迟，确认无异常再继续。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 将 FastAPI 推理服务模板化，新模型上线只需替换模型路径和请求/响应 schema |
| **测试团队** | 编写 API 集成测试，覆盖正常输入、异常输入（SQL 注入、超长文本、特殊字符）和高并发场景 |
| **运维团队** | 配置 Prometheus 指标采集（QPS、延迟 P50/P95/P99、错误率），设置告警规则 |

---

> **下一章预告**：第15章将汇总新手最常见的 5 类故障，给出排查 SOP 和最小复现脚本——从模型下载失败到训练 loss 为 NaN，从 CUDA OOM 到线上预测异常。
