# 第36章：自定义Provider开发——从零构建企业Operator包

## 1 项目背景

某企业的运维团队在日常工作中有一个高频需求：通过企业微信机器人向群聊发送数据管道的运行状态通知、告警信息和日报摘要。起初，开发者在每个 Dag 中手动使用 `PythonOperator` 调用企业微信的 Webhook API，但随着 Dag 数量从 5 个增长到 50 个，维护噩梦开始了：Webhook URL 散落在各处，Token 变更时需要修改 50 个 Dag 文件；某个 Dag 因为网络超时没发出告警，排查了 2 小时才发现是重试逻辑写错了；新同事接手后完全搞不清每个通知到底走的是什么逻辑。

更糟的是，团队需要实现一个"等待群聊回复"的功能——发出一条审批请求后，等待特定用户回复"同意"或"拒绝"，超时则自动降级。用 `PythonSensorOperator` 写这种轮询逻辑不仅代码冗长，而且轮询间隔很难把握——太频繁会触发企业微信 API 频率限制，太稀疏又影响业务时效。

> 这些痛点指向一个清晰的解决方案：**开发一个 `apache-airflow-providers-wechat` Provider**。将企业微信的能力封装成标准的 Airflow Hook、Operator、Sensor 和 Trigger，让所有 Dag 通过统一的接口消费，而不是每个 Dag 都重复造轮子。

---

## 2 项目设计

**小胖**（在第 50 个 Dag 里粘贴同样的 Webhook 代码）："每次都要复制粘贴这段发送企业微信的代码，已经粘了 50 次了。这感觉就像每家餐厅都自己挖一口井，而不是接自来水。"

**大师**："你说到点子上了。Airflow 的 Provider 机制就是'自来水系统'——把通用能力封装成标准的 Hook、Operator 和 Sensor，所有 Dag 直接调用。你现在的 Webhook 代码应该变成一个 `WeChatHook`，发送消息的操作变成一个 `WeChatOperator`，等待回复的操作变成一个 `WeChatSensor`。"

**小白**："Provider 的目录结构长什么样？怎么让 Airflow 发现它？"

**大师**："一个标准 Provider 包的目录结构非常规范，我们来看 `providers/common/sql/` 的真实布局：

```
apache-airflow-providers-wechat/
├── pyproject.toml          # 包元数据和依赖
├── provider.yaml           # Provider 注册信息
├── README.rst              # 文档入口
├── LICENSE / NOTICE        # Apache 2.0 许可
├── src/
│   └── airflow/
│       └── providers/
│           └── wechat/
│               ├── __init__.py
│               ├── get_provider_info.py   # 返回 provider.yaml 信息的入口
│               ├── hooks/
│               │   ├── __init__.py
│               │   └── wechat.py          # WeChatHook
│               ├── operators/
│               │   ├── __init__.py
│               │   └── wechat.py          # WeChatOperator
│               ├── sensors/
│               │   ├── __init__.py
│               │   └── wechat.py          # WeChatSensor
│               ├── triggers/
│               │   ├── __init__.py
│               │   └── wechat.py          # WeChatTrigger (deferrable)
│               └── notifications/
│                   ├── __init__.py
│                   └── wechat.py          # WeChatNotifier (可选)
├── tests/
│   └── providers/
│       └── wechat/
│           ├── hooks/
│           │   └── test_wechat.py
│           ├── operators/
│           │   └── test_wechat.py
│           └── sensors/
│               └── test_wechat.py
└── docs/
    ├── index.rst
    └── changelog.rst
```

其中的关键是 `provider.yaml`——它声明了包名、版本、以及集成信息。Airflow 通过 `entry_points` 发现 Provider：`pyproject.toml` 中 `[project.entry-points."apache_airflow_provider"]` 定义了 `provider_info` 入口，指向 `get_provider_info.py` 中的函数。"

**小胖**："Hook、Operator、Sensor 三者分别做什么？"

**大师**："这是 Provider 设计模式的三层抽象。**Hook**（钩子）是最底层——封装与外部系统的连接和认证逻辑，提供 `get_conn()` 和 `test_connection()` 方法。**Operator**（算子）是中间层——封装一个原子操作，在 `execute()` 方法中调用 Hook 完成具体任务。**Sensor**（传感器）是特殊类型的 Operator——`poke()` 方法返回布尔值表示条件是否满足，通常用于轮询等待。

举个例子：`WeChatHook` 负责管理企业微信的 API Token 和 HTTP 会话；`WeChatOperator` 的 `execute()` 方法调用 `WeChatHook.send_message()` 发送一条消息；`WeChatSensor` 的 `poke()` 方法调用 `WeChatHook.check_reply()` 检查是否收到回复。"

**小白**："Deferrable Operator 和 Trigger 又是什么？"

**大师**："这是为了优化 Sensor 的效率。传统的 Sensor 在每个 `poke_interval`（比如 30 秒）就占用一个 Worker Slot 去轮询——如果等待时间很长（比如等待人工审批可能几个小时），Worker Slot 资源就浪费了。

Deferrable Operator 解决了这个问题：当 Operator 判断条件不满足时，它调用 `self.defer(trigger=WeChatTrigger(...), method_name="execute_complete")`——这会释放 Worker Slot，把等待任务注册到 Triggerer 进程中。Triggerer 是一个轻量级轮询进程，专门负责等待异步条件。当条件满足时，Triggerer 通过 Trigger 的 `run()` 方法的 yield 事件通知 Scheduler，Scheduler 再重新调度 Task。"

> **技术映射**：Hook = 万能充电器插头（负责连接），Operator = 带插头的电器（执行具体功能），Sensor = 烟雾报警器（持续检测条件），Trigger = 门卫（替你守门，有事通知你），Provider = 一整套厨房电器套装（统一接口，即插即用）。

---

## 3 项目实战

### 3.1 环境准备

```bash
# 创建项目目录结构
mkdir -p apache-airflow-providers-wechat/src/airflow/providers/wechat/{hooks,operators,sensors,triggers}
mkdir -p apache-airflow-providers-wechat/tests/providers/wechat/{hooks,operators,sensors}
mkdir -p apache-airflow-providers-wechat/docs

# 创建 __init__.py 文件
New-Item -ItemType File -Path "apache-airflow-providers-wechat/src/airflow/__init__.py"
New-Item -ItemType File -Path "apache-airflow-providers-wechat/src/airflow/providers/__init__.py"
```

### 3.2 阶段一：构建 WeChatHook

**步骤目标**：继承 `BaseHook`，实现企业微信的 Token 管理和消息发送。

```python
"""
WeChatHook — 企业微信连接管理
位置: src/airflow/providers/wechat/hooks/wechat.py

职责:
1. 管理企业微信 API 的 access_token（自动刷新）
2. 封装 HTTP 请求（POST/GET）
3. 提供 send_message() 等高层方法
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests
from requests.exceptions import RequestException

from airflow.exceptions import AirflowException
from airflow.hooks.base import BaseHook


class WeChatHook(BaseHook):
    """
    企业微信 Hook。
    
    连接参数（通过 Airflow Connection 管理）:
    - host: 企业微信 API 基础 URL (默认: https://qyapi.weixin.qq.com)
    - login: CorpID (企业 ID)
    - password: CorpSecret (应用密钥)
    - extra (JSON): 
        - agent_id: 应用 AgentID
        - webhook_key: 群机器人 Webhook Key
    """
    
    conn_name_attr = "wechat_conn_id"
    default_conn_name = "wechat_default"
    conn_type = "wechat"
    hook_name = "WeChat"
    
    def __init__(
        self,
        wechat_conn_id: str = default_conn_name,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.wechat_conn_id = wechat_conn_id
        self._conn: Any = None
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
    
    def get_conn(self) -> Any:
        """
        获取 HTTP 连接。
        每次调用都可能刷新 access_token。
        """
        if self._conn is None:
            connection = self.get_connection(self.wechat_conn_id)
            
            # 解析 extra 字段中的 JSON 配置
            extra = connection.extra_dejson
            
            self._corp_id = connection.login
            self._corp_secret = connection.password
            self._agent_id = extra.get("agent_id", "")
            self._webhook_key = extra.get("webhook_key", "")
            self._api_base = connection.host or "https://qyapi.weixin.qq.com"
            
            self._conn = requests.Session()
            self._conn.headers.update({
                "Content-Type": "application/json",
                "User-Agent": "Airflow-WeChat-Provider/0.1.0",
            })
        
        # 自动刷新 Token
        self._ensure_access_token()
        
        return self._conn
    
    def _ensure_access_token(self) -> None:
        """如果 Token 过期或不存在，自动获取新的 access_token。"""
        now = time.time()
        
        if self._access_token and now < self._token_expires_at - 300:
            # Token 还有 5 分钟以上有效期，不需要刷新
            return
        
        url = f"{self._api_base}/cgi-bin/gettoken"
        params = {
            "corpid": self._corp_id,
            "corpsecret": self._corp_secret,
        }
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("errcode") != 0:
                raise AirflowException(
                    f"获取企业微信 access_token 失败: {data.get('errmsg')}"
                )
            
            self._access_token = data["access_token"]
            self._token_expires_at = now + data.get("expires_in", 7200)
            
            self.log.info(
                "企业微信 access_token 已刷新，有效期至: %s",
                time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(self._token_expires_at))
            )
        except RequestException as e:
            raise AirflowException(f"获取企业微信 access_token 网络错误: {e}")
    
    def send_message(
        self,
        content: str,
        msg_type: str = "text",
        to_user: str = "@all",
        to_party: str = "",
        to_tag: str = "",
        safe: int = 0,
    ) -> dict:
        """
        通过企业微信应用发送消息。
        
        :param content: 消息内容
        :param msg_type: 消息类型 (text/image/file/markdown/news)
        :param to_user: 接收者用户 ID，@all 表示全体
        :param to_party: 接收者部门 ID
        :param to_tag: 接收者标签 ID
        :param safe: 保密消息 (0=否, 1=是)
        :return: API 响应字典
        """
        conn = self.get_conn()
        
        url = f"{self._api_base}/cgi-bin/message/send?access_token={self._access_token}"
        
        payload = {
            "touser": to_user,
            "toparty": to_party,
            "totag": to_tag,
            "msgtype": msg_type,
            "agentid": self._agent_id,
            "safe": safe,
        }
        
        if msg_type == "text":
            payload["text"] = {"content": content}
        elif msg_type == "markdown":
            payload["markdown"] = {"content": content}
        
        try:
            resp = conn.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("errcode") != 0:
                raise AirflowException(
                    f"发送企业微信消息失败: {data.get('errmsg')} (errcode={data.get('errcode')})"
                )
            
            self.log.info("消息发送成功: msgtype=%s, touser=%s", msg_type, to_user)
            return data
        except RequestException as e:
            raise AirflowException(f"发送企业微信消息网络错误: {e}")
    
    def send_webhook_message(
        self,
        content: str,
        msg_type: str = "text",
        mentioned_list: list[str] | None = None,
    ) -> dict:
        """
        通过群机器人 Webhook 发送消息。
        不需要 access_token，只需 webhook_key。
        
        :param content: 消息内容
        :param msg_type: 消息类型 (text/markdown)
        :param mentioned_list: @的成员列表
        :return: API 响应字典
        """
        url = f"{self._api_base}/cgi-bin/webhook/send?key={self._webhook_key}"
        
        payload: dict[str, Any] = {"msgtype": msg_type}
        
        if msg_type == "text":
            payload["text"] = {
                "content": content,
                "mentioned_list": mentioned_list or [],
            }
        elif msg_type == "markdown":
            payload["markdown"] = {"content": content}
        
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("errcode") != 0:
                raise AirflowException(
                    f"Webhook 消息发送失败: {data.get('errmsg')}"
                )
            
            self.log.info("Webhook 消息发送成功")
            return data
        except RequestException as e:
            raise AirflowException(f"Webhook 消息发送网络错误: {e}")
    
    def test_connection(self) -> tuple[bool, str]:
        """
        测试连接是否可用。
        Airflow UI 的 "Test Connection" 按钮调用此方法。
        """
        try:
            self.get_conn()
            # 尝试发送一条测试消息给自己
            if self._access_token:
                return True, "企业微信连接成功"
            return False, "未能获取 access_token"
        except Exception as e:
            return False, f"连接失败: {e}"
```

### 3.3 阶段二：实现 WeChatOperator

**步骤目标**：继承 `BaseOperator`，实现发送消息的 Operator。

```python
"""
WeChatOperator — 发送企业微信消息的 Operator
位置: src/airflow/providers/wechat/operators/wechat.py
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from airflow.providers.wechat.hooks.wechat import WeChatHook
from airflow.sdk.definitions.context import Context
from airflow.sdk.operators.baseoperator import BaseOperator

if TYPE_CHECKING:
    from airflow.utils.context import Context


class WeChatOperator(BaseOperator):
    """
    通过企业微信应用或群机器人发送消息。
    
    :param wechat_conn_id: WeChat Hook 连接 ID
    :param content: 消息内容（支持 Jinja 模板）
    :param msg_type: 消息类型 (text/markdown)
    :param to_user: 接收者用户 ID
    :param use_webhook: 是否使用群机器人 Webhook 方式发送
    :param mentioned_list: Webhook 模式下 @ 的成员列表
    """
    
    template_fields: Sequence[str] = ("content",)
    template_ext: Sequence[str] = (".md", ".txt")
    ui_color = "#07C160"  # 微信绿色
    
    def __init__(
        self,
        *,
        content: str,
        wechat_conn_id: str = WeChatHook.default_conn_name,
        msg_type: str = "text",
        to_user: str = "@all",
        use_webhook: bool = False,
        mentioned_list: list[str] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.wechat_conn_id = wechat_conn_id
        self.content = content
        self.msg_type = msg_type
        self.to_user = to_user
        self.use_webhook = use_webhook
        self.mentioned_list = mentioned_list
    
    def execute(self, context: Context) -> Any:
        """执行消息发送。"""
        hook = WeChatHook(wechat_conn_id=self.wechat_conn_id)
        
        if self.use_webhook:
            result = hook.send_webhook_message(
                content=self.content,
                msg_type=self.msg_type,
                mentioned_list=self.mentioned_list,
            )
        else:
            result = hook.send_message(
                content=self.content,
                msg_type=self.msg_type,
                to_user=self.to_user,
            )
        
        self.log.info(
            "WeChatOperator 执行完成: dag=%s, task=%s, msg_type=%s",
            context.get("dag").dag_id,
            self.task_id,
            self.msg_type,
        )
        
        return result
```

### 3.4 阶段三：实现 WeChatSensor

**步骤目标**：继承 `BaseSensorOperator`，实现等待企微回复的 Sensor。

```python
"""
WeChatSensor — 等待企业微信消息回复的 Sensor
位置: src/airflow/providers/wechat/sensors/wechat.py
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from airflow.sdk.definitions.operators.basesensor import BaseSensorOperator
from airflow.providers.wechat.hooks.wechat import WeChatHook

if TYPE_CHECKING:
    from airflow.utils.context import Context


class WeChatSensor(BaseSensorOperator):
    """
    轮询检测企业微信是否收到特定消息回复。
    
    场景: 发送审批请求后，等待特定用户回复"同意"或"拒绝"。
    
    :param wechat_conn_id: WeChat Hook 连接 ID
    :param expected_reply: 期望的回复关键词列表
    :param from_user: 检查来自哪个用户的消息
    :param timeout: 超时时间（秒）
    :param poke_interval: 轮询间隔（秒），注意企业微信 API 频率限制
    """
    
    def __init__(
        self,
        *,
        wechat_conn_id: str = WeChatHook.default_conn_name,
        expected_reply: list[str] | None = None,
        from_user: str | None = None,
        timeout: float = 3600.0,
        poke_interval: float = 30.0,  # 企业微信 API 限制: 最少 30 秒间隔
        **kwargs,
    ):
        super().__init__(timeout=timeout, poke_interval=poke_interval, **kwargs)
        self.wechat_conn_id = wechat_conn_id
        self.expected_reply = expected_reply or ["同意", "拒绝"]
        self.from_user = from_user
        self._hook: WeChatHook | None = None
        self._last_msg_time: int = 0  # 记录最后处理的消息时间
    
    def _get_hook(self) -> WeChatHook:
        if self._hook is None:
            self._hook = WeChatHook(wechat_conn_id=self.wechat_conn_id)
        return self._hook
    
    def poke(self, context: Context) -> bool:
        """
        检查是否收到了期望的回复。
        
        :return: True 表示条件满足，Sensor 完成
        """
        hook = self._get_hook()
        
        # 调用企业微信"获取回调消息"的 API
        # 注意: 企业微信的回调消息机制需要配置回调 URL
        # 这里演示的是简化版: 通过企业微信"获取聊天记录"API 轮询
        
        messages = self._fetch_recent_messages(hook)
        
        if not messages:
            self.log.debug("没有新消息，继续等待...")
            return False
        
        for msg in messages:
            sender = msg.get("sender", "")
            content = msg.get("content", "")
            msg_time = msg.get("msg_time", 0)
            
            # 过滤发送者
            if self.from_user and sender != self.from_user:
                continue
            
            # 过滤已处理的消息
            if msg_time <= self._last_msg_time:
                continue
            
            self._last_msg_time = msg_time
            
            # 检查回复内容是否匹配期望关键词
            for keyword in self.expected_reply:
                if keyword in content:
                    self.log.info(
                        "收到符合条件回复: from=%s, content='%s', matched='%s'",
                        sender, content, keyword
                    )
                    # 将匹配结果写入 XCom 供下游使用
                    context["ti"].xcom_push(
                        key="wechat_reply",
                        value={"sender": sender, "content": content, "keyword": keyword}
                    )
                    return True
        
        self.log.debug("收到 %d 条消息，但未匹配期望关键词", len(messages))
        return False
    
    def _fetch_recent_messages(self, hook: WeChatHook) -> list[dict]:
        """
        获取最近的消息记录。
        
        实际环境中需要根据企业微信的具体 API 来实现。
        企业微信没有直接的"获取聊天记录"API，需要通过以下方式之一:
        1. 回调 URL 模式: 企业微信推送消息到你的服务器
        2. 会话存档 API: 需要企业开通会话内容存档功能
        3. 自建消息中间件: 群机器人将消息存储到 Redis/DB，Sensor 从中查询
        """
        # 这里是演示实现，实际需要替换为真实 API
        import requests
        
        conn = hook.get_conn()
        # 企业微信"获取会话内容"的 API (需要会话存档权限)
        # url = f"{hook._api_base}/cgi-bin/msgaudit/get_permit_user_list"
        # 由于会话存档 API 的复杂性，这里展示接入 Redis 的方案
        
        # 演示: 从 Redis 读取消息（假设群机器人已经把消息存到了 Redis）
        try:
            import redis
            r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
            raw = r.lrange("wechat:messages", 0, 9)
            messages = []
            for item in raw:
                import json
                messages.append(json.loads(item))
            return messages
        except Exception as e:
            self.log.debug("无法从 Redis 获取消息: %s", e)
            return []
```

### 3.5 阶段四：实现 Deferrable WeChatTrigger

**步骤目标**：实现异步的 Deferrable Operator，优化长时间等待的资源占用。

```python
"""
WeChatTrigger — 异步等待企业微信回复的 Trigger
位置: src/airflow/providers/wechat/triggers/wechat.py

Deferrable Operator 的优势:
- 传统 Sensor: 每个 poke_interval 占用一个 Worker Slot
- Deferrable: 释放 Worker Slot，由 Triggerer 轻量级轮询
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from airflow.triggers.base import BaseTrigger, TriggerEvent
from airflow.providers.wechat.hooks.wechat import WeChatHook


class WeChatReplyTrigger(BaseTrigger):
    """
    异步等待企业微信回复的 Trigger。
    
    运行在 Triggerer 进程中，不占用 Worker 资源。
    
    :param wechat_conn_id: WeChat Hook 连接 ID
    :param expected_reply: 期望的回复关键词
    :param from_user: 期望的发送者
    :param poll_interval: 轮询间隔（秒）
    :param timeout: 超时时间（秒）
    """
    
    def __init__(
        self,
        wechat_conn_id: str = WeChatHook.default_conn_name,
        expected_reply: list[str] | None = None,
        from_user: str | None = None,
        poll_interval: float = 30.0,
        timeout: float = 3600.0,
    ):
        super().__init__()
        self.wechat_conn_id = wechat_conn_id
        self.expected_reply = expected_reply or ["同意", "拒绝"]
        self.from_user = from_user
        self.poll_interval = poll_interval
        self.timeout = timeout
    
    def serialize(self) -> tuple[str, dict[str, Any]]:
        """
        序列化 Trigger 参数，用于在 Triggerer 重启后恢复。
        """
        return (
            "airflow.providers.wechat.triggers.wechat.WeChatReplyTrigger",
            {
                "wechat_conn_id": self.wechat_conn_id,
                "expected_reply": self.expected_reply,
                "from_user": self.from_user,
                "poll_interval": self.poll_interval,
                "timeout": self.timeout,
            },
        )
    
    async def run(self) -> AsyncIterator[TriggerEvent]:
        """
        Trigger 的主循环。
        
        在 Triggerer 进程中运行，通过 async/await 实现非阻塞轮询。
        使用 run_in_executor 将同步的 HTTP 调用放到线程池执行。
        """
        start_time = asyncio.get_event_loop().time()
        last_msg_time = 0
        
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            
            # 超时检查
            if elapsed > self.timeout:
                yield TriggerEvent({
                    "status": "timeout",
                    "message": f"等待回复超时 ({self.timeout}s)",
                })
                return
            
            # 在线程池中执行同步的 API 调用
            messages = await asyncio.get_event_loop().run_in_executor(
                None, self._check_messages, last_msg_time
            )
            
            # 检查是否有匹配的回复
            if messages:
                for msg in messages:
                    content = msg.get("content", "")
                    for keyword in self.expected_reply:
                        if keyword in content:
                            yield TriggerEvent({
                                "status": "received",
                                "sender": msg.get("sender"),
                                "content": content,
                                "keyword": keyword,
                            })
                            return
                
                # 更新最后处理的消息时间
                if messages:
                    last_msg_time = messages[-1].get("msg_time", last_msg_time)
            
            # 等待下一次轮询
            await asyncio.sleep(self.poll_interval)
    
    def _check_messages(self, last_msg_time: int) -> list[dict]:
        """
        同步的消息检查方法（在线程池中运行）。
        """
        # 实际实现: 查询 Redis/数据库中的消息
        try:
            import json
            import redis
            
            r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
            raw = r.lrange("wechat:messages", 0, 19)
            messages = []
            for item in raw:
                msg = json.loads(item)
                if msg.get("msg_time", 0) > last_msg_time:
                    if self.from_user is None or msg.get("sender") == self.from_user:
                        messages.append(msg)
            return messages
        except Exception:
            return []


# === Deferrable WeChatSensor ===
from airflow.sdk.definitions.operators.basesensor import BaseSensorOperator


class WeChatSensorAsync(BaseSensorOperator):
    """
    Deferrable 版本的 WeChatSensor。
    
    当 poke 返回 False 时，调用 self.defer() 释放 Worker Slot，
    将等待任务转交给 Triggerer 进程。
    """
    
    def __init__(
        self,
        *,
        wechat_conn_id: str = WeChatHook.default_conn_name,
        expected_reply: list[str] | None = None,
        from_user: str | None = None,
        poke_interval: float = 30.0,
        timeout: float = 3600.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.wechat_conn_id = wechat_conn_id
        self.expected_reply = expected_reply
        self.from_user = from_user
        self.poke_interval = poke_interval
        self.timeout = timeout
    
    def execute(self, context: Context) -> Any:
        """
        执行 Sensor，如果条件不满足则 defer。
        """
        # 首先做一次同步检查
        sensor = WeChatSensor(
            wechat_conn_id=self.wechat_conn_id,
            expected_reply=self.expected_reply,
            from_user=self.from_user,
            task_id=self.task_id,
            timeout=1,  # 只检查一次
            poke_interval=1,
        )
        
        result = sensor.poke(context)
        if result:
            self.log.info("首次检查即匹配，无需 defer")
            return context["ti"].xcom_pull(key="wechat_reply")
        
        # 条件不满足，释放 Worker Slot
        self.log.info("条件不满足，进入 defer 模式")
        self.defer(
            trigger=WeChatReplyTrigger(
                wechat_conn_id=self.wechat_conn_id,
                expected_reply=self.expected_reply,
                from_user=self.from_user,
                poll_interval=self.poke_interval,
                timeout=self.timeout,
            ),
            method_name="execute_complete",
        )
    
    def execute_complete(self, context: Context, event: dict) -> Any:
        """
        Trigger 完成后 Scheduler 重新调度执行。
        
        :param event: Trigger 发出的 TriggerEvent 数据
        """
        if event["status"] == "timeout":
            raise AirflowException(f"等待回复超时: {event['message']}")
        
        self.log.info(
            "收到企业微信回复: sender=%s, content='%s', keyword='%s'",
            event.get("sender"),
            event.get("content"),
            event.get("keyword"),
        )
        
        # 将结果写入 XCom
        context["ti"].xcom_push(key="wechat_reply", value=event)
        return event
```

### 3.6 阶段五：Provider 打包与注册

**步骤目标**：完成 `provider.yaml`、`pyproject.toml` 和入口函数。

```yaml
# provider.yaml
# 位置: apache-airflow-providers-wechat/provider.yaml
---
package-name: apache-airflow-providers-wechat
name: WeChat
description: |
  `WeChat Work (企业微信) Provider <https://work.weixin.qq.com/>`__
state: ready
lifecycle: alpha
source-date-epoch: 1746624000
versions:
  - 0.1.0

integrations:
  - integration-name: WeChat
    external-doc-url: https://developer.work.weixin.qq.com/document
    logo: /docs/integration-logos/wechat.png
    tags: [communication]

hooks:
  - integration-name: WeChat
    python-modules:
      - airflow.providers.wechat.hooks.wechat

operators:
  - integration-name: WeChat
    python-modules:
      - airflow.providers.wechat.operators.wechat

sensors:
  - integration-name: WeChat
    python-modules:
      - airflow.providers.wechat.sensors.wechat

triggers:
  - integration-name: WeChat
    python-modules:
      - airflow.providers.wechat.triggers.wechat

connection-types:
  - hook-class-name: airflow.providers.wechat.hooks.wechat.WeChatHook
    connection-type: wechat
```

```python
# get_provider_info.py — 入口函数
# 位置: src/airflow/providers/wechat/get_provider_info.py

def get_provider_info():
    """返回 provider.yaml 的内容供 Airflow 注册。"""
    return {
        "package-name": "apache-airflow-providers-wechat",
        "name": "WeChat",
        "description": "WeChat Work (企业微信) Provider",
        "state": "ready",
        "lifecycle": "alpha",
        "versions": ["0.1.0"],
        "hooks": [
            {
                "integration-name": "WeChat",
                "python-modules": ["airflow.providers.wechat.hooks.wechat"],
            }
        ],
        "operators": [
            {
                "integration-name": "WeChat",
                "python-modules": ["airflow.providers.wechat.operators.wechat"],
            }
        ],
        "sensors": [
            {
                "integration-name": "WeChat",
                "python-modules": ["airflow.providers.wechat.sensors.wechat"],
            }
        ],
        "triggers": [
            {
                "integration-name": "WeChat",
                "python-modules": ["airflow.providers.wechat.triggers.wechat"],
            }
        ],
        "connection-types": [
            {
                "hook-class-name": "airflow.providers.wechat.hooks.wechat.WeChatHook",
                "connection-type": "wechat",
            }
        ],
    }
```

```toml
# pyproject.toml
# 位置: apache-airflow-providers-wechat/pyproject.toml
[build-system]
requires = ["flit_core==3.12.0"]
build-backend = "flit_core.buildapi"

[project]
name = "apache-airflow-providers-wechat"
version = "0.1.0"
description = "Provider package apache-airflow-providers-wechat for Apache Airflow"
readme = "README.rst"
license = "Apache-2.0"
requires-python = ">=3.10"
dependencies = [
    "apache-airflow>=2.11.0",
    "requests>=2.28.0",
    "redis>=4.5.0",
]

[project.entry-points."apache_airflow_provider"]
provider_info = "airflow.providers.wechat.get_provider_info:get_provider_info"

[project.urls]
"Documentation" = "https://github.com/your-org/airflow-providers-wechat"
"Source Code" = "https://github.com/your-org/airflow-providers-wechat"

[tool.flit.module]
name = "airflow.providers.wechat"
```

**Dag 示例——使用自定义 Provider**：

```python
"""
使用 apache-airflow-providers-wechat 的实际 Dag
"""
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.sdk.operators.empty import EmptyOperator
from airflow.providers.wechat.operators.wechat import WeChatOperator
from airflow.providers.wechat.sensors.wechat import WeChatSensor

with DAG(
    dag_id="wechat_notification_pipeline",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["wechat", "notification"],
) as dag:
    
    start = EmptyOperator(task_id="start")
    
    # 发送每日数据汇总通知
    send_report = WeChatOperator(
        task_id="send_daily_report",
        wechat_conn_id="wechat_prod",
        content="""
        ## 每日数据汇总
        **日期**: {{ ds }}
        **数据量**: {{ ti.xcom_pull(task_ids='count_data') }}
        **状态**: 正常
        """,
        msg_type="markdown",
        to_user="@all",
    )
    
    # 等待审批回复 (Deferrable 版本)
    wait_approval = WeChatSensor(
        task_id="wait_approval",
        wechat_conn_id="wechat_prod",
        expected_reply=["同意", "拒绝"],
        from_user="zhangsan",
        timeout=1800,  # 30 分钟超时
        poke_interval=30,
    )
    
    start >> send_report >> wait_approval
```

### 3.7 完整代码清单

- 参考 Provider: `providers/common/sql/` (完整标准结构)
- 参考 Provider: `providers/amazon/` (大型 Provider 示例)
- 参考 Provider: `providers/google/` (带 Trigger 的 Provider)
- 本项目完整代码: `apache-airflow-providers-wechat/`

---

## 4 项目总结

### 优点 & 缺点对比

| 维度 | 自定义 Provider | 直接 PythonOperator |
|------|----------------|-------------------|
| 代码复用 | 一次封装，所有 Dag 共享 | 每个 Dag 复制粘贴 |
| 可测试性 | Hook/Operator/Sensor 可独立单元测试 | 嵌入 Dag 中，难以隔离测试 |
| 可维护性 | 集中管理连接和逻辑，修改一处生效 | 散落各处，修改需全局搜索 |
| 可视化 | Airflow UI 自动展示连接类型和颜色 | 无差异化展示 |
| 开发成本 | 初始封装需要时间投入 | 即时可用 |
| 安全合规 | 连接凭据通过 Airflow Connection 管理 | 凭据可能硬编码在代码中 |

### 适用场景

1. **企业自有系统集成**：企业微信、钉钉、飞书等内部工具。
2. **多团队共享能力**：数据团队、运维团队都需要使用同一外部系统。
3. **复杂连接管理**：需要 OAuth、Token 自动刷新等高级认证。
4. **需异步等待**：长时间轮询的场景，适合用 Deferrable Operator。
5. **开源贡献**：开发通用 Provider 贡献回 Apache Airflow 社区。

### 不适用场景

1. **一次性脚本**：只在一个 Dag 中调用一次的简单 HTTP 请求。
2. **原型验证阶段**：需求不明确时，先用 PythonOperator 快速验证。

### 注意事项

- **provider.yaml 版本管理**：每次发布需要手动更新 `versions` 列表。
- **Connection 安全**：Hook 中不要通过 `**conn.extra_dejson` 传参给底层库，使用白名单模式逐个提取需要的字段（详见 `providers/AGENTS.md` 安全规则）。
- **Jinja 模板字段**：Operator 中需要支持模板的字段必须声明在 `template_fields` 中。
- **Trigger 序列化**：Deferrable Operator 的 Trigger 必须实现 `serialize()` 方法，确保 Triggerer 重启后能恢复。

### 常见踩坑经验

1. **access_token 全局共享导致冲突**：多个 Dag 同时执行时，Hook 的实例级 Token 缓存可能过期。解决：在 `_ensure_access_token()` 中加锁，或使用 TTL 缓存。
2. **Sensor poke_interval 过短触发 API 限流**：企业微信 API 有 QPS 限制。解决：严格遵守官方文档的速率限制，`poke_interval` 建议 30-60 秒。
3. **Deferrable Operator 的 Triggerer 进程内存泄漏**：Trigger 长时间运行可能导致内存增长。解决：定期重启 Triggerer，在 Trigger 中及时释放不需要的对象引用。

### 思考题

1. **进阶题**：如果要为 WeChatProvider 添加"消息模板"功能——允许用户在 Airflow UI 中预定义消息模板（如"{{ dag_id }} 执行完成，耗时 {{ duration }}秒"），在 Operator 中引用模板名称而非直接写内容——需要修改哪些模块？请画出从 UI 配置 → Hook 渲染 → API 发送的完整数据流。

2. **设计题**：假设你需要开发一个"多云消息 Provider"——支持企业微信、钉钉、飞书、Slack 四套消息系统，但提供统一的 `send_message()` 接口。请设计多层次抽象架构：通用 `BaseMessageHook` → 各平台 Hook → 通用 `MessageOperator` 通过 `message_conn_id` 动态选择后端。考虑如何利用 Airflow 的 Connection type 机制实现多态分发。

> **推广计划提示**：开发团队重点阅读 `BaseHook`、`BaseOperator` 和 `BaseSensorOperator` 的源码。运维团队掌握 `provider.yaml` 的 Connection types 注册。测试团队为每个 Hook/Operator/Sensor 编写独立单元测试，使用 `mock` 模拟企业微信 API 响应。
