# 第7章：Workflow 高级节点——让流程真正"智能"起来

## 1. 项目背景

上一章我们搭建了"开始→LLM→结束"的线性流水线，但现实中很少有业务流程是一条直线走到底的。比如 HR 部门需要处理员工请假流程：如果请假天数 ≤ 3 天，自动审批；如果 > 3 天，转给主管审批。又比如运营部门需要批量处理 500 条用户评论：逐条判断是好评还是差评，好评发给客服部门做案例，差评提取关键词分析原因，最后生成图表报告。

这些场景有三个核心诉求：**条件分支**（根据不同情况走不同路线）、**循环遍历**（对一批数据逐条处理）、**自定义代码**（在 LLM 能力不够的地方插入自己的逻辑）。Chat App 做不到这些，简单的线性 Workflow 也做不到。这就是本章要攻克的三大节点：IF/ELSE 节点、Iteration 节点和 Code 节点。

掌握这三个节点后，你的 Workflow 就从"单线铁路"升级为"立体交通网"——能根据条件自动分流、能批量处理数据、能在沙箱里执行自定义逻辑。配合模板转换和变量赋值器，你可以将 LLM 的非结构化输出转换成下游系统需要的结构化数据。最终，你会实现一个完整的"简历解析器"实例——上传一份简历 PDF，自动提取姓名、技能、经验并分类评分，输出结构化 JSON。

## 2. 项目设计——剧本式交锋对话

**小胖**：（在画布上拖了一堆节点，连线像蜘蛛网一样复杂）"大师，我现在想做个 Workflow：用户输入'我想请假'，如果理由里写了'病假'就走 A 路线（生成医院证明模板），如果写了'年假'就走 B 路线（查剩余年假天数），怎么写这个判断？"

**大师**："这就要用到 IF/ELSE 节点了。条件判断和我们写代码的 `if-else` 一样——定义条件，满足走 True 分支，不满足走 False 分支。在 Dify 里，IF/ELSE 节点的条件可以用变量表达式，比如 `{{#llm_output.text#}} contains '病假'`。"

**技术映射**：IF/ELSE 节点 = 工作流中的条件路由（Conditional Routing），将图从线性分支为树状。

**小白**："条件支持哪些运算符？"

**大师**："常用的有：
- **文本**：`contains`（包含）、`not contains`（不包含）、`is`（等于）、`is not`（不等）、`starts with`、`ends with`
- **数字**：`>`、`<`、`>=`、`<=`、`==`、`!=`
- **逻辑**：AND（所有条件同时满足）、OR（任一条件满足）

而且 IF/ELSE 支持多条件分支——不只是 True/False 两个出口，你可以添加 ELIF 实现多路分流。"

**小胖**："那如果我要处理一批数据呢？比如 100 条评论，我不想建 100 个 Workflow 实例。"

**大师**："Iteration 节点就是干这个的。你把一个数组传给 Iteration 节点，它会把数组里的每个元素分别丢给循环体内的节点依次处理，最后把所有结果收集到一个数组里输出。这就好比你有一筐苹果（数组），Iteration 节点逐个拿出苹果，在循环体里洗苹果→削皮→切块，最后得到一盘切好的苹果块（输出数组）。"

**技术映射**：Iteration 节点 = Workflow 中的 for-each 循环（Iterator Pattern）。

**小白**："那 Code 节点呢？我看它支持 Python 和 JavaScript，是在哪里运行的？"

**大师**："Code 节点是在 Dify Sandbox（沙箱）里运行的。你的 Python 代码被发送到 Sandbox 容器中执行，执行结果返回给 Workflow。沙箱是隔离的环境——它只能访问你传入的变量，不能访问数据库、文件系统、网络（默认配置下）。这就保证了安全——即使你写了恶意代码，也破坏不了 Dify 的系统。"

**小胖**："哦！那如果我的 Python 需要导入第三方库呢？比如我想用 `re` 做正则匹配，或者用 `json` 解析数据？"

**大师**："Sandbox 预装了一批常用库：`json`、`re`、`math`、`datetime`、`base64`、`hashlib` 等。你可以直接在代码里 `import` 使用。但如果要用 `pandas`、`numpy` 这种重型库，需要自定义 Sandbox 镜像安装。另外，Code 节点的执行有时间限制（默认 30 秒）和内存限制（默认 256MB），防止死循环和内存溢出。"

**技术映射**：Code 节点 = 沙箱中的自定义处理逻辑（Serverless Function in Workflow），弥补 LLM 的短板（精确计算、正则匹配、格式转换）。

**小白**："那模板转换节点和参数提取器又是什么场景用？"

**大师**：
- **模板转换**（Template Transform）：用 Jinja2 语法把多个变量拼成一段新文本。比如把 `name`、`age`、`skills` 三个变量拼成一段 Markdown 格式的个人简介。
- **参数提取器**（Parameter Extractor）：从 LLM 的非结构化输出中提取结构化字段。比如 LLM 输出了一段话："张三，3 年工作经验，擅长 Python 和 React"，参数提取器能自动提取出 `name: "张三"`，`experience: "3年"`，`skills: ["Python", "React"]`。"

**小胖**："感觉这些节点组合起来能做很多事情啊！比如搞一个'简历解析器'——上传 PDF，IF/ELSE 判断文件格式对不对，LLM 提取关键信息，参数提取器转成结构化 JSON，Code 节点评分，迭代节点批量处理……"

**大师**：（笑）"你已经开始用架构师的思维思考了。这就是 Workflow 的威力——把 AI 能力像乐高积木一样组合起来。接下来我们就把这个简历解析器做出来。"

## 3. 项目实战

### 环境准备

| 条件 | 说明 |
|------|------|
| Workflow 基础操作已掌握 | 第 6 章完成 |
| LLM Provider 已配置 | 第 3 章完成 |
| Dify Sandbox 正常运行 | `docker ps` 确认 sandbox 容器 running |

### 分步实现

#### 步骤1：IF/ELSE 多路分支——请假审批流程（目标：掌握条件路由）

1. 创建 Workflow → 命名为"请假审批"

2. 节点编排：

```
开始（输入：leave_type, days, reason）
    ↓
LLM（意图识别：判断请假类型和紧急程度）
    ↓
IF/ELSE（条件分支）
    ├── 条件1：{{#llm.text#}} contains "病假"  →  [True] LLM_病情（生成医院证明模板）
    ├── 条件2：{{#start.days#}} <= 3 →  [True] LLM_短假（自动审批回复）
    └── ELSE  →  LLM_长假（转主管审批回复）
    ↓（所有分支汇聚）
结束（输出审批结果）
```

3. IF/ELSE 节点配置详情：

```yaml
条件 1（ELIF）：
  变量：{{#llm_node_id.text#}}
  条件：contains
  值：病假

条件 2（ELIF）：
  变量：{{#start.days#}}
  条件：<=
  值：3

ELSE：
  其他所有情况
```

4. 测试不同输入：

```
测试 1：leave_type=病假, days=2, reason=感冒发烧
  → 走"病假"分支，生成医院证明模板

测试 2：leave_type=年假, days=2, reason=旅游
  → 走"<=3天"分支，自动审批

测试 3：leave_type=年假, days=10, reason=蜜月旅行
  → 走 ELSE 分支，转主管审批
```

**常见坑**：IF/ELSE 节点的每个分支需要独立的后续节点，不能多个分支连到同一个节点（需要先汇聚到结束节点）。如果必须汇聚，可以在分支末尾各放一个变量赋值器，写入同一个变量名。

#### 步骤2：Iteration 节点——批量处理列表数据（目标：实现循环遍历）

场景：LLM 从一段文本中提取出了多个实体（如人名列表），需要对每个实体逐一查询处理。

1. 新建 Workflow → "批量实体查询"

2. 节点编排：

```
开始（输入：text = "张三、李四、王五参加了会议"）
    ↓
LLM（提取人名列表）→ 输出："['张三', '李四', '王五']"
    ↓
Code 节点（将字符串转为数组）
    ↓
Iteration（循环处理每个人名）
    ├── 循环体内：
    │   LLM（查询该人员的部门信息）
    │   输出：{{#iteration.item#}} 在研发部
    ↓
Code 节点（收集所有结果）→ 输出：["张三在研发部", "李四在市场部", "王五在运维部"]
    ↓
结束
```

3. Code 节点（字符串转数组）：

```python
import json

def main(text: str):
    # 将 LLM 输出的字符串形式列表转为真正的数组
    try:
        result = json.loads(text.replace("'", '"'))
        return {"list": result}
    except:
        # 如果 JSON 解析失败，手动分割
        items = [item.strip() for item in text.strip("[]").split(",")]
        return {"list": items}
```

4. Iteration 节点配置：
   - 输入列表：`{{#code_node.list#}}`
   - 循环变量：`{{#iteration.item#}}`（当前迭代的元素）

**常见坑**：Iteration 节点的输入必须是数组类型。如果 LLM 输出的是字符串 `"['A', 'B']"`，需要先用 Code 节点转换成真正的数组。另外，Iteration 节点内部不能直接引用外部节点的输出变量，必须通过 Iteration 的"输入变量"面板显式传入。

#### 步骤3：Code 节点——简历评分计算（目标：掌握沙箱代码执行）

场景：根据提取的简历信息，计算一个综合评分。

在简历解析器 Workflow 中，Code 节点实现评分逻辑：

```python
import json
import re

def main(skills: str, experience: str, education: str):
    """
    根据技能、经验、学历计算简历评分
    skills: JSON 数组字符串，如 '["Python", "React", "Docker"]'
    experience: 文本，如 "5年全栈开发经验"
    education: 文本，如 "计算机科学硕士"
    """
    # 技能评分（每个技能 10 分，上限 50）
    try:
        skill_list = json.loads(skills) if skills.startswith("[") else [s.strip() for s in skills.split(",")]
    except:
        skill_list = []
    
    skill_score = min(len(skill_list) * 10, 50)
    
    # 经验评分（从文本中提取年限）
    years_match = re.search(r'(\d+)\s*年', experience)
    years = int(years_match.group(1)) if years_match else 0
    exp_score = min(years * 5, 30)
    
    # 学历评分
    edu_score = 20 if "硕士" in education or "博士" in education else 10
    
    total = skill_score + exp_score + edu_score
    level = "优秀" if total >= 80 else "良好" if total >= 60 else "一般"
    
    return {
        "total_score": total,
        "level": level,
        "detail": {
            "skill_score": skill_score,
            "experience_score": exp_score,
            "education_score": edu_score
        }
    }
```

**Code 节点配置**：
- 输入变量：`skills`（文本）、`experience`（文本）、`education`（文本）
- 输出变量：`total_score`（数字）、`level`（文本）、`detail`（对象）
- 代码语言：Python 3
- 超时时间：15 秒

**常见坑**：
- 返回值的 key 必须与节点配置的输出变量名一致
- Python 代码中不要使用 `print()`，打印内容不会返回给 Workflow，使用 `return` 返回字典
- 如果代码报错，检查 Sandbox 是否正常运行：`docker logs docker-sandbox-1 --tail 10`

#### 步骤4：简历解析器完整实现（目标：综合运用多个高级节点）

完整 Workflow 设计：

```
开始（文件上传：简历 PDF）
    ↓
文档提取器（将 PDF 转为文本）
    ↓
LLM_信息提取：
  System Prompt:
    从以下简历中提取关键信息，以 JSON 格式返回：
    {"name": "姓名", "email": "邮箱", "phone": "电话", 
     "skills": ["技能1", "技能2"], "experience": "工作经历概述", 
     "education": "学历信息"}

    简历内容：{{#doc_extractor.text#}}
    ↓
Code_解析JSON（将 LLM 输出的 JSON 字符串解析为对象）
    ↓
Code_综合评分（调用步骤 3 的评分逻辑）
    ↓
IF/ELSE（按评分等级分流）
    ├── 优秀(>=80) → Template_优秀模板（生成面试邀请）
    ├── 良好(60-79) → Template_良好模板（生成笔试通知）
    └── 一般(<60) → Template_一般模板（生成婉拒邮件）
    ↓
结束（输出结构化简历 + 评分 + 邮件模板）
```

### 测试验证

```bash
# 测试 IF/ELSE 分支
curl -X POST http://localhost/v1/workflows/run \
  -H "Authorization: Bearer app-xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": {"leave_type": "病假", "days": 5, "reason": "住院治疗"},
    "response_mode": "blocking",
    "user": "test"
  }'

# 预期输出：包含"医院证明模板"相关内容

# 测试 Iteration 循环
curl -X POST http://localhost/v1/workflows/run \
  -H "Authorization: Bearer app-xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": {"text": "张三、李四、王五参加了项目评审会"},
    "response_mode": "blocking",
    "user": "test"
  }'

# 预期输出：返回一个数组，包含三个人各自的处理结果
```

## 4. 项目总结

### 优点与缺点

| 节点 | 优点 | 缺点 |
|------|------|------|
| **IF/ELSE** | 支持多条件分支（ELIF），条件表达式丰富 | 条件不支持正则匹配，复杂条件需要前置 Code 节点处理 |
| **Iteration** | 原生支持数组遍历，自动收集结果 | 循环体内节点有数量限制（约 10 个），复杂逻辑需要拆分子 Workflow |
| **Code** | 支持 Python/JS，弥补 LLM 在精确计算上的短板 | 沙箱限制了第三方库使用，重型计算需要外部 API |
| **模板转换** | Jinja2 语法灵活，直观的文本拼接 | 不支持调用外部 API，纯文本处理 |
| **参数提取器** | 自动将非结构化输出转为结构化数据 | 依赖 LLM 输出格式，稳定性不如 Code 节点手动解析 |

### 适用场景

| 场景 | 核心节点组合 |
|------|------------|
| **智能审批** | 开始 → LLM（意图识别）→ IF/ELSE（按条件分流）→ 不同处理链路 |
| **批量数据处理** | HTTP（拉取列表）→ Iteration → LLM（逐条处理）→ 汇总 |
| **数据清洗转换** | Code（正则清洗）→ LLM（语义理解）→ Code（格式转换）→ 结束 |
| **内容审核分级** | LLM（风险评分）→ IF/ELSE（高危/中危/低危）→ 分级处理 |
| **多维度评分** | Code（计算指标）→ LLM（综合评价）→ Template（生成报告） |

### 注意事项

1. **IF/ELSE 汇聚问题**：多个分支最终都要连到结束节点（或共同的后续节点），否则未执行的分支会导致 Workflow"不完整"
2. **Iteration 变量作用域**：循环内不能直接引用循环外的节点输出，需通过 Iteration 节点的"输入变量"面板显式传入
3. **Code 节点安全**：不要尝试 `import os` 或 `import subprocess`，Sandbox 会拦截系统调用
4. **模板转换中的变量**：使用 `{{#node_id.field#}}` 格式，与 Prompt 中的变量引用方式一致

### 常见踩坑经验

1. **坑：IF/ELSE 的"不满足"分支永远不执行** → 根因：所有 IF/ELIF 条件覆盖了所有可能情况。解决：保留 ELSE 作为兜底
2. **坑：Iteration 输出为空数组** → 根因：输入列表本身就是空的；或循环体内的节点报错被静默跳过。解决：在迭代前加一个 IF/ELSE 检查数组长度
3. **坑：Code 节点返回 `null`** → 根因：`return` 语句返回的不是字典（如返回了字符串）。解决：确保 `def main(...)` 返回 `{"字段名": 值}` 格式

### 思考题

1. **进阶题**：Iteration 节点内部能否再嵌套一个 Iteration 节点？如果能，变量作用域如何处理？（提示：两层循环时，内层如何引用外层的 item？）

2. **进阶题**：如果你需要在 Workflow 中执行一个耗时 5 分钟的数据处理任务，但单个节点的超时限制是 60 秒。你会如何设计？（提示：考虑异步任务的触发和轮询机制）

> **参考答案**：见附录 D

---

> **推广计划提示**：本章的三个核心节点（IF/ELSE、Iteration、Code）是区分"会用 Dify"和"精通 Dify"的分水岭。开发人员务必完成简历解析器的完整实现。测试人员应针对每个分支路径设计覆盖测试。
