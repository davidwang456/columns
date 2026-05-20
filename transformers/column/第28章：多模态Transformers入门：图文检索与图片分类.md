# 第28章：多模态 Transformers 入门：图文检索与图片分类

## 1 项目背景

### 业务场景

某电商平台的内容审核团队每天要审核约 2 万张商品图片——检查图片是否与商品标题描述一致、图片中是否包含违禁内容（如二维码、联系方式、血腥暴力等）。目前依赖人工逐张审核，平均每人每天审核 800 张，团队 25 人刚好覆盖日常量。但大促期间图片量翻 3 倍，团队必须临时招聘外包审核员，培训成本高、质量不稳定。

更头疼的是"图文不符"问题——不少商家上传了与标题完全无关的图片。比如商品标题为"春季新款连衣裙"，配图却是一双运动鞋；或标题为"品牌 A 手机壳"，配图却放了品牌 B 的 logo。这种欺诈行为单纯靠 OCR 文字识别无法检测——因为图片上的文字可能也是假的。

技术总监提出："能否用 AI 判断图片和文字描述是否匹配？同时自动过滤违规图片？"

### 痛点放大

传统方案只能单独处理文本或图片：
- 文本分类能识别商品标题的类别，但无法判断图片是否符合
- 图片分类能识别图片中的物体，但无法理解图片和文本的语义关系
- OCR 能提取图片中的文字，但对于"图片里是一双鞋但标题是连衣裙"这种语义冲突无能为力

多模态 Transformer（CLIP、BLIP等）将文本和图片编码到**同一个向量空间**——可以直接计算"这双鞋的图片"和"春季连衣裙"的语义相似度，前者是图片向量，后者是文本向量，它们的 cosine 相似度很低（不相干），而"这双鞋的图片"和"运动鞋"的相似度很高——这就是图文匹配的核心能力。

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周二下午 3:00，AI Lab。小陈正在看 CLIP 的论文，小胖端着一杯冰美式过来。

---

**小胖**:"小陈你看什么呢？CLIP？这不是个回形针吗？"

**小陈**:"不是回形针。CLIP 是 OpenAI 的一个多模态模型——它能把图片和文字编到同一个向量空间里，然后就能直接算它们的相似度。比如一张猫的图片和文字'一只猫'在向量空间里离得很近，和'一条狗'就离得很远。"

**小胖**:"哦！就像一个翻译官——把英文和中文都翻译成同一种'国际语言'，然后就能比较意思是否一致了？"

**小白**（放下手中的 ViT 论文）:"这个比喻抓住了本质。但我有个技术问题——CLIP 是怎么把图片变成向量的？图片是像素矩阵，文字是 token 序列，两者的结构完全不同。"

**大师**:"好问题。多模态模型的核心就是**双塔架构** + **对比学习**。

**架构层面：** CLIP 有两个编码器——Image Encoder（通常是 ViT 或 ResNet）和 Text Encoder（通常是 GPT 风格的 Transformer）。图片经过 Image Encoder 变成一个向量，文本经过 Text Encoder 变成一个向量。两个向量归一化后，点积就是它们语义相似度。

**训练层面：** CLIP 用了 4 亿图文对（从互联网上爬取的图片+alt text），用对比学习训练——让匹配的图文对的向量更近，不匹配的更远。这就像老师给了你 4 亿张'图片+文字描述'的配对卡片，让你学会把同义的卡片放在一起。

**推理层面：** CLIP 最惊艳的能力是零样本分类——你不需要训练任何分类头。比如你要做宠物分类，只需要写：
- '一张猫的照片'
- '一张狗的照片'
- '一张鸟的照片'

把图片和这三段文字分别算相似度，相似度最高的就是分类结果。完全不用标注数据。"

**小胖**:"那我们需要在商品图片上重新训练一个 CLIP 吗？"

**大师**:"大多数场景不需要——CLIP 在 4 亿图文对上预训练过，常识级别的视觉概念（衣服、鞋子、动物、场景等）已经很好了。但对于垂直领域（如工业零件、医学影像），推荐用 CLIP 作为基座，在你的领域数据上做对比微调。

在 Transformers 库中，多模态模型引入了 **Processor** 的概念——它是 Tokenizer + ImageProcessor 的组合体。你可以直接 `processor(text=..., images=...)` 同时处理文本和图片，返回 `input_ids`、`attention_mask` 和 `pixel_values`。"

**技术映射总结**：
- CLIP = 图文"翻译官"，把图片和文字翻译成同一种向量语言
- Image Encoder + Text Encoder = 两个编码器，一个看图片一个看文字
- 零样本分类 = 不用标注数据，只靠"图片像不像这段描述"来判断类别
- Processor = 文本预处理 + 图片预处理的合体工具

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 torch torchvision
pip install pillow>=10.0.0  # 图片处理
pip install matplotlib>=3.7.0  # 可视化
```

### 3.2 CLIP 图文相似度

```python
# clip_similarity.py
"""CLIP 图文相似度计算 —— 零样本图文匹配"""

import torch
import numpy as np
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
import requests
from io import BytesIO


class CLIPMatcher:
    """基于 CLIP 的图文匹配器"""

    def __init__(self, model_name: str = "openai/clip-vit-base-patch32"):
        self.model = CLIPModel.from_pretrained(model_name)
        self.processor = CLIPProcessor.from_pretrained(model_name)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        self.model.eval()

    def compute_similarity(self, image: Image.Image,
                           texts: list) -> dict:
        """
        计算图片与多个文本的相似度

        Returns:
            {"text": "...", "score": 0.89, "rank": 1}
        """
        inputs = self.processor(
            text=texts,
            images=image,
            return_tensors="pt",
            padding=True,
        ).to(self.device)

        with torch.no_grad():
            outputs = self.model(**inputs)
            logits_per_image = outputs.logits_per_image  # (1, num_texts)
            probs = logits_per_image.softmax(dim=-1)

        results = []
        for i, text in enumerate(texts):
            results.append({
                "text": text,
                "score": round(probs[0][i].item(), 4),
                "rank": 0,  # 后续排序
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        for i, r in enumerate(results):
            r["rank"] = i + 1

        return results

    def zero_shot_classify(self, image: Image.Image,
                           categories: list) -> dict:
        """
        零样本图片分类

        Args:
            image: PIL Image
            categories: ["猫", "狗", "鸟", "鱼"]
        """
        texts = [f"这是一张{c}的照片" for c in categories]
        results = self.compute_similarity(image, texts)

        best = results[0]
        return {
            "category": categories[best["rank"] - 1],
            "confidence": best["score"],
            "all_scores": {r["text"]: r["score"] for r in results},
        }

    def image_text_match(self, image: Image.Image,
                         description: str,
                         threshold: float = 0.3) -> dict:
        """
        判断图片与文字描述是否匹配

        Returns:
            {"match": True/False, "score": 0.85}
        """
        results = self.compute_similarity(
            image,
            [description, "与描述完全无关的随机图片"]
        )

        score = results[0]["score"] if results[0]["text"] == description else 0
        return {
            "match": score >= threshold,
            "score": score,
            "description": description,
        }


# ===== 使用示例 =====
if __name__ == "__main__":
    matcher = CLIPMatcher()

    # 创建一张简单的测试图（纯色图模拟，实际中加载真实图片）
    test_image = Image.new("RGB", (224, 224), color=(100, 150, 200))

    # 1. 图文相似度
    print("=" * 50)
    print("1. 图文相似度计算")
    texts = [
        "一只可爱的猫",
        "一只活泼的狗",
        "一个蓝色的方块",
        "一辆红色汽车",
    ]
    results = matcher.compute_similarity(test_image, texts)
    for r in results:
        bar = "█" * int(r["score"] * 20)
        print(f"  [{r['rank']}] {r['text'][:30]:<30} {r['score']:.4f} {bar}")

    # 2. 零样本分类
    print("\n" + "=" * 50)
    print("2. 零样本分类")
    categories = ["连衣裙", "运动鞋", "手机壳", "双肩包", "太阳镜"]
    result = matcher.zero_shot_classify(test_image, categories)
    print(f"  预测类别: {result['category']} (置信度: {result['confidence']:.2%})")

    # 3. 图文匹配验证
    print("\n" + "=" * 50)
    print("3. 图文匹配检测")
    # 模拟商品标题和图片匹配
    # 实际中: image = Image.open("product.jpg")
    match_result = matcher.image_text_match(
        test_image,
        "一件蓝色的T恤",
    )
    print(f"  图片与描述匹配: {match_result['match']} (score={match_result['score']:.4f})")
```

### 3.3 商品图文审核系统

```python
# product_auditor.py
"""商品图文审核系统 —— CLIP + 规则 + 敏感内容过滤"""

import torch
import numpy as np
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
from typing import List, Dict, Optional


class ProductImageAuditor:
    """商品图片审核器"""

    # 违规内容类别
    FORBIDDEN_CATEGORIES = [
        "二维码或条形码",
        "手机号码或联系方式文本",
        "血腥暴力内容",
        "色情或低俗内容",
        "第三方平台水印或logo",
    ]

    # 商品大类
    PRODUCT_CATEGORIES = [
        "服装", "鞋靴", "箱包", "数码产品", "美妆护肤",
        "食品饮料", "家居用品", "运动户外", "图书音像", "其他",
    ]

    def __init__(self, model_name: str = "openai/clip-vit-base-patch32"):
        self.model = CLIPModel.from_pretrained(model_name)
        self.processor = CLIPProcessor.from_pretrained(model_name)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        self.model.eval()

    def _encode_pair(self, image: Image.Image, texts: list):
        """编码图文对"""
        inputs = self.processor(
            text=texts, images=image, return_tensors="pt", padding=True
        ).to(self.device)

        with torch.no_grad():
            outputs = self.model(**inputs)
            logits = outputs.logits_per_image  # (1, N)
            probs = logits.softmax(dim=-1)[0]

        return probs.cpu().numpy()

    def check_forbidden(self, image: Image.Image,
                        threshold: float = 0.15) -> dict:
        """检查是否包含违规内容"""
        forbidden_texts = [f"图片中包含{c}" for c in self.FORBIDDEN_CATEGORIES]
        normal_text = "正常的商品展示图片"

        all_texts = forbidden_texts + [normal_text]
        probs = self._encode_pair(image, all_texts)

        violations = []
        is_safe = True

        for i, cat in enumerate(self.FORBIDDEN_CATEGORIES):
            score = float(probs[i])
            if score > threshold:
                violations.append({"category": cat, "score": round(score, 4)})
                is_safe = False

        return {
            "is_safe": is_safe,
            "violations": sorted(violations, key=lambda x: -x["score"]),
            "safe_score": round(float(probs[-1]), 4),
            "action": "pass" if is_safe else "review",
        }

    def classify_product(self, image: Image.Image) -> dict:
        """商品类别分类"""
        texts = [f"这是一张{c}类商品的照片" for c in self.PRODUCT_CATEGORIES]
        probs = self._encode_pair(image, texts)

        top_idx = np.argmax(probs)
        top_score = float(probs[top_idx])

        # Top 3
        sorted_indices = np.argsort(probs)[::-1]
        top3 = [
            {"category": self.PRODUCT_CATEGORIES[i],
             "score": round(float(probs[i]), 4)}
            for i in sorted_indices[:3]
        ]

        return {
            "predicted_category": self.PRODUCT_CATEGORIES[top_idx],
            "confidence": round(top_score, 4),
            "top3": top3,
        }

    def verify_title_match(self, image: Image.Image,
                           title: str,
                           category: str = None) -> dict:
        """
        验证图片与商品标题是否匹配

        Args:
            image: 商品图片
            title: 商品标题
            category: 商品声称的类别（可选，用于更精确的验证）
        """
        # 构造对比文本
        match_texts = [
            f"这张图片与'{title}'描述的商品一致",
            f"这张图片与'{title}'描述的商品不一致",
        ]

        if category:
            match_texts.append(f"这是一张{category}类商品的图片")

        probs = self._encode_pair(image, match_texts)

        match_score = float(probs[0])
        mismatch_score = float(probs[1])

        # 判断逻辑
        if match_score > 0.6:
            verdict = "match"
        elif match_score > 0.35:
            verdict = "uncertain"
        else:
            verdict = "mismatch"

        return {
            "verdict": verdict,
            "match_score": round(match_score, 4),
            "mismatch_score": round(mismatch_score, 4),
            "title": title,
            "action": "approve" if verdict == "match" else
                      "manual_review" if verdict == "uncertain" else "reject",
        }

    def full_audit(self, image: Image.Image, title: str) -> dict:
        """完整审核流程"""
        # 步骤1: 违规检查
        forbidden_check = self.check_forbidden(image)

        # 步骤2: 类别分类
        category_check = self.classify_product(image)

        # 步骤3: 图文匹配
        match_check = self.verify_title_match(
            image, title, category=category_check["predicted_category"]
        )

        # 综合判定
        audit_pass = (
            forbidden_check["is_safe"]
            and match_check["verdict"] in ("match", "uncertain")
        )

        return {
            "audit_pass": audit_pass,
            "forbidden": forbidden_check,
            "category": category_check,
            "title_match": match_check,
            "final_action": "approve" if audit_pass else "reject_or_review",
        }


# ===== 使用示例 =====
if __name__ == "__main__":
    auditor = ProductImageAuditor()

    # 模拟商品图片
    test_image = Image.new("RGB", (224, 224), color=(200, 180, 160))

    # 完整审核
    result = auditor.full_audit(test_image, "春季新款女士连衣裙")
    print(json.dumps(result, ensure_ascii=False, indent=2))
```

### 3.4 多模态 Processor 深入

```python
# processor_demo.py
"""理解多模态 Processor 的工作机制"""

from transformers import CLIPProcessor, AutoProcessor
from PIL import Image
import torch


def demo_processor():
    """Processor = Tokenizer + ImageProcessor"""
    processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

    # 创建测试图片
    image = Image.new("RGB", (224, 224), color=(255, 100, 50))

    # 同时处理文本和图片
    texts = ["一只猫", "一条狗", "一只鸟"]
    inputs = processor(
        text=texts,
        images=image,
        return_tensors="pt",
        padding=True,
    )

    print("Processor 输出:")
    print(f"  input_ids shape:    {inputs['input_ids'].shape}")       # (3, 77)
    print(f"  attention_mask shape: {inputs['attention_mask'].shape}") # (3, 77)
    print(f"  pixel_values shape:   {inputs['pixel_values'].shape}")   # (1, 3, 224, 224)

    # 文本部分 = Tokenizer 的职责
    print(f"\n文本 token 示例（第一条）:")
    tokens = processor.tokenizer.convert_ids_to_tokens(inputs["input_ids"][0])
    print(f"  {tokens[:15]}")

    # 图片部分 = ImageProcessor 的职责
    print(f"\n图片预处理 = ImageProcessor 的职责:")
    print(f"  原始图片 size: {image.size}")
    print(f"  pixel_values shape: {inputs['pixel_values'].shape}")
    print(f"  值范围: [{inputs['pixel_values'].min():.2f}, {inputs['pixel_values'].max():.2f}]")
    print(f"  归一化方式: CLIP 使用 mean=[0.481, 0.457, 0.408], std=[0.268, 0.261, 0.275]")


if __name__ == "__main__":
    demo_processor()
```

### 3.5 测试验证

```python
# test_multimodal.py
import pytest
from PIL import Image
from clip_similarity import CLIPMatcher

@pytest.fixture(scope="module")
def matcher():
    return CLIPMatcher()

class TestCLIPMatcher:
    def test_similarity_output_format(self, matcher):
        img = Image.new("RGB", (224, 224), color="blue")
        results = matcher.compute_similarity(img, ["蓝色", "红色", "绿色"])
        assert len(results) == 3
        assert "score" in results[0]
        assert results[0]["rank"] == 1

    def test_zero_shot_classify(self, matcher):
        img = Image.new("RGB", (224, 224), color="green")
        result = matcher.zero_shot_classify(img, ["猫", "狗"])
        assert "category" in result
        assert "confidence" in result

    def test_output_scores_sum_to_one(self, matcher):
        img = Image.new("RGB", (224, 224), color="red")
        results = matcher.compute_similarity(img, ["A", "B", "C"])
        scores_sum = sum(r["score"] for r in results)
        assert abs(scores_sum - 1.0) < 0.01  # softmax 归一化
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **CLIP 零样本能力** | 无需标注数据即可分类/匹配 | 精度低于领域微调的专有模型 |
| **图文统一向量空间** | 跨模态语义比较直观 | 对抽象概念（如"品质""奢华"）理解有限 |
| **Processor 统一接口** | 文本+图片一条调用 | 批次中图片尺寸不同时 padding 复杂 |
| **ViT 视觉编码器** | 比传统 CNN 更灵活 | 推理比 ResNet 慢，需要更多显存 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 商品图文一致性审核 | CLIP 零样本 + 置信度阈值 |
| 图片自动分类（无标注数据） | CLIP 零样本分类 |
| 以图搜图/以文搜图 | CLIP 向量编码 + FAISS |
| 内容审核（敏感图片过滤） | CLIP + 违禁类别描述 |

**不适用场景**：
- 需要精确 OCR 文字提取 → 用 PaddleOCR/Tesseract
- 医学影像诊断 → 需要专门的医学多模态模型
- 细粒度分类（如汽车型号识别）→ 需在领域数据上微调 CLIP

### 4.3 注意事项

1. **图片预处理一致性**：推理时必须使用与训练相同的 `processor`（含 resize、crop、normalize）
2. **prompt 工程**：零样本分类时，类别描述的措辞影响结果——"一张狗的照片" vs "狗"，前者效果更好
3. **显存管理**：CLIP ViT-L/14 需要约 2GB 显存，batch 图片时注意 `pixel_values` 的形状

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 所有图片分类到同一个类别 | prompt 措辞有偏差（某个类描述太泛） | 统一模板，如"一张{class}的照片" |
| 相似度全为 0.5 左右 | 图片未正确归一化 | 使用 `processor(images=...)` 而非手动 `transforms` |
| RGB vs BGR 混乱 | PIL 是 RGB，OpenCV 是 BGR | 统一用 PIL 或 `cv2.cvtColor(img, cv2.COLOR_BGR2RGB)` |

### 4.5 思考题

1. **初级**：在 CLIP 零样本分类中，如果将 categories 从 ["猫", "狗"] 改为 ["一张可爱的猫", "一条忠诚的狗"]，分类结果会变化吗？为什么？
2. **进阶**：设计一个**图文联合检索系统**——支持用户上传图片 + 输入文字描述，同时利用图片和文字信息检索最匹配的商品。（提示：图片向量和文本向量如何融合？）

（答案将在第29章末尾给出）

### 4.6 第27章思考题答案

**第27章思考题1**：
- 实现 `diff_versions(v1, v2)`：分别调用 `get_version(v1)` 和 `get_version(v2)`，对比两者的 `metrics`、`dataset`、`training_date`、`base_model` 字段，输出差异 JSON。关键代码：`{k: (m1.get(k), m2.get(k)) for k in set(m1) | set(m2) if m1.get(k) != m2.get(k)}`。

**第27章思考题2**：
- 影子流量系统：(1) 在推理服务中对每个请求并行调用新旧两个模型；(2) 新模型的预测结果不返回给用户，而是写入独立的日志 topic（如 Kafka）；(3) 后台定期消费影子流量日志，计算新旧模型的预测一致性、置信度分布差异；(4) 若连续 N 小时影子模型各项指标无异常，可发起灰度切换。存储用 Parquet 列式格式，按小时分区。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 将 `ProductImageAuditor` 集成到商品发布流程，商家上传图片时自动触发审核 |
| **测试团队** | 准备 500 张标注过的商品图片（含违规、不匹配、正常三类），计算审核准确率 |
| **运维团队** | CLIP 模型首次加载需下载约 600MB 权重，提前在内网缓存 |

---

> **下一章预告**：第29章将进入 Agent 智能体——让大模型从"会说话"走向"会办事"，实现能查询天气、计算价格、检索知识库的客服 Agent。
