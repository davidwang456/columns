# 第4章：第一个项目——创建Flow与Job

## 1. 项目背景

### 业务场景

数据开发工程师小刘接到需求：每天凌晨2点，从MySQL数据库中将昨天的订单数据抽取到Hive数据仓库中。这条数据管道包含三个步骤：①连接MySQL导出数据为CSV文件；②将CSV文件上传到HDFS；③在Hive中创建外表并加载数据。

领导要求他使用Azkaban来编排这个流程，因为后续还有更多类似的ETL管道需要统一管理。小刘虽然会用SQL和Linux命令，但从来没接触过工作流调度系统。他需要在一天之内搞定第一个Flow，作为后续所有ETL任务的模板。

### 痛点放大

第一次创建Azkaban Flow时，新手常踩的坑包括：

1. **依赖定义错误**：把`dependsOn`写成了`dependOn`（少了一个s），Flow上传后不报错但依赖关系失效。
2. **Job文件格式问题**：用Windows记事本编辑后，文件编码变成GBK，Azkaban读取时乱码。
3. **打包结构错误**：直接把整个文件夹打包，而不是在文件夹内部打包，导致Azkaban识别不到.job文件根路径。
4. **命令换行遗漏**：`command.n`的编号不连续（比如1,3,5缺少2），导致部分命令不执行。

## 2. 项目设计——剧本式交锋对话

**小胖**（盯着一屏幕的Shell脚本发愁）：大师，我写了三个shell脚本——一个导数据、一个传HDFS、一个建Hive表。但我不知道怎么让Azkaban把它们串起来？是不是要写个XML描述文件，还是Python脚本？

**大师**：不用那么复杂。Azkaban的核心哲学是"配置即代码"——所有配置都放在`.job`文件中，用最简单的`key=value`格式来描述。就拿你这三个脚本来说，只需要创建三个`.job`文件，以及一个`.flow`文件来声明依赖关系。

**小胖**：等等，`.job`文件和`.flow`文件是什么关系？一个项目可以有多个Flow吗？

**大师**（在白板上写下）：关系其实很简单。我给你一个清晰的层次结构：

```
Project（项目）—— 最高层级的组织单元
  ├── .flow文件1 —— 工作流定义
  │   ├── .job文件A
  │   ├── .job文件B —— dependsOn: A
  │   └── .job文件C —— dependsOn: B
  │
  └── .flow文件2 —— 另一个独立的工作流
      ├── .job文件D
      └── .job文件E —— dependsOn: D
```

一个Project可以包含多个Flow，每个Flow包含多个Job。你上传一个zip包时，Azkaban会根据里面的`.flow`文件识别出有哪些Flow。

**小白**（举手提问）：那`.flow`文件里面写什么？是JSON还是XML？

**大师**：`.flow`文件本质上也是一个key-value配置文件。核心字段只有一个——`nodes`，列出这个Flow包含哪些Job。比如你有一个Flow叫`etl_pipeline.flow`，内容就这么简单：

```
nodes=export_mysql,hdfs_upload,hive_load
```

但这里有个非常重要的细节——**依赖关系不写在.flow文件里，而是写在.job文件里**。

**小胖**：啊？我以为依赖关系应该在Flow文件里定义的……

**大师**：这就是Azkaban和Airflow最大的设计差异。Airflow把依赖定义在DAG文件（Python）中；而Azkaban把依赖定义在每个Job自己的文件里，通过`dependsOn`字段。举个例子：

`hdfs_upload.job`的内容：
```
type=command
command=sh hdfs_upload.sh
dependsOn=export_mysql
```

`hive_load.job`的内容：
```
type=command
command=sh hive_load.sh
dependsOn=hdfs_upload
```

**小白**：这种设计有什么好处？

**大师**：好处是Job是高内聚的——每个Job文件写清楚"我是谁"和"我需要谁先完成"。坏处是当Flow很大时，你需要打开多个.job文件才能理清整体依赖关系。但这正好倒逼我们将Flow拆分成合理的粒度。

**小胖**：那如果我只定义一个Flow，还需要显式创建`.flow`文件吗？能不能直接把.job文件打包上传就完事？

**大师**：在Azkaban中，如果你不创建`.flow`文件，Azkaban会把zip包根目录下的每一个`.job`文件当作独立的Flow。就是说——
- 1个.job文件 → 1个Flow（Flow名就是Job名）
- N个.job文件 + 1个.flow文件 → 1个Flow（Flow名是.flow文件名，包含N个Job）

简单场景（一个Job一个Flow）可以省略.flow文件；复杂场景（多Job有依赖）必须创建.flow文件。

### 技术映射总结

- **.job文件** = 一份工作说明书（声明"我是谁、做什么、依赖谁"）
- **.flow文件** = 一个项目计划书（列出该项目包含哪些工作）
- **dependsOn** = 前置条件（先做完A才能做B）
- **zip打包** = 文件归档（把所有说明书归档成一份交付物）
- **不支持子目录** = 扁平化管理（所有文件放在同一个层级，不存在嵌套结构）

## 3. 项目实战

### 3.1 环境准备

| 组件 | 版本 | 用途 |
|------|------|------|
| Azkaban Solo Server | 3.90.0 | 工作流调度引擎 |
| MySQL | 5.7 | 源数据库 |
| Hadoop/HDFS | 2.7+ | 目标存储 |
| Hive | 2.3+ | 数据仓库 |

### 3.2 分步实现

#### 步骤1：创建项目目录结构

**目标**：建立规范的Flow文件目录。

```bash
# 创建项目目录
mkdir -p ~/azkaban-flows/etl_pipeline
cd ~/azkaban-flows/etl_pipeline

# 目录结构如下
tree
# ├── export_mysql.job      # 步骤1：MySQL导出
# ├── hdfs_upload.job       # 步骤2：上传HDFS
# ├── hive_load.job         # 步骤3：Hive加载
# ├── etl_pipeline.flow     # Flow定义文件
# └── scripts/              # 存放shell脚本
#     ├── export_mysql.sh
#     ├── hdfs_upload.sh
#     └── hive_load.sh
```

#### 步骤2：编写SQL导出Job

**目标**：创建从MySQL导出数据到CSV的Job。

`scripts/export_mysql.sh`：

```bash
#!/bin/bash
# 导出昨天的订单数据到CSV文件

YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)
OUTPUT_DIR="/tmp/etl_export"
mkdir -p $OUTPUT_DIR
OUTPUT_FILE="$OUTPUT_DIR/orders_${YESTERDAY}.csv"

echo "[$(date)] Starting MySQL export for date: $YESTERDAY"

mysql -h localhost -u etl_user -p'etl_pass123' demo_db -N -e "
SELECT 
    order_id,
    user_id,
    product_id,
    amount,
    status,
    created_at
FROM orders 
WHERE DATE(created_at) = '${YESTERDAY}'
" | sed 's/\t/,/g' > $OUTPUT_FILE

ROW_COUNT=$(wc -l < $OUTPUT_FILE)
echo "[$(date)] Exported $ROW_COUNT rows to $OUTPUT_FILE"

# 检查是否有数据
if [ $ROW_COUNT -eq 0 ]; then
    echo "[$(date)] WARNING: No data exported for $YESTERDAY. Continuing..."
fi
```

`export_mysql.job`：

```bash
# export_mysql.job
type=command
command=bash scripts/export_mysql.sh
command.1=echo "Export job completed at $(date)"
failure.emails=dba@company.com
```

**参数说明**：
- `type=command`：使用command类型执行shell命令
- `command`和`command.n`：多行命令，n从1开始编号
- `failure.emails`：Job失败时发送告警邮件到指定地址

#### 步骤3：编写HDFS上传Job

**目标**：将CSV文件上传到HDFS。

`scripts/hdfs_upload.sh`：

```bash
#!/bin/bash
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)
LOCAL_FILE="/tmp/etl_export/orders_${YESTERDAY}.csv"
HDFS_PATH="/user/hive/warehouse/ods/orders/dt=${YESTERDAY}/"

echo "[$(date)] Starting HDFS upload..."

if [ ! -f "$LOCAL_FILE" ]; then
    echo "[$(date)] ERROR: Source file not found: $LOCAL_FILE"
    exit 1
fi

# 创建HDFS目录
hdfs dfs -mkdir -p $HDFS_PATH

# 上传文件
hdfs dfs -put -f $LOCAL_FILE $HDFS_PATH

# 验证上传
HDFS_COUNT=$(hdfs dfs -cat ${HDFS_PATH}orders_${YESTERDAY}.csv | wc -l)
echo "[$(date)] Uploaded $HDFS_COUNT rows to HDFS: $HDFS_PATH"
```

`hdfs_upload.job`：

```bash
# hdfs_upload.job
type=command
command=bash scripts/hdfs_upload.sh
dependsOn=export_mysql
retries=2
retry.backoff=60000
```

**参数说明**：
- `dependsOn=export_mysql`：必须等export_mysql这个Job成功后才能执行
- `retries=2`：失败后自动重试2次
- `retry.backoff=60000`：每次重试间隔60秒

#### 步骤4：编写Hive加载Job

**目标**：创建Hive外表并加载数据。

`scripts/hive_load.sh`：

```bash
#!/bin/bash
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)

echo "[$(date)] Starting Hive table operations..."

hive -e "
-- 创建分区外表
CREATE EXTERNAL TABLE IF NOT EXISTS ods.orders (
    order_id    STRING,
    user_id     STRING,
    product_id  STRING,
    amount      DOUBLE,
    status      STRING,
    created_at  STRING
)
PARTITIONED BY (dt STRING)
ROW FORMAT DELIMITED
FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION '/user/hive/warehouse/ods/orders';

-- 添加分区
ALTER TABLE ods.orders ADD IF NOT EXISTS PARTITION (dt='${YESTERDAY}');

-- 刷新元数据
MSCK REPAIR TABLE ods.orders;
"

echo "[$(date)] Hive table operations completed."

# 验证数据
RECORD_COUNT=$(hive -e "SELECT COUNT(*) FROM ods.orders WHERE dt='${YESTERDAY}'" 2>/dev/null | tail -1)
echo "[$(date)] Verified: $RECORD_COUNT records in ods.orders for dt=$YESTERDAY"
```

`hive_load.job`：

```bash
# hive_load.job
type=command
command=bash scripts/hive_load.sh
dependsOn=hdfs_upload
```

#### 步骤5：编写Flow定义文件

**目标**：创建.flow文件声明Flow中的Job列表。

`etl_pipeline.flow`：

```
# etl_pipeline.flow
# 声明此Flow包含的Job列表
nodes=export_mysql,hdfs_upload,hive_load

# 可选：Flow级别的配置
flow.param.dataset=orders
flow.param.owner=data_team
```

#### 步骤6：打包与上传

**目标**：将Flow打包为zip并通过API上传到Azkaban。

```bash
cd ~/azkaban-flows/etl_pipeline

# 关键：在etl_pipeline目录内部打包，而不是在外层
# 确保zip包根目录直接包含.job和.flow文件
zip -r ../etl_pipeline.zip ./*

# 验证打包结构
unzip -l ../etl_pipeline.zip
# 应输出：
# export_mysql.job
# hdfs_upload.job
# hive_load.job
# etl_pipeline.flow
# scripts/export_mysql.sh
# scripts/hdfs_upload.sh
# scripts/hive_load.sh

# 登录Azkaban
curl -c cookies.txt \
  -X POST "http://localhost:8081" \
  --data "action=login&username=azkaban&password=azkaban"

# 创建项目
curl -b cookies.txt \
  -X POST "http://localhost:8081/manager?action=create" \
  --data "name=etl_pipeline&description=Order ETL Pipeline"

# 上传Flow
curl -b cookies.txt \
  -X POST "http://localhost:8081/manager?project=etl_pipeline&ajax=upload" \
  -F "file=@../etl_pipeline.zip"
```

**可能遇到的坑**：
- **zip路径错误**：`zip -r etl_pipeline.zip ./*` 不要在 `etl_pipeline/` 外面执行 `zip -r etl_pipeline.zip etl_pipeline/`，会导致多一层嵌套
- **文件权限**：`.sh`脚本必须可执行，使用 `chmod +x scripts/*.sh`

#### 步骤7：执行Flow并观察

**目标**：在Web界面执行Flow并查看DAG依赖执行效果。

```bash
# API方式执行Flow
curl -b cookies.txt \
  -X POST "http://localhost:8081/executor?ajax=executeFlow&project=etl_pipeline&flow=etl_pipeline"
```

**Web界面观察**：
1. 进入 `etl_pipeline` 项目 → Flows Tab
2. 点击 `Execute Flow`
3. 执行详情页会展示DAG图：
   ```
   export_mysql (绿色 ✓)
       ↓
   hdfs_upload  (蓝色 ⟳ 运行中)
       ↓
   hive_load    (灰色 ○ 等待中)
   ```
4. 依次观察三个Job完成，最终DAG全变绿色

### 3.3 完整代码清单

完整项目文件见Git仓库：`https://github.com/your-org/azkaban-flows/etl_pipeline`

### 3.4 测试验证

```bash
#!/bin/bash
# test_flow.sh —— 验证Flow的正确性

echo "=== ETL Flow验证 ==="

# 1. 检查.job文件语法
echo "[Test 1] 检查Job文件是否存在..."
for job in export_mysql hdfs_upload hive_load; do
    if [ -f "${job}.job" ]; then
        echo "  [PASS] ${job}.job 存在"
    else
        echo "  [FAIL] ${job}.job 不存在"
    fi
done

# 2. 检查.flow文件
echo "[Test 2] 检查Flow文件..."
if grep -q "nodes=" etl_pipeline.flow; then
    echo "  [PASS] etl_pipeline.flow 包含nodes定义"
else
    echo "  [FAIL] etl_pipeline.flow 缺少nodes定义"
fi

# 3. 检查依赖关系一致性
echo "[Test 3] 验证依赖关系一致性..."
JOB_IN_FLOW=$(grep "nodes=" etl_pipeline.flow | cut -d= -f2 | tr ',' '\n' | sort)
JOB_FILES=$(ls *.job | sed 's/\.job//' | sort)
if diff <(echo "$JOB_IN_FLOW") <(echo "$JOB_FILES") > /dev/null; then
    echo "  [PASS] Flow中的节点与.job文件一致"
else
    echo "  [FAIL] Flow节点与.job文件不一致"
fi

# 4. 执行API测试
echo "[Test 4] 通过API执行Flow..."
RESULT=$(curl -s -b cookies.txt \
  "http://localhost:8081/executor?ajax=executeFlow&project=etl_pipeline&flow=etl_pipeline")
if echo "$RESULT" | grep -q "execid"; then
    echo "  [PASS] Flow执行成功发起"
else
    echo "  [FAIL] Flow执行失败: $RESULT"
fi

echo "=== 验证完成 ==="
```

## 4. 项目总结

### 优点 & 缺点

| 维度 | Azkaban Flow | Shell脚本+& | Airflow DAG |
|------|-------------|------------|-------------|
| 编写成本 | ★☆☆ 简单的key-value | ★☆☆ 简单 | ★★★ 需Python |
| 依赖可视化 | ★★★ DAG图直观 | ★☆☆ 需判断 | ★★★ 可定制 |
| 失败重试 | ★★★ 内置retries | ★☆☆ 需手动实现 | ★★★ 丰富策略 |
| 并行能力 | ★★★ 自动并行 | ★★☆ 需&后台 | ★★★ 灵活定义 |
| 调试难度 | ★★☆ 日志在Web | ★☆☆ 本地日志 | ★★☆ Web看日志 |

### 适用场景

- **适用**：ETL流水线、日报/月报生成、Hadoop/Spark批处理、数据备份与迁移
- **不适用**：需要毫秒级实时响应的任务、需要动态分支（if-then-else）的复杂工作流、大规模的微服务编排

### 注意事项

- `.job`文件编码必须是UTF-8（Unix换行符），Windows记事本编辑后务必用`dos2unix`转换
- `dependsOn`的大小写敏感：`dependsOn`和`dependencies`都是有效的（取决于Azkaban版本）
- zip打包时不要包含顶层目录，Azkaban从zip根路径查找.job和.flow文件
- `command.n`的编号从1开始，且必须连续，否则后面编号的命令不会执行

### 常见踩坑经验

1. **dependsOn拼写错误**：写成`dependOn`或`depends_on`都不会被识别，Flow能运行但依赖关系被忽略，所有Job并发执行。
2. **脚本权限问题**：Shell脚本在本地Windows编辑后上传到Linux服务器，执行时报`Permission denied`。解决：打包前执行`chmod +x scripts/*.sh`。
3. **Flow未定义导致的Job独立运行**：忘记创建`.flow`文件，每个Job被视为独立Flow。虽然都能在界面看到，但无法实现依赖串联。

### 思考题

1. 如果`hdfs_upload`Job失败了两次后自动跳过，`hive_load`Job的`dependsOn=hdfs_upload`会如何处理？请设计一个"跳过失败Job"的策略并给出配置方案。
2. 假设你需要每天凌晨2点自动执行这个Flow，而不是手动触发。请写出需要添加的调度配置（提示：下一章内容）。
