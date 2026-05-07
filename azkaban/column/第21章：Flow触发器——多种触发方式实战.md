# 第21章：Flow触发器——多种触发方式实战

## 1. 项目背景

### 业务场景

数据团队除了凌晨2点的定时批处理外，还需要支持以下触发模式：

1. **API触发**：运营系统在促销活动结束时，需要立即触发"活动效果分析"Flow
2. **依赖触发**：当上游数据团队的"日志采集"Flow完成后，自动触发本团队的"数据清洗"Flow
3. **文件到达触发**：当HDFS上某个目录出现了今日的数据文件时，自动触发入库流程
4. **手动触发**：开发同学调试时需要在Web界面手动执行

团队当前的Azkaban只配了Cron定时调度，每次依赖触发都要写脚本轮询等待——运营系统调用一个REST API执行Flow，但那边的开发同学说"API文档太旧，参数不明确"。

### 痛点放大

触发方式单一时：

1. **实时性差**：事件已经发生了，但要等到下一个Cron周期才处理
2. **耦合度高**：上游团队执行完自己的Flow后，要手动调下游的API
3. **集成困难**：外部系统要触发Azkaban Flow，文档不全、认证复杂
4. **重复造轮子**：每个团队都写了自己的"等待-触发"脚本

## 2. 项目设计——剧本式交锋对话

**小胖**（接到运营部紧急电话）：大师，运营那边说双十一活动结束了，需要马上跑"活动数据分析"Flow。但现在才下午2点，这个Flow只配了凌晨2点的调度啊！

**大师**：手动执行就行了。打开Azkaban Web界面，找到那个Project → 点击Flow → Execute Flow。或者用API：

```bash
curl -X POST "http://azkaban:8081/executor?ajax=executeFlow" \
  --data "project=marketing&flow=campaign_analysis" \
  -b cookies.txt
```

**小白**：那能不能让外部系统也这样触发？比如运营系统点了"活动结束"按钮，自动触发这个Flow？

**大师**：当然可以。你给他们一个封装好的API就行。但要注意几个关键点：

1. **认证**：外部系统调用需要带上有效的session或token
2. **幂等性**：运营系统可能会重复发送请求——你要保证同一个Flow不会被触发两次
3. **参数传递**：运营系统可能需要传入活动ID等业务参数

**小胖**：还有一个需求——我们团队有一个"数据清洗"Flow，它必须等上游团队的"日志采集"Flow跑完才能开始。但上游团队不归我们管，没法在一个Flow里写dependsOn。怎么办？

**大师**：这是典型的"跨Flow依赖"场景。Azkaban原生不支持，但你可以有几种变通方案：

- **方案A：下游轮询**。在"日志采集"Flow的最后一个Job中，通过API触发下游Flow。
- **方案B：事件驱动**。写一个服务定期查询上游Flow的状态，看到成功后触发下游。
- **方案C：文件信号**。上游跑完后在HDFS上写一个`_SUCCESS`标记文件，下游监控到后自动启动。

**小白**：文件信号方案最解耦！具体怎么实现？

**大师**（写下来）：

```bash
# 上游Flow最后一个Job（成功后才执行）
type=command
command=hdfs dfs -touchz /data/flags/log_collection_${YESTERDAY}/_SUCCESS

# 下游Flow用一个独立的监控Job
# 定期检查标记文件是否存在，存在则触发真正的ETL
type=command
command=while true; do
  if hdfs dfs -test -e /data/flags/log_collection_${YESTERDAY}/_SUCCESS; then
    echo "Signal file found, triggering ETL..."
    break
  else
    echo "Waiting for upstream flow..."
    sleep 60
  fi
done
```

**小胖**：那如果上游Flow失败了怎么办？标记文件永远不会生成，下游就一直等着？

**大师**：需要在监控Job中加超时+告警：

```bash
TIMEOUT=$((4 * 3600))  # 最多等4小时
START_TIME=$(date +%s)
while true; do
    if hdfs dfs -test -e "$SIGNAL_FILE"; then
        break
    fi
    NOW=$(date +%s)
    if [ $((NOW - START_TIME)) -gt $TIMEOUT ]; then
        echo "TIMEOUT: Signal file not generated after 4 hours, aborting"
        exit 1
    fi
    sleep 60
done
```

### 技术映射总结

- **Cron触发** = 闹钟（到点就响，不管别人准备好没）
- **API触发** = 门铃（有人按了你再开门）
- **依赖触发** = 接力赛（前一棒跑到终点，你才能出发）
- **文件信号** = 信箱（看到信到了就知道可以干活了）

## 3. 项目实战

### 3.1 环境准备

Azkaban运行中，准备3个测试项目。

### 3.2 分步实现

#### 步骤1：REST API封装（外部系统调用）

**目标**：提供简单易用的API供外部系统触发Flow。

```python
#!/usr/bin/env python3
# azkaban_api_client.py —— Azkaban REST API封装

import requests
import time
import logging

class AzkabanClient:
    """Azkaban REST API客户端"""
    
    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self._login(username, password)
    
    def _login(self, username, password):
        """登录并获取session"""
        resp = self.session.post(
            f"{self.base_url}",
            data={"action": "login", "username": username, "password": password}
        )
        resp.raise_for_status()
        if "session.id" not in resp.text:
            raise Exception(f"Login failed: {resp.text[:200]}")
        logging.info("Login successful")
    
    def execute_flow(self, project, flow, flow_params=None, 
                     failure_action="finishPossible", concurrent_option="skip"):
        """触发Flow执行"""
        data = {
            "ajax": "executeFlow",
            "project": project,
            "flow": flow,
            "failureAction": failure_action,
            "concurrentOption": concurrent_option,
        }
        
        # 添加Flow级参数
        if flow_params:
            for key, value in flow_params.items():
                data[f"flowOverride[{key}]"] = value
        
        resp = self.session.post(
            f"{self.base_url}/executor",
            data=data
        )
        result = resp.json()
        
        if "execid" in result:
            exec_id = result["execid"]
            logging.info(f"Flow {flow} started, execution ID: {exec_id}")
            return exec_id
        elif "error" in result:
            raise Exception(f"Execute failed: {result['error']}")
        else:
            raise Exception(f"Unknown response: {result}")
    
    def wait_for_completion(self, exec_id, timeout=3600, poll_interval=10):
        """等待Flow执行完成"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            resp = self.session.get(
                f"{self.base_url}/executor",
                params={"execid": exec_id, "ajax": "fetchexecflow"}
            )
            data = resp.json()
            status = data.get("status", "UNKNOWN")
            
            logging.info(f"Execution {exec_id}: {status}")
            
            if status in ("SUCCEEDED", "FAILED", "KILLED"):
                return status
            
            time.sleep(poll_interval)
        
        raise TimeoutError(f"Execution {exec_id} did not complete within {timeout}s")
    
    def get_executions(self, project, flow, limit=10):
        """获取Flow的执行历史"""
        resp = self.session.get(
            f"{self.base_url}/manager",
            params={
                "project": project,
                "ajax": "fetchFlowExecutions",
                "flow": flow,
                "start": 0,
                "length": limit
            }
        )
        return resp.json()["executions"]

# ===== 使用示例 =====
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    
    client = AzkabanClient("http://azkaban.company.com:8081", "api_user", "api_pass")
    
    # 触发一个Flow并等待完成
    exec_id = client.execute_flow(
        "marketing",
        "campaign_analysis",
        flow_params={"campaign_id": "2025-double11", "date": "2025-11-11"}
    )
    
    status = client.wait_for_completion(exec_id, timeout=1800)
    print(f"Flow completed with status: {status}")
```

#### 步骤2：幂等触发防重复

**目标**：确保同一个Flow不会被重复触发。

```python
# 在execute_flow方法中加入幂等检查
def execute_flow_idempotent(self, project, flow, idempotent_key):
    """
    幂等触发：相同的idempotent_key在30分钟内不会重复触发
    """
    # 1. 检查最近30分钟是否有同一个key的执行
    recent_execs = self.get_executions(project, flow, limit=50)
    
    for exec_info in recent_execs:
        # 检查时间范围
        start_time = exec_info.get("startTime", 0)
        if time.time() - start_time / 1000 > 1800:  # 30分钟
            continue
        
        # 检查是否有相同的idempotent_key
        # （需要在触发时把key作为Flow参数传入）
        if exec_info.get("flowParams", {}).get("idempotent_key") == idempotent_key:
            logging.info(f"Flow already triggered with key {idempotent_key}, "
                         f"execution: {exec_info['execId']}")
            return exec_info['execId']
    
    # 2. 没有重复，正常触发
    return self.execute_flow(
        project, flow,
        flow_params={"idempotent_key": idempotent_key}
    )
```

#### 步骤3：跨Flow依赖触发

**目标**：上游Flow完成后自动触发下游Flow。

**上游Flow的最后一个Job**：

```bash
# upstream_trigger.job —— 上游Flow最后一个Job
type=command
command=bash -c '
echo "=== 跨Flow依赖触发 ==="

# 上游执行成功，写入标记文件
SIGNAL_DIR="/data/flags/pipeline/daily"
SIGNAL_FILE="${SIGNAL_DIR}/log_collection_${process_date}/_SUCCESS"

hdfs dfs -mkdir -p "${SIGNAL_DIR}/log_collection_${process_date}"
hdfs dfs -touchz "$SIGNAL_FILE"

echo "信号文件已创建: $SIGNAL_FILE"

# 可选：直接触发下游Flow
python3 /opt/scripts/trigger_downstream.py \
  --project=data_warehouse \
  --flow=data_cleanse \
  --date=${process_date}
'
dependsOn=log_collection,hdfs_archive
```

**下游Flow的等待Job**：

```bash
# wait_upstream.job —— 下游Flow第一个Job
type=command
command=bash -c '
SIGNAL_DIR="/data/flags/pipeline/daily"
SIGNAL_FILE="${SIGNAL_DIR}/log_collection_${process_date}/_SUCCESS"
TIMEOUT=$((4 * 3600))  # 4小时超时
POLL_INTERVAL=60       # 每60秒检查一次

echo "等待上游 Flow 完成..."
echo "检查信号文件: $SIGNAL_FILE"
echo "超时时间: ${TIMEOUT}秒"

START_TIME=$(date +%s)

while true; do
    if hdfs dfs -test -e "$SIGNAL_FILE" 2>/dev/null; then
        echo "✓ 上游Flow已完成，信号文件存在！"
        echo "  信号文件时间: $(hdfs dfs -ls "$SIGNAL_FILE" | tail -1)"
        exit 0
    fi
    
    ELAPSED=$(($(date +%s) - START_TIME))
    if [ $ELAPSED -gt $TIMEOUT ]; then
        echo "✗ 超时！等待了${TIMEOUT}秒，上游Flow未完成"
        exit 1
    fi
    
    echo "  等待中... 已等待 ${ELAPSED}秒 (将等到 ${TIMEOUT}秒)"
    sleep $POLL_INTERVAL
done
'
retries=0
failure.emails=oncall@company.com
```

#### 步骤4：事件驱动触发服务

**目标**：构建一个独立的监控服务，监听标记文件并触发Flow。

```python
#!/usr/bin/env python3
# event_driven_trigger.py —— 事件驱动的触发服务

import os
import time
import json
import logging
import subprocess
from datetime import datetime

class EventDrivenTrigger:
    """基于事件驱动的Flow触发器"""
    
    def __init__(self, config_file):
        with open(config_file, 'r') as f:
            self.config = json.load(f)
        
        self.watchers = self.config.get("watchers", [])
        self.trigger_cache = {}  # 防重复触发缓存
    
    def watch(self):
        """启动监控循环"""
        logging.info("=== Event-Driven Trigger 启动 ===")
        logging.info(f"监控规则数: {len(self.watchers)}")
        
        while True:
            for watcher in self.watchers:
                self._check_watcher(watcher)
            time.sleep(self.config.get("poll_interval", 30))
    
    def _check_watcher(self, watcher):
        """检查单个监控规则"""
        rule_id = watcher["id"]
        
        # 检查触发条件
        if not self._check_condition(watcher["condition"]):
            return
        
        # 防重复：同一个规则3分钟内不重复触发
        last_trigger = self.trigger_cache.get(rule_id, 0)
        if time.time() - last_trigger < 180:
            return
        
        # 执行触发动作
        self._execute_action(watcher["action"])
        self.trigger_cache[rule_id] = time.time()
    
    def _check_condition(self, condition):
        """检查触发条件"""
        cond_type = condition["type"]
        
        if cond_type == "hdfs_file_exists":
            path = condition["path"]
            cmd = f"hdfs dfs -test -e {path}"
            return subprocess.call(cmd, shell=True) == 0
        
        elif cond_type == "http_status":
            try:
                import requests
                resp = requests.get(condition["url"], timeout=5)
                return resp.status_code == 200
            except:
                return False
        
        elif cond_type == "time":
            # 指定时间点触发
            target_time = condition["time"]
            now = datetime.now().strftime("%H:%M")
            return now == target_time
        
        return False
    
    def _execute_action(self, action):
        """执行触发动作"""
        action_type = action["type"]
        
        if action_type == "azkaban_execute":
            logging.info(f"Triggering Flow: {action['flow']}")
            from azkaban_api_client import AzkabanClient
            client = AzkabanClient(
                self.config["azkaban_url"],
                self.config["azkaban_user"],
                self.config["azkaban_password"]
            )
            client.execute_flow(
                action["project"],
                action["flow"],
                flow_params=action.get("params", {})
            )

# ===== 配置文件示例 =====
# config.json
if __name__ == '__main__':
    # 写一个示例配置
    config = {
        "azkaban_url": "http://localhost:8081",
        "azkaban_user": "trigger_bot",
        "azkaban_password": "bot_pass",
        "poll_interval": 30,
        "watchers": [
            {
                "id": "log_collection_done",
                "condition": {
                    "type": "hdfs_file_exists",
                    "path": "/data/flags/log_collection_$(date -d yesterday +%Y-%m-%d)/_SUCCESS"
                },
                "action": {
                    "type": "azkaban_execute",
                    "project": "data_warehouse",
                    "flow": "data_cleanse",
                    "params": {"date": "yesterday"}
                }
            }
        ]
    }
    
    with open("trigger_config.json", "w") as f:
        json.dump(config, f, indent=2)
    
    trigger = EventDrivenTrigger("trigger_config.json")
    trigger.watch()
```

#### 步骤5：统一触发管理

**目标**：所有触发方式由统一接口管理。

```bash
#!/bin/bash
# unified_trigger.sh —— 统一触发入口

PROJECT=$1
FLOW=$2
TRIGGER_TYPE=${3:-manual}  # manual | cron | event | api
TRIGGER_PARAMS=$4

AZKABAN_URL="http://localhost:8081"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${TRIGGER_TYPE}] $*" \
        >> /var/log/azkaban/triggers.log
}

case $TRIGGER_TYPE in
    manual)
        log "手动触发: $PROJECT/$FLOW by user=${USER}"
        curl -s -b /tmp/azkaban.cookie \
          -X POST "${AZKABAN_URL}/executor?ajax=executeFlow" \
          --data "project=${PROJECT}&flow=${FLOW}"
        ;;
    
    cron)
        # 检查是否有正在运行的实例（防止quartz misfire重复触发）
        RUNNING=$(curl -s -b /tmp/azkaban.cookie \
          "${AZKABAN_URL}/manager?project=${PROJECT}&ajax=fetchFlowExecutions&flow=${FLOW}&start=0&length=5" \
          | python3 -c "import json,sys; data=json.load(sys.stdin); print(len([e for e in data.get('executions',[]) if e.get('status')=='RUNNING']))")
        
        if [ "$RUNNING" -gt 0 ]; then
            log "跳过Cron触发: 已有 $RUNNING 个实例运行中"
            exit 0
        fi
        
        log "Cron触发: $PROJECT/$FLOW"
        curl -s -b /tmp/azkaban.cookie \
          -X POST "${AZKABAN_URL}/executor?ajax=executeFlow" \
          --data "project=${PROJECT}&flow=${FLOW}" \
          --data "concurrentOption=skip"
        ;;
    
    event)
        log "事件触发: $PROJECT/$FLOW params=${TRIGGER_PARAMS}"
        curl -s -b /tmp/azkaban.cookie \
          -X POST "${AZKABAN_URL}/executor?ajax=executeFlow" \
          --data "project=${PROJECT}&flow=${FLOW}" \
          --data "flowOverride[event_params]=${TRIGGER_PARAMS}"
        ;;
    
    api)
        log "API触发: $PROJECT/$FLOW"
        curl -s -b /tmp/azkaban.cookie \
          -X POST "${AZKABAN_URL}/executor?ajax=executeFlow" \
          --data "project=${PROJECT}&flow=${FLOW}"
        ;;
    
    *)
        log "未知触发类型: $TRIGGER_TYPE"
        exit 1
        ;;
esac

log "触发成功"
```

### 3.3 测试验证

```bash
#!/bin/bash
# verify_triggers.sh

echo "=== 触发系统验证 ==="
CLIENT_PY="python3 azkaban_api_client.py"

# 1. 测试API触发
echo "[Test 1] API触发测试..."
RESULT=$($CLIENT_PY execute marketing test_flow)
if echo "$RESULT" | grep -q "execid"; then
    echo "  [PASS] API触发成功"
fi

# 2. 测试幂等性
echo "[Test 2] 幂等性测试..."
ID1=$($CLIENT_PY execute_idempotent test_project test_flow "key-001")
ID2=$($CLIENT_PY execute_idempotent test_project test_flow "key-001")
if [ "$ID1" = "$ID2" ]; then
    echo "  [PASS] 幂等性验证通过（相同key返回相同exec_id）"
fi

# 3. 测试文件信号触发
echo "[Test 3] 文件信号触发..."
hdfs dfs -touchz /tmp/test_signal
sleep 30
# 检查event_driven_trigger是否检测到并触发了Flow

echo "=== 验证完成 ==="
```

## 4. 项目总结

### 触发方式对比

| 触发方式 | 延迟 | 解耦性 | 实现复杂度 | 适用场景 |
|---------|------|--------|----------|---------|
| Cron调度 | 分钟级 | ★☆☆ | ★☆☆ | 定期批处理 |
| Web手动 | 即时 | — | ★☆☆ | 调试/补跑 |
| REST API | 秒级 | ★★☆ | ★★☆ | 外部系统集成 |
| 文件信号 | 分钟级 | ★★★ | ★★☆ | 跨系统解耦依赖 |
| 事件驱动 | 秒级 | ★★★ | ★★★ | 复杂依赖链 |

### 适用场景

- **适用**：跨团队协作的复杂数据管道、需要外部系统集成的场景、实时性要求<1分钟的场景
- **不适用**：单团队的简单定时任务（Cron就够了）、毫秒级实时要求

### 注意事项

- 幂等性是触发设计的核心——确保同一条件不会触发两次同一Flow
- 文件信号方案的HDFS目录需要统一命名规范
- 客户端API的session有有效期（默认24小时），长时间运行需要重新登录
- 监控服务需要考虑自身的HA——不能因为它挂了导致所有Flow触发链断裂

### 常见踩坑经验

1. **API session过期**：外部系统的触发脚本长期运行，24小时后session过期，所有触发失败。解决：每次调用前检查session有效性，无效则重新登录。
2. **文件信号的时间窗口**：上游凌晨2点写信号文件，下游凌晨1:55开始等待——结果等到凌晨3点都没发现。原因：日期参数不匹配（上游用的"当天"，下游用的"昨天"）。
3. **连环触发导致资源耗尽**：A触发B，B触发C，结果AB同时运行时，B被触发两次——导致C也被触发两次。解决：在Flow级别加锁防重复。

### 思考题

1. 如何实现一个"条件触发"——Flow执行后，根据其执行结果（成功/失败）和输出参数的值，决定是否触发下一个Flow？
2. 如果你有50个Flow之间的复杂依赖关系（包括跨团队依赖），如何设计一个"全局依赖看板"——一眼就能看出当前哪些Flow在等谁？
