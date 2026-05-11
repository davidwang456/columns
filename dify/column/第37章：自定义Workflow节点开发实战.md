# 第37章：自定义 Workflow 节点开发实战

## 1. 项目背景

Dify 内置了 20+ 种 Workflow 节点。但当金融客户要求"所有 AI 回复必须经过数据脱敏——手机号中间 4 位打星、身份证中间 10 位打星、邮箱前 2 位保留"时，内置节点不够用了。Code 节点可以写正则，但每个 Workflow 都要复制粘贴一遍——团队维护成本高。

你需要开发一个**可拖拽的、可配置的、可复用的"数据脱敏节点"**。本章带你完整走通自定义节点的全流程——**后端 Python 类**（定义输入输出 + 核心算法）、**注册到 NodeFactory**、**前端 React 组件**（画布上的节点卡片）、**单元测试**。读完本章，你将具备开发任意自定义节点的能力。

## 2. 项目设计——剧本式交锋对话

**小胖**："大师，公司合规要求所有 AI 回复里的手机号和身份证号自动打星。我用 Code 节点写了个正则——问题是每个 Workflow 都要复制粘贴一遍，而且团队其他人不知道怎么用。能不能封装成一个可拖拽的节点？"

**大师**："当然可以。自定义节点只需要三步：**① 后端**——写一个继承 `BaseNode` 的类，实现 `get_default_config()`（定义节点在画布上的名称、输入输出 Schema）和 `_run()`（核心执逻辑——收到输入文本，返回脱敏后的文本）。**② 注册**——在 `node_factory.py` 的 `NODE_TYPE_MAP` 字典里加一行映射（如 `'data_masking': DataMaskingNode`）。**③ 前端**——写一个 React 组件展示节点在画布上的样子（图标 + 标题 + 当前配置摘要）。三步累计约 2 小时工作量。"

**小白**："前端的配置面板呢？如果我想让用户在下拉框里选'脱敏类型：手机号/邮箱/身份证'，怎么实现？"

**大师**："`get_default_config()` 返回的 `inputs` 数组就是配置面板的参数列表。每个参数有 `type`（决定前端渲染什么控件）、`label`（控件显示名）、`options`（如果 type 是 select，这就是下拉框的选项）。Dify 前端会根据这个 Schema **自动渲染**配置面板——不需要你写 HTML。你只需要声明'我需要一个下拉框，选项是手机号/邮箱/身份证'。"

**技术映射**：节点配置面板 = `get_default_config()` 中的 `inputs` 数组。Dify 前端根据 Schema 自动渲染，无需手写 HTML。

## 3. 项目实战

### 步骤 1：后端节点实现

```python
# api/core/workflow/nodes/data_masking/data_masking_node.py
import re
from typing import Optional
from pydantic import BaseModel
from core.workflow.nodes.base import BaseNode
from core.workflow.node_runtime import NodeRuntime

class DataMaskingNodeData(BaseModel):
    """节点的配置数据结构"""
    input_text: str          # 待脱敏的输入文本
    mask_type: str = "phone" # 脱敏类型：phone/email/id_card/custom
    custom_pattern: Optional[str] = None  # 自定义正则（仅 mask_type=custom 时使用）

class DataMaskingNode(BaseNode):
    """
    数据脱敏节点
    功能：对文本中的敏感信息（手机号、邮箱、身份证）进行打星号脱敏
    场景：金融/医疗等合规要求场景
    """
    
    # ★ 脱敏规则：正则匹配 → 替换模板
    MASK_RULES = {
        'phone':   (r'(1[3-9]\d)\d{4}(\d{4})', r'\1****\2'),          # 138****1234
        'email':   (r'(.{2}).*(@.*)', r'\1****\2'),                    # ab****@mail.com
        'id_card': (r'(\d{4})\d{10}(\d{4})', r'\1**********\2'),      # 1101**********1234
    }
    
    @classmethod
    def get_node_type(cls) -> str:
        """★ 唯一节点类型标识——在 DSL 和 NodeFactory 中使用"""
        return "data_masking"
    
    @classmethod
    def get_default_config(cls) -> dict:
        """
        ★ 定义节点的默认配置
        包含：节点名称、描述、输入参数 Schema、输出参数 Schema
        Dify 前端根据此 Schema 自动渲染配置面板
        """
        return {
            "type": "data_masking",
            "title": "数据脱敏",
            "description": "对文本中的手机号、邮箱、身份证号进行打星号脱敏处理。适用于金融、医疗等合规场景。",
            "inputs": [
                {
                    "name": "input_text",
                    "type": "string",
                    "required": True,
                    "label": "输入文本",
                    "description": "需要进行脱敏处理的原始文本。可以引用上游节点的输出。"
                },
                {
                    "name": "mask_type",
                    "type": "select",
                    "required": True,
                    "label": "脱敏类型",
                    "default": "phone",
                    "options": [
                        {"value": "phone", "label": "手机号"},
                        {"value": "email", "label": "邮箱"},
                        {"value": "id_card", "label": "身份证号"},
                    ]
                },
            ],
            "outputs": [
                {
                    "name": "masked_text",
                    "type": "string",
                    "label": "脱敏后文本",
                    "description": "经过星号替换后的文本。下游节点可通过 {{#节点名.masked_text#}} 引用。"
                }
            ]
        }
    
    def _run(self, runtime: NodeRuntime) -> dict:
        """★ 核心执行逻辑——从 runtime 获取输入、执行脱敏、返回输出"""
        # Step 1: 获取输入参数
        input_text = runtime.get_input('input_text')
        mask_type = runtime.get_input('mask_type', 'phone')
        
        # Step 2: 查找脱敏规则并执行替换
        if mask_type in self.MASK_RULES:
            pattern, replacement = self.MASK_RULES[mask_type]
            masked_text = re.sub(pattern, replacement, input_text)
        
        elif mask_type == 'custom':
            # 自定义正则（暂不支持，留作扩展点）
            masked_text = input_text
        
        else:
            masked_text = input_text  # 未知类型，原样返回
        
        # Step 3: 返回结构化输出（字段名必须与 get_default_config 中的 outputs 一致）
        return {"masked_text": masked_text}
```

### 步骤 2：注册节点到 NodeFactory

```python
# api/core/workflow/node_factory.py
from core.workflow.nodes.data_masking.data_masking_node import DataMaskingNode

class DifyNodeFactory:
    NODE_TYPE_MAP = {
        "start": StartNode,
        "end": EndNode,
        "llm": LLMNode,
        "code": CodeNode,
        "if-else": IfElseNode,
        "iteration": IterationNode,
        "http-request": HTTPRequestNode,
        # ... 更多内置节点
        "data_masking": DataMaskingNode,  # ★ 就这一行！
    }
    
    def create(self, node_config: dict):
        node_type = node_config.get('type')
        node_cls = self.NODE_TYPE_MAP.get(node_type)
        if not node_cls:
            raise UnknownNodeTypeError(f"未知节点类型: {node_type}")
        return node_cls(node_config)
```

### 步骤 3：前端节点卡片组件

```tsx
// web/app/components/workflow/nodes/data-masking/node.tsx
import { FC, memo } from 'react';
import type { NodeProps } from '@/app/components/workflow/types';

const DataMaskingNode: FC<NodeProps> = ({ id, data }) => {
  const maskTypeLabel = {
    phone: '手机号',
    email: '邮箱',
    id_card: '身份证号',
  }[data.inputs?.mask_type || 'phone'] || '手机号';

  return (
    <div className="flex flex-col gap-1 p-3 rounded-lg border border-gray-200 bg-white shadow-sm min-w-[200px]">
      {/* 节点头部：图标 + 标题 */}
      <div className="flex items-center gap-2">
        <span className="text-lg">🔒</span>
        <span className="font-medium text-sm text-gray-900">
          {data.title || '数据脱敏'}
        </span>
      </div>
      
      {/* 节点信息：当前配置摘要 */}
      <div className="text-xs text-gray-500 space-y-0.5">
        <div>脱敏类型: {maskTypeLabel}</div>
        {data.inputs?.input_text && (
          <div className="truncate max-w-[180px]">
            输入: {data.inputs.input_text}
          </div>
        )}
      </div>
    </div>
  );
};

export default memo(DataMaskingNode);
```

### 步骤 4：单元测试

```python
# tests/unit_tests/core/workflow/nodes/test_data_masking_node.py
import pytest
from core.workflow.nodes.data_masking.data_masking_node import DataMaskingNode

class TestDataMaskingNode:
    
    def test_phone_masking(self):
        """测试手机号脱敏"""
        node = DataMaskingNode()
        text = "联系客服：13812345678 或 13987654321"
        result = node._apply_mask(text, 'phone')
        assert "13812345678" not in result  # 原始号码不应出现
        assert "138****5678" in result      # 应出现脱敏后号码
        assert "139****4321" in result      # 两个号码都脱敏
    
    def test_email_masking(self):
        """测试邮箱脱敏"""
        text = "邮箱：zhangsan@example.com"
        result = DataMaskingNode._apply_mask(text, 'email')
        assert "zhangsan@example.com" not in result
        assert "zh****@example.com" in result
    
    def test_id_card_masking(self):
        """测试身份证脱敏"""
        text = "身份证号：110101199001011234"
        result = DataMaskingNode._apply_mask(text, 'id_card')
        assert "1101**********1234" in result
    
    def test_no_sensitive_data(self):
        """测试无敏感信息时原样返回"""
        text = "今天天气真好"
        result = DataMaskingNode._apply_mask(text, 'phone')
        assert result == text  # 无匹配内容，原样返回
    
    def test_mixed_data(self):
        """测试混合数据——多种敏感信息同时脱敏"""
        text = "张三，13812345678，zhang@example.com，110101199001011234"
        result = DataMaskingNode._apply_mask(text, 'phone')
        # 手机号已脱敏，邮箱和身份证保持原样（因为选的是 phone 模式）
        assert "138****5678" in result
        assert "zhang@example.com" in result
```

### 测试验证

```bash
# 运行单元测试
cd api && uv run pytest tests/unit_tests/core/workflow/nodes/test_data_masking_node.py -v

# 在 Dify 画布中使用自定义节点
# 1. 重启 API 容器使后端生效
# 2. 打开 Workflow 画布 → 左侧节点列表应出现"数据脱敏"
# 3. 拖入画布 → 连线 → 配置脱敏类型 → 运行
```

## 4. 项目总结

| 层级 | 文件 | 核心内容 | 工作量 |
|------|------|---------|-------|
| 后端逻辑 | `data_masking_node.py` | 继承 BaseNode, 实现 `_run()` + `get_default_config()` | ~30min |
| 节点注册 | `node_factory.py` | NODE_TYPE_MAP 加一行 | 1min |
| 前端卡片 | `node.tsx` | React 组件展示节点状态 | ~20min |
| 配置面板 | `panel.tsx` | Schema 自动渲染（无需手写） | ~0min |
| 单元测试 | `test_data_masking_node.py` | 5 个关键测试用例 | ~20min |

**思考题**：
1. 如何让节点支持输入数组——多条文本分别脱敏后输出数组？（提示：检测 input 类型，如果为 array 则对每个元素分别脱敏）
2. 如何将自定义节点贡献给 Dify 开源社区？（提示：遵循 CONTRIBUTING.md，提交 PR 包含后端 + 前端 + 测试 + 文档）

> **参考答案**：见附录 D
