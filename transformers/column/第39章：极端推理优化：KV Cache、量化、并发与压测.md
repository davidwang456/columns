# 第39章：极端推理优化：KV Cache、量化、并发与压测

## 1 项目背景

### 业务场景

客服系统在大促期间面临极限压力——QPS 从日常的 30 飙升至 300，推理服务的 P95 延迟从 80ms 飙升到 5 秒，GPU 利用率 100% 但吞吐反而下降了（排队过长导致超时重试增多）。运维团队的临时扩容方案是加机器——开 8 个 GPU 实例才勉强扛住，单日成本超过 2 万元。

CTO 下了死命令："下次大促前，推理成本至少降低 70%，单卡 QPS 提升 5 倍。不准只靠堆机器。"

技术团队对比了三种推理引擎：
- 原生 Transformers（当前方案）：QPS 30, 显存 14GB
- INT4 量化 + Transformers：QPS 60, 显存 4GB
- vLLM（专用推理框架）：QPS 200+, 显存 12GB

差距如此之大，根因在于原生 Transformers 使用的是**静态 batch**（每个请求独立推理），而 vLLM 用的是 **Continuous Batching**（完成的请求立即返回，新请求立即加入）。

### 痛点放大

生产级 LLM 推理面临三重瓶颈：

```
瓶颈 1: KV Cache 爆炸
  生成长度 4096 × 14B × 2(FP16) × 2(K+V) ≈ 450MB per sequence
  10 个并发 × 450MB = 4.5GB KV Cache 专用

瓶颈 2: 静态 Batching 效率低
  请求 A(50 tokens)+ 请求 B(200 tokens) → batch 等待 B 完成(200 tokens)
  请求 A 完成后 GPU 空转 150 tokens 的生成时间

瓶颈 3: 内存管理碎片化
  每个请求预分配 max_length 的 KV Cache → 短请求浪费大量显存
  vLLM 的 PagedAttention 用虚拟内存方式管理 KV Cache，碎片率接近 0
```

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周三下午 3:00，War Room。大屏上是三种推理方案的性能对比表。运维小王吃惊地看着 vLLM 那行数字。

---

**小胖**:"vLLM 比原生 Transformers 快 6 倍？这不科学吧——跑的明明是同一个模型，算力一样，怎么会有这么大差距？"

**小陈**:"关键不是算力，是算力的利用方式。原生 Transformers 是"来一个处理一个"（或者凑 batch 但不灵活）。vLLM 用的是 Continuous Batching——请求不用一起开始一起结束。哪个请求先生成完就先返回，马上有新的请求补上位置。GPU 几乎没有空闲。"

**小白**:"Continuous Batching 的底层原理是什么？KV Cache 是怎么在请求之间动态分配和回收的？"

**大师**:"这就是 vLLM 的核心创新 PagedAttention。让我把三个关键概念讲清楚。

**概念一：PagedAttention。** 操作系统的虚拟内存把物理内存分成 4KB 的 page，每个进程看到的地址是连续的，但物理上可以分散存储。KV Cache 也一样——vLLM 把 KV Cache 分成固定大小的 block（如 16 个 token 一个 block），请求的 KV Cache 由多个 block 链接而成。好处：
- 不需要预分配 max_length 这么大的连续显存
- 短请求只用少量 block，多的可以分给其他请求
- block 用完就回收，显存碎片率接近 0

**概念二：Continuous Batching 调度。** 传统 batch 的问题：所有请求必须"同进同出"。如果 A 只要生成 20 tokens，B 要生成 200 tokens，A 完成后 GPU 空转等 B。

Continuous Batching 的解法：每生成一步后重新检查——已完成的请求退出，新到达的请求加入。GPU 上始终是 "当前活跃的请求" 的 batch，最大化利用率。

**概念三：prefill vs decode 分离。** 生成过程分两个阶段：
- **Prefill（预填充）**：一次性处理 prompt（计算所有 token 的 KV Cache），计算量大，I/O 密集型
- **Decode（解码）**：逐 token 生成，每次只计算 1 个新的 token，计算量小，内存密集型

vLLM 将这两阶段分开调度——prefill 可以 batch 很大（最大化吞吐），decode 用小 batch（保持低延迟）。

**vLLM vs TGI vs TensorRT-LLM 的边界：**
- **vLLM**：开源、上手快、Continuous Batching + PagedAttention，适合大多数场景
- **TGI (Text Generation Inference)**：HuggingFace 官方，与 Transformers 生态深度集成，支持水印
- **TensorRT-LLM**：NVIDIA 官方，C++ 实现，性能极致但部署复杂"

**技术映射总结**：
- PagedAttention = 把显存分成"小格子"，不浪费每寸空间
- Continuous Batching = 公交车随上随下，不在站点干等
- Prefill/Decode 分离 = 先集中算完所有输入（prefill），再逐个生成输出（decode）

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 torch
pip install vllm>=0.5.0  # 高性能推理框架
```

### 3.2 KV Cache 估算工具

```python
# kv_cache_estimator.py
"""KV Cache 显存占用估算"""


def estimate_kv_cache_memory(
    model_params_b: float,    # 模型参数量 (B)
    num_layers: int,
    num_heads: int,
    head_dim: int,
    batch_size: int,
    seq_len: int,
    dtype_bytes: int = 2,     # FP16=2, FP32=4
) -> dict:
    """
    估算 KV Cache 显存

    公式: KV Cache = 2 × batch × num_layers × num_heads × seq_len × head_dim × dtype_bytes
          (×2 因为 K 和 V 各一份)
    """
    single_seq_mb = (2 * num_layers * num_heads * seq_len * head_dim * dtype_bytes) / (1024**2)
    total_mb = single_seq_mb * batch_size

    return {
        "per_sequence_mb": round(single_seq_mb, 1),
        "total_mb": round(total_mb, 1),
        "total_gb": round(total_mb / 1024, 2),
    }


def estimate_model_memory(model_params_b: float, dtype_bytes: int = 2):
    """估算模型权重显存"""
    return model_params_b * dtype_bytes


def full_inference_memory(
    model_params_b: float, batch_size: int, seq_len: int,
    num_layers: int = 32, num_heads: int = 32, head_dim: int = 128,
):
    """完整推理显存估算"""
    model_mem = estimate_model_memory(model_params_b)
    kv_mem = estimate_kv_cache_memory(model_params_b, num_layers,
                                      num_heads, head_dim,
                                      batch_size, seq_len)
    total = model_mem + kv_mem["total_gb"] + 2  # +2GB 为激活值预留

    return {
        "model_gb": round(model_mem, 1),
        "kv_cache_gb": kv_mem["total_gb"],
        "activations_gb": 2.0,
        "total_gb": round(total, 1),
        "gpu_recommendation": _recommend_gpu(total),
    }


def _recommend_gpu(total_gb: float) -> str:
    if total_gb <= 24:
        return "RTX 3090/4090 (24GB) 或 A10 (24GB)"
    elif total_gb <= 48:
        return "A6000 (48GB) 或 2×A10"
    elif total_gb <= 80:
        return "A100 (80GB)"
    else:
        return f"需要 {total_gb/80:.1f} 张 A100 (80GB)"


if __name__ == "__main__":
    # LLaMA-7B 估算
    print("=" * 60)
    print("LLaMA-7B 推理显存估算")
    llama7b = full_inference_memory(7, batch_size=8, seq_len=2048,
                                    num_layers=32, num_heads=32, head_dim=128)
    print(f"  模型权重: {llama7b['model_gb']} GB")
    print(f"  KV Cache:  {llama7b['kv_cache_gb']} GB")
    print(f"  激活值:    {llama7b['activations_gb']} GB")
    print(f"  总计:      {llama7b['total_gb']} GB")
    print(f"  推荐GPU:   {llama7b['gpu_recommendation']}")

    # 不同 seq_len 的 KV Cache 变化
    print("\n" + "=" * 60)
    print("不同生成长度下的 KV Cache (LLaMA-7B, batch=4)")
    for sl in [512, 1024, 2048, 4096, 8192]:
        kv = estimate_kv_cache_memory(7, 32, 32, 128, 4, sl)
        print(f"  seq_len={sl:5d}: {kv['total_mb']:8.1f} MB ({kv['total_gb']:.2f} GB)")

    # 不同模型的对比
    print("\n" + "=" * 60)
    print("不同模型大小对比 (batch=8, seq_len=2048)")
    for params in [0.1, 1, 7, 13, 70]:
        result = full_inference_memory(params, 8, 2048)
        print(f"  {params:4.0f}B: {result['total_gb']:5.1f} GB → {result['gpu_recommendation']}")
```

### 3.3 压测对比脚本

```python
# benchmark_inference.py
"""推理框架基准测试"""

import time
import torch
import numpy as np
from transformers import AutoTokenizer, AutoModelForCausalLM


class NativeBenchmark:
    """原生 Transformers 推理压测"""

    def __init__(self, model_name: str = "gpt2"):
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.tokenizer.pad_token = self.tokenizer.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(model_name)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        self.model.eval()

    def benchmark(self, prompt: str, max_new_tokens: int = 50,
                  num_runs: int = 20, batch_sizes: list = [1, 2, 4, 8]):
        """测试不同 batch_size 下的延迟和吞吐"""
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)

        print(f"\n{'batch':<8} {'延迟P50(ms)':<12} {'延迟P95(ms)':<12} "
              f"{'吞吐(tok/s)':<12} {'显存(GB)':<10}")
        print("-" * 55)

        for bs in batch_sizes:
            latencies = []
            token_counts = []

            for _ in range(num_runs):
                # 复制输入到 batch
                batch_inputs = {k: v.repeat(bs, 1) for k, v in inputs.items()}

                torch.cuda.synchronize() if self.device == "cuda" else None
                start = time.time()

                with torch.no_grad():
                    outputs = self.model.generate(
                        **batch_inputs,
                        max_new_tokens=max_new_tokens,
                        do_sample=True,
                        pad_token_id=self.tokenizer.eos_token_id,
                    )

                torch.cuda.synchronize() if self.device == "cuda" else None
                elapsed = (time.time() - start) * 1000

                latencies.append(elapsed)
                new_tokens = outputs.shape[1] - batch_inputs["input_ids"].shape[1]
                token_counts.append(new_tokens * bs)

            latencies = sorted(latencies)
            p50 = latencies[len(latencies)//2]
            p95 = latencies[int(len(latencies)*0.95)]
            throughput = sum(token_counts) / (sum(latencies) / 1000)

            mem = torch.cuda.max_memory_allocated() / 1024**3 if self.device == "cuda" else 0

            print(f"{bs:<8} {p50:<12.1f} {p95:<12.1f} {throughput:<12.0f} {mem:<10.2f}")


def compare_batching_strategies():
    """对比静态 vs 连续批处理的效率（模拟）"""
    import random

    print("\n" + "=" * 60)
    print("批处理策略对比（模拟）")
    print("=" * 60)

    # 模拟 20 个请求，生成长度随机分布
    np.random.seed(42)
    request_lengths = np.random.randint(10, 200, 20)  # 每个需要生成的 token 数

    # 静态批处理：等最长的完成
    static_time = max(request_lengths) * 20  # 假设每 token 20ms
    static_waste = sum(max(request_lengths) - rl for rl in request_lengths) * 20
    print(f"静态批处理: 总耗时={static_time/1000:.1f}s, 空转={static_waste/1000:.1f}s")

    # 连续批处理：已完成就退出，新请求加入
    # 简化模拟：每 5 个 token 检查一次
    active = list(request_lengths)
    continuous_time = 0
    while active:
        active = [l - 5 for l in active if l > 5]
        continuous_time += 5 * 20
    print(f"连续批处理: 总耗时={continuous_time/1000:.1f}s")

    improvement = (static_time - continuous_time) / static_time * 100
    print(f"连续比静态快: {improvement:.0f}% (GPU空转减少)")


if __name__ == "__main__":
    bench = NativeBenchmark("gpt2")
    bench.benchmark("The future of artificial intelligence is",
                    max_new_tokens=30, num_runs=10)

    compare_batching_strategies()
```

### 3.4 vLLM 推理服务

```python
# vllm_server_demo.py
"""vLLM 推理服务 —— 高性能 LLM 推理"""

# vLLM 通常作为独立服务运行
# 安装: pip install vllm
# 启动: python -m vllm.entrypoints.openai.api_server \
#          --model uer/gpt2-chinese-cluecorpussmall \
#          --port 8000

# Python 客户端调用示例
if __name__ == "__main__":
    print("vLLM 服务使用示例:\n")

    print("1. 启动 vLLM 服务:")
    print("   python -m vllm.entrypoints.openai.api_server \\")
    print("     --model Qwen/Qwen2.5-7B-Instruct \\")
    print("     --tensor-parallel-size 1 \\")
    print("     --max-model-len 4096 \\")
    print("     --port 8000\n")

    print("2. Python 客户端调用:")
    print("""
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="not-needed")

response = client.completions.create(
    model="Qwen/Qwen2.5-7B-Instruct",
    prompt="你好，请介绍一下你自己",
    max_tokens=100,
    temperature=0.7,
)
print(response.choices[0].text)
""")

    print("3. 关键参数说明:")
    print("   --max-num-seqs:     最大并发序列数 (默认256)")
    print("   --max-model-len:    最大上下文长度")
    print("   --gpu-memory-utilization: GPU 显存使用率 (默认0.9)")
    print("   --tensor-parallel-size: 张量并行度 (多卡)")
    print("   --enable-prefix-caching: 启用 prefix cache\n")

    print("4. vLLM 性能对比 (7B 模型, A100-80G):")
    print("   原生 Transformers: ~30 req/s")
    print("   vLLM:             ~200 req/s")
    print("   提升:             6-7x\n")

    print("5. vLLM vs TGI vs TensorRT-LLM 选型:")
    print("""
                     vLLM         TGI          TensorRT-LLM
上手难度              低            中            高
推理速度              ★★★★★         ★★★★         ★★★★★
与 HF 集成            ★★★★          ★★★★★        ★★
社区活跃度            ★★★★★         ★★★          ★★★
适用场景              通用          HF 深度用户   极致性能
""")
```

### 3.5 测试验证

```python
# test_perf.py
import pytest
from kv_cache_estimator import estimate_kv_cache_memory, full_inference_memory

class TestKVEstimator:
    def test_estimate_scales_linearly_with_seq_len(self):
        r1 = estimate_kv_cache_memory(7, 32, 32, 128, 1, 512)
        r2 = estimate_kv_cache_memory(7, 32, 32, 128, 1, 1024)
        assert abs(r2["per_sequence_mb"] - r1["per_sequence_mb"] * 2) < 0.1

    def test_estimate_scales_linearly_with_batch(self):
        r1 = estimate_kv_cache_memory(7, 32, 32, 128, 1, 2048)
        r2 = estimate_kv_cache_memory(7, 32, 32, 128, 4, 2048)
        assert abs(r2["total_mb"] - r1["total_mb"] * 4) < 0.1

    def test_full_estimate_positive(self):
        result = full_inference_memory(7, 8, 2048)
        assert result["total_gb"] > 0
        assert result["total_gb"] > result["model_gb"]
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方案 | 吞吐 | 延迟 | 显存效率 | 上手难度 |
|------|------|------|---------|---------|
| **原生 Transformers** | 基准 | P50=50ms | 低 | 极低 |
| **Transformers + INT4** | 2x | P50=40ms | 中 | 低 |
| **vLLM** | 6-7x | P50=30ms | 高 | 中 |
| **TensorRT-LLM** | 8-10x | P50=20ms | 极高 | 高 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 低 QPS(< 10) 的原型验证 | 原生 Transformers |
| 中等 QPS(10-50) + 有限 GPU | Transformers + INT4 量化 |
| 高 QPS(>50) 在线服务 | vLLM |
| 极致性能 + NVIDIA 生态 | TensorRT-LLM |

**不适用场景**：
- 非 NVIDIA GPU → vLLM 和 TensorRT-LLM 支持有限
- Encoder-only 模型（BERT）→ 这些框架主要优化 Decoder 模型
- 离线批处理 → 原生 Transformers + 大 batch 可能更简单

### 4.3 注意事项

1. **vLLM 的内存预估**：`gpu-memory-utilization=0.9` 是默认值，如果 OOM 降低到 0.8
2. **量化精度损失**：INT4 对生成质量影响大于 INT8，上线前需评估
3. **Prefill/Decode 的延迟构成**：prefill 是计算瓶颈（优化 batch size），decode 是内存瓶颈（优化 KV Cache）

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| vLLM OOM | `max-model-len` 设太大 | 降低到实际需要的上下文长度 |
| INT4 量化后输出质量骤降 | 量化校准数据不足 | 用 100+ 条领域数据做校准 |
| 高并发下延迟波动大 | Prefill 阶段阻塞 Decode | 调整 vLLM 的 `max-num-batched-tokens` |

### 4.5 思考题

1. **初级**：在 `kv_cache_estimator.py` 中增加 Prefix Cache 的估算——如果 10 个请求共享相同的 system prompt（长度 200 tokens），Prefix Cache 能节省多少 KV Cache？
2. **进阶**：设计一个**混合推理方案**——短请求（<100 tokens）用原生 Transformers（延迟优先），长请求（>500 tokens）用 vLLM（吞吐优先），在 API 网关层做路由分发。

（答案将在第40章末尾给出）

### 4.6 第38章思考题答案

**第38章思考题1**：
- 修改：(1) 在 `MyDualConfig` 中新增 `num_keyword_labels=2`；(2) 在 `MyDualModel.__init__` 中新增 `self.keyword_head = nn.Linear(hidden_size, sequence_len, 2)`；(3) 在 `forward()` 中新增 `keyword_logits = self.keyword_head(bert_outputs.last_hidden_state)`；(4) 在 `DualModelOutput` 中新增 `keyword_logits` 字段；(5) loss 中加入 `nn.CrossEntropyLoss()(keyword_logits.view(-1,2), keyword_labels.view(-1))`。

**第38章思考题2**：
- 动态任务头方案：(1) 底座模型单独保存；(2) 每个任务头保存为独立 checkpoint（只存 head 的 state_dict）；(3) 加载时先 `load_state_dict(base_ckpt)` 再 `load_state_dict(head_ckpt, strict=False)`；(4) 用 `nn.ModuleDict` 将所有 head 放在一起，运行时通过 `self.active_head = "classify"` 切换活跃头（forward 中只计算活跃头）。这本质上是手动实现的"multi-adapter"模式。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 新推理服务默认评估 vLLM 作为推理引擎 |
| **测试团队** | 大促前用 Locust 压测目标 QPS × 1.5 倍，确认 P95 延迟 < 阈值 |
| **运维团队** | vLLM 部署用 Docker 镜像，配置 `--gpu-memory-utilization` 和 HPA |

---

> **下一章预告**：第40章是高级篇综合实战——从零构建生产级 Transformers AI 平台，包括训练管道、模型注册、推理网关、质量评测和运维监控的全套方案。
