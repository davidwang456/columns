# 第7章：Python SDK 项目化封装

> **定位**：把零散 API 调用封装成可维护的业务模块。
> **版本**：Milvus 2.5.x / PyMilvus 2.5.5
> **源码关联**：pymilvus/orm/collection.py、pymilvus/client/grpc_handler.py

---

## 1. 项目背景

某电商平台搜索团队已经有 3 个工程师在各自的模块里直接调 PyMilvus API。开发 A 在商品搜索服务里写了一份连接管理逻辑，开发 B 在推荐服务里复制粘贴了另一份，开发 C 在后台管理服务里又写了一份。三个人的代码风格各异——A 用函数式编程，B 用类但没做连接池，C 压根没处理超时和异常。

很快问题集中爆发：

1. **连接泄漏**——B 的服务每处理一次搜索就 new 一个 Milvus Connection，但从不 close，运行两小时后 gRPC 连接数接近 10000，Proxy 内存告警。
2. **配置散落各处**——Milvus 的连接地址、超时时间、重试次数散落在三个服务的 7 个配置文件中，运维改一次要改 7 处，改漏了生产就报错。
3. **异常处理不一致**——A 的服务在 Milvus 不可用时返回空列表，B 抛 500 异常，C 重试 3 次后兜底走 MySQL 全文搜索。相同的问题场景，三个服务的行为完全不同。
4. **单元测试写不了**——三个服务都直接依赖 `pymilvus.connections.connect()`，测试时必须连真实的 Milvus Standalone，本地跑不了、CI 跑得更慢。
5. **重复造轮子**——Collection 初始化检查、索引状态检查、Load 状态检查这些通用逻辑每人都写了一遍，代码重复率超过 60%。

技术 Lead 李明决定做一次彻底的重构：将 PyMilvus 的零散调用封装成统一的 Repository 层，对上游业务提供一致的 API，下游隔离 Milvus 版本变化的影响。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：为什么需要封装——DAO 层 vs 直接用 SDK**

*（代码评审会上，新人小王提交了一份 500 行的搜索服务代码，其中 300 行是 Milvus 操作代码）*

**小胖**（震惊地）："小王你疯了吧？一个搜索接口里塞了 300 行 Milvus 代码？其他同事不也得写一遍？"

**小王**（委屈地）："这……我照着文档写的啊。先 connect，再 get_collection，检查 load 状态，调 search，最后 format 结果……不都这么写的吗？"

**大师**："来，我问你一个问题——如果下周 Milvus 升级到 2.6，API 有 breaking change，你要改几个文件？"

**小王**（沉默片刻）："……三个服务各改一份，至少改 7 个文件。"

**大师**："这就是'无封装'的代价。在软件工程里有一个经典原则：**Don't Repeat Yourself（DRY）**。同样的逻辑出现 3 次，就应该抽取成独立模块。对于 Milvus 操作，这个模块我们叫它 Repository 层或者 DAO 层。"

**大师**（在白板上画分层图）：

```
┌─────────────────────────────────────────────────────────┐
│                     业务服务层                            │
│  ProductSearchService / RecommendService / AdminService │
└──────────────────────────┬──────────────────────────────┘
                           │ 只依赖接口
┌──────────────────────────▼──────────────────────────────┐
│              MilvusRepository（DAO 层）                   │
│  - connect / disconnect  （连接生命周期管理）             │
│  - insert / upsert        （写入封装）                    │
│  - search / query         （查询封装）                    │
│  - delete / drop          （删除封装）                    │
│  - create_index / load    （索引与加载封装）               │
│  - 异常处理、重试、日志、超时                              │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                   PyMilvus SDK                           │
│  pymilvus.connections / pymilvus.Collection / ...        │
└─────────────────────────────────────────────────────────┘
```

**小白**："那这个 Repository 层具体要封装哪些东西？总不能只是包一层皮吧？"

**大师**："问得好。一个好的 Repository 层至少要封装五个维度——"

| 封装维度 | 要解决的问题 | 反例 |
|---------|-------------|------|
| **连接管理** | 连接池、健康检查、超时管理 | 每次请求 new 一个 connection |
| **懒初始化** | Collection 不存在时自动创建、索引不存在时自动构建 | 手动先建 Collection 再跑服务 |
| **异常标准化** | 将 SDK 原生异常转成业务异常（ConnectionError / CollectionNotFound / SearchTimeout） | 直接 `raise e` |
| **配置集中化** | 连接地址、超时、重试策略统一从配置文件读取 | 硬编码在代码中 |
| **日志与可观测性** | 每次操作打印结构化日志（操作类型、耗时、数据量、是否成功） | 无日志或只用 `print` |

**小胖**："听着工作量不小啊——封装这些得花多久？"

**大师**："第一次封装可能花半天到一天。但每节省一次复制粘贴、每避免一次配置遗漏、每少写一次异常处理——都是 ROI。记住：**封装成本是写一次，收益是贯穿整个项目生命周期**。"

> **技术映射**：Repository 层 = 统一收银台（不管哪个窗口的顾客都走同一个结账逻辑）；连接管理 = 收银台的排队机制；异常标准化 = 统一的异常处理流程（不会有员工直接跟顾客吵架）；配置集中化 = 统一的价目表（不会每个窗口贴不同的价格）。

---

**第二幕：连接管理——单例 vs 连接池 vs 每次新建**

**小白**："大师，连接管理应该用什么模式？我看到网上有人用单例、有人用连接池、还有人每次新建——"

**大师**："这三种模式各有适用场景，但要搞清楚 PyMilvus 的连接本质——"

**大师**（对比图）：

```
每次新建 (Per-Request)               单例 (Singleton)                连接池 (Connection Pool)
───────────────────────             ──────────────────              ──────────────────────
for req in requests:                 _connection = None              pool = [conn1, conn2, conn3]
    conn = connect()    ──┐          def get_conn():                 for req in requests:
    search(conn)          │              if _conn is None:               conn = pool.get()
    conn.disconnect()   ──┘              ──→ connect()                  search(conn)
    每次 TCP 握手 + gRPC 建立            return _conn                    pool.release(conn)
    ✗ 开销大（>10ms/次）              第一次使用时建立                  预建 N 个连接
    ✗ 不适合高并发                     ✓ 简单、够用                    ✓ 高并发友好
                                       ✗ 非线程安全（需加锁）            ✗ PyMilvus 未原生支持
```

**大师**："对于 PyMilvus，推荐用**单例 + 健康检查**模式。因为 PyMilvus 的 Connection 对象内部已经使用了 gRPC 长连接（HTTP/2 多路复用），一个 Connection 上可以并发发送多个 Search/Insert 请求，不需要连接池。"

**小白**："那线程安全呢？多个线程同时用一个 Connection 不会出问题吗？"

**大师**："PyMilvus 的 gRPC stub 是线程安全的——这也是 gRPC 的设计保证。但有一个小细节需要注意：`connections.connect()` 本身使用了全局 alias 字典，在多线程同时 connect 同一个 alias 时确实有竞争。解决方案是加锁或者在启动时一次性完成连接。"

**小胖**："那健康检查呢？万一连接断了呢？"

**大师**："PyMilvus 的 gRPC channel 有内置的 Keep-Alive 机制，但不够可靠。建议在封装层加一层 `ping()` 方法，定期（比如每 30 秒）调用 `utility.get_server_version()` 检查连接状态。如果失败，自动重连。"

> **技术映射**：单例 + 健康检查 = 一卡通用（一张员工卡可以刷多个门禁，偶尔需要去前台更新卡片）；连接池 = 多窗口银行柜台（PyMilvus 不需要，gRPC 长连接本身已支持并发）。

---

**第三幕：可测试性设计——依赖注入与 Mock**

**小白**："大师，你刚才说封装还有利于单元测试。具体怎么实现的？"

**大师**："这就是'好的架构设计会让测试自然变简单'的典型案例——"

```python
# ❌ 不可测试的代码（直接依赖 PyMilvus）
class ProductSearchService_BAD:
    def search(self, query_text):
        connections.connect(host="localhost", port="19530")  # 硬编码连接！
        collection = Collection("product_search")             # 硬编码 Collection 名！
        return collection.search(...)

# ✓ 可测试的代码（依赖注入）
class ProductSearchService_GOOD:
    def __init__(self, milvus_repo: MilvusRepository):       # 注入依赖
        self.repo = milvus_repo
    
    def search(self, query_text):
        # 只关心业务逻辑，不管连接和 Collection 管理
        return self.repo.search(...)
```

**大师**："当你把 Milvus 操作全部收敛到 `MilvusRepository` 后，测试就变成了——"

```python
# 单元测试：Mock Repository，不需要真实的 Milvus
from unittest.mock import Mock

def test_product_search():
    mock_repo = Mock()                                       # Mock 掉整个 Repository
    mock_repo.search.return_value = [                        # 预设返回值
        {"id": 1, "title": "露营椅", "score": 0.95},
    ]
    service = ProductSearchService_GOOD(mock_repo)
    results = service.search("露营折叠椅")
    
    assert len(results) == 1
    assert results[0]["title"] == "露营椅"
    mock_repo.search.assert_called_once()                    # 验证调用了一次
```

**小胖**："哇，这样测试就不用连真的 Milvus 了？CI 上也能跑？"

**大师**："没错。把 Repository 抽象成接口后，你可以造三种实现——"

| 实现 | 用途 | 何时用 |
|------|------|-------|
| `MilvusRepository`（真实） | 生产环境，连接真实 Milvus | 集成测试、生产部署 |
| `MockMilvusRepository`（Mock） | 单元测试，返回预设数据 | 本地开发、CI 快速验证 |
| `InMemoryRepository`（内存） | 用 numpy + 暴力搜索模拟向量检索 | 离线 Demo、无 Docker 环境 |

> **技术映射**：依赖注入 = 把'用哪个 Milvus 实例'的决定权从代码内部交给外部配置；Mock = 考试用模拟卷（不需要真实考场，但能验证答题逻辑是否正确）；三种 Repository 实现 = 三套不同环境用的考试卷（真实/模拟/自带答案）。

---

## 3. 项目实战

### 3.1 实战目标

封装一个可复用的 `MilvusRepository`，支持商品写入、搜索、删除和测试验证，并提供配置管理、连接管理、异常处理和日志记录。

### 3.2 环境准备

```bash
pip install pymilvus==2.5.5 sentence-transformers
```

项目结构：
```
project/
├── config.yaml          # 配置文件
├── milvus_repo/
│   ├── __init__.py
│   ├── repository.py    # MilvusRepository 核心类
│   ├── config.py        # 配置加载
│   └── exceptions.py    # 自定义异常
└── test_repository.py   # 单元测试
```

### 3.3 分步实现

#### 步骤 1：配置文件与异常定义

```python
# milvus_repo/config.py
"""配置管理：从 YAML/环境变量/字典加载配置"""
import os
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class MilvusConfig:
    """Milvus 连接配置"""
    host: str = "localhost"
    port: int = 19530
    alias: str = "default"
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0
    
    @classmethod
    def from_env(cls) -> "MilvusConfig":
        """从环境变量加载"""
        return cls(
            host=os.getenv("MILVUS_HOST", "localhost"),
            port=int(os.getenv("MILVUS_PORT", "19530")),
            timeout=float(os.getenv("MILVUS_TIMEOUT", "30")),
            max_retries=int(os.getenv("MILVUS_MAX_RETRIES", "3")),
        )


# milvus_repo/exceptions.py
"""自定义异常层次结构"""

class MilvusRepoError(Exception):
    """Repository 层基础异常"""
    pass

class ConnectionError(MilvusRepoError):
    """连接失败"""
    pass

class CollectionNotFoundError(MilvusRepoError):
    """Collection 不存在"""
    pass

class IndexBuildError(MilvusRepoError):
    """索引构建失败"""
    pass

class SearchTimeoutError(MilvusRepoError):
    """搜索超时"""
    pass

class InsertError(MilvusRepoError):
    """写入失败"""
    pass

class LoadError(MilvusRepoError):
    """Load 失败"""
    pass
```

#### 步骤 2：MilvusRepository 核心实现

```python
# milvus_repo/repository.py
"""Milvus 操作 Repository 层 —— 统一封装连接管理、CRUD、异常处理"""
import time
import logging
from typing import List, Dict, Optional, Any
from contextlib import contextmanager

from pymilvus import (
    connections, Collection, utility,
    CollectionSchema, FieldSchema, DataType,
)

from .config import MilvusConfig
from .exceptions import (
    ConnectionError, CollectionNotFoundError,
    IndexBuildError, SearchTimeoutError,
    InsertError, LoadError,
)

logger = logging.getLogger(__name__)


class MilvusRepository:
    """Milvus 操作统一封装类
    
    职责：连接管理、Collection 生命周期、CRUD 操作、异常标准化
    """
    
    def __init__(self, config: MilvusConfig):
        self.config = config
        self._connected = False
    
    # ========== 连接管理 ==========
    
    def connect(self) -> None:
        """建立连接（单例模式 + 健康检查）"""
        if self._connected:
            try:
                utility.get_server_version(using=self.config.alias)
                return  # 连接仍有效
            except Exception:
                logger.warning("已有连接失效，尝试重连")
                self._connected = False
        
        for attempt in range(self.config.max_retries):
            try:
                connections.connect(
                    alias=self.config.alias,
                    host=self.config.host,
                    port=self.config.port,
                    timeout=self.config.timeout,
                )
                self._connected = True
                logger.info(f"✓ 已连接 Milvus {self.config.host}:{self.config.port}")
                return
            except Exception as e:
                logger.warning(f"连接失败 ({attempt+1}/{self.config.max_retries}): {e}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay)
        
        raise ConnectionError(
            f"无法连接 Milvus {self.config.host}:{self.config.port} "
            f"(重试 {self.config.max_retries} 次后仍失败)"
        )
    
    def disconnect(self) -> None:
        """断开连接"""
        try:
            connections.disconnect(self.config.alias)
        except Exception:
            pass
        self._connected = False
    
    def ping(self) -> bool:
        """健康检查"""
        try:
            utility.get_server_version(using=self.config.alias)
            return True
        except Exception:
            self._connected = False
            return False
    
    def ensure_connected(self) -> None:
        """确保连接可用，否则重连"""
        if not self._connected or not self.ping():
            self.connect()
    
    # ========== Collection 生命周期 ==========
    
    def collection_exists(self, name: str) -> bool:
        """检查 Collection 是否存在"""
        self.ensure_connected()
        return utility.has_collection(name, using=self.config.alias)
    
    def create_collection(self, name: str, fields: List[FieldSchema],
                          description: str = "", **kwargs) -> Collection:
        """创建 Collection"""
        self.ensure_connected()
        if self.collection_exists(name):
            logger.info(f"Collection '{name}' 已存在，跳过创建")
            return Collection(name, using=self.config.alias)
        
        schema = CollectionSchema(fields, description=description, **kwargs)
        collection = Collection(name, schema=schema, using=self.config.alias)
        logger.info(f"✓ Collection '{name}' 创建完成")
        return collection
    
    def get_collection(self, name: str) -> Collection:
        """获取 Collection（不校验存在性，调用者自己处理）"""
        self.ensure_connected()
        if not self.collection_exists(name):
            raise CollectionNotFoundError(f"Collection '{name}' 不存在")
        return Collection(name, using=self.config.alias)
    
    def drop_collection(self, name: str) -> None:
        """删除 Collection"""
        self.ensure_connected()
        if self.collection_exists(name):
            utility.drop_collection(name, using=self.config.alias)
            logger.info(f"✓ Collection '{name}' 已删除")
    
    # ========== 索引管理 ==========
    
    def create_index(self, collection_name: str, field_name: str,
                     index_params: Dict, timeout: int = 300) -> None:
        """创建索引并等待完成"""
        self.ensure_connected()
        collection = self.get_collection(collection_name)
        
        t0 = time.time()
        collection.create_index(field_name, index_params)
        logger.info(f"索引创建触发: {collection_name}.{field_name} "
                    f"(type={index_params.get('index_type')})")
        
        utility.wait_for_index_building_complete(
            collection_name, timeout=timeout, using=self.config.alias
        )
        logger.info(f"✓ 索引构建完成 ({collection_name}.{field_name}), "
                     f"耗时 {time.time()-t0:.1f}s")
    
    def load_collection(self, name: str, timeout: int = 60) -> None:
        """加载 Collection 到内存"""
        self.ensure_connected()
        collection = self.get_collection(name)
        collection.load()
        
        t0 = time.time()
        utility.wait_for_loading_complete(
            name, timeout=timeout, using=self.config.alias
        )
        logger.info(f"✓ Collection '{name}' Load 完成 ({time.time()-t0:.1f}s)")
    
    def release_collection(self, name: str) -> None:
        """释放 Collection"""
        self.ensure_connected()
        collection = self.get_collection(name)
        collection.release()
        logger.info(f"✓ Collection '{name}' 已 Release")
    
    # ========== 数据写入 ==========
    
    def insert(self, collection_name: str, entities: List[List],
               batch_size: int = 500) -> Dict:
        """批量写入（自动分批 + 重试）"""
        self.ensure_connected()
        collection = self.get_collection(collection_name)
        total = len(entities[0])
        inserted = 0
        
        t0 = time.time()
        for i in range(0, total, batch_size):
            batch = [col[i:i+batch_size] for col in entities]
            for attempt in range(self.config.max_retries):
                try:
                    result = collection.insert(batch)
                    inserted += result.insert_count
                    break
                except Exception as e:
                    if attempt == self.config.max_retries - 1:
                        raise InsertError(
                            f"写入失败 ({collection_name}), "
                            f"batch {i//batch_size}: {e}"
                        )
                    time.sleep(self.config.retry_delay)
        
        elapsed = time.time() - t0
        logger.info(f"✓ 写入完成: {inserted}/{total} 条 → {collection_name} "
                     f"({total/elapsed:.0f} 条/s)")
        
        return {"inserted": inserted, "total": total, "elapsed": elapsed}
    
    def upsert(self, collection_name: str, entities: List[List],
               batch_size: int = 500) -> Dict:
        """批量 Upsert"""
        self.ensure_connected()
        collection = self.get_collection(collection_name)
        total = len(entities[0])
        upserted = 0
        
        t0 = time.time()
        for i in range(0, total, batch_size):
            batch = [col[i:i+batch_size] for col in entities]
            result = collection.upsert(batch)
            upserted += result.upsert_count
        
        logger.info(f"✓ Upsert: {upserted} 条 → {collection_name} "
                     f"({total/(time.time()-t0):.0f} 条/s)")
        return {"upserted": upserted, "total": total}
    
    # ========== 查询 ==========
    
    def search(self, collection_name: str, query_vectors: List[List[float]],
               anns_field: str, limit: int = 10,
               expr: Optional[str] = None,
               output_fields: Optional[List[str]] = None,
               search_params: Optional[Dict] = None,
               timeout: float = 30.0) -> List[List[Dict]]:
        """向量搜索（封装异常和超时）"""
        self.ensure_connected()
        collection = self.get_collection(collection_name)
        
        if search_params is None:
            search_params = {"metric_type": "COSINE", "params": {"ef": 64}}
        
        t0 = time.time()
        try:
            results = collection.search(
                data=query_vectors,
                anns_field=anns_field,
                param=search_params,
                expr=expr,
                limit=limit,
                output_fields=output_fields,
                timeout=timeout,
            )
        except Exception as e:
            if "timeout" in str(e).lower():
                raise SearchTimeoutError(
                    f"搜索超时 ({collection_name}, limit={limit}): {e}"
                )
            raise
        
        elapsed = (time.time() - t0) * 1000
        
        # 格式化为统一结构
        formatted = []
        for batch in results:
            batch_results = []
            for hit in batch:
                item = {"id": hit.id, "distance": round(hit.distance, 6)}
                if hit.entity:
                    for field_name in hit.entity.fields:
                        item[field_name] = hit.entity.get(field_name)
                batch_results.append(item)
            formatted.append(batch_results)
        
        logger.info(f"Search: {len(formatted[0])} results, {elapsed:.1f}ms")
        return formatted
    
    def query(self, collection_name: str, expr: str,
              output_fields: List[str], limit: int = 100) -> List[Dict]:
        """标量查询"""
        self.ensure_connected()
        collection = self.get_collection(collection_name)
        return collection.query(expr=expr, output_fields=output_fields, limit=limit)
    
    def get_by_id(self, collection_name: str, ids: List[int],
                  output_fields: List[str]) -> List[Dict]:
        """按主键批量查询"""
        self.ensure_connected()
        collection = self.get_collection(collection_name)
        expr = f"id in {ids}"
        return collection.query(expr=expr, output_fields=output_fields)
    
    # ========== 删除 ==========
    
    def delete_by_expr(self, collection_name: str, expr: str) -> int:
        """按表达式删除"""
        self.ensure_connected()
        collection = self.get_collection(collection_name)
        result = collection.delete(expr)
        logger.info(f"✓ 删除: expr='{expr}' → {collection_name}")
        return result.delete_count
    
    def delete_by_ids(self, collection_name: str, ids: List[int]) -> int:
        """按主键批量删除"""
        expr = f"id in {ids}"
        return self.delete_by_expr(collection_name, expr)
    
    def flush(self, collection_name: str) -> None:
        """Flush 数据"""
        self.ensure_connected()
        utility.flush([collection_name], using=self.config.alias)
    
    # ========== 上下文管理器 ==========
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
```

#### 步骤 3：业务服务封装 + 使用示例

```python
# product_service.py
"""基于 Repository 封装商品搜索业务服务"""
from typing import List, Dict, Optional
from sentence_transformers import SentenceTransformer
from milvus_repo.repository import MilvusRepository, MilvusConfig
from milvus_repo.exceptions import CollectionNotFoundError

class ProductSearchService:
    """商品语义搜索服务（依赖 Repository 而非直接调 PyMilvus）"""
    
    def __init__(self, repo: MilvusRepository, collection_name: str,
                 model_name: str = "all-MiniLM-L6-v2"):
        self.repo = repo
        self.collection = collection_name
        self.model = SentenceTransformer(model_name)
    
    def search(self, query_text: str, top_k: int = 10,
               category: Optional[str] = None,
               min_price: Optional[float] = None,
               max_price: Optional[float] = None,
               in_stock_only: bool = False) -> List[Dict]:
        """搜索相似商品"""
        # 构造 Expr
        conditions = []
        if category:
            conditions.append(f"category == '{category}'")
        if min_price is not None:
            conditions.append(f"price >= {min_price}")
        if max_price is not None:
            conditions.append(f"price <= {max_price}")
        if in_stock_only:
            conditions.append("in_stock == true")
        expr = " and ".join(conditions) if conditions else None
        
        # Embedding
        query_vec = self.model.encode([query_text]).tolist()
        
        # 通过 Repository 搜索（不感知连接管理、异常处理）
        try:
            results = self.repo.search(
                collection_name=self.collection,
                query_vectors=query_vec,
                anns_field="title_vec",
                limit=top_k,
                expr=expr,
                output_fields=["title", "price", "category", "in_stock"],
            )
            return results[0]
        except CollectionNotFoundError:
            return []


# 使用示例
if __name__ == "__main__":
    config = MilvusConfig(host="localhost", port=19530)
    
    with MilvusRepository(config) as repo:
        service = ProductSearchService(repo, "product_search")
        results = service.search(
            "户外露营帐篷", top_k=5,
            category="户外运动", min_price=100, in_stock_only=True
        )
        for item in results:
            print(f"  {item['title']:<40} ¥{item['price']:>7.1f}  [{item['category']}]")
```

#### 步骤 4：单元测试

```python
# test_repository.py
"""Repository 层单元测试（使用 Mock，不需要真实 Milvus）"""
import unittest
from unittest.mock import Mock, patch, MagicMock
from milvus_repo.repository import MilvusRepository, MilvusConfig
from milvus_repo.exceptions import ConnectionError

class TestMilvusRepository(unittest.TestCase):
    
    def setUp(self):
        self.config = MilvusConfig(host="localhost", port=19530)
        self.repo = MilvusRepository(self.config)
    
    @patch("milvus_repo.repository.connections")
    @patch("milvus_repo.repository.utility")
    def test_connect_success(self, mock_utility, mock_connections):
        """测试连接成功"""
        mock_utility.get_server_version.return_value = "v2.5.5"
        
        self.repo.connect()
        
        mock_connections.connect.assert_called_once_with(
            alias="default", host="localhost", port=19530, timeout=30.0
        )
        self.assertTrue(self.repo._connected)
    
    @patch("milvus_repo.repository.connections")
    def test_connect_failure(self, mock_connections):
        """测试连接失败（重试后仍失败）"""
        mock_connections.connect.side_effect = Exception("Connection refused")
        
        with self.assertRaises(ConnectionError):
            self.repo.connect()
        
        self.assertEqual(mock_connections.connect.call_count, 3)  # 重试了 3 次
    
    @patch.object(MilvusRepository, "ensure_connected")
    @patch("milvus_repo.repository.utility")
    def test_collection_exists(self, mock_utility, mock_ensure):
        """测试 Collection 存在性检查"""
        mock_utility.has_collection.return_value = True
        
        result = self.repo.collection_exists("test_collection")
        
        self.assertTrue(result)
        mock_utility.has_collection.assert_called_once_with(
            "test_collection", using="default"
        )
    
    @patch.object(MilvusRepository, "ensure_connected")
    @patch("milvus_repo.repository.Collection")
    @patch("milvus_repo.repository.utility")
    def test_search_with_output_fields(self, mock_utility, mock_col_cls, mock_ensure):
        """测试搜索封装（验证返回格式）"""
        mock_collection = MagicMock()
        mock_hit = MagicMock()
        mock_hit.id = 1
        mock_hit.distance = 0.95
        mock_hit.entity.fields = ["title", "price"]
        mock_hit.entity.get.side_effect = lambda k: {"title": "露营椅", "price": 199.0}[k]
        mock_collection.search.return_value = [[mock_hit]]
        mock_col_cls.return_value = mock_collection
        mock_utility.has_collection.return_value = True
        
        results = self.repo.search(
            "test_collection", [[0.1]*384], "title_vec", limit=5
        )
        
        self.assertEqual(len(results[0]), 1)
        self.assertEqual(results[0][0]["title"], "露营椅")
        self.assertEqual(results[0][0]["price"], 199.0)

if __name__ == "__main__":
    unittest.main()
```

---

## 4. 项目总结

### 4.1 优缺点对比

| 维度 | 直接调 PyMilvus | Repository 封装 | ORM 框架（如 SQLAlchemy） |
|------|----------------|----------------|--------------------------|
| 开发效率 | 初期高、后期低 | 初期中、后期高 | 初期高（若有现成） |
| 可测试性 | 差（必须连真实服务） | 好（可 Mock） | 好 |
| 版本升级影响 | 大（每个调用点都要改） | 小（只改 Repository） | 取决于框架 |
| 跨语言复用 | 不适用 | 不适用 | 不适用（每种语言独立） |
| 学习成本 | 低 | 中 | 高 |

### 4.2 适用场景

- **多服务共享同一套 Milvus 操作逻辑**（本章核心场景）
- **需要快速切换 Milvus 版本或部署环境**
- **团队中有初级开发者，需要降低误操作风险**
- **需要单元测试覆盖 Milvus 相关业务逻辑**

**不适用场景**：一次性脚本、快速 POC（直接用 PyMilvus 更快）。

### 4.3 注意事项

- **封装不能太厚**：如果 Repository 变成了"万能瑞士军刀"（包含了对结果的排序、聚合、格式转换等业务逻辑），就失去了分层意义。
- **配置不要硬编码**：连接信息、超时时间、重试次数都应从外部注入（环境变量 / YAML / K8s ConfigMap）。
- **日志要结构化**：建议使用 JSON 格式日志，方便接入 ELK/Splunk 等日志平台。
- **连接安全**：生产环境应开启 TLS，Repository 需适配证书配置。

### 4.4 常见踩坑经验

1. **每个线程独立 connect 导致连接数爆炸**：在 `connect()` 中加入 `_connected` 标志位和健康检查逻辑，避免重复 connect。
2. **Mock 测试时忘记 mock `utility.has_collection`**：`get_collection()` 内部调用了 `has_collection`，测试时经常漏 mock 导致测试挂死。
3. **上下文管理器异常吞没**：`__exit__` 返回 `False`（不吞异常），确保异常能正常向上传播，不要 `return True`。

### 4.5 思考题

1. 如果业务需要同时操作两个不同的 Milvus 集群（比如国产区和国际区），Repository 的单例模式需要怎样改造？
2. 如何在 Repository 中集成 OpenTelemetry 实现全链路 Trace（每个 search/insert 操作自动产生 Span）？

### 4.6 推广计划提示

- 将 `MilvusRepository` 抽取为团队内部公共库，发布到公司的 PyPI 私仓。
- 编写一个 `README.md` 包含快速入门示例、API 文档和常见问题。
- 在 CI 中增加 Repository 层的单元测试 + 集成测试（集成测试连 Standalone）。

---

> **下一章预告**：第8章我们将覆盖 Java 和 Go 两种企业后端常用语言的 Milvus SDK 接入实战。读完本章，你应该能将零散的 PyMilvus 调用重构为可维护、可测试的 Repository 层。
