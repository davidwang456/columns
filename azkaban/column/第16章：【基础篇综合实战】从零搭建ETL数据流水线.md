# 第16章：【基础篇综合实战】从零搭建ETL数据流水线

## 1. 项目背景

### 业务场景

某电商公司需要搭建一套数据仓库ETL流水线。需求如下：

每天凌晨2点，系统自动执行以下数据流转：
1. 从MySQL业务库中导出前一天的订单、用户、商品数据为CSV文件
2. 将这些CSV文件上传到HDFS
3. 在Hive中创建对应的ODS（操作数据层）外部表，并加载数据
4. 对ODS数据进行清洗和转换，生成DWD（明细数据层）表
5. 基于DWD数据，计算当日的GMV、UV、订单转化率等核心指标
6. 将计算结果写入报表表（DWS），供BI工具查询
7. 完成后发送邮件通知数据团队

这条流水线包含7个步骤、涉及MySQL、HDFS、Hive、Spark多个系统，是一个典型的中等规模ETL管道。团队要求用Azkaban来编排和管理整个流程。

### 痛点放大

没有合理编排时：
- **步骤混乱**：新手手动执行，顺序经常搞错（没导数据就开始清洗）
- **失败难追踪**：7个步骤中第4步失败，算不出GMV，报表为空
- **重复劳动**：每天手工执行，耗时1小时以上

## 2. 项目设计——剧本式交锋对话

**小胖**（激动地搓手）：大师，基础篇学完了！Shell、Flow、调度、告警、权限、日志……感觉我已经掌握了Azkaban的方方面面。现在能给我一个综合实战吗？我想检验一下学习成果！

**大师**：好。我正好有个真实的电商ETL案例——从数据采集到报表生成全流程，用Azkaban一管到底。你先说说你会怎么设计这个Flow？

**小胖**：我想想……7个Job，串行编排。第一Job导数据，第二个Job上传HDFS……这样对吗？

**大师**（摇头）：串行太慢了。你看，步骤2（上传HDFS）和步骤4（数据清洗）之间没有直接依赖——2只负责传输文件，4依赖的是3（Hive建表）。但3又依赖2——因为表建好了没数据也不行。所以正确的依赖关系应该是：

```
       ┌─────────────────┐
       │ Job1: MySQL导出  │
       └────────┬────────┘
                │
       ┌────────┴────────┐
       │ Job2: 上传HDFS    │
       └────────┬────────┘
                │
       ┌────────┴────────┐
       │ Job3: Hive建表    │
       └────────┬────────┘
                │
       ┌────────┴────────┐
       │ Job4: 数据清洗    │
       └────────┬────────┘
       ┌────────┴────────┐
       │ Job5: 指标计算    │          Job6: 用户画像
       └────────┬────────┘          (独立、并行)
                │
       ┌────────┴────────┐
       │ Job7: 报表生成    │
       └─────────────────┘
```

**小白**：等等，Job5和Job6独立的意思是说——Job5失败不影响Job6的执行？但Job7同时依赖它们两个？

**大师**：对。GMV计算和用户画像各自独立，但报表需要两者都就绪。这种"收敛式"依赖在Azkaban中直接用`dependsOn=job5,job6`就行。

**小胖**：那如果有50个类似的Job要同时跑，Executor线程池不够怎么办？

**大师**：这就要在Job级别控制——对资源消耗大的Job（如Spark），串行化；对轻量Job（如Shell导出），可以并行。同时建议给Spark类Job指定资源标签（队列），让Yarn做集群级别的资源管理。

### 技术映射总结

- **DAG编排** = 搭积木（认清每块积木的依赖关系，不是所有积木都粘在一起）
- **并行+串行混合** = 厨房做菜（凉菜和热菜分开做，最后统一摆盘）
- **资源标签** = 高速公路分车道（大货车走慢车道，小汽车走快车道）

## 3. 项目实战

### 3.1 项目目录结构

```
etl_pipeline/
├── etl_pipeline.flow          # Flow定义
├── jobs/
│   ├── 01_mysql_export.job     # 步骤1: MySQL数据导出
│   ├── 02_hdfs_upload.job      # 步骤2: HDFS上传（依赖1）
│   ├── 03_hive_ddl.job         # 步骤3: Hive建表（依赖2）
│   ├── 04_data_cleanse.job     # 步骤4: 数据清洗（依赖3）
│   ├── 05_core_metrics.job     # 步骤5: 核心指标计算（依赖4）
│   ├── 06_user_profile.job     # 步骤6: 用户画像（依赖4）
│   └── 07_notify.job           # 步骤7: 通知（依赖5,6）
├── scripts/
│   ├── export_mysql.sh         # MySQL导出脚本
│   ├── hdfs_upload.sh          # HDFS上传脚本
│   ├── data_cleanse.py         # Spark清洗脚本
│   └── notify.py               # 通知脚本
├── sql/
│   ├── hive_ods_ddl.sql        # ODS层建表SQL
│   ├── hive_dwd_ddl.sql        # DWD层建表SQL
│   └── dws_metrics.sql         # 指标计算SQL
└── config/
    ├── dev.properties
    ├── test.properties
    └── prod.properties
```

### 3.2 分步实现

#### 步骤1：MySQL数据导出Job

**目标**：从MySQL导出订单、用户、商品数据到CSV文件。

`scripts/export_mysql.sh`：

```bash
#!/bin/bash
set -e

YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)
EXPORT_DIR="/data/etl/export/${YESTERDAY}"
mkdir -p "$EXPORT_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "=== MySQL数据导出开始 ==="
log "目标日期: $YESTERDAY"

# 导出订单数据
log "1/3 导出订单数据..."
mysql -h "${SOURCE_DB_HOST}" -P "${SOURCE_DB_PORT}" \
  -u "${SOURCE_DB_USER}" -p"${SOURCE_DB_PASS}" \
  "${SOURCE_DB_NAME}" -N -e "
    SELECT id, user_id, product_id, amount, status, created_at, updated_at
    FROM orders WHERE DATE(created_at)='${YESTERDAY}'
" | sed 's/\t/,/g' > "${EXPORT_DIR}/orders.csv"

ORDER_COUNT=$(wc -l < "${EXPORT_DIR}/orders.csv")
log "  导出订单数: ${ORDER_COUNT}"

# 导出用户数据（全量增量）
log "2/3 导出用户数据..."
mysql -h "${SOURCE_DB_HOST}" -P "${SOURCE_DB_PORT}" \
  -u "${SOURCE_DB_USER}" -p"${SOURCE_DB_PASS}" \
  "${SOURCE_DB_NAME}" -N -e "
    SELECT id, name, email, city, level, created_at
    FROM users WHERE DATE(created_at)='${YESTERDAY}'
       OR DATE(modified_at)='${YESTERDAY}'
" | sed 's/\t/,/g' > "${EXPORT_DIR}/users.csv"

USER_COUNT=$(wc -l < "${EXPORT_DIR}/users.csv")
log "  导出用户数: ${USER_COUNT}"

# 导出商品数据
log "3/3 导出商品数据..."
mysql -h "${SOURCE_DB_HOST}" -P "${SOURCE_DB_PORT}" \
  -u "${SOURCE_DB_USER}" -p"${SOURCE_DB_PASS}" \
  "${SOURCE_DB_NAME}" -N -e "
    SELECT id, name, category_id, price, stock, status
    FROM products
" | sed 's/\t/,/g' > "${EXPORT_DIR}/products.csv"

PRODUCT_COUNT=$(wc -l < "${EXPORT_DIR}/products.csv")
log "  导出商品数: ${PRODUCT_COUNT}"

# 写入统计摘要
cat > "${EXPORT_DIR}/summary.txt" << EOF
DATE=${YESTERDAY}
ORDER_COUNT=${ORDER_COUNT}
USER_COUNT=${USER_COUNT}
PRODUCT_COUNT=${PRODUCT_COUNT}
STATUS=SUCCESS
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
EOF

log "=== MySQL数据导出完成 ==="
log "摘要: $(cat ${EXPORT_DIR}/summary.txt)"
```

`jobs/01_mysql_export.job`：

```bash
type=command
command=bash scripts/export_mysql.sh
retries=2
retry.backoff=60000
failure.emails=${alert.email}
```

#### 步骤2：HDFS上传Job

`scripts/hdfs_upload.sh`：

```bash
#!/bin/bash
set -e

YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)
EXPORT_DIR="/data/etl/export/${YESTERDAY}"
HDFS_BASE="${TARGET_HDFS_BASE}/ods"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "=== HDFS上传开始 ==="

# 创建HDFS目录
hdfs dfs -mkdir -p "${HDFS_BASE}/orders/dt=${YESTERDAY}"
hdfs dfs -mkdir -p "${HDFS_BASE}/users/dt=${YESTERDAY}"
hdfs dfs -mkdir -p "${HDFS_BASE}/products/dt=${YESTERDAY}"

# 上传数据文件
for table in orders users products; do
    SRC="${EXPORT_DIR}/${table}.csv"
    DST="${HDFS_BASE}/${table}/dt=${YESTERDAY}/"
    
    if [ -f "$SRC" ]; then
        log "上传 ${table}.csv -> ${DST}"
        hdfs dfs -put -f "$SRC" "$DST"
        
        # 验证上传
        HDFS_COUNT=$(hdfs dfs -cat "${DST}${table}.csv" 2>/dev/null | wc -l)
        LOCAL_COUNT=$(wc -l < "$SRC")
        log "  ${table}: 本地${LOCAL_COUNT}行, HDFS${HDFS_COUNT}行"
        
        if [ "$HDFS_COUNT" -ne "$LOCAL_COUNT" ]; then
            log "  ✗ ${table}行数不一致！"
            exit 1
        fi
    else
        log "  ✗ 本地文件缺失: $SRC"
        exit 1
    fi
done

log "=== HDFS上传完成 ==="
```

`jobs/02_hdfs_upload.job`：

```bash
type=command
command=bash scripts/hdfs_upload.sh
dependsOn=01_mysql_export
retries=1
retry.backoff=30000
```

#### 步骤3：Hive建表Job

`sql/hive_ods_ddl.sql`：

```sql
-- ODS层建表SQL（外部表，指向HDFS数据目录）

CREATE EXTERNAL TABLE IF NOT EXISTS ods.orders (
    order_id STRING,
    user_id STRING,
    product_id STRING,
    amount DOUBLE,
    status STRING,
    created_at STRING,
    updated_at STRING
) PARTITIONED BY (dt STRING)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION '${hive.warehouse.dir}/ods/orders';

CREATE EXTERNAL TABLE IF NOT EXISTS ods.users (
    user_id STRING,
    user_name STRING,
    email STRING,
    city STRING,
    level STRING,
    created_at STRING
) PARTITIONED BY (dt STRING)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION '${hive.warehouse.dir}/ods/users';

CREATE EXTERNAL TABLE IF NOT EXISTS ods.products (
    product_id STRING,
    product_name STRING,
    category_id STRING,
    price DOUBLE,
    stock INT,
    status STRING
) PARTITIONED BY (dt STRING)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION '${hive.warehouse.dir}/ods/products';
```

`jobs/03_hive_ddl.job`：

```bash
type=command
command=bash -c '
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)
echo "[$(date)] === Hive建表与分区加载 ==="

hive -e "
-- 执行DDL
source sql/hive_ods_ddl.sql;

-- 添加分区
ALTER TABLE ods.orders ADD IF NOT EXISTS PARTITION (dt=\"${YESTERDAY}\");
ALTER TABLE ods.users ADD IF NOT EXISTS PARTITION (dt=\"${YESTERDAY}\");
ALTER TABLE ods.products ADD IF NOT EXISTS PARTITION (dt=\"${YESTERDAY}\");

-- 验证数据
SELECT \"orders\" AS tbl, COUNT(*) AS cnt FROM ods.orders WHERE dt=\"${YESTERDAY}\"
UNION ALL
SELECT \"users\", COUNT(*) FROM ods.users WHERE dt=\"${YESTERDAY}\"
UNION ALL
SELECT \"products\", COUNT(*) FROM ods.products WHERE dt=\"${YESTERDAY}\";
"
'
dependsOn=02_hdfs_upload
```

#### 步骤4&5：数据清洗与指标计算（Spark）

`jobs/04_data_cleanse.job`：

```bash
type=spark
spark.master=yarn
spark.deploy.mode=cluster
spark.class=com.etl.DataCleanse
spark.jars=spark-etl.jar
spark.executor.memory=4g
spark.executor.cores=2
spark.num.executors=5
spark.yarn.queue=etl
spark.args=${process_date},${ods_db},${dwd_db}
spark.conf.spark.sql.adaptive.enabled=true
dependsOn=03_hive_ddl
retries=1
retry.backoff=120000
```

`jobs/05_core_metrics.job`：

```bash
type=spark
spark.master=yarn
spark.deploy.mode=cluster
spark.class=com.etl.CoreMetrics
spark.jars=spark-etl.jar
spark.executor.memory=6g
spark.executor.cores=3
spark.num.executors=8
spark.yarn.queue=etl
spark.args=${process_date},${dwd_db},${dws_db}
dependsOn=04_data_cleanse
retries=1
retry.backoff=180000
```

#### 步骤6：用户画像（独立并行）

`jobs/06_user_profile.job`：

```bash
type=spark
spark.master=yarn
spark.deploy.mode=cluster
spark.class=com.etl.UserProfile
spark.jars=spark-etl.jar
spark.executor.memory=4g
spark.executor.cores=2
spark.num.executors=4
spark.yarn.queue=etl
spark.args=${process_date},${dwd_db},${dws_db}
dependsOn=04_data_cleanse
```

#### 步骤7：通知Job

`jobs/07_notify.job`：

```bash
type=command
command=bash -c '
python3 scripts/notify.py \
  --project="${azkaban.flow.projectname}" \
  --flow="${azkaban.flow.flowid}" \
  --execid="${azkaban.flow.execid}" \
  --date="${process_date}" \
  --status=SUCCESS
'
dependsOn=05_core_metrics,06_user_profile
failure.emails=${alert.email}
```

#### 步骤8：Flow定义与调度配置

`etl_pipeline.flow`：

```
nodes=01_mysql_export,02_hdfs_upload,03_hive_ddl,04_data_cleanse,05_core_metrics,06_user_profile,07_notify
```

`config/prod.properties`：

```properties
process_date=$(date -d "yesterday" +%Y-%m-%d)
source.db.host=prod-mysql.company.com
source.db.port=3306
source.db.name=prod_orders
source.db.user=etl_user
target.hdfs.base=hdfs://nn-prod:8020/user/hive/warehouse
ods.db=ods
dwd.db=dwd
dws.db=dws
alert.email=oncall@company.com
hive.warehouse.dir=/user/hive/warehouse
```

### 3.3 部署与调度

```bash
#!/bin/bash
# deploy_prod.sh —— 生产环境部署脚本

# 1. 构建部署包
python3 ../tools/build_deploy_package.py prod

# 2. 登录Azkaban生产
curl -c cookies.txt -X POST "${PROD_AZKABAN}" \
  --data "action=login&username=${AK_USER}&password=${AK_PASS}"

# 3. 上传Flow
curl -b cookies.txt \
  -X POST "${PROD_AZKABAN}/manager?project=etl_pipeline&ajax=upload" \
  -F "file=@etl_pipeline_prod.zip"

# 4. 创建每日调度（凌晨2:00）
curl -b cookies.txt \
  -X POST "${PROD_AZKABAN}/schedule" \
  --data "ajax=scheduleCronFlow" \
  --data "projectName=etl_pipeline" \
  --data "flow=etl_pipeline" \
  --data "cronExpression=0 0 2 * * ?" \
  --data "scheduleTimezone=Asia/Shanghai" \
  --data "failureAction=finishCurrent" \
  --data "failureEmails=oncall@company.com"

# 5. 验证部署
echo "=== 部署验证 ==="
curl -b cookies.txt \
  "${PROD_AZKABAN}/manager?project=etl_pipeline&ajax=fetchprojectflows"

echo "部署完成！调度将于每天凌晨2:00自动执行。"
```

### 3.4 验收测试

```bash
#!/bin/bash
# acceptance_test.sh

echo "=== ETL流水线验收测试 ==="
echo ""

# 测试1：手动执行Flow
echo "[Test 1] 手动触发执行..."
EXEC_RESULT=$(curl -s -b cookies.txt \
  -X POST "${AZKABAN_URL}/executor?ajax=executeFlow" \
  --data "project=etl_pipeline&flow=etl_pipeline")
EXEC_ID=$(echo "$EXEC_RESULT" | grep -o '"execid":[0-9]*' | grep -o '[0-9]*')
echo "  Execution ID: $EXEC_ID"

# 测试2：监控执行状态（轮询30分钟）
echo "[Test 2] 监控执行状态..."
for i in $(seq 1 30); do
    sleep 60
    STATUS=$(curl -s -b cookies.txt \
      "${AZKABAN_URL}/executor?execid=${EXEC_ID}&ajax=fetchexecflow" \
      | python3 -c "import json,sys;print(json.load(sys.stdin).get('status','UNKNOWN'))")
    echo "  [${i}min] 状态: $STATUS"
    
    if [ "$STATUS" = "SUCCEEDED" ]; then
        echo "  ✓ Flow执行成功！"
        break
    elif [ "$STATUS" = "FAILED" ] || [ "$STATUS" = "KILLED" ]; then
        echo "  ✗ Flow执行失败: $STATUS"
        exit 1
    fi
done

# 测试3：验证数据质量
echo "[Test 3] 验证数据质量..."
hive -e "
SELECT 'ods.orders' AS layer, COUNT(*) AS cnt FROM ods.orders WHERE dt='$(date -d yesterday +%Y-%m-%d)'
UNION ALL
SELECT 'dwd.order_detail', COUNT(*) FROM dwd.order_detail WHERE dt='$(date -d yesterday +%Y-%m-%d)'
UNION ALL
SELECT 'dws.daily_gmv', COUNT(*) FROM dws.daily_gmv WHERE dt='$(date -d yesterday +%Y-%m-%d)';
"

# 测试4：验证调度配置
echo "[Test 4] 验证调度配置..."
SCHEDULE=$(curl -s -b cookies.txt \
  "${AZKABAN_URL}/schedule?ajax=fetchAllScheduledFlows")
if echo "$SCHEDULE" | grep -q "etl_pipeline"; then
    echo "  ✓ 调度配置正确"
else
    echo "  ✗ 调度配置未找到"
fi

# 测试5：验证告警配置
echo "[Test 5] 验证告警配置..."
JOB_CONFIG=$(curl -s -b cookies.txt \
  "${AZKABAN_URL}/manager?project=etl_pipeline&ajax=fetchprojectflows")
if echo "$JOB_CONFIG" | grep -q "failure.emails"; then
    echo "  ✓ 告警配置存在"
fi

echo ""
echo "=== 验收测试完成 ==="
```

## 4. 项目总结

### 实现成果

| 指标 | 目标值 | 实际值 |
|------|--------|--------|
| 全流程耗时 | < 1小时 | ~45分钟 |
| 数据延迟 | T+1（凌晨可用） | ✓ |
| 失败自动重试 | 支持 | ✓ |
| 告警通知 | 邮件+企业微信 | ✓ |
| 多环境支持 | dev/test/prod | ✓ |

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 可靠性 | 多级重试+失败阻断，核心链路不会丢数据 | 依赖外部系统（HDFS/Yarn故障无法自愈） |
| 可维护性 | Job拆分清晰，修改单个步骤不影响全局 | 新增Job需修改.flow文件 |
| 可观测性 | 每个Job独立日志，执行时间可追踪 | 缺乏全局的DAG运行时监控 |
| 扩展性 | 新增步骤只需加Job和dependsOn | 长链路的调试成本随Job数增加 |

### 适用场景

- **适用**：T+1的离线数据仓库ETL、常规报表生成、电商/金融等行业的日终批处理
- **不适用**：需要秒级响应的实时计算、不确定性高的探索性数据分析

### 注意事项

- 数据导出的SQL必须带上条件（`WHERE dt=...`），避免全表扫描
- Spark任务需指定Yarn队列，防止资源争抢
- MySQL密码不要硬编码，通过环境变量或外部Key管理传入
- 通知Job必须作为最后一个Job，且同时依赖所有上游收敛节点

### 思考题

1. 如果ETL过程中需要对接的外部系统增加到10个（MySQL、HDFS、Hive、Spark、Elasticsearch、Redis、Kafka、Druid、Presto、ClickHouse），如何在不增加复杂度的情况下管理这个Flow？
2. 如何为此流水线设计"数据质量监控"——当某个环节的数据量比前一天下降超过30%时，自动阻断并告警？
