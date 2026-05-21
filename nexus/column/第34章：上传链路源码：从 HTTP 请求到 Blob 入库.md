# 第34章：上传链路源码：从 HTTP 请求到 Blob 入库

## 1. 项目背景

云鲸科技的核心开发小李正在 debug 一个诡异的上传问题：通过 Nexus Web UI 上传一个 500MB 的 Raw 文件，进度条到 98% 时突然消失，Nexus 日志中没有异常，BlobStore 中也没有对应文件。但如果用 curl 直接 PUT 到 Raw 仓库，同样大小的文件成功上传。更蹊跷的是——上传 100MB 的文件没问题，200MB 偶尔失败，500MB 几乎必败。

小李在第 32 章学会了反向追踪法——从 `POST /service/rest/v1/components` 找到了 `UploadResource.java`，但追到一半迷路了。"请求体是一个 `multipart/form-data`，包含了文件流和组件信息——但 Nexus 怎么把流分派到对应格式的处理器？为什么 UI 上传和 curl 上传走的路径不一样？500MB 的问题到底是 HTTP 层的超时、Nexus 的内存 buffer、还是 BlobStore 的写入限制？"

上传是制品仓库最核心也最频繁的写入操作——从 Bundle 上传（UI）到直接 PUT（API），从 Maven 的 POM 校验到 Docker 的 layer 去重，从 Blob 写入到元数据更新到事件发布——这是一条几十个类协作的复杂调用链。本章将沿着上传请求的完整链路，从 HTTP 入口一路追踪到 Blob 文件落盘，标注出关键类、关键方法、日志埋点和可能的瓶颈点。

## 2. 项目设计

小李把 IDE 里打开的 12 个文件标签页逐个给大师看，满脸挫败。

**小李**："大师，我追踪了一个 UI 上传请求，走过的类比我预想的多得多。能不能帮我理一下主干路径？"

**大师**："Nexus 的上传链路分两条并行的路径。路径 A——**Web UI 上传**：`UploadResource`（JAX-RS 入口）→ `UploadManager`（上传协调器）→ `UploadHandler`（格式特定处理器）→ `BlobStoreMultipartForm`（磁盘暂存）→ `StorageFacet`（Blob 写入）。路径 B——**直接 HTTP PUT**：`RepositoryResource`（JAX-RS 入口）→ `StorageFacet`（跳过 UploadManager，直接写入）。你遇到的 500MB UI 上传问题，根因在高概率在 `BlobStoreMultipartForm` 这一环。"

**小胖**："为什么 UI 上传要经过 UploadManager 而 curl 不用？"

**大师**："因为 UI 上传是**多文件 + 元数据**的复合操作——一次请求可能包含 jar + pom + sources.jar 三个文件，还要附带 groupId、artifactId、version 等元数据。Nexus 用 `multipart/form-data` 格式承载这些内容。`UploadManager` 的职责就是解析 multipart 流——把'组件元数据'和'文件流'分离开来，然后找到对应格式的 `UploadHandler`，由它决定如何组织组件和资产。而 curl PUT 是**单文件操作**——请求体就是文件内容本身，URL 路径本身就包含了元数据（仓库名 + 文件路径），不需要额外的解析层。"

> **技术映射**：UI 上传 = Multipart 解析（UploadManager）→ 格式路由（UploadHandler）→ Blob 写入（StorageFacet）。curl PUT = 路径路由（RepositoryResource）→ Blob 写入（StorageFacet）。前者多了一个"批量协调"层，是 UI 上传的便利代价。

**小李**："那 500MB 的问题到底在哪？"

**大师**："分类定位。如果 500MB 用 curl PUT 成功 → 排除 StorageFacet 和 BlobStore 的问题。如果 UI 上传 100MB 成功 500MB 失败 → 问题集中在 `BlobStoreMultipartForm` 的临时磁盘空间、Nexus JVM 的 `MaxDirectMemorySize` 不足导致的 OOM、或前端 Nginx 的 `client_max_body_size`/`proxy_read_timeout`。你需要按层次逐级缩小范围——先看 HTTP 层（Nginx 日志中有没有 413/504），再看 Nexus 层（`request.log` 中请求是否到达），最后看 BlobStore 层（磁盘空间是否充足）。"

> **技术映射**：上传瓶颈定位 = HTTP 层（超时/大小限制）→ Nexus JVM 层（直接内存/GC）→ BlobStore 层（磁盘 IO/文件系统限制）。按这个顺序从外到内排查。

**小白**："上传过程中如果 Nexus 重启了，已经写入一半的 Blob 会怎样？"

**大师**："Nexus 的上传不是事务的——一旦 Blob 开始写入磁盘，内容就是持久化的。但组件（Component）和资产（Asset）的元数据是在所有文件上传完成后才写入数据库的。如果中途崩溃——Blob 文件可能存在但 Component 记录缺失，这个 Blob 就变成了'孤儿 Blob'。Compact BlobStore 任务后续会检测到这些孤儿并回收。所以从外部看，**上传要么完全成功（Component + Asset 都可见），要么完全失败（什么都没有）**——这是一个原子性的近似实现。"

## 3. 项目实战

### 3.1 环境准备

- Nexus 源码已导入 IDE（参考第 32 章）
- Nexus 实例运行中（用于观察日志和测试上传）
- curl

### 3.2 分步实战

#### 步骤一：追踪 UI 上传的入口和参数解析

**目标**：定位 UploadResource 并理解 Multipart 参数的解析流程。

```bash
# 在源码中定位 UploadResource
grep -r "class UploadResource" --include="*.java" plugins/nexus-coreui-plugin/ | head -1

# 打开 UploadResource.java（以下为简化示意）
```

**UploadResource 入口方法简化示意**：

```java
@Path("/v1/components")
public class UploadResource {
    
    @Inject
    private UploadManager uploadManager;
    
    @POST
    @Consumes(MediaType.MULTIPART_FORM_DATA)
    public ComponentXO upload(
        @QueryParam("repository") String repositoryName,  // ← URL 参数：仓库名
        @FormDataParam("raw.directory") String directory,  // ← 表单参数：Raw 格式的目录
        @FormDataParam("raw.asset1") InputStream assetStream, // ← 文件流
        @FormDataParam("raw.asset1.filename") String filename // ← 文件名
    ) {
        // 构建 ComponentUpload 对象，封装所有上传信息
        ComponentUpload upload = new ComponentUpload();
        upload.setRepository(repositoryName);
        upload.getAssets().add(new AssetUpload(assetStream, filename, directory));
        
        // 委托给 UploadManager 处理
        return uploadManager.upload(upload);
    }
}
```

**关键观察**：
- `@FormDataParam` 注解的参数由 Jersey（JAX-RS 实现）自动从 multipart 流中解析
- `InputStream assetStream` 不是一次性加载到内存——Jersey 使用的是流式解析
- 目录参数对于 Raw 格式决定了 asset 在仓库中的路径

#### 步骤二：追踪 UploadManager 的分发逻辑

**目标**：理解 UploadManager 如何根据格式路由到正确的 UploadHandler。

```java
// UploadManager 核心逻辑简化示意
@Named
@Singleton
public class UploadManagerImpl implements UploadManager {
    
    // 注入所有格式的 UploadHandler（Map<FormatName, UploadHandler>）
    @Inject
    private Map<String, UploadHandler> uploadHandlers;  // ← Sisu 自动收集所有 @Named 的 UploadHandler
    
    @Override
    public ComponentXO upload(ComponentUpload upload) throws IOException {
        // 1. 获取目标仓库
        Repository repo = repositoryManager.get(upload.getRepository());
        String format = repo.getFormat().getValue();  // "maven2", "npm", "raw", ...
        
        // 2. 按格式查找对应的 UploadHandler
        UploadHandler handler = uploadHandlers.get(format);
        if (handler == null) {
            throw new IllegalArgumentException("No upload handler for format: " + format);
        }
        
        // 3. 委托格式处理器执行上传
        return handler.handle(repo, upload);
    }
}
```

**格式路由机制**：
- Nexus 通过 `Map<String, UploadHandler>` 的 key（格式名）分发到对应的处理器
- UI 上传时，前端在请求 URL 中传入 `?repository=maven-releases`，后端通过仓库的 format 决定使用哪个 Handler
- Raw 格式的 Handler 最简单——因为 Raw 没有任何格式特定的校验

#### 步骤三：对比 Maven 和 Raw 的上传处理差异

**目标**：理解不同格式的 UploadHandler 在上传时做了什么额外操作。

```bash
# 在 Nexus 容器中执行上传操作并观察 request.log

# 1. Raw 上传（最简单）
echo "raw-content" > /tmp/raw-test.bin
curl -u admin:admin123 -X PUT \
  "http://localhost:8081/repository/raw-hosted/test/upload-34.bin" \
  --data-binary @/tmp/raw-test.bin -w "\nHTTP %{http_code}"

# 2. 观察 request.log
docker compose exec nexus tail -3 /nexus-data/log/request.log
# 格式: IP - user [time] "PUT /repository/raw-hosted/..." 201 10 45
# 解读: 请求大小 10B → 状态码 201 → 处理耗时 45ms

# 3. Maven 上传（需要完整的 jar 包结构）
# Maven 上传不能简单用 curl PUT——因为 Maven 需要 POM 解析和 GAV 坐标验证
# 正确方式是通过 mvn deploy 或 UI 上传

echo "=== 对比 ==="
echo "Raw 上传: PUT → StorageFacet.put() → BlobStore.create() → 201"
echo "Maven 上传: POST multipart → UploadManager → MavenUploadHandler → POM 解析 → GAV 校验 → StorageFacet → 201"
echo "差异: Maven 多了 POM 解析（验证 GAV 一致性）和 version policy 检查（RELEASE vs SNAPSHOT）"
```

#### 步骤四：追踪 Blob 写入的最终环节

**目标**：理解 StorageFacet → BlobStore 的写入过程。

```java
// StorageFacet 写入 Blob 简化示意
public class StorageFacetImpl implements StorageFacet {
    
    @Override
    public Content put(InputStream content, Payload payload) throws IOException {
        // 1. 生成 Blob 的唯一 ID
        BlobId blobId = blobStore.create(payload.getContentType(), content);
        
        // 2. 更新 Component/Asset 元数据（写入数据库）
        Asset asset = createAsset(blobId, payload.getPath());
        
        // 3. 生成 checksum（SHA1/SHA256/MD5）
        Map<String, String> checksums = blobStore.getChecksums(blobId);
        
        // 4. 发布事件（触发 Webhook 和审计日志）
        eventBus.post(new AssetCreatedEvent(asset));
        
        return new Content(blobId, checksums);
    }
}

// FileBlobStore 写入磁盘简化示意
public class FileBlobStore implements BlobStore {
    
    @Override
    public BlobId create(InputStream content, Map<String, String> headers) {
        // 1. 生成两层哈希路径（vol-01/chap-0a/）
        BlobId id = generateBlobId();
        Path blobPath = getBlobPath(id);
        
        // 2. 写入 .bytes 文件（Direct ByteBuffer 零拷贝写入）
        Files.createDirectories(blobPath.getParent());
        try (OutputStream out = Files.newOutputStream(blobPath.resolve(id + ".bytes"))) {
            content.transferTo(out); // ← Java 9+ 零拷贝传输
        }
        
        // 3. 写入 .properties 文件（元数据：contentType, size, sha1, createdBy）
        storeProperties(blobPath, id, headers);
        
        return id;
    }
}
```

#### 步骤五：模拟上传失败并观察日志

**目标**：在请求日志中识别上传失败的不同根因。

```bash
#!/bin/bash
# upload-diag.sh：上传故障诊断脚本
echo "=== 上传故障诊断 ==="

# 测试1：上传超大文件（验证 HTTP 层限制）
echo "[测试1] 超大文件上传（应被 Nginx/HTTP 层拒绝）"
dd if=/dev/urandom of=/tmp/huge.bin bs=1M count=1000 2>/dev/null
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -u admin:admin123 -X PUT \
  "http://localhost:8081/repository/raw-hosted/test/huge.bin" --data-binary @/tmp/huge.bin)
echo "  HTTP $HTTP (413=Entity Too Large, 504=Gateway Timeout)"
rm -f /tmp/huge.bin

# 测试2：上传到不存在的仓库
echo "[测试2] 上传到不存在的仓库"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -u admin:admin123 -X PUT \
  "http://localhost:8081/repository/ghost-repo/test.txt" -d "test")
echo "  HTTP $HTTP (预期 404 Not Found)"

# 测试3：不带认证上传
echo "[测试3] 无认证上传"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X PUT \
  "http://localhost:8081/repository/raw-hosted/test/unauth.txt" -d "test")
echo "  HTTP $HTTP (预期 401 Unauthorized)"

# 测试4：正常上传作为基线
echo "[测试4] 正常上传（基线）"
START=$(date +%s%N)
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -u admin:admin123 -X PUT \
  "http://localhost:8081/repository/raw-hosted/test/baseline.txt" -d "baseline")
END=$(date +%s%N)
DURATION=$(( (END - START) / 1000000 ))
echo "  HTTP $HTTP  耗时: ${DURATION}ms  (基线参考)"
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| Multipart 上传大文件 OOM | 500MB 文件导致 Nexus 堆溢出 | 确保 `MaxDirectMemorySize` >= 上传文件大小的 2 倍 |
| UI 上传 file name 编码 | 中文文件名上传后变成乱码 | `@FormDataParam` 的 `filename` 使用 `filename*`（RFC 5987）编码 |
| UploadManager 找不到 Handler | 上传到某种格式的仓库时 500 错误 | 检查该格式是否注册了 `@Named` 的 `UploadHandler` 实现 |
| PUT vs POST 路径差异 | 用 curl POST 到 PUT 的端点返回 405 | Nexus 严格区分 HTTP 方法——UI 上传用 POST multipart，直接文件 PUT 用 PUT |

## 4. 项目总结

### 4.1 上传链路关键类速查

| 层次 | 关键类/接口 | 职责 | 源码位置 |
|------|-----------|------|---------|
| HTTP 入口 | `UploadResource` | UI 上传的 JAX-RS 端点 | `plugins/nexus-coreui-plugin/` |
| HTTP 入口 | `RepositoryResource` | 直接 PUT 的 JAX-RS 端点 | `components/nexus-repository/` |
| 批量协调 | `UploadManager` | 解析 multipart，查找 Handler | `components/nexus-repository/` |
| 格式处理 | `UploadHandler` (接口) | 各格式的上传处理策略 | `plugins/nexus-repository-{format}/` |
| 存储写入 | `StorageFacet` | Blob 写入 + 元数据创建 | `components/nexus-repository/` |
| 物理存储 | `BlobStore.create()` | 磁盘/S3 的字节写入 | `components/nexus-blobstore/` |
| 事件通知 | `EventBus.post()` | 触发 Webhook + 审计日志 | `components/nexus-core/` |

### 4.2 适用场景

1. **大文件上传优化**：追踪从 HTTP 到 Blob 的完整路径，找出性能瓶颈
2. **格式插件开发**：实现自定义的 `UploadHandler` 进行上传校验
3. **上传安全审计**：在上传链路中的关键节点插入安全检查
4. **故障排查**：上传失败时根据日志定位到具体出错环节
5. **自定义上传策略**：如在 `UploadHandler` 中增加文件类型白名单检查

**不适用场景**：
1. 下载性能问题——应看第 35 章下载链路
2. 上传后的自动化处理——应看第 36 章 EventBus + Webhook 事件驱动

### 4.3 注意事项

- **上传不是事务的**：Blob 写入和 Component 创建不在同一事务中，崩溃可能导致孤儿 Blob
- **Multipart 上传的临时文件**：`BlobStoreMultipartForm` 会将超大文件先暂时存储在磁盘上（默认临时目录在 `/nexus-data/tmp/`）
- **直接内存是关键调优点**：上传链路中多处使用 Direct ByteBuffer 做零拷贝流式传输
- **不同格式的上传校验不同**：Maven 校验 POM 中的 GAV 一致性，npm 校验 package.json，Raw 无校验

### 4.4 思考题

1. 如果需要在所有上传操作执行前增加一个企业自定义的安全检查（如扫描文件内容是否包含禁止关键词、检查文件大小是否超出团队配额），应该在哪一层注入？是在 `UploadHandler`、`StorageFacet` 还是 HTTP 层的 Filter？
2. Docker 镜像的上传链路与 Maven/Raw 有什么本质不同？为什么 Docker push 不经过 `UploadResource`？它的上传入口在哪里？

（第33章思考题答案：1. 不需要新 Recipe 和 Facet——通过仓库的 `writePolicy: DENY` 配置就可以禁止上传和删除。`writePolicy` 是在 `MavenHostedFacet` 中被校验的，它读取仓库配置中的 `writePolicy` 字段，如果为 `DENY` 则在 `put()` 方法中直接抛异常。所以只读需求通过配置就能满足，体现了 Facet 设计"行为参数化"的灵活性。2. 应该建一个新的 Facet——`TarAutoExtractFacet`。这个 Facet 在 `RawHostedRecipe.apply()` 中 attach，当 raw 文件上传完成后拦截 `StorageFacet.put()` 的返回值，检测如果 asset 的 contentType 是 `application/gzip` 且文件名以 `.tar.gz` 结尾，就触发解压逻辑并创建子 asset。如果直接扩展 `RawHostedFacet`，会导致其职责不清晰——`RawHostedFacet` 的职责是'不做任何格式特定处理'，属于占位 Facet。）

### 4.5 推广计划提示

- **核心开发**：在 IDE 中以 `UploadResource.upload()` 为起点，用调试器单步走一遍 Raw 格式和 Maven 格式的完整上传流程
- **运维团队**：理解上传失败的不同根因（HTTP 层 vs Nexus 层 vs BlobStore 层），快速定位问题归属
- **安全团队**：在上传链路的关键节点（`UploadHandler` 或 `StorageFacet`）评估是否可以注入安全扫描逻辑
