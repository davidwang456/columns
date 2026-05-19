# 第2章：用 Docker 启动第一个 Milvus 单机环境

> **定位**：从零搭建可实验的本地环境。
> **版本**：Milvus 2.5.x
> **源码关联**：cmd/milvus/、deployments/docker/standalone/docker-compose.yml、configs/milvus.yaml

---

## 1. 项目背景

一家 AI 创业公司的后端开发小王刚接到任务，要在本地开发环境搭建 Milvus 用于原型验证。他按照公司 wiki 上的文档装了一天都没跑起来——先是 Port 冲突（本地 19530 被其他服务占用），然后是内存不足（笔记本电脑只有 8GB，Milvus 启动直接 OOM），接着遇到 etcd 启动失败、MinIO 报权限错误、Milvus 容器反复重启……

好不容易启动了，却不知道怎么验证服务健康——是 ping 端口？还是需要其他工具？同事说用 Attu 可以可视化查看，但他又在安装和连接 Attu 上卡住了。

更让人头疼的是，团队用了三种部署方式做实验：有人在 Windows 上装 Docker Desktop，有人在 macOS 上用 Colima，还有人直接在 Linux 物理机上跑——每个人的环境差异导致了不同的坑，排查建议互不通用。

本章的目标是：提供一份"只要能跑 Docker，就能一次性启动成功"的标准化部署方案，覆盖 Standalone、Cluster、Lite 三种模式的选择逻辑，并引入 Attu 可视化管理工具，最终形成一份团队可复用的本地开发环境搭建文档。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：Standalone vs Cluster vs Lite，到底用哪个？**

*（小胖抱着笔记本，屏幕上六个终端窗口全是红色报错信息）*

**小胖**（绝望地）："大师救命！我按官网文档搞了一天，Milvus 就是起不来。一会儿说端口冲突，一会儿说内存不够。而且官网有三种模式——Standalone、Cluster、Lite——我到底该用哪个？"

**大师**："你先深呼吸。这三种模式选哪个，取决于你现在要干什么。我问你，你是在自己电脑上做实验还是要上线生产？"

**小胖**："当然是自己电脑做实验啊，我连 Milvus 的 Hello World 都没跑通呢。"

**大师**："那就 Standalone。来，我用一个比喻帮你理清三种模式——"

| 模式 | 类比 | 组成 | 适用场景 |
|------|------|------|---------|
| **Lite** | 学校食堂的盒饭 | 一个进程包含了所有组件 | Python 本地开发（`pip install pymilvus[milvus_lite]`），零依赖 |
| **Standalone** | 小餐馆的后厨 | 单一实例，但独立部署 etcd + MinIO + Milvus | 个人开发机、小规模测试、教学演示 |
| **Cluster** | 五星酒店中央厨房 | 多节点分布式：Proxy ×N + Coord ×N + Node ×N | 生产环境，需要高可用和横向扩展 |

**小白**："Lite 模式一个进程包含所有组件——那它和 Standalone 的核心区别是什么？性能会差很多吗？"

**大师**："核心区别在于持久化和扩展性。Lite 模式的数据默认存本地文件，进程关闭后数据丢失（除非指定持久化路径）。它的核心价值是'零依赖快速启动'——不需要 Docker，不需要 etcd，不需要 MinIO，`pip install` 完就能跑。适合写单元测试和本地做向量检索的 POC。Standalone 则模拟了完整的生产架构，虽然也是单机但包含 etcd 元数据存储、MinIO 对象存储、Pulsar 消息队列三个独立服务，适合做功能验证和集成测试。"

**小胖**："那我本地实验用 Standalone 就对了？"

**大师**："没错。Lite 虽然快，但它屏蔽了太多底层细节，你学不到真正的 Milvus 架构。Cluster 太重在本地跑不动。Standalone 是学习的最佳起点。"

> **技术映射**：Lite = 单进程内嵌模式（开发测试用）；Standalone = 单机完整部署（包含所有外部依赖）；Cluster = 分布式集群部署（生产用）。

---

**第二幕：Docker Compose 中 etcd、MinIO、Milvus 的职责划分**

**小白**："大师，Standalone 的 docker-compose.yml 我看到有三个服务——etcd、minio、milvus-standalone。它们各自负责什么？为什么不能像 MySQL 一样一个容器搞定？"

**大师**："你把 Milvus 想简单了。Milvus 不是单纯的数据库，而是一个'存储 + 计算 + 元数据'分离的系统。这三个外部依赖各自有自己的使命——"

**大师**（在白板上画）：

```
┌────────────────────────────────────────────────────┐
│                   Milvus Standalone                 │
│  ┌──────────┐  ┌───────────┐  ┌────────────────┐  │
│  │ Proxy    │  │ DataCoord │  │ RootCoord      │  │
│  │          │  │ QueryCoord│  │                │  │
│  └────┬─────┘  └─────┬─────┘  └───────┬────────┘  │
│       │              │                │            │
│       │       ┌──────┴──────┐         │            │
│       │       │ DataNode    │         │            │
│       │       │ QueryNode   │         │            │
│       │       │ IndexNode   │         │            │
│       │       └──────┬──────┘         │            │
│       │              │                │            │
└───────┼──────────────┼────────────────┼────────────┘
        │              │                │
   ┌────▼────┐   ┌─────▼──────┐   ┌────▼─────┐
   │  etcd   │   │  MinIO/S3  │   │  Pulsar  │
   │ 元数据   │   │  对象存储   │   │  消息队列 │
   └─────────┘   └────────────┘   └──────────┘
```

| 服务 | 默认端口 | 职责 | 挂了会怎样 |
|------|---------|------|-----------|
| **etcd** | 2379 | 存储所有元数据：Collection 定义、Schema、Segment 状态、节点注册信息 | 整个集群不可用（核心元数据丢失） |
| **MinIO** | 9000, 9001 | 持久化存储 Binlog、索引文件、Delta 日志（模拟生产中的 S3） | 无法写入、无法加载已存数据（但已在内存的 Collection 仍可搜索） |
| **Pulsar** | 6650 | 解耦写入链路的消息队列，保证数据不丢失 | 无法写入新数据（但已消费的数据不受影响） |
| **Milvus** | 19530 | 核心服务进程，包含所有 Coordinator 和 Node 组件的 Standalone 合体版本 | 所有服务不可用 |

**小胖**："我懂了大半——etcd 是'账本'，MinIO 是'仓库'，Pulsar 是'传送带'，Milvus 是'工厂'本身。但为啥要分开部署，不能塞进一个容器？"

**大师**："分开部署有几个重要原因。第一，资源隔离——etcd 需要稳定的低延迟磁盘 IO，MinIO 需要大容量存储，如果全塞在一个容器，磁盘 IO 抢占会导致整个系统不稳定。第二，独立升级——你可以单独升级 MinIO 而不用重启 Milvus。第三，生产环境几乎不会自己维护 etcd 和 Pulsar，而是用云厂商的托管服务——如果开发环境强行打包在一起，你就认知不到这些外部依赖的存在。"

**小白**："那如果我的本地开发机内存不够，不想跑 Pulsar 呢？"

**大师**："Milvus 2.5.x 开始支持 `streamingNode` 替代 Pulsar，可以在 Standalone 中内嵌消息组件，减少外部依赖。具体配置在 `docker-compose.yml` 中设置环境变量 `KNOWHERE_BUILD_LEVEL=STANDALONE_ONLY`。不过这对入门来说可能有点超前，建议先用完整 Standalone 理解架构，之后再优化。"

> **技术映射**：etcd = 分布式配置中心（类似 ZooKeeper）；MinIO = S3 兼容对象存储（类似 AWS S3）；Pulsar = 高吞吐消息队列（类似 Kafka）。

---

**第三幕：本地环境避坑与健康检查**

**小胖**："好吧，那我按 docker-compose.yml 启动之后，怎么知道它到底有没有成功？不能每次都盯着终端等吧？"

**大师**："有三层验证手段，从简单到深入——"

**1. 端口检测（最浅）**
```bash
# 检查 Milvus 19530 端口是否在监听
curl http://localhost:19530/healthz
# 预期输出: OK
```

**2. Attu 可视化（最直观）**
Attu 是 Milvus 官方提供的 Web GUI 管理工具。在浏览器打开 `http://localhost:3000`，输入连接地址 `localhost:19530`，就能看到所有 Collection、数据预览、索引状态和搜索测试界面。

**3. Python SDK 验证（最可靠）**
```python
from pymilvus import connections, utility
connections.connect(host="localhost", port="19530")
print(utility.get_server_version())  # 能打印出版本号就是真的通了
```

**小白**："等等，你说 etcd 和 MinIO 也要单独验证吧？"

**大师**："对，完整的健康检查应该是四件套——"

```bash
# etcd 健康检查
curl http://localhost:2379/health

# MinIO 健康检查
curl http://localhost:9001/minio/health/live

# Pulsar 健康检查（若使用）
curl http://localhost:8080/admin/v2/clusters

# Milvus 健康检查
curl http://localhost:19530/healthz
```

**小胖**："那内存和磁盘空间呢？我笔记本才 8GB……"

**大师**："这个要重点提醒。Milvus Standalone 整个栈（etcd + MinIO + Pulsar + Milvus）至少 4GB 内存才能稳定运行。如果只有 8GB，建议把 MinIO 的内存限制降到 256MB，etcd 降到 128MB。具体配置见后面的实战部分。"

> **技术映射**：端口检测 = 敲门看有没有人应；Attu = 进门参观各个房间；SDK 调用 = 真正入住体验。

---

## 3. 项目实战

### 3.1 实战目标

使用 Docker Compose 一键启动 Milvus Standalone + Attu，完成全组件健康检查和 Python SDK 连接验证。

### 3.2 环境准备

| 依赖 | 最低版本 | 验证命令 |
|------|---------|---------|
| Docker | 20.10+ | `docker --version` |
| Docker Compose | v2.0+ | `docker compose version` |
| Python | 3.10+ | `python --version` |
| 内存 | 4GB 可用 | `docker stats` 启动后观察 |

### 3.3 分步实现

#### 步骤 1：编写 docker-compose.yml（最小化配置）

```yaml
# docker-compose.yml
# Milvus Standalone 2.5.x + etcd + MinIO + Attu
version: "3.5"

services:
  # ============================================
  # 1. etcd — 元数据存储（配置中心）
  # ============================================
  etcd:
    container_name: milvus-etcd
    image: quay.io/coreos/etcd:v3.5.16
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296        # 4GB 配额
      - ETCD_SNAPSHOT_COUNT=50000
    volumes:
      - ./volumes/etcd:/etcd                        # 数据持久化到宿主机
    command: etcd -advertise-client-urls=http://127.0.0.1:2379 \
                   -listen-client-urls http://0.0.0.0:2379 \
                   --data-dir /etcd
    healthcheck:
      test: ["CMD", "etcdctl", "endpoint", "health"]
      interval: 30s
      timeout: 20s
      retries: 3

  # ============================================
  # 2. MinIO — 对象存储（模拟 S3）
  # ============================================
  minio:
    container_name: milvus-minio
    image: minio/minio:RELEASE.2024-12-18T13-15-44Z
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    volumes:
      - ./volumes/minio:/minio_data
    command: minio server /minio_data --console-address ":9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 20s
      retries: 3

  # ============================================
  # 3. Milvus Standalone — 核心服务
  # ============================================
  standalone:
    container_name: milvus-standalone
    image: milvusdb/milvus:v2.5.5
    command: ["milvus", "run", "standalone"]
    security_opt:
      - seccomp:unconfined
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
      MINIO_ACCESS_KEY_ID: minioadmin
      MINIO_SECRET_ACCESS_KEY: minioadmin
    volumes:
      - ./volumes/milvus:/var/lib/milvus
    ports:
      - "19530:19530"                                # gRPC 端口（SDK 连接用）
      - "9091:9091"                                  # Metrics 端口（Prometheus 用）
    depends_on:
      etcd:
        condition: service_healthy
      minio:
        condition: service_healthy

  # ============================================
  # 4. Attu — 可视化管理工具
  # ============================================
  attu:
    container_name: milvus-attu
    image: zilliz/attu:v2.4.7
    environment:
      MILVUS_URL: standalone:19530                  # 连接 Milvus
    ports:
      - "3000:3000"                                  # Web UI 端口
    depends_on:
      - standalone

networks:
  default:
    name: milvus-network
```

#### 步骤 2：启动与健康检查

```bash
# 启动全部服务
docker compose up -d

# 等待服务就绪（大约 30-60 秒）
docker compose ps

# 期望输出：
# NAME                IMAGE                         STATUS
# milvus-attu         zilliz/attu:v2.4.7            Up
# milvus-etcd         quay.io/coreos/etcd:v3.5.16   Up (healthy)
# milvus-minio        minio/minio:RELEASE...        Up (healthy)
# milvus-standalone   milvusdb/milvus:v2.5.5        Up
```

```bash
# 四件套健康检查
echo "=== etcd ==="
curl -s http://localhost:2379/health
echo ""

echo "=== MinIO ==="
curl -s http://localhost:9001/minio/health/live
echo ""

echo "=== Milvus ==="
curl -s http://localhost:19530/healthz
echo ""

echo "=== Attu ==="
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
echo ""
```

**预期输出**：
```
=== etcd ===
{"health":"true"}
=== MinIO ===
(返回 200)
=== Milvus ===
OK
=== Attu ===
200
```

#### 步骤 3：Python SDK 连接验证

```python
# verify_connection.py
"""验证 Milvus SDK 连接与基础操作"""
from pymilvus import connections, utility, Collection
from pymilvus import CollectionSchema, FieldSchema, DataType

# 建立连接
connections.connect(
    alias="default",
    host="localhost",
    port="19530",
    timeout=30
)

# 验证连接
version = utility.get_server_version()
print(f"✓ 连接成功！Milvus 版本: {version}")

# 尝试创建一个测试 Collection
fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=256),
    FieldSchema(name="vec", dtype=DataType.FLOAT_VECTOR, dim=128),
]
schema = CollectionSchema(fields, description="Hello Milvus!")
test_collection = Collection("hello_milvus", schema)
print(f"✓ Collection '{test_collection.name}' 创建成功")

# 清理测试 Collection
utility.drop_collection("hello_milvus")
print(f"✓ Collection 清理完成")
print(f"✓ 全部验证通过！Milvus Standalone 环境就绪")
```

**预期输出**：
```
✓ 连接成功！Milvus 版本: v2.5.5
✓ Collection 'hello_milvus' 创建成功
✓ Collection 清理完成
✓ 全部验证通过！Milvus Standalone 环境就绪
```

#### 步骤 4：通过 Attu 可视化探索

1. 浏览器打开 `http://localhost:3000`
2. 输入连接地址 `localhost:19530`
3. 观察界面中的功能区域：
   - **Overview**：集群基本信息和存储用量
   - **Collections**：所有 Collection 列表及其 Schema、索引状态
   - **Search**：交互式向量搜索测试
   - **Data Preview**：数据预览和条件查询

#### 步骤 5：常用运维命令速查

```bash
# 查看所有容器的资源使用（内存/CPU）
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"

# 查看 Milvus 日志（排查启动问题）
docker compose logs standalone -f --tail=100

# 查看 MinIO 是否运行
docker compose logs minio | grep -i "error\|warn"

# 重启某个服务
docker compose restart standalone

# 停止全部服务
docker compose down

# 清理所有数据（包括 etcd、MinIO 中的数据）
docker compose down -v && rm -rf ./volumes
```

### 3.4 可能遇到的坑及解决方法

| 问题 | 现象 | 解决方法 |
|------|------|---------|
| **端口冲突** | `Error: port is already allocated` | 检查 19530/2379/9000/9001/9091 是否被占用：`netstat -ano \| findstr 19530`；在 compose 文件中修改端口映射 |
| **内存不足** | Milvus 容器反复重启，日志显示 OOM Killed | 给 MinIO 和 etcd 设置 `mem_limit`：在 `minio` 服务下添加 `deploy: resources: limits: {memory: 256M}` |
| **etcd 启动失败** | Milvus 日志显示 `context deadline exceeded` 连接 etcd | 确认 etcd 健康检查通过后再启动 Milvus，或增加 `depends_on` 的等待时间 |
| **MinIO 权限错误** | `Access Denied` | 确认 `MINIO_ROOT_USER` 和 `MINIO_ACCESS_KEY_ID` 一致 |
| **Windows 卷挂载问题** | `.\volumes\etcd` 路径无权限 | 将 `./volumes` 改为绝对路径，如 `//d/data/milvus/volumes` |
| **Docker Desktop WSL2 内存限制** | 容器启动一段时间后卡死 | 在 Docker Desktop 设置中调整 WSL2 内存限制到至少 4GB |

### 3.5 最小资源配置建议

```
# 低配开发机（8GB 总内存）
minio:    256MB 内存限制
etcd:     128MB 内存限制
milvus:   未限制，但实际消耗约 1-2GB
attu:     约 50MB

# 推荐开发机（16GB+ 总内存）
minio:    512MB
etcd:     256MB
milvus:   2-4GB（取决于加载的 Collection 大小）
attu:     约 50MB
```

---

## 4. 项目总结

### 4.1 优缺点对比

| 维度 | Docker Standalone | Milvus Lite | 手动编译运行 |
|------|------------------|-------------|------------|
| 启动速度 | 慢（~60s，需拉镜像并启动3个服务） | 快（~3s，`pip install` 即可） | 极慢（需配 Go/C++ 编译环境） |
| 架构完整性 | 完整（etcd+MinIO+Pulsar+Milvus） | 简化（单进程，无外部依赖） | 自定义 |
| 资源占用 | 高（4GB+ 内存） | 低（512MB 即可） | 取决于配置 |
| 生产对齐 | 是（与 Cluster 架构一致） | 否（架构差异大） | 是 |
| 学习成本 | 中（需理解 Docker 和每个服务角色） | 低（开箱即用） | 极高 |

### 4.2 适用场景

- **本地功能验证**：在开发机上用 Standalone 跑通 Collection 创建、数据写入、索引构建、搜索的全流程
- **集成测试环境**：CI/CD 中使用 Standalone 做自动化测试
- **小规模 Demo**：给业务方演示 Milvus 功能时直接用 Standalone
- **团队知识库 POC**：搭建小规模的 RAG 知识库验证方案可行性

**不适用场景**：生产环境（需 Cluster 模式）、大规模压测（单机瓶颈明显）、需要源码调试（需手动编译）。

### 4.3 注意事项

- **镜像版本一致性**：etcd、MinIO、Milvus 的版本需要与 Milvus 官方 docker-compose 文档对齐，避免兼容性问题。
- **数据持久化路径**：`volumes` 目录不要放在系统临时目录（如 `/tmp`），否则重启后数据丢失。
- **网络隔离**：确保 `docker compose` 创建的网络不与公司 VPN 或内网 IP 段冲突。

### 4.4 常见踩坑经验

1. **Windows Docker Desktop 内存限制**：默认 WSL2 内存只有 2GB，需要手动调整到 4GB+。路径：Docker Desktop → Settings → Resources → WSL Integration → Memory limit。
2. **minio 控制台端口**：MinIO 的 API 端口是 9000，Web 控制台端口是 9001。很多人只映射了 9000 导致无法访问控制台做健康检查。
3. **etcd 数据目录权限**：如果使用 Docker Desktop on macOS/Linux，etcd 的 `--data-dir` 目录需要映射到有写权限的路径，否则 etcd 会启动失败。

### 4.5 思考题

1. 如果要求在不使用 Docker 的情况下在本机启动 Milvus，需要手动安装和配置哪些外部依赖？请按启动顺序列出。
2. Attu 连接 Milvus 后，查看一个 Collection 的信息时，这些信息是从哪个组件获取的？（提示：元数据存储在哪里？）

### 4.6 推广计划提示

- **开发团队**：将本章的 `docker-compose.yml` 提交到项目仓库，所有开发者一键启动，消除"在我机器上能跑"问题。
- **测试团队**：基于 Standalone 环境编写 API 级别的集成测试，在 CI 中使用 `docker compose up -d` 作为测试前置步骤。
- **运维团队**：将 Standalone 视为学习 Milvus 运维的最小单元，理解 etcd / MinIO / Pulsar 各自的健康检查指标。

---

> **下一章预告**：第3章我们将开始设计第一个 Collection Schema，理解向量字段与标量字段的建模关系。读完本章，你应该能在任何开发机上 10 分钟内启动一套完整的 Milvus 实验环境。
