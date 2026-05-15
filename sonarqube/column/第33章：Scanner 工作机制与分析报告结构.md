# 第33章：Scanner 工作机制与分析报告结构

## 1. 项目背景

**业务场景**：某团队在编写自定义 SonarQube 插件时，需要理解 Scanner 如何调度 Sensor、如何生成分析报告。他们发现 `sonar-scanner` 的执行日志里有大量中间状态——"Load quality profiles"、"Sensor JavaSensor"、"Analysis report generated"——但不知道每个阶段在做什么、如何影响最终结果。

特别是当覆盖率数据没有正确导入时，他们需要知道 Scanner 到底读取了哪个覆盖率报告文件、是否成功解析、有没有在报告中包含对应文件的覆盖率数据。

**痛点放大**：Scanner 是一个黑盒——输入是源码和配置，输出是一个 zip 报告。如果输出有问题（如覆盖率缺失、某些文件没被分析），无法定位是配置问题还是 Scanner 内部逻辑问题。

**更多现实场景**：

- **场景一：覆盖率"幽灵消失"**：团队在 CI 中配置了 JaCoCo 覆盖率报告路径，CI 日志显示 Scanner "SUCCESS"，但 SonarQube UI 中的覆盖率始终是 0%。排查了 2 小时才发现——Scanner 读取的 JaCoCo XML 路径少了一层 `target/` 目录，Scanner 静默跳过了（没有报错，只在 DEBUG 日志中有一条 `No coverage report found`）。

- **场景二：自定义插件 Sensor 未执行**：开发团队写了一个自定义 Sensor 来检查代码中的硬编码密钥。Sensor 代码写好了，JAR 也放到了 `extensions/plugins/` 目录，但 Web UI 中始终看不到对应的 Issue。最后发现是 Sensor 的 `@Phase` 注解声明的阶段不对——声明在 POST 阶段但依赖的数据还没产生。

- **场景三：多模块项目分析不完整**：一个 Maven 多模块项目（10 个子模块），使用 `sonar-scanner`（非 Maven 模式）扫描。Scanner 报告显示分析了 500 个文件，但实际项目有 800 个文件。原因是 `sonar.sources` 只配置了根目录的 `src/`，没有包含各子模块的源码路径。

**关键问题清单**：
1. Scanner 的 Sensor 执行顺序是如何确定的？自定义 Sensor 如何安排执行时机？
2. 分析报告（zip）里到底包含什么数据？如何查看报告内容？
3. `sonar-project.properties` 和命令行参数冲突时，哪个优先级更高？
4. 增量分析是如何工作的？`.scannerwork` 目录里有什么？
5. Scanner 日志中"files indexed"和最终分析的 files 数量为什么不一致？

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（看 Scanner 日志看晕了）："Scanner 的日志里有一大段'Sensor'的执行记录——`Sensor JavaSensor`、`Sensor JaCoCoSensor`、`Sensor SurefireSensor`……Sensor 到底是什么？"

**大师**："Sensor 是 Scanner 的核心执行单元。每个 Sensor 负责分析一个维度——JavaSensor 分析 Java 源码的质量问题，JaCoCoSensor 导入覆盖率报告，SurefireSensor 导入测试执行报告。它们就像一条流水线上的不同工位——一个工位检查代码缺陷，下一个工位检查测试覆盖率。"

**小白**："这些 Sensor 的执行顺序有规定吗？如果我想加一个自定义 Sensor，什么时候执行？"

**大师**："有规定。Scanner 按照以下顺序执行 Sensor：
1. 文件索引 Sensor（识别语言和文件类型）
2. 全局 Sensor（项目级指标，如项目是否同时有 Java 和 JS 代码）
3. 独立 Sensor（大多数语言分析器，如 JavaSensor、JavaScriptSensor）
4. 后置 Sensor（依赖前序 Sensor 的结果，如重复代码检测需要先有源码分析结果）

你自定义的 Sensor 会按照你声明的优先级自动归入合适的执行阶段。"

**小胖**："大师，我还有个疑问——Scanner 日志里经常看到 'Load quality profiles' 这一步。它从 SonarQube 服务器下载了什么？如果我离线（没有网络）能扫描吗？"

**大师**："'Load quality profiles' 这一步做了三件事：

1. **从服务器拉取当前项目关联的 Quality Profile**（包含激活了哪些规则、规则的参数配置）
2. **下载对应的分析器插件**（如 Java 分析器、JavaScript 分析器的 JAR 包）
3. **缓存到 `~/.sonar/cache` 目录**——下次扫描时如果 Profile 没变，直接使用缓存

如果你完全离线（首次扫描），Scanner 会报错退出——因为它必须从服务器下载分析器。但如果是第二次扫描（缓存已存在），并且服务器上的规则集没有变化，Scanner 可以复用缓存。不过最终上传报告时仍然需要连接到 SonarQube。

另外注意：`~/.sonar/cache` 目录随着分析器版本更新会越来越大。建议定期清理——当缓存超过 500MB 时，删掉让 Scanner 重新下载即可。"

**小白**："分析报告上传到 SonarQube 后，Server 端怎么处理的？为什么扫完了还要等 CE 处理？"

**大师**："这是 SonarQube 架构的关键设计——**异步处理**：

1. **Scanner 上传**：Scanner 将分析报告（一个 zip 文件，包含所有原始分析数据）上传到 SonarQube Server。这个操作是同步的——上传完成后 Scanner 就退出了。
2. **CE 异步处理**：Server 接收报告后创建一个 CE Task，放入 CE 队列。CE Worker 从队列中取出任务，执行入库、指标计算、门禁评估。
3. **UI 可见**：只有当 CE 处理完成后，Web UI 才会显示最新的分析结果。

这种设计的好处是：Scanner 不需要等待 CE 处理完成——大型项目的 CE 处理可能耗时 5-10 分钟，如果同步等待，Scanner 会阻塞 CI Pipeline。但代价是：Scanner 成功不等于结果立即可见。"

**小胖**："那分析报告 zip 里到底有什么？我能解压看看吗？"

**大师**："可以——但需要知道保存位置。分析报告存储在 SonarQube 的 `data/ce/tasks/<task-uuid>/` 目录下。你可以这样查看：

```bash
# 找到最近的 CE 任务 ID
TASK_ID=$(curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/activity?ps=1" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['tasks'][0]['id'])")

# 进入 CE 任务目录（需要服务器权限）
ls -la /opt/sonarqube/data/ce/tasks/$TASK_ID/
# 会看到类似：
#   analysis-report.zip   ← 完整的分析报告（二进制）
#   scanner-report/       ← 解压后的报告目录
#     issues.json         ← 所有 Issue 的列表
#     measures.json       ← 所有度量数据
#     components.json     ← 项目文件结构
#     syntax-highlighting/← 语法高亮数据
```

但这些是内部格式，直接查看不太直观。更推荐通过 Scanner 的 `-Dsonar.verbose=true` 输出或 API 来排查问题。"

---

## 3. 项目实战

### 3.1 分步实现

**步骤 1：开启 Scanner Debug 模式**

```bash
sonar-scanner -X -Dsonar.verbose=true 2>&1 | tee scanner-debug.log
```

关键日志段解读：

```
# 阶段 1: Bootstrap（启动）
Load global settings
Load/download plugins         # 从 SonarQube 下载分析器
Load quality profiles          # 加载规则集
Load active rules              # 加载激�的规则

# 阶段 2: Index Files（文件索引）
Project configuration:
  Excluded sources: **/target/**, **/node_modules/**
1,247 files indexed             # 本次扫描了 1,247 个文件

# 阶段 3: Sensor Execution（分析器执行）
Sensor JavaSensor [java]        # Java 代码分析开始
  ...分析 856 个 Java 文件...
Sensor JavaSensor [java] (done) | time=23500ms

Sensor JaCoCoSensor [java]      # 覆盖率导入
  Importing coverage from target/site/jacoco/jacoco.xml
  ...导入 452 个文件的覆盖率数据...
Sensor JaCoCoSensor [java] (done) | time=1200ms

# 阶段 4: Report（报告生成）
Analysis report generated in 250ms
Analysis report compressed in 45ms
Analysis report uploaded in 800ms
```

**步骤 2：分析 Scanner 配置合并过程**

Scanner 从多个来源读取配置，优先级从高到低：

```
命令行参数 > sonar-project.properties > 全局 sonar-scanner.properties > 项目默认值
```

验证配置来源：

```bash
sonar-scanner -Dsonar.projectKey=test -Dsonar.sources=src -Dsonar.verbose=true 2>&1 \
  | grep -E "Load (global|project) settings|sonar\."
```

**步骤 3：探索分析报告内容**

Scanner 上传的报告存储在 CE 任务的工作目录。通过 API 获取报告元数据：

```bash
# 获取最近的 CE 任务 ID
TASK_ID=$(curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/activity?ps=1" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['tasks'][0]['id'])")

# 获取任务详情（包括 Scanner 上下文信息）
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/ce/task?id=$TASK_ID" \
  | python3 -m json.tool | grep -E "scannerContext|scannerVersion|organization"
```

**步骤 4：调试覆盖率导入失败问题**

```bash
# 开启 JaCoCo Sensor 的详细日志
sonar-scanner \
  -Dsonar.verbose=true \
  -Dsonar.coverage.jacoco.xmlReportPaths=target/site/jacoco/jacoco.xml \
  -Dsonar.log.level=DEBUG \
  2>&1 | grep -i jacoco
```

日志中关注：
- `Imported coverage data for N files` → 成功导入了多少文件
- `No coverage report found` → 报告路径错误
- `Unresolved file path` → 路径匹配失败

**步骤 5：理解增量分析与 .scannerwork 目录**

Scanner 的增量分析依赖 `.scannerwork` 目录缓存：

```bash
# 查看 .scannerwork 目录内容
ls -la .scannerwork/
# 包含：
#   report-tmp/          ← 临时报告文件
#   cache/               ← 文件哈希缓存（用于增量分析）
#   sonar-scanner.properties.bak ← 上次扫描配置备份

# 增量分析原理：
# 1. Scanner 计算每个文件的 MD5 哈希
# 2. 与 .scannerwork/cache 中上次的哈希对比
# 3. 只有内容变化的文件才重新分析（语法树解析 + 规则检查）
# 4. 未变化的文件直接复用上次的分析结果

# 强制全量分析（清理缓存）
rm -rf .scannerwork/
sonar-scanner  # 下次扫描将是全量分析
```

**步骤 6：诊断文件被排除的问题**

```bash
#!/bin/bash
# scanner-file-diagnostics.sh - 诊断哪些文件被排除

echo "=== 文件排除诊断 ==="

# 1. 检查 exclusions 配置
echo "--- 排除规则 ---"
grep -E "sonar\.exclusions|sonar\.inclusions" sonar-project.properties 2>/dev/null || echo "无自定义排除规则"

# 2. 分析 Scanner Debug 日志
echo ""
echo "--- 索引统计 ---"
grep -E "files? indexed|Excluded sources" scanner-debug.log 2>/dev/null

# 3. 检查语言分布
echo ""
echo "--- 语言分布 ---"
grep "Quality profile for" scanner-debug.log 2>/dev/null

# 4. 列出哪些文件被分析器跳过
echo ""
echo "--- 跳过的文件 ---"
grep -i "skip\|exclude\|ignore" scanner-debug.log 2>/dev/null | head -20

# 5. 统计分析结果
echo ""
echo "--- 结果统计 ---"
echo "Indexed files:   $(grep -c 'files indexed' scanner-debug.log 2>/dev/null || echo 0)"
echo "Sensor执行数:    $(grep -c '(done)' scanner-debug.log 2>/dev/null || echo 0)"
echo "报告耗时:        $(grep 'Analysis report generated' scanner-debug.log 2>/dev/null)"
```

**步骤 7：自定义 Sensor 开发模板**

如果你想开发一个自定义 Sensor（例如检查代码中是否包含 TODO 注释），可以参考以下模板：

```java
@ScannerSide
@Phase(name = Phase.Name.POST)  // 在后置阶段执行
public class TodoCommentSensor implements Sensor {

    @Override
    public void describe(SensorDescriptor descriptor) {
        descriptor.name("TODO Comment Sensor");
        descriptor.createIssuesForRuleRepository("custom-rules");
    }

    @Override
    public void execute(SensorContext context) {
        FileSystem fs = context.fileSystem();
        Iterable<InputFile> inputFiles = fs.inputFiles(
            fs.predicates().hasLanguage("java")
        );

        for (InputFile file : inputFiles) {
            try {
                List<String> lines = file.contents().split("\\n");
                for (int i = 0; i < lines.size(); i++) {
                    if (lines.get(i).contains("TODO")) {
                        NewIssue issue = context.newIssue()
                            .forRule(RuleKey.of("custom-rules", "todo-comment"))
                            .at(NewIssueLocation.newBuilder()
                                .on(file)
                                .at(file.selectLine(i + 1))
                                .message("发现 TODO 注释，请及时处理")
                                .build());
                        issue.save();
                    }
                }
            } catch (IOException e) {
                // 文件无法读取
            }
        }
    }
}
```

关键注解说明：
- `@ScannerSide`：声明为 Scanner 端组件
- `@Phase(name = Phase.Name.POST)`：声明在后置阶段执行（依赖前序分析结果时可选 `POST`）
- `describe()`：注册 Sensor 名称和规则仓库
- `execute()`：核心分析逻辑，通过 `SensorContext` 创建 Issue

### 3.3 验证

```bash
# 验证 Scanner 是否正确识别了语言
grep "Quality profile for" scanner-debug.log

# 验证覆盖率文件是否被读取
grep -i "coverage\|jacoco\|lcov" scanner-debug.log
```

---

## 4. 项目总结

### 4.1 Scanner 工作流程速查

| 阶段 | 描述 | 典型耗时 |
|------|------|---------|
| Bootstrap | 连接服务器、下载规则 | 2-60s |
| Index Files | 遍历源码目录 | 1-10s |
| Sensor Execution | 语言分析、覆盖率导入 | 10s-30min |
| Report | 生成和上传报告 | 1-30s |

### 4.2 Sensor 执行阶段详解

| 阶段 | Phase Name | 典型 Sensor | 执行时机说明 |
|------|-----------|------------|------------|
| 文件索引 | DEFAULT | FileIndexer | 最先执行，识别文件类型和语言 |
| 全局指标 | PRE | GlobalSensor | 项目级指标，在语言分析前 |
| 语言分析 | DEFAULT | JavaSensor, CSharpSensor | 主体分析逻辑 |
| 覆盖率导入 | DEFAULT | JaCoCoSensor, CoberturaSensor | 与语言分析同阶段 |
| 后置处理 | POST | DuplicationSensor, CoverageSensor | 依赖前序 Sensor 结果 |

### 4.3 Scanner 配置优先级

```
命令行参数 (-Dkey=value)
    ↓ 覆盖
sonar-project.properties (项目根目录)
    ↓ 覆盖
sonar-scanner.properties (全局配置, <install_dir>/conf/)
    ↓ 覆盖
sonar-project.properties 默认值
```

### 4.4 常见 Scanner 错误速查

| 错误日志 | 含义 | 解决方案 |
|---------|------|---------|
| `Fail to download file` | 无法从服务器下载插件 | 检查网络连接和 `sonar.host.url` |
| `You must define the following mandatory properties` | 缺少必需参数 | 补全 `sonar.projectKey`, `sonar.sources` |
| `Fail to parse file` | 文件语法解析失败 | 检查文件编码或 Java 版本兼容性 |
| `No coverage report found` | 覆盖率报告路径错误 | 检查 `sonar.coverage.jacoco.xmlReportPaths` |
| `java.lang.OutOfMemoryError` | Scanner 内存不足 | 增加 `SONAR_SCANNER_OPTS=-Xmx2g` |
| `Timeout uploading report` | 上传报告超时 | 检查网络或 `sonar.ws.timeout` |

### 4.5 注意事项

1. **Scanner 缓存**：`~/.sonar/cache` 目录缓存了已下载的分析器 JAR 包。如果分析器版本变更，缓存会自动更新。
2. **增量分析依赖 `.scannerwork`**：该目录存储上次分析的哈希值。如果被删除，下次扫描是全量扫描。
3. **Debug 日志不要在生产 CI 中永久开启**：Debug 日志包含大量内部细节，会显著增加日志量和轻微影响性能。

### 4.6 思考题

1. 如果 Scanner 日志显示 "1,000 files indexed" 但分析完成后只有 800 个文件的 Issue 数据，可能是什么原因？
2. 多语言项目中，Scanner 如何决定用哪个语言分析器分析某个文件？

---

> **推广计划提示**：Scanner 工作原理的理解有助于团队自主排障。建议在团队 Wiki 上维护一份 "Scanner 日志解读指南"，包含常见错误日志及其对应的解决方案。
