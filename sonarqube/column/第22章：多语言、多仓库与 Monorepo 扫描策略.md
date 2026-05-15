# 第22章：多语言、多仓库与 Monorepo 扫描策略

## 1. 项目背景

**业务场景**：某平台型公司的代码仓库结构极其复杂——一个名为 `platform-monorepo` 的 Git 仓库包含了 Java 后端（8 个微服务模块）、TypeScript 前端（2 个 SPA 应用）、Python 脚本（数据处理和机器学习）、Go 语言编写的 API 网关、以及一些 Shell 脚本和 Dockerfile。总计约 30 万行代码，按语言分布：

- Java: 180,000 行（60%）
- TypeScript: 80,000 行（27%）
- Python: 30,000 行（10%）
- Go: 8,000 行（3%）
- 其他: 2,000 行

团队接入 SonarQube 时遇到了三个核心挑战：如何定义项目（一个项目 vs 多个项目）？如何配置扫描（不同语言的 Scanner 配置差异巨大）？如何处理大型仓库的扫描性能（首次扫描耗时 45 分钟）？

**痛点放大**：

- **项目建模困难**：一个仓库 = 一个 SonarQube 项目？还是每个模块一个项目？
- **配置组合爆炸**：4 种语言 × 各自不同的构建工具和覆盖率格式 = 16 种配置组合
- **扫描时间过长**：30 万行代码全量扫描需要 45 分钟，CI 中不可接受
- **路径冲突**：多语言的源码目录互相嵌套，`sonar.sources` 和 `sonar.exclusions` 的配置容易冲突

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（对着 monorepo 的目录结构挠头）："大师，我们这一个仓库里有 Java、TypeScript、Python、Go——四门语言混在一起。听说 SonarQube 一次扫描只能分析一种语言？是真是假？"

**大师**："假的。SonarScanner 是语言无关的——它在扫描时会根据文件扩展名自动识别语言，然后加载对应的分析器。同一个扫描任务中，Java 文件用 Java 分析器，TS 文件用 JavaScript/TypeScript 分析器，Python 文件用 Python 分析器——全部并行处理。你只需要确保 `sonar.sources` 覆盖了所有目录。"

**小白**："那覆盖率报告呢？Java 用 JaCoCo 生成 XML，TypeScript 用 lcov，Python 用 coverage.py——三种格式能同时导入一个项目吗？"

**大师**："能。SonarQube 支持在一个项目中同时配置多种覆盖率报告：

```properties
sonar.coverage.jacoco.xmlReportPaths=backend/target/site/jacoco/jacoco.xml
sonar.javascript.lcov.reportPaths=frontend/coverage/lcov.info
sonar.python.coverage.reportPaths=scripts/coverage.xml
```

每个语言对应的覆盖率报告路径是独立的，互不冲突。"

**小胖**："那 Go 呢？Go 的覆盖率是 `cover.out` 格式，SonarQube 能读吗？"

**大师**："Go 项目需要引入 `sonar-go-to-sonarcloud` 等社区工具转换格式，或者用 Generic Test Data 格式导入。官方 SonarGo 插件支持 Go 语言自身的覆盖率格式，但 Community Edition 中 Go 插件的覆盖率有一定限制。"

**小白**："Monorepo 项目应该创建一个 SonarQube 项目还是多个？"

**大师**："从三个维度判断：

1. **团队维度**：如果仓库由一个团队维护 → 合并为 1 个项目。如果不同目录由不同团队维护 → 拆分为多个项目。
2. **发布维度**：如果仓库中的模块独立发布（不同版本号、不同发布周期）→ 建议拆分为多个项目。
3. **指标聚合维度**：如果想看全局的质量趋势 → 1 个项目。如果想分别对每个模块设置 Quality Gate → 多个项目。

实际大型 Monorepo 通常选择 '折中方案'：**创建 1 个 SonarQube 项目 + 在项目内按目录分割**。不同团队通过 Web UI 的 Issue 筛选器只看自己负责的目录。"

---

## 3. 项目实战

### 3.1 环境准备

- 一个包含 Java + TypeScript + Python 的示例 monorepo
- SonarQube 实例，已安装对应语言插件

### 3.2 分步实现

**步骤 1：创建 monorepo 示例项目**

```bash
mkdir platform-monorepo && cd platform-monorepo
```

目录结构：
```
platform-monorepo/
├── sonar-project.properties
├── backend/                     # Java Spring Boot
│   ├── pom.xml
│   └── src/main/java/.../OrderService.java
├── frontend/                    # TypeScript React
│   ├── package.json
│   ├── tsconfig.json
│   └── src/components/.../App.tsx
├── scripts/                     # Python 数据处理
│   ├── data_processor.py
│   └── requirements.txt
└── gateway/                     # Go API 网关
    ├── go.mod
    └── main.go
```

**步骤 2：配置多语言扫描**

`sonar-project.properties`（仓库根目录）：

```properties
# 项目基本信息
sonar.projectKey=com.company:platform-monorepo
sonar.projectName=Platform Monorepo
sonar.projectVersion=1.0.0

sonar.host.url=http://localhost:9000
sonar.token=squ_xxxxxxxxxxxxxxxxxx

# 多语言源码目录（逗号分隔）
sonar.sources=backend/src/main/java,frontend/src,scripts,gateway

# 测试目录
sonar.tests=backend/src/test/java,frontend/src

# 排除第三方代码和构建产物
sonar.exclusions=\
  **/node_modules/**,\
  **/target/**,\
  **/vendor/**,\
  **/__pycache__/**,\
  **/*.pb.go

# 编码
sonar.sourceEncoding=UTF-8

# === Java ===
sonar.java.binaries=backend/target/classes

# === TypeScript ===
sonar.typescript.tsconfigPath=frontend/tsconfig.json
sonar.javascript.lcov.reportPaths=frontend/coverage/lcov.info

# === Python ===
sonar.python.coverage.reportPaths=scripts/coverage.xml

# === Go ===
# Go 覆盖率需要转换工具
# sonar.go.coverage.reportPaths=gateway/coverage.out
```

**步骤 3：编写 CI 构建脚本**

```bash
#!/bin/bash
# build-and-scan.sh - Monorepo 全语言构建与扫描

echo "=== 1. Building Java backend ==="
cd backend && mvn clean verify -DskipITs && cd ..

echo "=== 2. Building TypeScript frontend ==="
cd frontend && npm ci && npx jest --coverage && cd ..

echo "=== 3. Running Python tests ==="
cd scripts && pip install -r requirements.txt && \
  pytest --cov=. --cov-report=xml && cd ..

echo "=== 4. Running SonarScanner ==="
sonar-scanner -Dsonar.token=$SONAR_TOKEN

echo "=== Done ==="
```

**步骤 4：子项目拆分方案（如果选择多个项目）**

如果决定拆分为独立项目，每个子模块有独立的 `sonar-project.properties`：

`backend/sonar-project.properties`：
```properties
sonar.projectKey=com.company:backend
sonar.projectName=Platform Backend
sonar.sources=src/main/java
sonar.java.binaries=target/classes
```

`frontend/sonar-project.properties`：
```properties
sonar.projectKey=com.company:frontend
sonar.projectName=Platform Frontend
sonar.sources=src
sonar.javascript.lcov.reportPaths=coverage/lcov.info
```

然后在 CI 中对每个子项目分别执行扫描：

```bash
cd backend && mvn sonar:sonar && cd ..
cd frontend && sonar-scanner && cd ..
cd scripts && sonar-scanner && cd ..
```

**步骤 5：大型仓库的扫描性能优化**

**(a) 使用 `sonar.inclusions` 精确指定扫描范围**：

```properties
# 只扫描指定文件模式
sonar.inclusions=backend/**/*.java,frontend/**/*.ts,frontend/**/*.tsx,scripts/**/*.py
```

**(b) 排除不需要扫描的大目录**：

```properties
sonar.exclusions=**/node_modules/**,**/target/**,**/vendor/**,**/*.pb.go,**/*.generated.*
```

**(c) 启用扫描缓存**：

```bash
# SonarScanner 自动使用缓存目录
export SONAR_USER_HOME=$HOME/.sonar

# 扫描时指定缓存目录（避免每次重新下载分析器）
sonar-scanner \
  -Dsonar.userHome=$HOME/.sonar
```

**(d) 使用增量扫描（默认行为）**：
SonarScanner 默认启用增量扫描——只分析 Git 变更的文件。如果项目全量扫描时间过长（> 15 分钟），确认 CI 中 `.git` 目录可用。

### 3.3 验证多语言结果

```bash
# 查看多语言的 Issue 分布
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/issues/search?projectKeys=com.company:platform-monorepo&facets=languages&ps=1" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for facet in data.get('facets', []):
    if facet['property'] == 'languages':
        for val in facet['values']:
            print(f'  {val[\"val\"]}: {val[\"count\"]}')"

# 输出示例：
#   java: 347
#   ts: 126
#   py: 48
#   go: 12
```

---

## 4. 项目总结

### 4.1 Monorepo 项目建模决策树

```
你的仓库有多个团队维护吗？
│
├── 是 → 团队之间需要不同的 Quality Gate 吗？
│   ├── 是 → 拆分为多个 SonarQube 项目（每个团队一个）
│   └── 否 → 合并为 1 个项目，团队按目录筛选 Issue
│
└── 否（单一团队） → 模块独立发布（不同版本号）吗？
    ├── 是 → 拆分为多个项目（每个模块一个）
    └── 否 → 合并为 1 个项目
```

### 4.2 适用场景

- **大型单体仓库**（Google/Bing 式的 monorepo）
- **全栈团队仓库**（后端 + 前端在同一仓库）
- **基础设施即代码仓库**（Terraform + Ansible + Shell 在同一仓库）

**不适用场景**：
- 50 个以上微服务的独立仓库（每个仓库 = 一个 SonarQube 项目，更清晰）
- 单一语言的简单项目（不需要复杂配置）

### 4.3 注意事项

1. **`sonar.exclusions` 要跨语言谨慎配置**：一个全局 exclusion 模式可能误伤其他语言的文件。
2. **不同语言的测试覆盖率不要混用路径**：每种语言的覆盖率报告路径独立配置，避免交叉覆盖。
3. **大仓库首次扫描可能需要 30 分钟+**：提前通知团队，安排在非 CI 高峰期执行。

### 4.4 思考题

1. 一个 monorepo 包含 Java、Python、TypeScript 三种语言，但 Python 脚本是 AI 生成的（不应计入质量统计）。你如何在配置中排除 Python 文件夹？
2. 如果 monorepo 中的 Java 和 TypeScript 模块由两个独立团队维护，但他们共享同一个 Git 仓库和 CI Pipeline，你如何设计数据隔离和权限隔离？

> **答案提示**：第1题使用 `sonar.exclusions=scripts/**` 或 `sonar.inclusions` 仅包含 Java 和 TS 目录。第2题拆分两个 SonarQube 项目并配置 Project Visibility，每个团队只看到自己的项目。

---

> **推广计划提示**：Monorepo 团队接入 SonarQube 前，应先完成一次"项目建模评审"——架构师和质量负责人一起决定项目拆分方案。错误的拆分决策会导致后续大量的重构工作。建议在 SonarQube Wiki 上维护一张"仓库-SonarQube 项目映射表"。
