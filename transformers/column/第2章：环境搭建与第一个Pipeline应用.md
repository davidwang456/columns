# 第2章：环境搭建与第一个 Pipeline 应用

## 1 项目背景

### 业务场景

某电商公司内容运营团队每天要撰写 30 条商品详情页文案。这些文案要求既能突出商品卖点，又要有情感共鸣。运营小周的日常工作流程是：参考竞品文案、手动改写、让组长审核，平均一条文案耗时 25 分钟。随着新商品上架频率从每周 50 款增加到每周 200 款，文案产出成了瓶颈。

运营总监找到技术团队，希望用 AI 辅助写文案。技术经理把这个任务交给了刚转岗做 NLP 的应届生小林——要求三天内交付一个可运行的原型："不要求多完美，但要让运营团队能用上，感受一下 AI 的能力。"

小林打开 Hugging Face 文档，准备搭环境，结果第一步就卡住了：Python 版本、PyTorch 版本、TensorFlow 还是 PyTorch？CUDA 12.1 还是 11.8？模型下载到一半网络断了怎么办？要不要用 Docker？

### 痛点放大

环境搭建是 NLP 新人的第一道门槛，常见问题如下：

1. **版本矩阵困惑**：`transformers` 依赖 `torch>=1.10`，但 `torch 2.0` 的 API 有 breaking change。`tokenizers`（Rust 版本）和 `tokenizers`（纯 Python 版本）性能差 10 倍。版本选择错误轻则功能残缺，重则无法运行。
2. **模型下载失败**：Hugging Face Hub 托管在国外，直连下载 500MB 的模型经常中断。内网环境下整个团队每人都要下载一遍，浪费带宽和时间。
3. **CUDA 踩坑**：`torch.cuda.is_available()` 返回 False 但显卡驱动确实装了——驱动版本、CUDA Toolkit 版本、PyTorch 编译版本三者必须匹配。
4. **第一个 pipeline 就跑不通**：`pipeline("sentiment-analysis")` 默认下载英文模型，丢一段中文进去输出全是 POSITIVE。

```
┌──────────┐     ┌──────────────┐    ┌───────────┐
│ 选择 Python │────→│ 装 PyTorch    │───→│ CUDA 未知 │
│ 版本       │     │ 装错 CUDA 版  │    │ 错误？？  │
└──────────┘     └──────────────┘    └───────────┘
                       │
                       ▼
                ┌──────────────┐
                │ 模型下载中断  │
                │ 内网下不动    │
                └──────────────┘
```

本章的目标是：把环境搭建的"标准答案"一次性给出来，让读者在 15 分钟内从零到跑通第一个中文情感分析应用。

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周二下午 2:00，茶水间，小林对着笔记本屏幕叹气。小胖端着一盒蛋挞过来。

---

**小胖**："小林你咋愁眉苦脸的？来，吃个蛋挞。"

**小林**："别提了。我按官网教程装 PyTorch，跑 `torch.cuda.is_available()` 输出 False。我们公司机器明明插了 RTX 3060 显卡。"

**小胖**："这不就跟食堂刷卡机一样嘛！机器是好的，卡也是好的，但系统没对接到位就刷不了。你是驱动没装还是 CUDA 版本不对？"

**小林**："驱动装了 535.xx，CUDA Toolkit 装了 12.1，PyTorch 官网明明写了支持 CUDA 12.1。但就是认不出 GPU。"

**小白**（端着咖啡走过来）:"你用的是 `pip install torch` 还是 `pip install torch --index-url ...`？默认 pip 装的是 CPU 版本，这是最高频的坑。还有，你确定你的 CUDA Toolkit 跟 PyTorch 的 CUDA 是同一个概念吗？PyTorch 自带 CUDA runtime，不需要系统装 CUDA Toolkit——只要驱动版本够就行。"

**小胖**:"等等，小白你说慢点。啥叫 PyTorch 自带 CUDA？那 NVIDIA 官网那个几 GB 的 CUDA Toolkit 是干啥用的？"

**大师**（路过，停下）:"好问题。我来厘清：CUDA Toolkit 是给编译 C++ 扩展用的，比如写自定义 CUDA kernel。PyTorch 的 pip 包已经内嵌了 CUDA runtime library，你只需要保证 NVIDIA 驱动版本就行。跑通 `nvidia-smi` 看到驱动版本 >= 450.80.02（针对 CUDA 11.x），pip install 时选对 index-url 就行。"

**大师** 在白板上写：

```
PyTorch 的 GPU 环境 = NVIDIA 驱动(≥ 450) + pip install torch --index-url 选对 CUDA 版本
不需要系统安装完整 CUDA Toolkit！
nvidia-smi 显示的是驱动支持的最高 CUDA 版本，但 PyTorch 不依赖它
```

**小林**:"那我还有个问题。模型下载太慢了，一个 bert-base-chinese 400MB，我下了三次都中断了。"

**大师**:"两个方案：一是设置 Hugging Face 镜像，`export HF_ENDPOINT=https://hf-mirror.com`；二是搭建团队级缓存——在公司服务器上预下载常用模型，其他人通过 `cache_dir` 指向共享路径。第二种方案对整个团队都有价值。"

**小胖**:"还有最后一个问题！教程里默认的 `pipeline('sentiment-analysis')` 是英文模型，我输入中文'这个东西真烂'，它居然输出 POSITIVE。我差点拿去给运营看了。"

**大师**（笑）:"这就是开篇说的——默认 pipeline 自动选的是 `distilbert-base-uncased-finetuned-sst-2-english`，它是用英文电影评论训练的，看到中文 token 完全乱码。解决方案是指定中文模型：`pipeline('sentiment-analysis', model='uer/roberta-base-finetuned-jd-binary-chinese')`。记住，改 model 参数和测试模型效果是一个工程师的基本素养。"

**小白**:"那大师，我怎么知道一个模型在 Hugging Face 上是否支持中文？上面几十万个模型呢。"

**大师**:"筛选技巧：在 Models 页面搜索 `chinese text-classification`，看 model card 里有没有中文评测数据，下载量高不高，最近有没有更新。另外，中文模型优先看哈工大（HIT）、清华大学（THU）、UER 团队和阿里（alibaba）发布的。我把常用中文模型清单发你们群里。"

**技术映射总结**：
- PyTorch GPU 安装 = `pip install torch --index-url https://download.pytorch.org/whl/cu118`（cu118 对应 CUDA 11.8 版本）
- Hugging Face Hub 下载慢 = 设置镜像 `HF_ENDPOINT` 或搭建内网缓存
- pipeline 默认模型不适用于中文 = 显式指定 `model` 参数为中文预训练模型

---

## 3 项目实战

### 3.1 环境准备

#### 目标

在 15 分钟内完成环境搭建，运行一个中文情感分析 Demo。

#### 步骤 1：检查硬件环境

```bash
# 查看 GPU 信息（有 GPU 的情况）
nvidia-smi

# 输出示例：
# +-----------------------------------------------------------------------------+
# | NVIDIA-SMI 535.129.03   Driver Version: 535.129.03   CUDA Version: 12.2     |
# |-------------------------------+----------------------+----------------------+
# | GPU  Name        Persistence-M| Bus-Id        Disp.A | Volatile Uncorr. ECC |
# | Fan  Temp  Perf  Pwr:Usage/Cap|         Memory-Usage | GPU-Util  Compute M. |
# |   0  NVIDIA GeForce RTX 3060  | 00000000:01:00.0 On  |                  N/A |
# +-------------------------------+----------------------+----------------------+
```

注意：`nvidia-smi` 右上角的 "CUDA Version: 12.2" 表示驱动支持的最高 CUDA 版本，不代表系统安装了 CUDA Toolkit。只要驱动版本>=450，就可以安装 PyTorch。

```bash
# 检查 Python 版本
python --version   # 需要 3.8-3.12
```

#### 步骤 2：创建虚拟环境

```bash
# Windows (PowerShell)
python -m venv transformers-env
.\transformers-env\Scripts\Activate.ps1

# Linux / macOS
python -m venv transformers-env
source transformers-env/bin/activate

# 确认环境隔离
which python  # 应该指向 transformers-env 目录
```

#### 步骤 3：安装依赖

**CPU 环境**（用于验证流程、小数据量场景）：

```bash
pip install transformers==4.44.0 torch --index-url https://download.pytorch.org/whl/cpu
pip install datasets==2.21.0 tokenizers==0.19.1
```

**GPU 环境（CUDA 11.8）**：

```bash
pip install transformers==4.44.0 torch --index-url https://download.pytorch.org/whl/cu118
pip install datasets==2.21.0 tokenizers==0.19.1
```

**GPU 环境（CUDA 12.1）**：

```bash
pip install transformers==4.44.0 torch --index-url https://download.pytorch.org/whl/cu121
pip install datasets==2.21.0 tokenizers==0.19.1
```

**踩坑提示**：如果下载慢，设置 pip 和 huggingface 双重镜像：

```bash
# pip 使用清华源
pip install transformers torch datasets -i https://pypi.tuna.tsinghua.edu.cn/simple

# Hugging Face 使用镜像（Windows PowerShell）
$env:HF_ENDPOINT = "https://hf-mirror.com"

# Hugging Face 使用镜像（Linux/macOS）
export HF_ENDPOINT=https://hf-mirror.com
```

#### 步骤 4：验证安装

创建 `verify_env.py`：

```python
import sys
import torch
import transformers
import tokenizers

print("=" * 50)
print("环境验证报告")
print("=" * 50)
print(f"Python 版本:    {sys.version}")
print(f"PyTorch 版本:   {torch.__version__}")
print(f"Transformers:   {transformers.__version__}")
print(f"Tokenizers:     {tokenizers.__version__}")
print(f"GPU 可用:       {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU 设备名:     {torch.cuda.get_device_name(0)}")
    print(f"GPU 数量:       {torch.cuda.device_count()}")
    print(f"当前 GPU 显存:  {torch.cuda.mem_get_info()[0] // 1024**3} GB")
print("=" * 50)

# 验证模型加载
from transformers import pipeline
try:
    pipe = pipeline("text-classification", model="distilbert-base-uncased-finetuned-sst-2-english")
    print("模型加载:       成功 ✓")
except Exception as e:
    print(f"模型加载:       失败 ✗ ({e})")
```

运行：

```bash
python verify_env.py
```

### 3.2 第一个中文 Pipeline 应用："AI 文案小助手"

#### 目标

构建一个命令行工具，输入商品描述，输出情感倾向和改写建议。

#### 完整代码

创建 `copywriter_assistant.py`：

```python
#!/usr/bin/env python3
"""AI 文案小助手 —— 输入商品描述，输出情感分析和改写建议"""

import sys
import time
from transformers import pipeline

class CopywriterAssistant:
    def __init__(self):
        print("正在加载模型，首次运行需要下载，请稍候...")
        start = time.time()

        # 中文情感分析模型（基于京东评论数据微调）
        self.sentiment = pipeline(
            "text-classification",
            model="uer/roberta-base-finetuned-jd-binary-chinese",
            device=-1  # -1 表示 CPU，0 表示第一块 GPU
        )

        # 中文文本生成模型
        self.generator = pipeline(
            "text-generation",
            model="uer/gpt2-chinese-cluecorpussmall",
            device=-1
        )

        load_time = time.time() - start
        print(f"模型加载完成，耗时 {load_time:.1f} 秒\n")

    def analyze_sentiment(self, text: str) -> dict:
        """分析文案情感倾向"""
        return self.sentiment(text)[0]

    def rewrite_copy(self, text: str, style: str = "精简") -> str:
        """基于原始描述生成改写建议"""
        prompts = {
            "精简": f"请将以下商品描述精简为一句话：{text}\n精简版：",
            "卖点": f"请提取以下商品描述的核心卖点：{text}\n核心卖点：",
            "情感": f"请用更具情感共鸣的语言改写以下商品描述：{text}\n改写：",
        }
        prompt = prompts.get(style, prompts["精简"])
        result = self.generator(prompt, max_length=100, num_return_sequences=1)[0]
        # 去掉 prompt 本身，只返回生成的部分
        generated = result["generated_text"][len(prompt):].strip()
        return generated if generated else "(生成失败，请尝试其他风格)"

    def run(self):
        print("=" * 60)
        print("  🤖 AI 文案小助手 - v1.0")
        print("  支持功能: 情感分析 | 文案改写（精简/卖点/情感）")
        print("  输入 'exit' 退出，输入 '风格' 切换改写风格")
        print("=" * 60)

        current_style = "精简"
        while True:
            text = input("\n请输入商品描述: ").strip()

            if text.lower() == "exit":
                print("再见！")
                break
            if text == "风格":
                styles = ["精简", "卖点", "情感"]
                idx = (styles.index(current_style) + 1) % len(styles)
                current_style = styles[idx]
                print(f"切换改写风格为: {current_style}")
                continue
            if not text:
                print("输入不能为空，请重新输入。")
                continue

            # 情感分析
            sent = self.analyze_sentiment(text)
            label = "正面 😊" if sent["label"] == "positive (stars 4 and 5)" else "负面 😞"
            print(f"\n📊 情感分析: {label} (置信度: {sent['score']:.2%})")

            # 文案改写
            rewritten = self.rewrite_copy(text, style=current_style)
            print(f"✏️  改写建议 ({current_style}): {rewritten}")


if __name__ == "__main__":
    assistant = CopywriterAssistant()
    assistant.run()
```

**交互示例**：

```
$ python copywriter_assistant.py
正在加载模型，首次运行需要下载，请稍候...
模型加载完成，耗时 8.3 秒

============================================================
  AI 文案小助手 - v1.0
  支持功能: 情感分析 | 文案改写（精简/卖点/情感）
  输入 'exit' 退出，输入 '风格' 切换改写风格
============================================================

请输入商品描述: 这款蓝牙耳机音质清晰，续航长达30小时，但是佩戴久了耳朵有点疼。

📊 情感分析: 正面 😊 (置信度: 72.35%)
✏️  改写建议 (精简): 这款蓝牙耳机音质清晰，续航长达30小时，但佩戴感有待改善。

请输入商品描述: 杯子质量太差了，用了一周就裂了，客服还不理人。

📊 情感分析: 负面 😞 (置信度: 95.12%)
✏️  改写建议 (精简): 杯子质量差，使用一周就出现裂痕，客服态度也需要改善。
```

### 3.3 模型缓存管理

```bash
# 查看当前缓存目录
python -c "from transformers.utils import HF_MODULES_CACHE; print(HF_MODULES_CACHE)"
# 默认: ~/.cache/huggingface/hub

# 修改缓存目录（Windows PowerShell）
$env:HF_HOME = "D:\models\huggingface"

# 修改缓存目录（Linux/macOS）
export HF_HOME=/data/models/huggingface

# 查看已下载的模型
python -c "
from huggingface_hub import scan_cache_dir
cache = scan_cache_dir()
for repo in cache.repos:
    print(f'{repo.repo_id}: {repo.size_on_disk_str}')
"

# 清理未使用的缓存（保留最近 7 天使用过的模型）
# pip install huggingface_hub[hf_transfer]
# huggingface-cli delete-cache --disable-tqdm
```

### 3.4 测试验证

```python
# test_assistant.py
from copywriter_assistant import CopywriterAssistant


def test_sentiment_positive():
    assistant = CopywriterAssistant()
    result = assistant.analyze_sentiment("这个东西非常好用，强烈推荐！")
    assert "score" in result
    assert result["score"] > 0.5

def test_sentiment_negative():
    assistant = CopywriterAssistant()
    result = assistant.analyze_sentiment("太失望了，完全是垃圾产品。")
    assert "score" in result

def test_rewrite():
    assistant = CopywriterAssistant()
    result = assistant.rewrite_copy("这款产品性价比很高，推荐购买。", style="精简")
    assert isinstance(result, str)
    assert len(result) > 0
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **环境搭建** | venv 隔离避免依赖冲突，pip 一行装好 | CUDA 版本选择需要适配驱动版本，初学者容易选错 |
| **Pipeline API** | 一行代码完成模型加载+推理+后处理 | 默认英文模型对中文不友好，需要额外指定 model 参数 |
| **模型下载** | 支持镜像加速、离线缓存、断点续传 | 首次下载 400MB+ 仍需较好网络，内网需搭建代理 |
| **可维护性** | 代码量少，易封装为 CLI 工具 | 模型和代码耦合，升级模型需要同步修改代码 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 快速原型验证 | `pipeline("text-classification")` 一行出结果 |
| 内网离线环境 | 提前下载模型到共享目录，`model=/path/to/local/model` |
| 多模型切换 | 封装 `load_model(model_name)` 工厂函数 |
| CI/CD 自动化测试 | 用小模型（如 `distilbert-base`）代替大模型加速测试 |

**不适用场景**：
- 高并发在线服务（pipeline 每次调用都重新初始化，性能差，应使用单例模式 + 异步队列）
- 超大模型（>7B 参数）的单卡推理（需要 GPU 显存优化或模型分片）

### 4.3 注意事项

1. **镜像源配置**：生产环境建议在 Dockerfile 中固化 `HF_ENDPOINT`，避免每次构建重新下载
2. **版本锁定**：`requirements.txt` 中应锁定 `transformers==4.44.0` 具体版本，避免自动升级导致 breaking change
3. **内存管理**：CPU 推理大模型可能占用 2GB+ 内存，建议在容器中配置 `--memory 4g`

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| `torch.cuda.is_available()` 返回 False | 安装了 CPU 版 PyTorch | 卸载后重新从 `download.pytorch.org/whl/cu*` 安装 |
| `ConnectionError: (MaxRetryError)` | 直连 Hugging Face Hub 超时 | 设置 `HF_ENDPOINT=https://hf-mirror.com` |
| `ImportError: cannot import name 'pipeline'` | Transformers 版本过旧（<3.0） | `pip install --upgrade transformers` |
| 中文情感分析全输出 positive | 用了默认的英文模型 `sst-2-english` | 显式指定中文模型 |

### 4.5 思考题

1. **初级**：在 `copywriter_assistant.py` 中，如果换用 `model="lxyuan/distilbert-base-multilingual-cased-sentiments-student"`（多语言模型），中文分析效果会有什么变化？请实际测试并记录。
2. **进阶**：`pipeline` 每次调用 `sentiment(text)` 会重新加载模型吗？如何验证？（提示：使用 `time.time()` 记录首次调用和第二次调用的耗时）

### 4.6 第1章思考题答案

**第1章思考题1答案**：
- `temperature=0.1`：输出确定性高，几乎每次生成相同的文本，适合需要稳定输出的场景
- `temperature=0.7`：输出有一定随机性，是目前主流默认值
- `temperature=1.5`：输出很随机，常出现跑题、无关内容
- 原理：temperature 控制 softmax 分布平滑程度，值越大词概率分布越均匀，随机性越高

**第1章思考题2答案**：
- 英文 BERT 的 tokenizer 采用 WordPiece 分词，中文会被切分为 Unicode 字节级 token
- 例如"你好"可能被切分为 `[UNK]`（未登录词），因为英文词表中没有中文字符
- 大量 `[UNK]` token 导致模型无法理解语义，输出不可靠
- 解决方案：使用 BERT 的 multilingual 版本或专门的中文预训练模型

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 将 `copywriter_assistant.py` 集成到商品管理后台的 Web API，用 FastAPI 包装为 HTTP 接口 |
| **测试团队** | 准备 200 条中文商品描述（含好评/差评/中性/混合情绪），人工标注后测试准确率 |
| **运维团队** | 提前在内网服务器下载 `uer/roberta-base-finetuned-jd-binary-chinese` 和 `uer/gpt2-chinese-cluecorpussmall` 两个模型，配置共享缓存 |

---

> **下一章预告**：第3章将深入 Tokenizer——为什么模型不能直接读中文？BPE、WordPiece、SentencePiece 有何区别？如何为客服工单构建预处理脚本，确保长文本、表情、繁简体混合输入不出错？
