# 第5章：Job类型详解（一）——Command与Shell

## 1. 项目背景

### 业务场景

某金融科技公司每天需要处理数百个离线计算任务。数据开发团队使用Azkaban管理这些任务，但很快就遇到了瓶颈——大部分的任务本质上是执行一个shell脚本或Python程序，开发同学天天反复写相似的`.job`文件模板：`type=command, command=python xxx.py`。

更麻烦的是，有些脚本需要传递环境变量，有些需要接收上游Job的输出作为输入，有些需要在不同服务器上执行。开发同学们开始困惑：Azkaban到底能执行哪些类型的命令？环境变量怎么传？多行命令和单行命令有什么差异？

### 痛点放大

不理解Job类型的细节时，常见问题包括：

1. **环境变量丢失**：Python脚本依赖`PATH`中配置的环境变量，但在Azkaban中执行时这些变量为空，导致脚本报错"命令未找到"。
2. **命令串联失败**：写了5行`command.X`，但只有第1行执行了——因为X没有从1开始编号。
3. **退出码误判**：脚本中间某条命令返回非0退出码，但期望的行为是"非关键步骤失败可忽略"，却被Azkaban判定为Job失败。
4. **资源泄漏**：Job执行中途Azkaban重启，启动的子进程变成了孤儿进程。

## 2. 项目设计——剧本式交锋对话

**小胖**（崩溃地挠头）：大师，我的脚本在终端里跑得好好的，放到Azkaban里就报"command not found"！明明`python`命令我已经装了……

**大师**：这是因为Azkaban执行Job时，默认不会加载你的`.bashrc`或`.bash_profile`。你用终端执行时，Shell会自动source这些配置文件；但Azkaban启动的是一个非交互式Shell，不会source这些文件。

**小白**（若有所思）：那我把环境变量写在Job文件的`command`里面行不行？比如：

```
command=export PATH=/usr/local/bin:$PATH && python my_script.py
```

**大师**：理论上可以，但这会把Job文件搞得很臃肿。更好的做法是使用Job级别的`env.property`或在工作目录下放一个`env.sh`文件，在command里先source它。

**小胖**：等等，我刚才发现一个问题——我的Job文件里写了`command.1`、`command.3`、`command.5`，但只有第一条执行了，后面两条都跳过了……

**大师**（笑）：这就是典型的新手坑。Azkaban的`command.n`编号必须是连续的——从1开始，2、3、4依次递增。如果中间缺了`command.2`，Azkaban读完`command.1`后找不到2，就认为命令序列结束了。

**小白**：那如果我一定要跳过某条命令呢？比如在test环境不加某个参数，prod环境再加？

**大师**：那就不要用编号的跳过方式。你可以把条件判断写在shell脚本里面：

```bash
command=bash -c '
  echo "Step 1: Always run"
  if [ "${ENV}" = "prod" ]; then
    echo "Step 2: Production only"
  else
    echo "Step 2: Skipped in ${ENV}"
  fi
  echo "Step 3: Always run"
'
```

**小胖**：还有一个问题——我的脚本里面有个`rm -f`删除临时文件的操作，如果文件不存在，`rm`会返回非0退出码吗？会不会导致整个Job失败？

**大师**：`rm -f`不会返回非0，所以这点你不用担心。但如果你用的是`rm`（不带-f），文件不存在时会返回1，这就会导致Azkaban判定Job失败。Azkaban的逻辑是：**只要脚本的最终退出码不是0，Job就标记为失败**。

**小白**：那如果我有些命令的失败是可以容忍的呢？比如一个"非核心"的数据校验步骤失败了，我不想它影响后面的流程。

**大师**：有几种处理方式：

1. 在shell脚本中使用`|| true`：`data_check.py || true`
2. 使用`set +e`（Bash不退出模式）：在脚本开头加`set +e`
3. 把非关键步骤拆成独立的Job，设置`retries=0`但不设置`dependsOn`依赖

但要注意，过度容忍失败会让真正的异常被掩盖。我的建议是——关键路径用严格模式，非关键路径用独立Job + 非阻塞依赖。

**小胖**：大师，再问最后一个问题——`type=command`和`type=javaprocess`这些有什么区别？是不是我什么任务都能用command搞定？

**大师**：在Azkaban里，`type`定义了Job的执行方式。系统内置了多种类型，最常用的就是`command`——因为它最通用。我给你画一个分类图：

```
Job类型体系：
├── command      —— 执行Shell命令（最通用，适合脚本/命令行）
├── hadoopJava   —— 提交MapReduce/Spark Jar到Yarn集群
├── hadoopShell  —— 执行Hadoop集群脚本（如hive, hdfs）
├── java         —— 在Azkaban进程中运行Java类
├── pig          —— 运行Pig Latin脚本
├── spark        —— Spark作业提交
└── [自定义]     —— 继承AbstractJob实现自己的Job类型
```

对于90%的场景，`command`够用。但当你的任务是Hadoop/Spark作业时，用专用类型可以获得更好的集群管理和资源隔离。

### 技术映射总结

- **command.n编号** = 一次完整的流水线步骤（必须连续编号，Azkaban顺序执行）
- **exit code** = 员工的验收单（0=合格，非0=不合格）
- **env.property** = 工作车间的基础设施（预先准备的环境变量）
- **非交互式Shell** = 自动化车间（不加载个人配置，保证环境一致性）

## 3. 项目实战

### 3.1 环境准备

确保第2章安装的Azkaban Solo Server正常运行。

### 3.2 分步实现

#### 步骤1：Command基础——单行命令

**目标**：使用最基本的command类型执行一条命令。

```bash
# simple_command.job
type=command
command=echo "Hello from Azkaban! Server: $(hostname), Time: $(date)"
```

上传后执行，可在日志中看到输出。

#### 步骤2：Command进阶——多行命令串联

**目标**：执行多条命令并理解编号规则。

```bash
# multi_command.job
type=command
command=echo "=== Data Pipeline Start ==="
command.1=echo "Step 1: Check environment..."
command.2=java -version 2>&1
command.3=echo "Step 2: Create output directory..."
command.4=mkdir -p /tmp/azkaban_output
command.5=echo "Step 3: Generate report..."
command.6=df -h / > /tmp/azkaban_output/disk_report.txt
command.7=echo "=== Data Pipeline End ==="
```

**关键点**：
- `command`（无编号）= 第0条命令
- `command.1`到`command.7` = 第1到第7条命令
- 编号必须连续，Azkaban按编号顺序执行

**错误示例**（编号不连续）：

```bash
# wrong_command.job —— 这个Job只有command.1会执行！
type=command
command=echo "Start"
command.2=echo "This will NOT execute because command.1 is missing"
command.3=echo "Neither will this"
```

#### 步骤3：环境变量配置

**目标**：在Job中设置和使用环境变量。

```bash
# env_demo.job
type=command
env.custom.path=/usr/local/bin:/usr/bin
env.custom.db_url=jdbc:mysql://prod-db:3306/demo
env.custom.db_user=etl_user
env.custom.db_pass=etl_pass

command=echo "Custom PATH: ${custom.path}"
command.1=echo "DB URL: ${custom.db_url}"
command.2=echo "DB User: ${custom.db_user}"
command.3=bash -c '
  # 在子shell中使用环境变量
  export PATH="${custom.path}:$PATH"
  echo "Current PATH: $PATH"
  echo "DB Password length: ${#custom.db_pass}"
'
```

**输出示例**：
```
Custom PATH: /usr/local/bin:/usr/bin
DB URL: jdbc:mysql://prod-db:3306/demo
DB User: etl_user
Current PATH: /usr/local/bin:/usr/bin:/usr/sbin:...
DB Password length: 10
```

#### 步骤4：Shell脚本——Python程序调用

**目标**：在Azkaban中调用Python脚本，处理好依赖和异常。

`scripts/data_processor.py`：

```python
#!/usr/bin/env python3
import os
import sys
import json
from datetime import datetime

def main():
    print(f"[{datetime.now()}] Data processor started")

    # 从环境变量读取参数
    input_path = os.environ.get('INPUT_PATH', '/tmp/default_input')
    output_path = os.environ.get('OUTPUT_PATH', '/tmp/default_output')
    db_url = os.environ.get('DB_URL', 'jdbc:mysql://localhost/test')

    print(f"  Input:  {input_path}")
    print(f"  Output: {output_path}")
    print(f"  DB URL: {db_url}")

    # 模拟数据处理
    if not os.path.exists(input_path):
        print(f"  WARNING: Input path {input_path} does not exist, creating dummy data")
        os.makedirs(input_path, exist_ok=True)
        with open(f"{input_path}/data.txt", "w") as f:
            f.write("dummy_data\n" * 100)

    # 处理数据
    lines = 0
    for fname in os.listdir(input_path):
        with open(os.path.join(input_path, fname)) as f:
            lines += len(f.readlines())

    # 输出结果
    os.makedirs(output_path, exist_ok=True)
    with open(f"{output_path}/result.json", "w") as f:
        json.dump({"input_files": len(os.listdir(input_path)),
                    "total_lines": lines,
                    "processed_at": str(datetime.now())}, f)

    print(f"  Processed {lines} lines from {len(os.listdir(input_path))} files")
    print(f"[{datetime.now()}] Data processor completed successfully")

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
```

`python_job.job`：

```bash
# python_job.job
type=command
env.INPUT_PATH=/tmp/azkaban_input
env.OUTPUT_PATH=/tmp/azkaban_output
env.DB_URL=jdbc:mysql://prod-db:3306/demo

command=echo "=== Python Job Demo ==="
command.1=echo "1. Check Python version..."
command.2=python3 --version
command.3=echo "2. Run data processor..."
command.4=python3 scripts/data_processor.py
command.5=echo "3. Verify output..."
command.6=cat /tmp/azkaban_output/result.json
```

#### 步骤5：错误处理——退出码与容错

**目标**：掌握不同退出码场景的处理方式。

```bash
# error_handling.job
type=command
retries=1

command=echo "=== Error Handling Demo ==="

# 场景1：正常退出（exit 0）
command.1=echo "Task 1: Always succeeds"
command.2=echo "Task 1 completed with exit code: $?"

# 场景2：可容忍的错误（使用 || true）
command.3=echo "Task 2: Non-critical task, failure is acceptable"
command.4=bash -c 'rm /tmp/non_existent_file 2>/dev/null || true'
command.5=echo "Task 2 completed (errors ignored)"

# 场景3：致命错误（不使用 || true）
command.6=echo "Task 3: Critical task, must succeed"
command.7=bash -c '
  if [ -f /tmp/critical_flag ]; then
    echo "Critical flag exists, proceeding..."
    exit 0
  else
    echo "FATAL: Critical flag missing!"
    exit 1
  fi
'

command.8=echo "This line will NOT execute if Task 3 fails"
```

**输出（Task 3失败时）**：
```
=== Error Handling Demo ===
Task 1: Always succeeds
Task 1 completed with exit code: 0
Task 2: Non-critical task, failure is acceptable
Task 2 completed (errors ignored)
Task 3: Critical task, must succeed
FATAL: Critical flag missing!
ERROR [JobRunner] Job error_handling failed with exit code 1
```

#### 步骤6：Orphan进程清理实战

**目标**：确保Job被Kill时，启动的子进程也被清理。

`scripts/long_running_task.sh`：

```bash
#!/bin/bash
# 启动一个长时间运行的后台任务
PID_FILE="/tmp/azkaban_task.pid"

cleanup() {
    echo "[$(date)] Received termination signal, cleaning up..."
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        kill -TERM "$PID" 2>/dev/null
        echo "[$(date)] Killed subprocess PID=$PID"
        rm -f "$PID_FILE"
    fi
    exit 0
}

trap cleanup SIGTERM SIGINT

# 启动后台任务
echo "[$(date)] Starting background task..."
(
    while true; do
        echo "[$(date)] Background task heartbeat..."
        sleep 5
    done
) &

echo $! > "$PID_FILE"
echo "[$(date)] Background task PID=$!"

# 等待后台任务或信号
wait
```

`orphan_guard.job`：

```bash
# orphan_guard.job
type=command
command=echo "Starting long-running task with cleanup hook..."
command.1=chmod +x scripts/long_running_task.sh
command.2=bash scripts/long_running_task.sh
```

**验证方式**：
```bash
# 在另一个终端监控
watch -n 1 'ps aux | grep long_running_task | grep -v grep'

# 在Azkaban Web界面执行 Cancel，观察子进程是否被清理
```

### 3.3 测试验证

```bash
#!/bin/bash
# verify_command_jobs.sh

echo "=== Command Job验证 ==="
TEST_URL="http://localhost:8081"

# 1. 测试基础Command Job
echo "[Test 1] 验证基础Command Job..."
RESULT=$(curl -s -b cookies.txt \
  "${TEST_URL}/executor?ajax=executeFlow&project=test&flow=simple_command")
if $RESULT | grep -q "execid"; then
    echo "  [PASS] Command Job提交成功"
else
    echo "  [FAIL] 提交失败"
fi

# 2. 测试多行命令
echo "[Test 2] 验证多行命令执行..."
# 执行后查看日志，确认command.1~command.7都执行了
sleep 10
EXEC_ID=$(curl -s -b cookies.txt \
  "${TEST_URL}/executor?ajax=getRunning&project=test&flow=simple_command" \
  | grep -o '"execid":[0-9]*')
# ... 省略详细检查逻辑

# 3. 测试环境变量注入
echo "[Test 3] 验证环境变量..."
# 检查env_demo Job的日志输出是否包含正确的变量值

# 4. 测试错误处理
echo "[Test 4] 验证错误处理..."
# 故意触发错误，验证retry机制是否生效

echo "=== 验证完成 ==="
```

## 4. 项目总结

### Command Job特性对比

| 特性 | Command类型 | 独立Shell脚本+& | 容器化任务 |
|------|-----------|---------------|-----------|
| 易用性 | ★★★ 直接在.job中写 | ★★☆ 需额外文件 | ★☆☆ 需Dockerfile |
| 环境隔离 | ★☆☆ 共享OS环境 | ★☆☆ 共享OS环境 | ★★★ 完全隔离 |
| 失败重试 | ★★★ 内置retries | ★☆☆ 手动 | ★★☆ 容器重启 |
| 资源控制 | ★☆☆ 无限制 | ★☆☆ 无限制 | ★★★ cgroup限制 |
| 多服务器 | ★☆☆ 单Executor | ★☆☆ 需ssh | ★★★ 天然分布式 |

### 适用场景

- **适用**：Linux Shell脚本执行、Python/R数据任务、系统运维命令、文件操作、数据库命令行操作
- **不适用**：需要强资源隔离的任务、需要在异构操作系统上运行的任务、需要毫秒级启动的超高频任务

### 注意事项

- `command.n`编号必须从1开始且连续，否则后面的命令不会执行
- 使用`|| true`可以容忍命令失败，但会掩盖真正的错误，慎用
- 脚本中的后台进程（&启动的）在Job结束后不会自动清理，需使用trap + cleanup机制
- `env.KEY=VALUE`中的VALUE不能包含等号和特殊字符时需要用引号包裹

### 常见踩坑经验

1. **环境变量不生效**：把`env.custom.path`写成了`env.CUSTOM_PATH`，在command中用`${CUSTOM_PATH}`引用（大小写未匹配）。解决：环境变量在Azkaban中是大小写敏感的。
2. **Shell函数调用失败**：在`command.n`中定义了bash函数，但在`command.n+1`中调用时报"command not found"。原因：每条`command.n`在独立的子Shell中执行，函数定义不会跨命令传递。解决：将所有命令写在一个`bash -c '...'`块中。
3. **Python脚本路径问题**：`.job`文件用相对路径`python scripts/task.py`，但上传后找不到文件。原因：Azkaban的工作目录不是zip包的解压目录。解决：使用绝对工作目录或确认Azkaban的工作目录位置。

### 思考题

1. 如果需要在Azkaban的command Job中执行一个需要1小时的长任务，中途Azkaban Server因维护需要重启，如何保证任务不丢失且能从断点续跑？
2. `type=command`和`type=javaprocess`在Azkaban内部的执行机制有什么本质区别？为什么后者可以直接访问Azkaban的内部API？
