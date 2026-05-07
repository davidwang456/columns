# 第37章：与调度系统集成实战——DolphinScheduler与Airflow

## 1. 项目背景

某电商公司的数据团队在 DataX 接入半年后，同步任务从 20 个增长到 120 个。运维小张每天的工作变成了：早上 6 点起床，登录跳板机，手动执行 `python datax.py job_orders_inc.json`，盯着终端看日志，做完一个手动触发下一个——整个过程持续到 10 点（尤其在任务失败时要手动重跑），平均耗时 3~4 小时。这期间小张什么也干不了，完全成了"人肉调度器"。

问题不止于人力浪费。某个周三凌晨，MySQL 源库做了在线 DDL（加大表字段），导致增量同步任务连续 3 次失败。小张因为前一晚上线到凌晨 2 点，早上睡过头到 8 点半才起床——等他上线手动重跑时，业务方已经投诉"今天报表数据又延迟了"。CTO 当场拍板：**DataX 任务必须接入调度系统，实现全自动化执行**。

架构组调研了市面主流的调度方案：XXL-JOB 轻量但缺乏 DAG 编排能力，Azkaban 易用但社区不够活跃，Airflow 功能强大但 Python 技术栈重，DolphinScheduler 有官方的 DataX 任务类型且可视化 DAG 编排体验最好。最终决定采用 DolphinScheduler 作为主力调度器，Airflow 作为备选方案，并为团队制定了一套"DataX 生产调度模板"。

本章讲三件事：为什么调度系统是 DataX 生产化的必选项；DolphinScheduler 如何原生态支持 DataX（包括参数化配置和上游依赖编排）；Airflow 如何通过 BashOperator 和 XCom 集成 DataX。最终手把手搭建一个完整的"数据校验 → DataX 同步 → 数据质量报告"调度 DAG。

## 2. 项目设计——剧本式交锋对话

**（周一上午，CTO 拍完桌子的第二天，数据团队紧急开会）**

**小胖**：（一脸无辜）我觉得 crontab 也挺好用的啊，每天早上 6 点自动执行，不比手动强？

**小白**：（推了推眼镜）crontab 能做的事非常有限。我问你三个问题：第一，如果上游数据源还没准备好（比如 Hadoop 的 `sqoop` 还在导出），你的 DataX 任务就启动了，读到一个空表或半成品表——你怎么处理？第二，A 任务（MySQL→Hive）跑完后，B 任务（Hive 分区刷新）才能跑，crontab 怎么保证这个先后顺序？第三，昨天任务失败了，crontab 能不能自动重试 3 次、重试失败后发钉钉告警？

**大师**：（翻开笔记本电脑，投影到屏幕上）小白问到了三个核心需求——**定时触发、依赖编排、失败重试和告警**。crontab 只能解决第一个（而且是用纯时间触发，不懂"上游是否就绪"），后面两个是调度系统的专属能力。

**技术映射**：crontab = 闹钟（到点叫醒你），调度系统 = 智能管家（先检查冰箱有没有食材，再按菜谱步骤做饭，中间出错了会叫你，做完还会通知全家人吃饭）。

---

**小胖**：（举手）那你们说的 DolphinScheduler 和 Airflow，哪个更适合我们？

**大师**：我先说结论——如果你的技术栈主要是 Java/Shell，选 DolphinScheduler；如果团队 Python 成熟度高，选 Airflow。但更重要的是**功能对比**而不是语言偏好。

DolphinScheduler 有官方的 **DataX 任务类型**——你在 DS 的 Web UI 上新建一个 DataX 节点，可以直接粘贴 JSON 配置，DS 内部会调用 `python datax.py` 执行。这是最"开箱即用"的集成方式，不需要写任何脚本。

Airflow 没有 DataX 任务类型，但通过 **BashOperator** + **XCom** 组合，可以达到同样甚至更灵活的效果。比如 Airflow 的 XCom 可以在 Task 之间传递数据（源表行数、耗时、QPS），下游 Task 可以根据这些数据动态决策——"如果增量数据量 > 1 亿行，自动切换到全量同步兜底"。

**小胖**：慢着，"XCom"是什么？

**大师**：XCom（Cross-Communication）是 Airflow 的任务间消息传递机制。Task A 执行完后可以把结果（比如 JSON）push 到 XCom，Task B 从 XCom pull 出来用。这就好比一个消息黑板——A 写完走人，B 来看黑板继续干活。

---

**小白**：（在白板上画出对比表）我整理一下：

| 能力 | DolphinScheduler | Airflow |
|------|-----------------|---------|
| DataX 原生任务类型 | 支持，无需编码 | 不支持，需 BashOperator |
| DAG 可视化编排 | 拖拽式，中文友好 | 代码定义（Python DAG） |
| 参数化配置 | 全局变量 + 节点参数 | Jinja2 模板 + Variable |
| 任务间数据传递 | 依赖传递 | XCom push/pull |
| 失败重试 | 内置，单个节点配置 | retries 参数 |
| 告警 | 钉钉/邮件/飞书/企业微信 | Email + Callback 插件 |
| SLA 监控 | 支持 | 支持 SLA Miss |
| 部署复杂度 | 中（需 ZooKeeper） | 中（需 Redis/RabbitMQ） |

**小胖**：那我选 DolphinScheduler 了——不用写代码，拖拖拽拽就能搞定。

**大师**：别急着选。有一个关键差异：**参数化配置的动态能力**。DS 的全局变量是静态字符串替换——你在 JSON 里写 `${yesterday}`，DS 在运行时用昨天的日期替换掉。但如果你的需求是"根据数据量动态决定 channel 数"——比如增量 < 1000 万行用 channel=5，> 1000 万行用 channel=20——这种动态决策 DS 做不到，需要在 DS 外面预先算好。而 Airflow 的 PythonOperator 可以写任意 Python 逻辑来计算。

**技术映射**：DolphinScheduler = 乐高积木（用现成的组件拼搭），Airflow = 木工工具组（你可以自己削木头，自由度更高但门槛也高）。

---

**小胖**：（挠头）那我们干脆两个都部署，看哪个好用用哪个？

**大师**：（笑）按需选择。对于 80% 的场景——定时跑增量全量同步、数据校验、基本的失败重试和告警——DolphinScheduler 的 DataX 任务类型完全够用，拖拽式编排也足够直观。对于那 20% 的复杂场景——动态分片、自适应降级、多级回退——再引入 Airflow 也不迟。

我们今天的实战就聚焦 DolphinScheduler，因为它是大多数团队的"首选"。但我会预留 Airflow 的集成模板，给 Python 技术栈的同学参考。

---

**小白**：（追问）DS 的 DataX 任务类型内部是怎么调用 datax.py 的？如果我们自己改了 datax.py 的 JVM 参数，DS 还会生效吗？

**大师**：DS 的 DataX 任务实际上就是 `ProcessBuilder` 启动一个子进程执行 `python datax.py`，跟你在命令行里跑一模一样。区别在于 DS 把这个命令封装成了任务类型，你只需要填 JSON 配置，DS 自动把它写到临时文件、调用 datax.py、监控子进程退出码、收集 stdout/stderr 日志。

至于 JVM 参数——DS 尊重你在 `datax.py` 中的配置（实际上 datax.py 内部是用 `java` 命令启动的，JVM 参数也在脚本里），所以如果你自己改了 datax.py 里的 `-Xmx`，DS 调用时自然用你的新参数。但更好的做法是把 JVM 参数做成 DS 的自定义参数，做到"不同 Job 不同 JVM 参数"。

**技术映射**：DS DataX 任务 = 一个"智能遥控器"——你按一次按钮（提交 JSON），它自动帮你找到 DVD 播放器（datax.py），放入光盘（JSON 配置），按下播放键（执行同步），最后告诉你电影放完了（退出码）。

## 3. 项目实战

### 3.1 步骤一：部署 DolphinScheduler 并配置 DataX 环境

**目标**：在测试服务器上部署 DolphinScheduler Standalone 模式，配置 DataX 可执行路径。

```powershell
# 1. 下载并解压 DolphinScheduler
wget https://dlcdn.apache.org/dolphinscheduler/3.2.1/apache-dolphinscheduler-3.2.1-bin.tar.gz
tar -xzf apache-dolphinscheduler-3.2.1-bin.tar.gz -C /opt/
cd /opt/apache-dolphinscheduler-3.2.1-bin

# 2. 配置 DataX 环境变量（在 conf/env/dolphinscheduler_env.sh 中追加）
# 编辑 conf/env/dolphinscheduler_env.sh
```

**环境变量配置**（`conf/env/dolphinscheduler_env.sh` 追加内容）：

```bash
# DataX 相关环境变量
export DATAX_HOME=/opt/datax
export DATAX_PYTHON=/usr/bin/python3
export JAVA_HOME=/usr/lib/jvm/java-11-openjdk
export PATH=$DATAX_HOME/bin:$JAVA_HOME/bin:$PATH

# 可选：自定义 DataX 的 JVM 参数（DS 各 Worker 节点统一生效）
export DATAX_JVM_OPTS="-server -Xms2g -Xmx8g -XX:+UseG1GC"
```

```powershell
# 3. 初始化数据库并启动
# DS 使用 PostgreSQL 存储元数据（Standalone 模式可用内嵌 H2，生产建议 PG）
# 创建数据库
# CREATE DATABASE dolphinscheduler;
# GRANT ALL PRIVILEGES ON DATABASE dolphinscheduler TO ds_user;

# 执行初始化脚本
bash tools/bin/upgrade-schema.sh

# 启动 DS standalone
bash bin/dolphinscheduler-daemon.sh start standalone-server

# 4. 验证启动
curl http://localhost:12345/dolphinscheduler/ui/
# 默认用户名/密码: admin/dolphinscheduler123

# 5. 在 DS Web UI 中，进入【安全中心→环境管理】，新增一条环境：
#   - 环境名称: datax-env
#   - 环境配置: export DATAX_HOME=/opt/datax; export PATH=$DATAX_HOME/bin:$PATH
```

### 3.2 步骤二：创建第一个 DataX 任务——从 DS 直接运行

**目标**：在 DolphinScheduler 中创建一个 DataX 任务节点，直接粘贴 JSON 配置，单次执行验证。

**操作流程（DS Web UI）**：

```
1. 进入【项目管理】→ 新建项目 "data-sync-platform"
2. 进入项目 → 【工作流定义】→ 创建工作流 "test_datax_job"
3. 从左侧组件栏拖入一个 "DATAX" 节点到画布
4. 配置 DATAX 节点:
   - 节点名称: mysql_orders_full_sync
   - 自定义参数: 无需（先用静态配置）
   - JSON 配置: 粘贴以下 JSON
5. 点击【保存】→ 回到工作流定义页 →【上线】→【运行】
6. 在【任务实例】中查看运行日志
```

**DataX JSON 配置**（粘贴到 DS DATAX 节点的 JSON 编辑框中）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "etl_user",
                    "password": "etl_pass_2026",
                    "column": ["order_id","user_id","product_id","amount","status","channel","create_time","update_time"],
                    "splitPk": "order_id",
                    "connection": [{
                        "table": ["orders"],
                        "jdbcUrl": ["jdbc:mysql://10.0.1.100:3306/ecommerce?useSSL=false&useCursorFetch=true"]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "etl_user",
                    "password": "etl_pass_2026",
                    "writeMode": "replace",
                    "column": ["order_id","user_id","product_id","amount","status","channel","create_time","update_time"],
                    "preSql": ["TRUNCATE TABLE orders_dw"],
                    "batchSize": 4096,
                    "session": ["SET unique_checks=0","SET foreign_key_checks=0"],
                    "connection": [{
                        "table": ["orders_dw"],
                        "jdbcUrl": ["jdbc:mysql://10.0.2.100:3306/data_warehouse?useSSL=false&rewriteBatchedStatements=true"]
                    }]
                }
            }
        }],
        "setting": {
            "speed": {"channel": 12, "byte": 104857600},
            "errorLimit": {"record": 0, "percentage": 0}
        }
    }
}
```

**运行日志关键片段**（DS 任务实例 → 查看日志）：

```
[INFO] 2026-05-06 02:00:00.123 - Task starts, type: DATAX, name: mysql_orders_full_sync
[INFO] 2026-05-06 02:00:00.456 - Generated temp config file: /tmp/ds/datax_job_168.json
[INFO] 2026-05-06 02:00:00.789 - Executing: python3 /opt/datax/bin/datax.py /tmp/ds/datax_job_168.json
[INFO] 2026-05-06 02:12:34.001 - DataX job completed, exit code: 0
[INFO] 2026-05-06 02:12:34.123 - Total records: 58,230,000, Time: 12m 33s, Speed: 77,304 rec/s
```

### 3.3 步骤三：参数化增量同步——DS 全局变量 + 时间占位符

**目标**：利用 DS 的全局变量和内置时间参数，实现"每天凌晨自动跑前一天增量"。

**配置 DS 全局变量**（【安全中心→全局变量】）:

```
变量名: yesterday
变量值: $[yyyy-MM-dd-1]
说明: 自动计算昨天的日期

变量名: today
变量值: $[yyyy-MM-dd]
说明: 今天的日期

变量名: batch_id
变量值: INC_$[yyyyMMdd-1]
说明: 增量批次ID
```

**DataX JSON 配置**（增量版，使用 DS 全局变量）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "etl_user",
                    "password": "etl_pass_2026",
                    "column": ["order_id","user_id","product_id","amount","status","channel","create_time","update_time"],
                    "splitPk": "order_id",
                    "where": "update_time >= '${yesterday}' AND update_time < '${today}'",
                    "fetchSize": -2147483648,
                    "connection": [{
                        "table": ["orders"],
                        "jdbcUrl": ["jdbc:mysql://10.0.1.100:3306/ecommerce?useSSL=false&useCursorFetch=true"]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "etl_user",
                    "password": "etl_pass_2026",
                    "writeMode": "replace",
                    "column": ["order_id","user_id","product_id","amount","status","channel","create_time","update_time","sync_batch"],
                    "session": ["SET unique_checks=0","SET foreign_key_checks=0"],
                    "batchSize": 4096,
                    "connection": [{
                        "table": ["orders_dw"],
                        "jdbcUrl": ["jdbc:mysql://10.0.2.100:3306/data_warehouse?useSSL=false&rewriteBatchedStatements=true"]
                    }]
                }
            },
            "transformer": [{
                "name": "dx_groovy",
                "parameter": {
                    "code": "import com.alibaba.datax.common.element.*;\n"
                            + "record.addColumn(new StringColumn('${batch_id}'));\n"
                            + "return record;"
                }
            }]
        }],
        "setting": {
            "speed": {"channel": 8},
            "errorLimit": {"record": 100, "percentage": 0.01}
        }
    }
}
```

**设置定时调度**（在 DS 工作流中）：

```
1. 点击工作流画布空白处 → 右侧属性面板
2. 定时设置:
   - 生效时间: 2026-05-07 00:00:00 起
   - 调度周期: 天
   - 定时表达式: 0 0 6 * * ?  (每天凌晨 6:00)
   - 失败策略: 继续（DAG 其他分支不受影响）
   - 最大重试次数: 3
   - 重试间隔: 5 分钟
   - 超时告警: 60 分钟
   - 告警组: data-team (钉钉群机器人)
3. 参数优先级:
   - 优先级: HIGHEST
   - 租户: datax
```

**DS 运行时参数替换验证**：

```
DS 日志中可以看到实际执行的 JSON 中:
  "${yesterday}" → "2026-05-05"
  "${today}"     → "2026-05-06"
  "${batch_id}"  → "INC_20260505"

最终生成的 SQL: WHERE update_time >= '2026-05-05' AND update_time < '2026-05-06'
```

### 3.4 步骤四：构建完整数据同步流水线 DAG

**目标**：创建一个包含 5 个节点的 DAG——数据源检查 → 全量同步 → 增量同步 → 数据质量校验 → 钉钉通知。

**DAG 流程设计**：

```
[Source Check] → [Full Sync (MySQL→Hive)] → [Partition Refresh]
     ↓                                              ↓
     └────────────→ [Incremental Sync] ────────────→ [Quality Check] → [DingTalk Alert]
```

**各节点配置**：

**节点1：Source Check（Shell 类型）**——验证源表数据可用性

```bash
#!/bin/bash
# 检查 MySQL 源表是否有数据
ROW_COUNT=$(mysql -h10.0.1.100 -uetl_user -petl_pass_2026 -Ne "SELECT COUNT(*) FROM ecommerce.orders WHERE update_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY)")

if [ "$ROW_COUNT" -eq 0 ]; then
    echo "ERROR: Source table has 0 rows updated in last 24h, aborting sync."
    exit 1
fi
echo "Source check passed: $ROW_COUNT rows available for sync."
```

**节点2：Full Sync（DATAX 类型）**——全量同步订单表到 Hive

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "etl_user",
                    "password": "etl_pass_2026",
                    "column": ["*"],
                    "splitPk": "order_id",
                    "fetchSize": -2147483648,
                    "connection": [{
                        "table": ["orders"],
                        "jdbcUrl": ["jdbc:mysql://10.0.1.100:3306/ecommerce?useSSL=false&useCursorFetch=true"]
                    }]
                }
            },
            "writer": {
                "name": "hdfswriter",
                "parameter": {
                    "defaultFS": "hdfs://namenode:9000",
                    "fileType": "orc",
                    "path": "/data_warehouse/ods/orders_full/dt=${today}",
                    "fileName": "orders",
                    "writeMode": "truncate",
                    "compress": "SNAPPY",
                    "column": [
                        {"name":"order_id","type":"bigint"},
                        {"name":"user_id","type":"bigint"},
                        {"name":"product_id","type":"bigint"},
                        {"name":"amount","type":"double"},
                        {"name":"status","type":"int"},
                        {"name":"channel","type":"string"},
                        {"name":"create_time","type":"timestamp"},
                        {"name":"update_time","type":"timestamp"}
                    ],
                    "hadoopConfig": {
                        "dfs.nameservices": "namenode",
                        "dfs.ha.namenodes.namenode": "nn1,nn2"
                    }
                }
            }
        }],
        "setting": {
            "speed": {"channel": 20, "byte": 209715200}
        }
    }
}
```

**节点3：Partition Refresh（SQL 类型）**——Hive 分区刷新（也可用 Shell 类型调用 beeline）

```sql
ALTER TABLE ods.orders_full ADD IF NOT EXISTS PARTITION (dt='${today}');
MSCK REPAIR TABLE ods.orders_full;
```

**节点4：Quality Check（Shell 类型）**——行数校验

```bash
#!/bin/bash
# 对比源表（昨天增量部分）与目标表（Hive分区）的行数
SRC_COUNT=$(mysql -h10.0.1.100 -uetl_user -petl_pass_2026 -Ne "SELECT COUNT(*) FROM ecommerce.orders WHERE update_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY)")
DST_COUNT=$(beeline -u "jdbc:hive2://hiveserver:10000" --outputformat=csv2 -e "SELECT COUNT(*) FROM ods.orders_full WHERE dt='${today}'" 2>/dev/null | tail -1)

if [ "$SRC_COUNT" -ne "$DST_COUNT" ]; then
    echo "QUALITY CHECK FAILED: Source=$SRC_COUNT, Target=$DST_COUNT, diff=$((SRC_COUNT - DST_COUNT))"
    exit 1
fi
echo "QUALITY CHECK PASSED: $SRC_COUNT rows verified."
```

**节点5：DingTalk Alert（Shell 类型）**——发钉钉通知

```bash
#!/bin/bash
curl -H "Content-Type: application/json" \
  -X POST \
  -d "{
    \"msgtype\": \"markdown\",
    \"markdown\": {
      \"title\": \"DataX 同步完成通知\",
      \"text\": \"## DataX 订单数据同步完成\n- 日期: ${today}\n- 状态: ✅ 成功\n- 源表行数: ${SRC_COUNT}\n- 目标行数: ${DST_COUNT}\n- 耗时: 参考DS日志\n- [查看详情](http://10.0.1.10:12345/dolphinscheduler)\"
    }
  }" \
  "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN"
```

**DAG 依赖关系设置**（在 DS 中使用连线连接节点）：

```
Source Check ──成功──→ Full Sync ──成功──→ Partition Refresh
     │                                          │
     └────────成功──→ Incremental Sync ─────────┘
                        │
                        └──成功──→ Quality Check ──成功──→ DingTalk Alert
```

### 3.5 步骤五：Airflow 集成方案（Python 技术栈备选）

**目标**：在 Airflow 中用 BashOperator 执行 DataX 任务，XCom 传递结果，SLA 监控。

**Airflow DAG 定义**（`dags/datax_orders_sync.py`）：

```python
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator, BranchPythonOperator
from airflow.models import Variable
import json, os, re

default_args = {
    'owner': 'data-team',
    'depends_on_past': False,
    'start_date': datetime(2026, 5, 6),
    'retries': 3,
    'retry_delay': timedelta(minutes=5),
    'email_on_failure': True,
    'email': ['data-team@company.com'],
    'sla': timedelta(hours=1),
}

DATAX_HOME = '/opt/datax'
JOB_DIR = '/opt/datax/jobs'

with DAG(
    'datax_orders_sync',
    default_args=default_args,
    description='DataX MySQL → Hive 订单同步 DAG',
    schedule_interval='0 6 * * *',
    catchup=False,
    tags=['datax', 'etl', 'orders'],
) as dag:

    # Task 1: 源端数据检查
    check_source = BashOperator(
        task_id='check_source_data',
        bash_command='''
            ROWS=$(mysql -h{{ var.value.mysql_host }} -u{{ var.value.mysql_user }} -p{{ var.value.mysql_pass }} -Ne \
              "SELECT COUNT(*) FROM ecommerce.orders WHERE update_time >= '{{ ds }}'")
            if [ "$ROWS" -eq 0 ]; then
                echo "No data to sync, exiting."
                exit 2
            fi
            echo "{\"source_rows\": $ROWS}" > /tmp/datax_source_result.json
            echo "Source check PASSED: $ROWS rows"
        ''',
    )

    # Task 2: DataX 全量同步
    run_datax_full = BashOperator(
        task_id='datax_full_sync',
        bash_command='''
            YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)
            sed "s/\${yesterday}/$YESTERDAY/g" {{ params.job_template }} > /tmp/datax_job.json
            
            START_TS=$(date +%s)
            python3 {{ params.datax_home }}/bin/datax.py /tmp/datax_job.json
            EXIT_CODE=$?
            END_TS=$(date +%s)
            DURATION=$((END_TS - START_TS))
            
            if [ $EXIT_CODE -eq 0 ]; then
                echo "{\"status\": \"SUCCESS\", \"duration_sec\": $DURATION}" > /tmp/datax_result.json
            else
                echo "{\"status\": \"FAILED\", \"exit_code\": $EXIT_CODE}" > /tmp/datax_result.json
                exit $EXIT_CODE
            fi
            echo "DataX full sync completed in ${DURATION}s"
        ''',
        params={
            'datax_home': DATAX_HOME,
            'job_template': f'{JOB_DIR}/orders_full_to_hive.template.json',
        },
    )

    # Task 3: 提取并存储 DataX 统计结果（XCom）
    def extract_datax_stats(**context):
        with open('/tmp/datax_result.json') as f:
            result = json.load(f)
        context['ti'].xcom_push(key='datax_status', value=result['status'])
        context['ti'].xcom_push(key='datax_duration', value=result['duration_sec'])
        return result

    extract_stats = PythonOperator(
        task_id='extract_datax_stats',
        python_callable=extract_datax_stats,
    )

    # Task 4: 质量校验（根据 XCom 中的数据量动态决策）
    def quality_check(**context):
        duration = context['ti'].xcom_pull(task_ids='extract_datax_stats', key='datax_duration')
        # 如果同步耗时超过 60 分钟，触发告警分支
        if duration and duration > 3600:
            context['ti'].xcom_push(key='alert_level', value='HIGH')
            return 'send_alert'
        return 'hive_refresh_partition'

    qc_branch = BranchPythonOperator(
        task_id='quality_check_branch',
        python_callable=quality_check,
    )

    # Task 5: Hive 分区刷新
    hive_refresh = BashOperator(
        task_id='hive_refresh_partition',
        bash_command='''
            beeline -u "jdbc:hive2://hiveserver:10000" -e \
              "ALTER TABLE ods.orders_full ADD IF NOT EXISTS PARTITION (dt='{{ ds }}')"
        ''',
        sla=timedelta(minutes=30),
    )

    # Task 6: 钉钉告警
    send_alert = BashOperator(
        task_id='send_alert',
        bash_command='''
            curl -H "Content-Type: application/json" -X POST \
              -d "{\"msgtype\": \"text\", \"text\": {\"content\": \"⚠️ DataX full sync SLA exceeded: {{ ti.xcom_pull(task_ids='extract_datax_stats', key='datax_duration') }}s\"}}" \
              "{{ var.value.dingtalk_webhook }}"
        ''',
    )

    # DAG 依赖
    check_source >> run_datax_full >> extract_stats >> qc_branch
    qc_branch >> [hive_refresh, send_alert]
```

**Airflow Variable 配置**（Web UI → Admin → Variables）：

```
key: mysql_host     value: 10.0.1.100
key: mysql_user     value: etl_user
key: mysql_pass     value: etl_pass_2026
key: dingtalk_webhook  value: https://oapi.dingtalk.com/robot/send?access_token=xxx
```

### 3.6 生产调度模板

**目标**：提供一套可直接复用的生产调度策略。

**模板1：每日全量 + 每小时增量**

```
调度策略:
  06:00 — 全量同步（channel=24, 无 byte 限速）→ 全量 T+1 数据仓库
  07:00~23:00 — 每小时增量同步（channel=8, byte=50MB）→ 准实时报表
  00:00~05:00 — 维护窗口，无调度

DAG 结构（DS）:
  FullSync ──成功──→ IncrementalHourly_07 ...... IncrementalHourly_23
                           ↓
                     QualityCheck ──失败──→ Retry x3 ──失败──→ DingTalk Alert
```

**模板2：失败自动降级**

```
降级策略:
  第一次全量同步失败 → 自动重试（同配置，最多 3 次）
  3 次全量失败 → 降级为增量同步（只同步 yesterday~today 的变更数据）
  增量也失败 → 发紧急告警，转人工处理

DS 实现:
  FullSync ──失败(3次后)──→ FallbackIncremental
  FallbackIncremental ──失败──→ ManualIntervention (超时后自动告警)
```

### 3.7 可能遇到的坑及解决方法

**坑1：DS 中 DataX JSON 包含 `${}` 占位符被 DS 本身解析**

DS 的自定义参数语法也是 `${param}`，如果 JSON 中的 `${yesterday}` 正好和 DS 节点参数名冲突——DS 会优先用自己的变量替换，导致 DataX 收到错误的替换值。

```
解决: 在 DS 的 DATAX 节点中，使用双花括号转义：${{yesterday}}
      或者将 DataX 的占位符改为 DS 风格的 $[yyyy-MM-dd-1]
```

**坑2：Airflow 的 BashOperator 中 `ds` 变量格式**

Airflow 的 `{{ ds }}` 默认为 `YYYY-MM-DD` 格式。如果 DataX 的 WHERE 条件需要 `YYYYMMDD` 格式，请使用 `{{ ds_nodash }}`。

**坑3：DS Worker 找不到 datax.py 的问题**

DS 的 Worker 进程以 `dolphinscheduler` 用户运行，其 PATH 和 DATAX_HOME 可能未配置。

```
解决: 在 DS 环境管理中配置完整的环境变量
      或在 DATAX 节点的【环境名称】中选择正确的环境配置
```

**坑4：生产调度的"重复执行"问题**

DS 任务跑得太慢，下一个调度周期到了而前一周期还在跑——会导致两个进程同时操作同一张表。

```
解决: 在 DS 工作流设置中开启"并行度=1"（同一工作流同时只能有一个实例运行）
      或者在 SQL 脚本开头加互斥锁: SELECT GET_LOCK('datax_full_sync', 0)
```

## 4. 项目总结

### 4.1 调度系统核心能力对比

| 能力 | crontab | DolphinScheduler | Airflow |
|------|---------|-----------------|---------|
| 定时触发 | 支持 | 支持（Cron + 日历） | 支持（Cron + Timetable） |
| DAG 依赖 | 不支持 | 拖拽可视化 | Python 代码 |
| DataX 原生集成 | 不支持 | DataX 任务类型 | BashOperator |
| 参数化配置 | 需手写 sed 替换 | 全局变量 + 内置参数 | Jinja2 + Variable |
| 失败重试 | 不支持 | 内置（次数 + 间隔） | retries + retry_delay |
| 告警通知 | 不支持 | 钉钉/邮件/飞书/企业微信 | Email + Callback |
| SLA 监控 | 不支持 | 超时告警 | sla_miss_callback |
| 任务间数据传递 | 不支持 | 上游参数→下游 | XCom push/pull |

### 4.2 优点

1. **DS 对 DataX 的原生支持**：零编码集成，拖拽 DataX 节点 → 粘贴 JSON → 上线即跑
2. **参数化配置**：DS 的 `$[yyyy-MM-dd-1]` 内置时间函数 + 全局变量，DataX JSON 秒变动态配置
3. **DAG 编排**：数据校验→全量同步→增量同步→质量检查→通知，一条龙自动执行
4. **Airflow 灵活性**：PythonOperator + XCom 实现动态决策——根据数据量动态选择 channel、根据耗时触发降级
5. **生产模板可复用**：每日全量 + 每小时增量 + 失败自动降级，三套模板覆盖 90% 的生产场景

### 4.3 缺点

1. **DS 部署重量**：依赖 ZooKeeper + PostgreSQL，小型团队 Standalone 模式可能不够稳定
2. **DS 参数能力有限**：全局变量是纯字符串替换，不支持条件判断和动态计算
3. **Airflow 学习曲线陡**：Python DAG 编写 + Operator 选择 + XCom 使用 + CeleryExecutor 配置——新人上手慢
4. **调度器本身的监控**：如果 DS/Airflow 自己挂了，所有 DataX 任务都不会跑——需要额外对调度器做存活监控

### 4.4 选型建议

| 团队特征 | 推荐方案 |
|---------|---------|
| Java 技术栈、< 100 个调度任务 | DolphinScheduler |
| Python 技术栈、需要复杂 DAG 逻辑 | Airflow |
| 企业已有 K8s 体系 | Airflow (KubernetesPodOperator) |
| 追求轻量、< 20 个调度任务 | XXL-JOB + Shell 脚本调用 datax.py |

### 4.5 思考题

1. 在 DS 中，如果全量同步任务（凌晨 6:00 触发）耗时超过了 4 小时，到了上午 10:00 增量同步的触发时间——增量任务应该如何感知"全量还在跑"这个状态？请设计一个互斥方案。
2. Airflow 的 XCom 默认使用元数据库（PostgreSQL/MySQL）存储，如果 DataX 任务产生的统计信息很大（比如几百 KB 的日志摘要），直接用 XCom 会有性能问题。请设计一种替代方案——如何在不压垮 Airflow 元数据库的前提下，让下游 Task 获取到 DataX 的运行统计？

（答案见附录）
