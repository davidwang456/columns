# 第9章：参数系统——Properties与变量替换

## 1. 项目背景

### 业务场景

数据团队的ETL流水线有一个"日报生成"Flow，其中包含一个SQL查询Job——每天查询电商平台前一天的GMV数据。这个SQL中的日期参数最初是硬编码的，每天开发同学手动修改`.job`文件中的日期然后重新上传。

很快团队发现这行不通——Flow通过Schedule自动触发后，没人会在凌晨2点起床改文件。于是他们把日期改为动态获取（`date -d "yesterday"`），但新问题又来了——测试环境和生产环境的数据库地址不同、分区名格式不同、告警邮箱不同。同样的Flow需要在不同环境独立维护多个版本，极易出错。

### 痛点放大

参数管理混乱时：

1. **硬编码灾难**：配置散落在每个`.job`文件中，修改一个数据库地址需要改动20个Job文件。
2. **环境不一致**：测试环境配了生产库地址，差点把生产数据清空。
3. **参数黑箱**：手动执行时，在Web界面可以填Flow Parameters覆盖部分参数，但覆盖规则和优先级没人能说清楚。
4. **敏感信息泄露**：数据库密码明文写在`.job`文件中，还提交到了Git仓库。

## 2. 项目设计——剧本式交锋对话

**小胖**（满头大汗地用sed批量替换文件）：大师，要命了！数据库从`192.168.1.100`迁移到`192.168.1.200`，我改了20个Job文件里的IP地址，手都快断了！

**大师**：这就是典型的"配置硬编码灾难"。Azkaban的参数系统就是为解耦这些问题设计的。核心思想很简单——**把"变化的部分"抽取到变量中，环境切换只改一处**。

**小白**：参数系统有哪些层级？优先级是什么样的？

**大师**（在白板上画出层级图）：

```
参数优先级（由高到低）：
┌─────────────────────────────────────┐
│ ① 手动执行时的Flow Parameters覆盖    │  ← 最高优先级（按需覆盖）
├─────────────────────────────────────┤
│ ② Job文件中的 env.* 参数             │  ← Job级别自定义
├─────────────────────────────────────┤
│ ③ Project级别的 .properties 文件      │  ← 项目级共享参数
├─────────────────────────────────────┤
│ ④ 全局 azkaban.properties 中的参数    │  ← 系统级默认参数
│    （不可被覆盖）                      │
└─────────────────────────────────────┘
```

**小胖**：那我是不是应该把所有数据库地址放到一个`.properties`文件里，然后每个Job引用这个变量？

**大师**：思路对了。Azkaban支持两种参数共享方式：

1. **全局Properties文件**：Azkaban启动时加载`conf/global.properties`，所有Job都能访问。
2. **Project Properties**：每个项目可以有一个`.properties`文件，作用域仅限该项目。

**小白**：那在Job文件中怎么引用这些变量？

**大师**：使用`${key}`语法。比如：

```bash
# project.properties（上传到Azkaban的项目属性文件）
db.host=192.168.1.200
db.port=3306
db.user=etl_user
db.name=data_warehouse

# some_job.job
type=command
command=echo "Connecting to ${db.host}:${db.port}/${db.name}"
command.1=mysql -h ${db.host} -P ${db.port} -u ${db.user} -e "SELECT COUNT(*) FROM orders"
```

Azkaban在执行时会自动做变量替换。

**小白**：那替换的范围是什么？只在`command`中生效，还是在`dependsOn`中也能用？

**大师**：变量替换覆盖Job文件中的所有配置项，包括`type`、`command`、`dependsOn`、`retries`等。所以你甚至可以实现动态依赖：

```bash
# dynamic_depends.job
env.my_upstream=job_a
dependsOn=${my_upstream}
```

这样同一个Job文件在不同环境中，可以通过不同参数改变它的依赖关系。

**小胖**：那密码怎么办？总不能把数据库密码明文写在文件里还传Git吧？

**大师**：安全方面，Azkaban处理密码有三种方式：

1. **环境变量注入**：在启动脚本中设置`export DB_PASS=secret`，然后在Job中通过shell读取`$DB_PASS`（不经过Azkaban的变量系统）。
2. **Azkaban加密存储**：3.80+版本支持`azkaban.encryption`功能，在UI中输入的Flow Parameters可以被加密存储。
3. **外部密钥管理**：在Job中调用Vault/AWS Secrets Manager API来获取密码。

### 技术映射总结

- **全局Properties** = 公司公告栏（全公司都看得到，但一般不频繁改动）
- **Project Properties** = 部门白板（只对部门内部可见）
- **env.*参数** = 工位便签（个人专用的临时配置，优先看）
- **Flow Parameters覆盖** = 紧急通知（直接覆盖所有配置，立刻生效）
- **变量替换** = 自动填空题（把 `${key}` 用实际值填进去）

## 3. 项目实战

### 3.1 环境准备

Azkaban运行中，准备一个包含多个Job的测试项目。

### 3.2 分步实现

#### 步骤1：创建全局Properties文件

**目标**：配置Azkaban级别的全局参数。

```properties
# conf/global.properties —— 全局共享参数
# 所有项目所有Flow都能访问这些参数

# 基础路径
base.data.dir=/data/warehouse
base.temp.dir=/tmp/azkaban
base.log.dir=/data/logs/azkaban

# 集群信息
yarn.resource.manager=rm-prod:8032
hdfs.namenode=hdfs://nn-prod:8020

# 版本信息
spark.version=3.2.1
hive.version=2.3.9

# 告警邮箱
alert.email=bigdata@company.com
```

在`azkaban.properties`中配置加载路径：

```properties
# azkaban.properties
executor.global.properties=conf/global.properties
```

#### 步骤2：创建Project级别Properties

**目标**：为指定项目配置专属参数。

```properties
# project.properties —— 上传到Azkaban项目根目录
# 此文件会作为项目的默认属性

# 数据源配置
source.db.host=192.168.1.200
source.db.port=3306
source.db.name=prod_orders
source.db.user=etl_user

# 目标配置
target.hdfs.base=hdfs://nn-prod:8020/user/hive/warehouse
target.hive.db=ods

# 分区格式
partition.dt=2025-01-16

# 业务参数
retention.days=90
batch.size=10000
```

**上传到Azkaban**：

```bash
# 通过API设置项目属性
curl -b cookies.txt \
  -X POST "http://localhost:8081/manager" \
  --data "ajax=setProjectProperty" \
  --data "project=etl_pipeline" \
  --data "name=source.db.host" \
  --data "value=192.168.1.200"

# 批量设置多个属性
for pair in \
  "source.db.host=192.168.1.200" \
  "source.db.port=3306" \
  "target.hive.db=ods"
do
  key=$(echo $pair | cut -d= -f1)
  val=$(echo $pair | cut -d= -f2-)
  curl -b cookies.txt \
    -X POST "http://localhost:8081/manager" \
    --data "ajax=setProjectProperty" \
    --data "project=etl_pipeline" \
    --data "name=$key" \
    --data "value=$val"
done
```

#### 步骤3：Job文件中使用参数

**目标**：在`.job`文件中通过`${}`引用全局/项目/Job级别的参数。

```bash
# mysql_export.job —— 动态参数的Job示例
type=command

# Job级别的参数（覆盖全局/项目同名参数）
env.db.host=${source.db.host}
env.source.table=orders
env.batch.size=5000

command=echo "=== MySQL Export Job ==="
command.1=echo "Source: ${source.db.host}:${source.db.port}/${source.db.name}"
command.2=echo "Batch Size: ${batch.size}"
command.3=bash -c '
  # 在shell中也能访问Azkaban注入的环境变量
  echo "Exporting from ${source.db.host}:${source.db.port}/${source.db.name}"
  echo "Table: ${source.table:-orders}"
  echo "Output: ${base.data.dir}/export/$(date +%Y%m%d)/"
  
  # 使用变量构建MySQL命令
  mysql -h "${source.db.host}" \
        -P "${source.db.port}" \
        -u "${source.db.user}" \
        "${source.db.name}" \
        -e "SELECT * FROM ${source.table:-orders} WHERE dt='"${partition.dt}"' LIMIT ${batch.size}"
'
```

#### 步骤4：参数优先级验证

**目标**：通过实验验证各级参数的覆盖顺序。

```bash
# priority_test.job
type=command
# global.properties中定义了：alert.email=bigdata@company.com
# project.properties中定义了：alert.email=data-team@company.com
# Job env中覆盖为：alert.email=oncall@company.com
env.alert.email=oncall@company.com

command=echo "Testing parameter priority..."
command.1=echo "alert.email = ${alert.email}"
# 预期输出: alert.email = oncall@company.com (Job级别覆盖了全局/项目级别)
command.2=echo "base.data.dir = ${base.data.dir}"
# 预期输出: base.data.dir = /data/warehouse (只有全局定义了)
```

**验证方式**：在上传到Azkaban前，先用脚本本地验证变量替换。

```python
#!/usr/bin/env python3
# validate_params.py —— 本地验证参数替换

import re
import os

def load_properties(filepath):
    """加载.properties文件"""
    props = {}
    if not os.path.exists(filepath):
        return props
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, _, val = line.partition('=')
                props[key.strip()] = val.strip()
    return props

def resolve_job_params(job_file, global_props, project_props):
    """解析Job文件中的参数，按优先级替换"""
    job_props = {}
    with open(job_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('env.'):
                key, _, val = line[4:].partition('=')
                # 先尝试替换val中的变量引用
                combined = {**global_props, **project_props, **job_props}
                val = resolve_vars(val.strip(), combined)
                job_props[key.strip()] = val

    # 合并所有参数（优先级：job > project > global）
    return {**global_props, **project_props, **job_props}

def resolve_vars(text, props):
    """替换${key}为实际值"""
    def replacer(match):
        key = match.group(1)
        return props.get(key, f"${{{key}}}")  # 未找到则保持原样
    return re.sub(r'\$\{(\w+(?:\.\w+)*)\}', replacer, text)

# 使用示例
if __name__ == '__main__':
    global_p = load_properties('/opt/azkaban/conf/global.properties')
    project_p = load_properties('project.properties')
    job_p = resolve_job_params('mysql_export.job', global_p, project_p)
    
    print("=== 最终生效的参数 ===")
    for k, v in sorted(job_p.items()):
        origin = 'job' if k in job_p else ('project' if k in project_p else 'global')
        print(f"  {k} = {v}  (来源: {origin})")
```

#### 步骤5：动态日期参数处理

**目标**：让Job自动获取正确的处理日期。

```bash
# date_aware.job —— 智能日期处理
type=command

# 支持显式指定日期（手动执行时），否则用昨天的日期
env.target_date=${target_date:-$(date -d "yesterday" +%Y-%m-%d)}

command=echo "=== Date-Aware ETL ==="
command.1=echo "Processing date: ${target_date}"
command.2=bash -c '
  YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)
  if [ -n "${target_date}" ] && [ "${target_date}" != "\${target_date}" ]; then
    PROCESS_DATE="${target_date}"
    echo "Using explicit date: ${PROCESS_DATE}"
  else
    PROCESS_DATE="${YESTERDAY}"
    echo "Using auto-detected date: ${PROCESS_DATE}"
  fi
  
  # 后续使用PROCESS_DATE变量
  echo "Query: SELECT * FROM orders WHERE dt='\''${PROCESS_DATE}'\''"
'
```

### 3.3 测试验证

```bash
#!/bin/bash
# verify_params.sh

echo "=== 参数系统验证 ==="

# 1. 验证全局参数加载
echo "[Test 1] 检查全局参数..."
RESP=$(curl -s -b cookies.txt \
  "http://localhost:8081/executor?ajax=getParam" \
  --data "project=&flow=")
if echo "$RESP" | grep -q "base.data.dir"; then
    echo "  [PASS] 全局参数已加载"
else
    echo "  [WARN] 全局参数可能未加载"
fi

# 2. 测试参数覆盖
echo "[Test 2] 验证参数优先级..."
# 执行priority_test Flow，检查日志输出
EXEC_ID=$(curl -s -b cookies.txt \
  -X POST "http://localhost:8081/executor?ajax=executeFlow" \
  --data "project=param_demo&flow=priority_test" \
  | grep -o '"execid":[0-9]*' | grep -o '[0-9]*')

sleep 5  # 等待执行完成

# 获取日志
LOGS=$(curl -s -b cookies.txt \
  "http://localhost:8081/executor?execid=${EXEC_ID}&jobId=priority_test&ajax=fetchExecJobLogs&offset=0&length=100")

if echo "$LOGS" | grep -q "alert.email = oncall@company.com"; then
    echo "  [PASS] Job级别参数覆盖成功"
else
    echo "  [FAIL] 参数覆盖未生效"
fi

# 3. 验证变量替换
echo "[Test 3] 验证${}变量替换..."
if echo "$LOGS" | grep -q "base.data.dir = /data/warehouse"; then
    echo "  [PASS] 全局变量替换成功"
else
    echo "  [FAIL] 变量替换失败"
fi

echo "=== 验证完成 ==="
```

## 4. 项目总结

### 参数管理方案对比

| 维度 | 硬编码 | Properties文件 | 环境变量 | 外部密钥管理 |
|------|--------|---------------|---------|------------|
| 易维护性 | ★☆☆ | ★★★ | ★★☆ | ★★☆ |
| 安全性 | ★☆☆ | ★★☆ | ★★★ | ★★★ |
| 可追溯性 | ★☆☆ | ★★★ Git可见 | ★★☆ 需查环境 | ★★☆ 审计日志 |
| 多环境切换 | ★☆☆ | ★★★ | ★★☆ | ★★★ |
| 实现复杂度 | ★☆☆ | ★☆☆ | ★★☆ | ★★★ |

### 适用场景

- **适用**：多环境（dev/test/prod）部署、参数频繁变更的ETL任务、多人协作的大型Flow项目
- **不适用**：极度敏感的参数（如API secret key，建议用外部密钥管理）、每个Job完全独立的参数（没有共享价值）

### 注意事项

- `env.*`参数的引用需使用`${key}`（不带`env.`前缀）
- 嵌套引用（`${${key}}`）在部分Azkaban版本中不支持
- Properties文件的编码必须是UTF-8，否则中文参数值会乱码
- 变量名中的`.`没有特殊含义，`db.host`就是一个名为`db.host`的变量

### 常见踩坑经验

1. **变量未替换**：日志中出现`${db.host}`而不是实际IP。原因：变量名在全局/project/Job中都未定义。解决：检查变量名拼写是否一致。
2. **参数优先级误解**：以为project参数能覆盖global参数，但实际上`env.*`的Job参数才是最高优先级。
3. **日期变量在shell中不生效**：在`command.1`中写了`echo ${target_date}`，但输出是空。原因：`target_date`只在`env.*`中设置了默认值，但Azkaban的变量替换发生在Job解析阶段，比shell执行早——如果外部未传值，默认值会表现为`$(date -d "yesterday" +%Y-%m-%d)`这个字符串字面量。

### 思考题

1. 如果你的项目有200个Job，需要把所有数据库连接地址从`192.168.1.200`改为`192.168.1.300`。给你两种方案：A）逐文件修改；B）使用全局Properties文件。请从安全性、维护成本、变更风险三个角度对比两种方案。
2. 在Azkaban中，一个Job能否根据上游Job的输出来决定自己的参数？如果可以，怎么实现？（提示：跨Job通信）。
