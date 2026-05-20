# 第33章：PreTrainedModel 与权重加载源码

## 1 项目背景

### 业务场景

算法团队训练了一个多语言客服分类模型，保存后一切正常。两周后需要在英文数据上做增量训练，小陈用 `from_pretrained()` 加载模型时看到了这样的警告：

```
Some weights of BertForSequenceClassification were not initialized from the model checkpoint:
- classifier.weight
- classifier.bias
```

小陈没当回事，直接开始训练。结果训练了 3 个 epoch 后验证集 F1 只有 0.45——因为分类头是随机初始化的，前 3 个 epoch 都在从零学习分类头。

更严重的问题发生在模型分片上：一个大模型保存时被分成了 3 个 shard 文件（`model-00001-of-00003.safetensors` 等）。运维人员部署时漏拷了 `model-00002`，加载时没报错——因为 `strict=False` 是默认行为，模型静默地用随机权重替代了缺失的分片。

### 痛点放大

`from_pretrained()` 看似简单，背后隐藏着复杂的权重匹配、分片加载和安全校验逻辑：

```
from_pretrained() 核心流程:
┌─────────────┐
│ 1. 加载config │ → 确定模型结构
├─────────────┤
│ 2. 定位权重   │ → 单文件 / 分片 / Hub 下载
├─────────────┤
│ 3. 初始化模型  │ → 随机初始化 → 加载 state_dict
├─────────────┤
│ 4. 权重匹配    │ → 匹配/跳过/警告 (missing_keys, unexpected_keys)
├─────────────┤
│ 5. 后处理      │ → tie_weights, post_init
└─────────────┘
```

理解这些细节对于排查模型加载问题至关重要。

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周四下午 3:00。小陈对着满屏的 `missing_keys` 警告发愁。

---

**小胖**:"你那模型是不是缺胳膊少腿？missing_keys——听起来像丢失了钥匙。难道权重文件里少了几把'钥匙'打不开对应的参数锁？"

**小陈**:"差不多。`missing_keys` 表示模型中有这些参数，但 checkpoint 文件中没有——这些参数会用随机初始化替代。`unexpected_keys` 表示 checkpoint 中有这些参数，但模型结构中不需要——被忽略。"

**小白**:"但我一直好奇——当 checkpoint 中 `classifier.weight` 的 shape 是 `(5, 768)`，而我用 `num_labels=3` 重新加载时，Transformers 是怎么处理的？直接报错还是强制适配？"

**大师**:"这就是 `from_pretrained` 中最精妙也最容易踩坑的部分——**权重匹配与容忍策略**。让我把源码中的关键逻辑拆解清楚。

**第一关：权重文件定位。** `from_pretrained()` 如何决定从哪里加载权重？

1. 先检查 `pretrained_model_name_or_path` 是本地目录还是 Hub ID
2. 本地目录优先查找 `model.safetensors`（单文件）→ 再找 `*.safetensors`（分片，需 `model.safetensors.index.json` 索引文件）→ 最后找 `pytorch_model.bin`（旧格式）
3. 分片权重通过 `index.json` 描述：`{"weight_map": {"bert.embeddings.weight": "model-00001-of-00003.safetensors", ...}}`
4. 每个 weight key 映射到具体的分片文件

**第二关：权重加载与匹配。** 核心代码在 `modeling_utils.py` 的 `_load_state_dict_into_model()` 中：

- **missing_keys**：`model.state_dict()` 中有的 key，但 checkpoint 中没有 → 打印 Warning，用模型初始化时的随机权重
- **unexpected_keys**：checkpoint 中有的 key，但 `model.state_dict()` 中没有 → 打印 Warning，被忽略（不加载）
- **mismatched_keys**：两边都有这个 key，但 shape 不同 → 如果是 `ignore_mismatched_sizes=True`，跳过；否则报错

**第三关：strict 参数。** 
- `strict=True`：missing 或 unexpected 直接报错（严格模式，适合调试）
- `strict=False`（默认）：只打 Warning，不阻止加载
- `ignore_mismatched_sizes=True`：允许 shape 不同的权重存在（用于改变 num_labels 等情况）

**第四关：tie_weights。** 某些模型（如 BERT 的 embedding 和 LM head）共享权重——训练时这些参数是同一份。加载后需要调用 `tie_weights()` 确保共享权重的一致性。"

**技术映射总结**：
- missing_keys = 新房子里有的插座，但装修师傅没装面板（随机初始化顶替）
- unexpected_keys = 装修师傅多带的面板，但墙上没有对应的插座孔（忽略）
- mismatched_keys = 面板和孔尺寸不匹配（跳过或报错）
- strict=False = 房东说"差不多能住就行"，缺的你自己补

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 torch safetensors
```

### 3.2 权重加载流程追踪

```python
# weight_loading_trace.py
"""追踪 from_pretrained 的权重加载流程"""

import torch
import logging
from transformers import AutoModel, AutoConfig

# 开启详细日志
logging.basicConfig(level=logging.INFO)


def inspect_checkpoint(model_path: str):
    """检查 checkpoint 中的权重结构"""
    import os
    from pathlib import Path
    import json

    path = Path(model_path)
    files = list(path.glob("*.safetensors")) + list(path.glob("*.bin"))

    print(f"\n{'='*60}")
    print(f"Checkpoint 检查: {model_path}")
    print(f"{'='*60}")
    print(f"包含权重文件: {[f.name for f in files]}")

    # 加载 safetensors
    st_files = list(path.glob("*.safetensors"))
    if st_files:
        from safetensors import safe_open
        for st_file in st_files[:1]:  # 只看第一个
            print(f"\n文件: {st_file.name}")
            with safe_open(str(st_file), framework="pt") as f:
                keys = list(f.keys())
                print(f"  包含 {len(keys)} 个张量")
                for key in keys[:5]:
                    tensor = f.get_tensor(key)
                    print(f"    {key:<45} shape={list(tensor.shape)}")
                if len(keys) > 5:
                    print(f"    ... 还有 {len(keys)-5} 个")

    # 检查分片索引
    index_file = path / "model.safetensors.index.json"
    if index_file.exists():
        with open(index_file) as f:
            index = json.load(f)
        print(f"\n分片索引文件存在:")
        print(f"  总权重数: {len(index['weight_map'])}")
        shards = set(index["weight_map"].values())
        print(f"  分片文件: {shards}")

    # 加载并检查 missing/unexpected
    print(f"\n加载模型（观察日志）:")
    model = AutoModel.from_pretrained(model_path)

    return model


def demo_mismatched_sizes():
    """演示 mismatched 权重的处理"""
    print(f"\n{'='*60}")
    print("演示: 修改 num_labels 后加载")
    print(f"{'='*60}")

    from transformers import AutoModelForSequenceClassification

    # 先保存一个 num_labels=2 的模型
    model_2 = AutoModelForSequenceClassification.from_pretrained(
        "prajjwal1/bert-tiny", num_labels=2
    )
    model_2.save_pretrained("./tmp_model_2labels")

    # 用 num_labels=5 加载（会有 mismatched）
    print("加载 num_labels=5（预期: classifier 权重 shape 不匹配）:")
    model_5 = AutoModelForSequenceClassification.from_pretrained(
        "./tmp_model_2labels",
        num_labels=5,
        ignore_mismatched_sizes=True,  # 允许跳过不匹配的权重
    )
    print(f"  模型加载成功，num_labels={model_5.num_labels}")

    # 检查 classifier 权重是否被随机初始化了
    from collections import OrderedDict
    state = model_5.state_dict()
    cls_weight = state.get("classifier.weight")
    if cls_weight is not None:
        print(f"  classifier.weight shape: {list(cls_weight.shape)}")
        print(f"  (预期 (5,128)，原始 checkpoint 是 (2,128))")


def demo_strict_mode():
    """演示 strict=True 与 strict=False 的区别"""
    print(f"\n{'='*60}")
    print("演示: strict 参数的行为")
    print(f"{'='*60}")

    import tempfile, os
    from transformers import BertModel, BertConfig

    # 创建两个不同的 config
    config1 = BertConfig(num_hidden_layers=2)
    config2 = BertConfig(num_hidden_layers=4)  # 层数不同

    model1 = BertModel(config1)

    with tempfile.TemporaryDirectory() as tmpdir:
        model1.save_pretrained(tmpdir)

        print(f"strict=True (层数不匹配，应报错):")
        try:
            _ = BertModel.from_pretrained(tmpdir, config=config2, strict=True)
        except RuntimeError as e:
            print(f"  ✓ 预期错误: {str(e)[:100]}...")

        print(f"\nstrict=False (层数不匹配，仅警告):")
        model2 = BertModel.from_pretrained(tmpdir, config=config2, strict=False)
        print(f"  ✓ 加载成功（不匹配的层随机初始化）")


if __name__ == "__main__":
    # 先保存一个模型用于测试
    model = AutoModel.from_pretrained("prajjwal1/bert-tiny")
    model.save_pretrained("./tmp_bert_tiny")
    AutoConfig.from_pretrained("prajjwal1/bert-tiny").save_pretrained("./tmp_bert_tiny")

    inspect_checkpoint("./tmp_bert_tiny")
    demo_mismatched_sizes()
    demo_strict_mode()
```

### 3.3 权重诊断工具

```python
# weight_diagnostics.py
"""诊断模型权重的工具"""

import torch
from typing import Dict, List, Tuple
from collections import OrderedDict


class WeightDiagnostics:
    """权重诊断工具 —— 检查模型与 checkpoint 的匹配情况"""

    @staticmethod
    def compare_state_dicts(model_state: Dict, checkpoint_state: Dict) -> Dict:
        """对比模型和 checkpoint 的 state_dict"""
        model_keys = set(model_state.keys())
        ckpt_keys = set(checkpoint_state.keys())

        missing = model_keys - ckpt_keys  # 模型有，checkpoint 没有
        unexpected = ckpt_keys - model_keys  # checkpoint 有，模型没有
        matched = model_keys & ckpt_keys  # 两者都有

        # 检查 shape 不匹配
        mismatched = []
        for key in matched:
            if model_state[key].shape != checkpoint_state[key].shape:
                mismatched.append({
                    "key": key,
                    "model_shape": list(model_state[key].shape),
                    "ckpt_shape": list(checkpoint_state[key].shape),
                })

        return {
            "total_model_params": len(model_keys),
            "total_ckpt_params": len(ckpt_keys),
            "matched": len(matched),
            "missing": list(missing)[:10],  # 只展示前 10 个
            "missing_count": len(missing),
            "unexpected": list(unexpected)[:10],
            "unexpected_count": len(unexpected),
            "mismatched": mismatched[:10],
            "mismatched_count": len(mismatched),
        }

    @staticmethod
    def analyze_weight_statistics(state_dict: Dict) -> Dict:
        """分析权重的统计信息"""
        stats = {
            "total_tensors": len(state_dict),
            "total_params": 0,
            "dtypes": {},
            "largest_tensor": {"key": "", "size": 0, "shape": []},
            "smallest_tensor": {"key": "", "size": float("inf"), "shape": []},
        }

        for key, tensor in state_dict.items():
            numel = tensor.numel()
            stats["total_params"] += numel
            dtype_str = str(tensor.dtype)
            stats["dtypes"][dtype_str] = stats["dtypes"].get(dtype_str, 0) + 1

            if numel > stats["largest_tensor"]["size"]:
                stats["largest_tensor"] = {"key": key, "size": numel,
                                           "shape": list(tensor.shape)}
            if numel < stats["smallest_tensor"]["size"]:
                stats["smallest_tensor"] = {"key": key, "size": numel,
                                            "shape": list(tensor.shape)}

        return stats

    @staticmethod
    def validate_checkpoint_completeness(model_path: str) -> Dict:
        """验证 checkpoint 文件完整性"""
        import os, json
        from pathlib import Path

        path = Path(model_path)
        result = {"valid": True, "issues": []}

        # 检查 config.json
        if not (path / "config.json").exists():
            result["valid"] = False
            result["issues"].append("缺少 config.json")

        # 检查权重文件
        st_files = list(path.glob("*.safetensors"))
        bin_files = list(path.glob("*.bin"))
        has_weights = len(st_files) > 0 or len(bin_files) > 0
        if not has_weights:
            result["valid"] = False
            result["issues"].append("缺少权重文件 (.safetensors 或 .bin)")

        # 检查分片完整性
        index_file = path / "model.safetensors.index.json"
        if index_file.exists():
            with open(index_file) as f:
                index = json.load(f)
            expected_shards = set(index["weight_map"].values())
            actual_shards = set(f.name for f in st_files)
            missing_shards = expected_shards - actual_shards
            if missing_shards:
                result["valid"] = False
                result["issues"].append(f"缺少分片文件: {missing_shards}")

        result["files"] = [f.name for f in path.iterdir() if f.is_file()]
        return result


# ===== 使用示例 =====
if __name__ == "__main__":
    from transformers import AutoModel

    model = AutoModel.from_pretrained("prajjwal1/bert-tiny")

    # 权重统计
    stats = WeightDiagnostics.analyze_weight_statistics(model.state_dict())
    print("权重统计:")
    print(f"  总张量数: {stats['total_tensors']}")
    print(f"  总参数量: {stats['total_params']:,}")
    print(f"  数据类型: {stats['dtypes']}")
    print(f"  最大张量: {stats['largest_tensor']['key']} "
          f"({stats['largest_tensor']['size']:,} params)")

    # 对比自己和自己（应该完全匹配）
    comparison = WeightDiagnostics.compare_state_dicts(
        model.state_dict(), model.state_dict()
    )
    print(f"\n自对比: missing={comparison['missing_count']}, "
          f"unexpected={comparison['unexpected_count']}, "
          f"mismatched={comparison['mismatched_count']}")
```

### 3.4 测试验证

```python
# test_weight_loading.py
import pytest
import torch
from transformers import AutoModel, AutoModelForSequenceClassification

class TestWeightLoading:
    def test_auto_model_loads(self):
        model = AutoModel.from_pretrained("prajjwal1/bert-tiny")
        assert model is not None

    def test_mismatch_ignore(self):
        model = AutoModelForSequenceClassification.from_pretrained(
            "prajjwal1/bert-tiny", num_labels=5,
            ignore_mismatched_sizes=True
        )
        assert model.num_labels == 5

    def test_different_num_labels_skips_classifier(self):
        """不同 num_labels 加载时分类头应被跳过"""
        base = AutoModelForSequenceClassification.from_pretrained(
            "prajjwal1/bert-tiny", num_labels=2
        )
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            base.save_pretrained(d)
            new = AutoModelForSequenceClassification.from_pretrained(
                d, num_labels=5, ignore_mismatched_sizes=True
            )
            assert new.classifier.out_features == 5
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **from_pretrained** | 自动处理权重加载、格式检测（safetensors/bin）、分片合并 | 默认 strict=False 可能隐藏严重问题 |
| **safetensors** | 安全加载，无代码注入风险，可内存映射 | 老模型可能只有 .bin 格式 |
| **分片加载** | 大模型可分解为多个小文件 | 分片文件缺失时静默跳过 |
| **ignore_mismatched_sizes** | 灵活改变模型结构 | 被跳过的权重随机初始化，效果不可预期 |

### 4.2 适用场景

| 场景 | 推荐配置 |
|------|---------|
| 标准微调 | strict=False（默认） |
| 调试模型加载 | strict=True |
| 改变分类头/任务头 | ignore_mismatched_sizes=True |
| 从旧格式迁移到 safetensors | 转换后加载 |

**不适用场景**：
- 需要精确控制每个权重的加载（应用自定义 weight mask）→ 自己写 `load_state_dict()`

### 4.3 注意事项

1. **strict=True 用于 CI**：在自动化测试中用 strict=True 验证模型完整性
2. **分片完整性**：部署时务必检查所有分片文件是否存在
3. **post_init**：自定义模型可重写 `_init_weights()` 和 `post_init()` 来控制初始化逻辑

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 训练后效果极差 | missing_keys 中的关键参数被随机初始化 | 检查 loading 日志，确保核心参数都在 matched 中 |
| 部署时 OOM | 以 FP32 加载了 FP16 训练的权重 | 加 `torch_dtype=torch.float16` |
| 分片模型加载不报错但效果差 | 分片文件缺失被静默跳过 | CI 中加完整性检查 |

### 4.5 思考题

1. **初级**：加载模型时 `missing_keys` 包含 `classifier.weight` 和 `classifier.bias`，你会怎么做？直接训练还是先处理？
2. **进阶**：如果 checkpoint 中的 `bert.encoder.layer.5.attention.self.query.weight` 与模型的 shape 一致但值全为零，Transformers 会检测到吗？如何设计一个"权重健康检查"来自动发现这类问题？

（答案将在第34章末尾给出）

### 4.6 第32章思考题答案

**第32章思考题1**：
- 不会成功。`AutoModel.from_pretrained()` 必须先通过 `AutoConfig.from_pretrained()` 加载 config，而 AutoConfig 需要 config 的 `model_type` 在 `CONFIG_MAPPING` 中才能找到对应的 Config 类。如果 Config 未注册，AutoConfig 加载就会失败，后续的 AutoModel 加载更无从谈起。

**第32章思考题2**：
- 通过多个映射表区分不同任务头：`MODEL_MAPPING_NAMES`（基础 model）、`MODEL_FOR_SEQUENCE_CLASSIFICATION_MAPPING_NAMES`（分类）、`MODEL_FOR_TOKEN_CLASSIFICATION_MAPPING_NAMES`（NER）等。每个都是独立的 `OrderedDict`，key 是 model_type，value 是对应的模型类。AutoModelForSequenceClassification 查的是第二个映射表。这种设计允许同一 base model 对应多个不同任务头的类。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 在 CI 中添加模型加载完整性测试（strict=True） |
| **测试团队** | 验证每个模型版本的 missing/unexpected/mismatched keys |
| **运维团队** | 部署脚本中检查所有分片文件完整性 |

---

> **下一章预告**：第34章深入 Tokenizer 内核——Python Tokenizer vs Fast Tokenizer(Rust)的差异，offset_mapping 的对齐机制，如何扩展词表。
