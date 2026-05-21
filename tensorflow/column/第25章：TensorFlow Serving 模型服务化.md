# 第25章：TensorFlow Serving 模型服务化

## 1. 项目背景

算法工程师小林训练好了一个商品分类模型（SavedModel 格式，第 9 章），后端工程师老李需要把它集成到公司的商品管理系统中——用户在网页上传一张商品图片，系统自动分类并填入商品信息。

一开始，老李直接用 Python Flask 加载模型，写了 API：

```python
@app.route("/classify", methods=["POST"])
def classify():
    img = request.files["image"]
    result = model.predict(process(img))
    return jsonify(result)
```

这个方案很快暴露了三个问题：(1) 每次重启服务都要重新加载模型（加载时间 8 秒），用户请求在前 8 秒全部失败；(2) 当模型需要更新时（小林训练了新版本），老李必须停服→换模型→重启→等待 8 秒加载——有 30 秒的服务不可用窗口；(3) 并发高了（QPS > 50），Flask + Python GIL 的推理延迟从 50ms 飙升到 800ms。

更麻烦的是，老板说"我们要灰度发布——先让 10% 流量用新模型，如果效果好再全量"。Flask 单模型部署根本做不了多版本灰度。

**痛点放大**：模型训练只是机器学习生命周期的一半——把模型稳定、高效地部署为在线服务同样重要。TensorFlow Serving 是 TF 生态的"模型服务器"——支持热加载、多版本管理、REST/gRPC 双协议、批量推理优化，解决了"训练→部署"最后一公里。

## 2. 项目设计

**小胖**（困惑地挠头）：模型不就是 SavedModel 目录吗？Flask 加载不就好了？搞个 TensorFlow Serving Docker 容器又是为啥？多此一举吧？

**大师**：Flask 加载模型就像一个"路边小摊"——老板一个人又收钱又炒菜，来 5 个客人就忙不过来。TensorFlow Serving 是一个"现代化厨房"——有专职厨师（模型推理线程池）、传菜员（batching）、菜单经理（多版本管理）、外卖小哥（gRPC 协议）。当你的客流量从 5 个变成 500 个，路边摊就崩了，而厨房还能稳定运行。

**技术映射**：TensorFlow Serving 是一个为生产环境设计的模型服务系统，提供模型热加载、请求批处理、多版本灰度、资源隔离等能力，远超简单的 Flask + model.predict。

**小白**（看文档）：SavedModel 的 SignatureDef 具体是什么格式？我看 `saved_model_cli` 能显示出签名信息。

**大师**：SignatureDef 就是模型的"接口契约"——它告诉你输入叫什么名字、是什么类型（DT_FLOAT）、什么形状（(-1, 224, 224, 3)）、输出是什么。Serving 启动时读取 SavedModel 里的 SignatureDef，自动暴露对应的 REST 和 gRPC 端点。

```bash
# 查看 SavedModel 的 Signature
saved_model_cli show --dir ./my_model --all

# 输出示例：
# signature_def['serving_default']:
#   inputs['image_bytes'] tensor_info: dtype: DT_STRING, shape: (-1)
#   outputs['class_probs'] tensor_info: dtype: DT_FLOAT, shape: (-1, 5)
```

有了这个，Serving 就知道"REST 接口的 input 字段叫 image_bytes，output 字段叫 class_probs"。

**小胖**：那版本管理呢？怎么做到不中断服务更新模型？

**大师**：这是 Serving 最强大的能力——**模型热加载（Hot Reload）**。你只需要把新版模型放到指定目录下，Serving 会自动发现并加载它，零停机。目录结构是这样的：

```
/models/
└── product_classifier/   ← 模型名称
    ├── 1/                 ← 版本 1
    │   └── saved_model.pb
    ├── 2/                 ← 版本 2（新增）
    │   └── saved_model.pb
    └── 3/                 ← 版本 3
        └── saved_model.pb
```

Serving 默认使用最新版本，但你也可以通过 gRPC 请求指定版本号。这天然支持了灰度发布和 A/B 测试：

```python
# 用 gRPC 指定版本调用
request.model_spec.version.value = 2  # 指定用版本 2
request.model_spec.version.value = 0  # 0 表示用最新版本
```

**技术映射**：Serving 的模型版本目录约定——`<模型名>/<版本号(整数)>/saved_model.pb`。Serving 自动监控目录变化，加载新版本和卸载旧版本，实现零停机更新。

**小白**：REST 和 gRPC 两种协议各有什么优势？

**大师**：REST 最简单——用 curl 就能测试，适合调试和低 QPS 场景。gRPC 使用 Protocol Buffers 序列化（比 JSON 快 3-10 倍），连接复用（HTTP/2 长连接），请求延迟更低，适合高并发生产场景。

```bash
# REST: 简单调试
curl -X POST http://localhost:8501/v1/models/product_classifier:predict \
  -d '{"instances": [{"image_bytes": {"b64": "..."}}]}'

# gRPC: 生产环境（需要 TF Serving 的 protobuf 定义）
# python -c "from tensorflow_serving.apis import predict_pb2, prediction_service_pb2_grpc; ..."
```

## 3. 项目实战

### 3.1 环境准备

```bash
# 拉取 TensorFlow Serving Docker 镜像
docker pull tensorflow/serving:2.16.1

# 安装 gRPC 客户端依赖
pip install tensorflow-serving-api==2.16.1 grpcio requests pillow numpy
```

### 3.2 分步实现

**步骤一：模型导出与目录结构准备**

目标：训练一个简单的二分类模型，导出为 SavedModel，并按 Serving 规范组织目录。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np
import os
import tempfile

# 1. 训练一个简单的商品分类模型（模拟）
np.random.seed(42)
X = np.random.randn(500, 20).astype(np.float32)
y = np.random.randint(0, 2, 500).astype(np.float32)

# 用 Functional API 构建模型（确保命名规范，Serving 能看到名字）
inputs = keras.Input(shape=(20,), name="features")
x = keras.layers.Dense(32, activation="relu", name="hidden")(inputs)
outputs = keras.layers.Dense(1, activation="sigmoid", name="prob")(x)
model = keras.Model(inputs=inputs, outputs=outputs, name="product_classifier")
model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
model.fit(X, y, epochs=3, batch_size=32, verbose=0)

# 2. 导出为 Serving 格式（带自定义 Signature）
export_root = tempfile.mkdtemp()
model_name = "product_classifier"
model_version = 1
export_path = os.path.join(export_root, model_name, str(model_version))

@tf.function(input_signature=[
    tf.TensorSpec(shape=[None, 20], dtype=tf.float32, name="features")
])
def serve_fn(features):
    result = model(features, training=False)
    return {"probability": result}

tf.saved_model.save(model, export_path, signatures={"serving_default": serve_fn})
print(f"模型已导出到: {export_path}")
print(f"目录结构:")
for root, dirs, files in os.walk(export_path):
    level = root.replace(export_path, "").count(os.sep)
    indent = " " * 2 * level
    print(f"{indent}{os.path.basename(root)}/")
    subindent = " " * 2 * (level + 1)
    for f in files:
        print(f"{subindent}{f}")

# 3. 验证 SavedModel CLI 可见
print(f"\n=== 查看 Signature ===")
print(f"运行: saved_model_cli show --dir {export_path} --all")
```

**步骤二：Docker 启动 TensorFlow Serving**

目标：用 Docker 启动 Serving，挂载模型目录，验证 REST 接口可用。

```bash
# 启动 TensorFlow Serving（注意：实际运行时替换 YOUR_MODEL_PATH）
MODEL_PATH="/tmp/product_classifier_model"  # 替换为上一步 export_root 的实际路径

docker run -d --name tf_serving \
  -p 8500:8500 -p 8501:8501 \
  -v "$MODEL_PATH:/models/product_classifier" \
  -e MODEL_NAME=product_classifier \
  tensorflow/serving:2.16.1

# 查看日志确认启动成功
docker logs tf_serving
# 期望输出: "Running gRPC ModelServer at 0.0.0.0:8500 ..."
#           "Exporting HTTP/REST API at:localhost:8501 ..."
```

```python
# REST API 调用测试（Python 客户端）
import requests
import numpy as np
import json

def test_rest_api(server_url="http://localhost:8501"):
    url = f"{server_url}/v1/models/product_classifier:predict"

    # 构造请求：批次 3 条记录，每条 20 个特征
    payload = {
        "instances": np.random.randn(3, 20).astype(np.float32).tolist()
    }

    response = requests.post(url, json=payload)
    result = response.json()

    print(f"HTTP 状态码: {response.status_code}")
    print(f"预测结果: {result}")
    print(f"预测概率: {[p[0] for p in result['predictions']]}")

# 仅当 Serving 在 localhost:8501 运行时才调用
try:
    test_rest_api()
    print("\nREST API 测试通过 ✅")
except Exception as e:
    print(f"\nREST API 不可用 (Serving 未运行): {e}")
    print("在 Docker 启动 Serving 后重试:")
    print("  docker run -d --name tf_serving -p 8501:8501 -v <model_path>:/models/product_classifier -e MODEL_NAME=product_classifier tensorflow/serving")
```

**步骤三：gRPC 高性能推理**

目标：使用 gRPC 协议进行批量推理，对比 REST 的延迟差异。

```python
import grpc
import numpy as np

# 尝试导入 TensorFlow Serving 的 gRPC 客户端
try:
    from tensorflow_serving.apis import predict_pb2
    from tensorflow_serving.apis import prediction_service_pb2_grpc

    def test_grpc_call(server="localhost:8500", num_samples=10):
        """gRPC 推理请求"""
        channel = grpc.insecure_channel(server)
        stub = prediction_service_pb2_grpc.PredictionServiceStub(channel)

        request = predict_pb2.PredictRequest()
        request.model_spec.name = "product_classifier"
        request.model_spec.signature_name = "serving_default"

        # 构造输入
        data = np.random.randn(num_samples, 20).astype(np.float32)
        request.inputs["features"].CopyFrom(
            tf.make_tensor_proto(data)
        )

        # 发送请求
        result = stub.Predict(request, timeout=5.0)
        probs = tf.make_ndarray(result.outputs["probability"])
        print(f"gRPC 推理: {num_samples} 个样本成功")
        print(f"输出 shape: {probs.shape}")
        return probs

    test_grpc_call()
except ImportError:
    print("tensorflow-serving-api 未安装。安装命令:")
    print("  pip install tensorflow-serving-api==2.16.1")
except Exception as e:
    print(f"gRPC 不可用 (Serving 未运行): {e}")
```

**步骤四：多版本管理与灰度发布策略**

目标：演示 Serving 的模型版本目录管理和灰度方案设计。

```python
# === 多版本模型目录结构 ===
import os

def prepare_multi_version_model(base_dir):
    """准备多版本模型目录"""
    for version in [1, 2, 3]:
        ver_dir = os.path.join(base_dir, str(version))
        os.makedirs(ver_dir, exist_ok=True)
        # 实际项目中每个版本目录下是 saved_model.pb + variables/

    print("多版本模型目录:")
    for root, dirs, files in os.walk(base_dir):
        level = root.replace(base_dir, "").count(os.sep)
        if level <= 1:  # 只展示到版本目录层
            print(f"  {'  ' * level}{os.path.basename(root)}/")

# 灰度发布策略伪代码
print("""
=== 灰度发布方案 ===

方案 A: Serving 版本标签 + 模型别名
  /models/product_classifier/
    1/  (v1: 当前线上 baseline)
    2/  (v2: 新模型, 10% 灰度)
    3/  (v3: 候选)

  服务端配置 model_config_list:
    config {
      name: "product_classifier"
      base_path: "/models/product_classifier"
    }
    config {
      name: "product_classifier_canary"  # 灰度的别名
      base_path: "/models/product_classifier"
      model_version_policy { specific { versions: 2 } }
    }

  线上路由:
    90% 流量 → model_name="product_classifier" → 自动取最新 (v3 或 v1)
    10% 流量 → model_name="product_classifier_canary" → 固定 v2

方案 B: 业务层路由
  在调用 Serving 的应用层做流量分发:
    if user_id_hash % 100 < 10:
        model_version = 2  # 10% 流量用 v2
    else:
        model_version = 1  # 90% 流量用 v1

  优点: 不需要额外部署灰度服务
  缺点: 需要在业务代码中做分流逻辑
""")
```

**步骤五：线上输入校验与错误码设计**

目标：在 Serving 客户端增加输入校验，防止异常输入导致推理崩溃。

```python
def validate_and_predict(server_url, instances):
    """
    带输入校验的推理客户端
    返回: (success: bool, result: dict or error: str)
    """
    # 1. 输入类型检查
    if not isinstance(instances, list):
        return False, {"error": "INPUT_TYPE_ERROR", "message": "instances must be a list"}

    # 2. 特征维度检查
    for i, inst in enumerate(instances):
        if not isinstance(inst, list):
            return False, {"error": "INPUT_SHAPE_ERROR",
                          "message": f"instances[{i}] must be a list"}
        if len(inst) != 20:
            return False, {"error": "INPUT_DIM_ERROR",
                          "message": f"instances[{i}] has {len(inst)} features, expected 20"}

    # 3. 数值范围检查
    import numpy as np
    data = np.array(instances, dtype=np.float32)
    if np.any(np.isnan(data)) or np.any(np.isinf(data)):
        return False, {"error": "INPUT_VALUE_ERROR",
                      "message": "Input contains NaN or Inf values"}

    # 4. 调用 Serving
    import requests
    try:
        resp = requests.post(
            f"{server_url}/v1/models/product_classifier:predict",
            json={"instances": data.tolist()},
            timeout=2.0,
        )
        if resp.status_code != 200:
            return False, {"error": "SERVING_ERROR",
                          "message": resp.text}
        return True, resp.json()
    except requests.exceptions.Timeout:
        return False, {"error": "TIMEOUT", "message": "Serving request timed out"}
    except requests.exceptions.ConnectionError:
        return False, {"error": "CONNECTION_ERROR", "message": "Cannot connect to Serving"}

# 测试校验逻辑
print("=== 输入校验测试 ===")
test_cases = [
    (np.random.randn(2, 20).astype(np.float32).tolist(), "正常输入"),
    ([1, 2, 3], "错误输入 (不是二维数组)"),
    ([[1.0] * 10], "错误输入 (特征维度=10, 期望20)"),
]
for data, desc in test_cases:
    ok, result = validate_and_predict("http://localhost:8501", data)
    print(f"  {desc}: {'PASS' if ok else 'REJECTED'} - {result.get('error', 'ok')}")
```

### 3.3 Serving 部署 Checklist

| # | 检查项 | 验证方式 |
|---|--------|----------|
| 1 | SavedModel 含 `serving_default` signature | `saved_model_cli show --all` |
| 2 | 输入输出名称与 API 一致 | REST 调用检查字段名 |
| 3 | 模型目录结构 `<name>/<version>/` | `ls -la /models/` |
| 4 | Docker 端口映射 8500(gRPC) + 8501(REST) | `docker ps` |
| 5 | 热加载正常工作 | 新增版本目录后 `docker logs` 确认 |
| 6 | 并发压测通过 | `wrk -t4 -c100 -d30s --latency <url>` |
| 7 | 模型元数据完整 | `GET /v1/models/product_classifier/metadata` |

## 4. 项目总结

### 4.1 Flask vs TF Serving

| 方面 | Flask + model.predict | TensorFlow Serving |
|------|----------------------|-------------------|
| 部署复杂度 | 简单（几行代码） | 需要 Docker/配置 |
| 热加载 | 不支持（需重启） | 原生支持（自动检测） |
| 多版本管理 | 需自己实现 | 原生支持（目录结构） |
| 请求批处理 | 单条推理 | 自动 batch（Dynamic Batching） |
| gRPC 协议 | 需手动实现 | 原生支持 |
| 并发性能 | 低（GIL 限制） | 高（C++ 线程池） |
| 监控/metrics | 需自己接入 | 内置 `/monitoring/prometheus/metrics` |
| GPU 推理 | Python 管理 | 原生命令行 `--use_gpu` |
| 适用场景 | 开发调试、内部小流量 | 生产环境、高并发 |

### 4.2 适用场景

1. **生产级模型服务**：对外提供 REST/gRPC API，承载商业流量
2. **多版本灰度发布**：通过版本目录 + 路由策略实现 A/B 测试
3. **模型热更新**：训练产出新模型 → 复制到 Serving 目录 → 自动生效
4. **批量离线推理**：用 gRPC streaming 或 REST batch 接口处理大批量数据
5. **多模型管理**：一台 Serving 实例可同时服务多个模型（通过 `model_config_list`）

**不适用场景**：
1. 本地开发和调试——直接在 Python 中 `model.predict()` 最快
2. 极端低延迟（< 1ms）——Serving 的调度开销约 2-5ms，对极致延迟敏感的场景考虑 TensorRT + Triton

### 4.3 注意事项

- **Serving 的 `--model_base_path` 必须指向包含版本子目录的父目录**，不是 saved_model.pb 所在目录本身。如 `/models/my_model` 下面应有 `1/saved_model.pb`
- **REST 请求体格式**：`{"instances": [...]}` 是批量，`{"inputs": {...}}` 是单条。混用会导致形状解析错误
- **Batching 参数调优**：`--batching_parameters_file` 可指定 max_batch_size、timeout 等。大的 max_batch_size 提高吞吐但增加延迟，需按 SLA 权衡
- **模型加载是串行的**：如果同时加载多个大模型，Serving 会串行加载，启动时间 = 所有模型加载时间之和。对大模型建议预加载或延迟加载

### 4.4 常见踩坑经验

1. **坑**：`curl -d '{"instances": [1.0, 2.0, 3.0]}'` 返回 `"instances" is not a list of lists`。
   **根因**：`instances` 必须是二维数组——即使只有一条数据也要再包一层：`{"instances": [[1.0, 2.0, 3.0]]}`。
   **解决**：始终用 `instances` 包裹为 `[样本1, 样本2, ...]`，每个样本本身是 list。

2. **坑**：新模型放到版本目录后，Serving 没有加载。
   **根因**：Serving 默认每 2 秒轮询一次文件系统，新模型目录必须有完整的 `saved_model.pb` + `variables/` 才算就绪。如果复制过程中被扫描到（文件不完整），会被标记为不可用。
   **解决**：先写到临时目录，复制完成后 `mv`（原子操作）到目标目录；或加 `--file_system_poll_wait_seconds=5` 增加轮询间隔减少误判。

3. **坑**：gRPC 客户端报 `StatusCode.UNAVAILABLE: failed to connect to all addresses`。
   **根因**：gRPC 端口是 8500（不是 8501），且 Docker 启动时需要映射 `-p 8500:8500`。
   **解决**：确认 `docker run` 映射了 8500 端口，gRPC 客户端连接 `localhost:8500`，REST 客户端连接 `localhost:8501`。

### 4.5 思考题

1. 你的模型服务 QPS 从 100 突然涨到 5000——Serving 开始返回延迟。你已经启用了 `--enable_batching`，还能做什么来提升吞吐？至少给出 3 种方案。

2. 你有一个图片分类模型（输入 224×224 图片），现在需要同时支持"返回 Top-1 类别"和"返回 Top-5 概率"两个推理接口。在不动原始 SavedModel 的前提下，如何通过 Serving 的 `signature_def` 实现？如果原始模型只有一个 `serving_default`，能同时暴露两个接口吗？

### 4.6 推广计划提示

- **算法工程师**：在模型训练脚本中增加一个"导出为 Serving 格式"的 step，保证每个模型默认可部署
- **平台工程师**：搭建团队的 TensorFlow Serving 集群（Docker + K8s），制定模型目录命名规范和版本策略
- **测试工程师**：为每个上线的模型服务编写一致性测试——`model.predict()` vs Serving REST vs Serving gRPC，输出差异应 < 1e-5
