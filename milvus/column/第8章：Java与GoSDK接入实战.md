# 第8章：Java 与 Go SDK 接入实战

> **定位**：覆盖企业后端常见技术栈。
> **版本**：Milvus 2.5.x / Java SDK 2.5.5 / Go SDK v2
> **源码关联**：milvus-sdk-java、milvus-sdk-go、internal/proxy/

---

## 1. 项目背景

某电商公司后端技术栈是 Java（Spring Boot 商品服务）和 Go（搜索网关服务）。第7章我们用 Python 封装了 Repository，但实际生产环境中，Python 主要用于数据分析和 AI 模型，真正的在线服务是 Java 和 Go 写的。

Java 团队负责人老王接到的任务是：在 Spring Boot 商品搜索微服务中接入 Milvus，替代现有的 Elasticsearch 向量插件。老王之前只写过 Spring Data JPA，对向量数据库完全是新手。他照着 Milvus Java SDK 的 README 写了一段代码，但遇到了三个问题：

1. **连接池配置不生效**——Java SDK 默认的连接池参数是针对 Python 调优的，Java 的高并发场景下 gRPC 连接数直接打满。
2. **批量写入 OOM**——老王把 10 万条数据按 1000 条一批 Insert，但没意识到 Java 的 List 对象在序列化到 gRPC 时会产生大量临时对象，GC 频繁触发 STOP THE WORLD。
3. **跨语言契约不一致**——老王用 Java 写入的 Collection，用 Python 搜出来的结果和 Java 搜出来的不一样。排查发现是 Java 和 Python 对 COSINE metric 的实现有细微差异。

与此同时，Go 团队的张工也在做类似的事情——在 Gin 框架中集成 Go SDK。他碰到的是 Go 特有的问题：context 取消、goroutine 泄露、高并发下的 gRPC 连接管理。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：Java SDK vs Go SDK vs Python SDK——API 风格差异**

*（老王和张工坐在会议室两端，各自对着自己的笔记本皱眉）*

**小胖**（幸灾乐祸地）："嘿嘿，两位大佬，同一个 Milvus，三种语言写出来的代码能有多不一样？不就是换个语法吗？"

**大师**："小胖，你这可就说错了。同样的功能，三种 SDK 的设计哲学完全不同——"

**大师**（在白板上并列展示）：

```
Python SDK                         Java SDK                            Go SDK
───────────────────                ────────────────────                ──────────────
from pymilvus import               import io.milvus.client.*;           import (
  Collection, connections             MilvusServiceClient                "github.com/milvus-io/
                                       client = new                      milvus-sdk-go/v2/client"
connections.connect(...)              MilvusServiceClient(
collection = Collection(...)           ConnectParam.builder()           ctx := context.Background()
collection.insert([...])               .withHost("localhost")          c, _ := client.NewClient(ctx,
                                       .build());                       client.Config{
                                     );                                   Address: "localhost:19530",
result = collection.search(...)                                          })
                                     R<MutationResult> resp =
                                       client.insert(                  insertResult, _ := c.Insert(ctx,
for hit in results[0]:                 InsertParam.builder()              client.NewInsertOption(
    print(hit.id,                      .withCollectionName(...)           "product_search", ...))
      hit.distance)                    .withFields(fields)
                                       .build());                     searchResult, _ := c.Search(ctx,
                                                                        client.NewSearchOption(...))
                                     R<SearchResults> resp =
                                       client.search(
                                         SearchParam.builder()
                                           .build());

动态类型 / 简洁读写                  Builder 模式 / 链式调用            Struct Config + Option 模式
```

**大师**："三种 SDK 的核心差异——"

| 维度 | Python (PyMilvus) | Java | Go |
|------|------------------|------|-----|
| 设计风格 | Collection 面向对象 + 直接调用 | Builder 模式链式构造参数 | Struct 配置 + Functional Option |
| 并发模型 | gRPC 多路复用，隐式线程安全 | 线程安全 + 连接池 | goroutine 安全，每操作建新 stub 或复用 |
| 异常处理 | 抛出 MilvusException | 返回 R<T> 包装（含状态码和异常） | 返回 (result, error) |
| 序列化 | Python 原生 → Protobuf | Java Bean → Protobuf（GC 压力） | Go struct → Protobuf（零拷贝优化） |
| 连接管理 | connections.connect() | ConnectParam + 内部连接池 | client.Config.Address + 底层 gRPC pool |
| 生态集成 | FastAPI / Flask | Spring Boot / Micronaut | Gin / Echo / gRPC-gateway |

**小胖**："所以我是 Python 开发者就不用学 Java 和 Go 了？"

**大师**："如果你只想写爬虫和处理数据，Python 够了。但如果要做在线搜索服务——那是 Java 和 Go 的主战场。而且，多语言团队协作时，三种 SDK 的接口契约必须统一，这就是为什么需要定义跨语言的服务接口。"

> **技术映射**：Python SDK = 瑞士军刀（快速灵活、适合原型）；Java SDK = 工厂流水线（规范严谨、适合大型项目）；Go SDK = 高性能赛车（轻量并发、适合网关和中间件）。

---

**第二幕：Java Spring Boot 集成——连接池与工程化**

**小白**："大师，Java 的 ConnectParam 里有一堆连接池参数——maxIdlePerKey、maxTotalPerKey、keepAliveTime——怎么配置才合理？"

**大师**："Java SDK 底层用的是 gRPC-Java 的 ManagedChannel，连接池参数直接映射到 gRPC 的 channel 配置。给你一份生产环境的推荐配置——"

```java
// 生产环境推荐连接配置
ConnectParam connectParam = ConnectParam.newBuilder()
    .withHost("milvus-proxy.production.svc.cluster.local")
    .withPort(19530)
    .withConnectTimeoutMs(10000)        // 连接超时：10 秒（不要设太短）
    .withKeepAliveTimeMs(30000)         // Keep-Alive 间隔：30 秒
    .withKeepAliveTimeoutMs(10000)      // Keep-Alive 超时：10 秒
    .withKeepAliveWithoutCalls(true)    // 无请求时也发 Keep-Alive
    .withMaxIdlePerKey(10)              // 每个后端地址的最大空闲连接数
    .withMaxTotalPerKey(20)             // 每个后端地址的最大总连接数
    .withRpcDeadlineMs(30000)           // RPC 调用 deadline：30 秒
    .build();
```

**小白**："这些参数分别有什么用？"

**大师**：

| 参数 | 作用 | 设太小 | 设太大 |
|------|------|-------|-------|
| `connectTimeoutMs` | 建立 TCP 连接的超时 | 网络波动时频繁失败 | 故障感知慢 |
| `keepAliveTimeMs` | 定期发送 PING 帧检测连接存活 | CPU 消耗 | 死连接清理慢 |
| `maxIdlePerKey` | 每个后端保持的空闲连接上限 | 频繁创建/销毁连接 | 占用 Proxy 连接数 |
| `rpcDeadlineMs` | 单次 RPC 调用的最大等待时间 | 搜索/写入超时 | 请求堆积 |

**小胖**："那 Spring Boot 里怎么注册成 Bean？"

**大师**："标准做法是——"

```java
@Configuration
public class MilvusConfig {
    
    @Value("${milvus.host:localhost}")
    private String host;
    
    @Value("${milvus.port:19530}")
    private int port;
    
    @Bean(destroyMethod = "close")
    public MilvusServiceClient milvusClient() {
        ConnectParam param = ConnectParam.newBuilder()
            .withHost(host)
            .withPort(port)
            .withConnectTimeoutMs(10000)
            .withKeepAliveTimeMs(30000)
            .withMaxIdlePerKey(10)
            .build();
        return new MilvusServiceClient(param);
    }
}
```

> **技术映射**：ConnectParam = 银行柜台的运营规则（开放几个窗口、排队多久、空闲窗口怎么管理）；Spring Bean = 银行正门（所有人都走这个入口）；`destroyMethod="close"` = 下班关门（释放资源）。

---

**第三幕：Go 服务中的 Context、并发和错误处理**

**小胖**："张工，Go SDK 有啥特殊的地方？"

**张工**："最大的坑是 Context 和 goroutine 泄露。你看这段代码——"

```go
// ❌ 危险写法：无视 context 取消
func searchProducts(queryVec []float32) ([]SearchResult, error) {
    ctx := context.Background()  // 没有超时控制！
    results, err := client.Search(ctx, ...)
    return results, err
}

// ✓ 正确写法：带超时和取消
func searchProducts(queryVec []float32) ([]SearchResult, error) {
    ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
    defer cancel()  // 务必 cancel 释放资源
    
    results, err := client.Search(ctx, ...)
    return results, err
}
```

**大师**："Go SDK 的三个核心要点——"

1. **Context 传播**：HTTP 请求的 context 要逐层传递到 Milvus 调用，这样上层 cancel（如客户端断开连接）时，Milvus 搜索也会停止，不浪费 QueryNode 资源。
2. **并发搜索**：Go 的 goroutine 是天然的并发工具。如果有多个查询需要并行执行，用 `errgroup` 而不是裸 `go func()`。
3. **错误处理**：Go SDK 没有 Java 的 `R<T>` 包装，直接返回 `(result, error)`。检查 `error` 时不仅要看 `err != nil`，还要看 `err` 的具体类型——是超时、连接断开、还是 Collection 不存在，不同错误需要不同处理。

```go
// Go 并发搜索示例（使用 errgroup）
func concurrentSearch(ctx context.Context, queries [][]float32) ([][]SearchResult, error) {
    g, ctx := errgroup.WithContext(ctx)
    g.SetLimit(10)  // 最多 10 个并发搜索
    
    results := make([][]SearchResult, len(queries))
    for i, qv := range queries {
        i, qv := i, qv
        g.Go(func() error {
            r, err := client.Search(ctx, client.NewSearchOption(
                "product_search", qv, 10, "title_vec", "COSINE", "ef:64",
            ))
            if err != nil {
                return fmt.Errorf("search[%d] failed: %w", i, err)
            }
            results[i] = r.Results
            return nil
        })
    }
    if err := g.Wait(); err != nil {
        return nil, err
    }
    return results, nil
}
```

> **技术映射**：Context = 请求的生命线（上层断了线，下层自动停）；errgroup = 并发的安全带（不会让 goroutine 满天飞）；defer cancel() = 用完了关水龙头（释放 timer 和底层资源）。

---

## 3. 项目实战

### 3.1 实战目标

分别用 Java 和 Go 实现相同的"写入商品向量并搜索相似商品"接口，验证跨语言 SDK 的搜索结果一致性。

### 3.2 环境准备

```bash
# Java
# JDK 17+, Maven 3.8+
# pom.xml 添加:
# <dependency>
#     <groupId>io.milvus</groupId>
#     <artifactId>milvus-sdk-java</artifactId>
#     <version>2.5.5</version>
# </dependency>

# Go
# Go 1.21+
# go get github.com/milvus-io/milvus-sdk-go/v2
```

### 3.3 分步实现

#### 步骤 1：Java Spring Boot 实现

```java
// MilvusProductRepository.java
package com.example.product.repository;

import io.milvus.client.MilvusServiceClient;
import io.milvus.grpc.*;
import io.milvus.param.*;
import io.milvus.param.collection.*;
import io.milvus.param.dml.*;
import io.milvus.param.index.*;
import io.milvus.response.*;
import org.springframework.stereotype.Repository;

import java.util.*;
import java.util.stream.Collectors;

@Repository
public class MilvusProductRepository {
    
    private final MilvusServiceClient client;
    
    public MilvusProductRepository(MilvusServiceClient client) {
        this.client = client;
    }
    
    /**
     * 创建 Collection（如果不存在）
     */
    public void createCollectionIfNotExists(String collectionName, int dim) {
        // 先检查是否存在
        R<Boolean> hasResp = client.hasCollection(
            HasCollectionParam.newBuilder()
                .withCollectionName(collectionName)
                .build()
        );
        if (hasResp.getData()) return;
        
        // 定义 Schema
        List<FieldType> fields = new ArrayList<>();
        fields.add(FieldType.newBuilder()
            .withName("id").withDataType(DataType.Int64)
            .withPrimaryKey(true).withAutoID(false).build());
        fields.add(FieldType.newBuilder()
            .withName("title").withDataType(DataType.VarChar)
            .withMaxLength(512).build());
        fields.add(FieldType.newBuilder()
            .withName("title_vec").withDataType(DataType.FloatVector)
            .withDimension(dim).build());
        fields.add(FieldType.newBuilder()
            .withName("price").withDataType(DataType.Float).build());
        fields.add(FieldType.newBuilder()
            .withName("category").withDataType(DataType.VarChar)
            .withMaxLength(64).build());
        fields.add(FieldType.newBuilder()
            .withName("in_stock").withDataType(DataType.Bool).build());
        
        CreateCollectionParam createParam = CreateCollectionParam.newBuilder()
            .withCollectionName(collectionName)
            .withDescription("商品语义搜索（Java SDK）")
            .withFieldTypes(fields)
            .build();
        
        R<RpcStatus> createResp = client.createCollection(createParam);
        handleResponse(createResp, "createCollection");
    }
    
    /**
     * 创建索引
     */
    public void createIndex(String collectionName, String fieldName) {
        IndexParam indexParam = IndexParam.newBuilder()
            .withCollectionName(collectionName)
            .withFieldName(fieldName)
            .withIndexType(IndexType.HNSW)
            .withMetricType(MetricType.COSINE)
            .withExtraParam("{\"M\": 16, \"efConstruction\": 200}")
            .build();
        
        R<RpcStatus> resp = client.createIndex(indexParam);
        handleResponse(resp, "createIndex");
    }
    
    /**
     * 加载 Collection
     */
    public void loadCollection(String collectionName) {
        R<RpcStatus> resp = client.loadCollection(
            LoadCollectionParam.newBuilder()
                .withCollectionName(collectionName)
                .build()
        );
        handleResponse(resp, "loadCollection");
    }
    
    /**
     * 批量插入商品向量（带分批处理）
     */
    public void insertProducts(String collectionName, List<ProductEntity> products,
                               List<List<Float>> embeddings, int batchSize) {
        for (int i = 0; i < products.size(); i += batchSize) {
            int end = Math.min(i + batchSize, products.size());
            List<ProductEntity> batch = products.subList(i, end);
            List<List<Float>> embBatch = embeddings.subList(i, end);
            
            List<InsertParam.Field> fields = new ArrayList<>();
            fields.add(new InsertParam.Field("id",
                batch.stream().map(ProductEntity::getId).collect(Collectors.toList())));
            fields.add(new InsertParam.Field("title",
                batch.stream().map(ProductEntity::getTitle).collect(Collectors.toList())));
            fields.add(new InsertParam.Field("title_vec", embBatch));
            fields.add(new InsertParam.Field("price",
                batch.stream().map(ProductEntity::getPrice).collect(Collectors.toList())));
            fields.add(new InsertParam.Field("category",
                batch.stream().map(ProductEntity::getCategory).collect(Collectors.toList())));
            fields.add(new InsertParam.Field("in_stock",
                batch.stream().map(ProductEntity::getInStock).collect(Collectors.toList())));
            
            InsertParam insertParam = InsertParam.newBuilder()
                .withCollectionName(collectionName)
                .withFields(fields)
                .build();
            
            R<MutationResult> resp = client.insert(insertParam);
            handleResponse(resp, "insert batch " + (i / batchSize));
        }
    }
    
    /**
     * 向量搜索
     */
    public List<ProductSearchResult> search(String collectionName,
                                            List<Float> queryVector,
                                            int topK,
                                            String expr,
                                            List<String> outputFields) {
        SearchParam searchParam = SearchParam.newBuilder()
            .withCollectionName(collectionName)
            .withVectorFieldName("title_vec")
            .withVectors(Collections.singletonList(queryVector))
            .withParams("{\"ef\": 64}")
            .withMetricType(MetricType.COSINE)
            .withTopK(topK)
            .withExpr(expr)
            .withOutFields(outputFields)
            .build();
        
        R<SearchResults> resp = client.search(searchParam);
        handleResponse(resp, "search");
        
        // 解析结果
        SearchResultsWrapper wrapper = new SearchResultsWrapper(
            resp.getData().getResults());
        
        List<ProductSearchResult> results = new ArrayList<>();
        for (int i = 0; i < wrapper.getIDScore(0).size(); i++) {
            results.add(new ProductSearchResult(
                wrapper.getIDScore(0).get(i).getLongID(),
                wrapper.getIDScore(0).get(i).getScore(),
                (String) wrapper.getFieldData("title", 0).get(i),
                (Double) wrapper.getFieldData("price", 0).get(i),
                (String) wrapper.getFieldData("category", 0).get(i),
                (Boolean) wrapper.getFieldData("in_stock", 0).get(i)
            ));
        }
        return results;
    }
    
    /**
     * 按 ID 删除
     */
    public void deleteByIds(String collectionName, List<Long> ids) {
        String expr = "id in " + ids;
        R<MutationResult> resp = client.delete(
            DeleteParam.newBuilder()
                .withCollectionName(collectionName)
                .withExpr(expr)
                .build()
        );
        handleResponse(resp, "delete");
    }
    
    private void handleResponse(R<?> resp, String operation) {
        if (resp.getStatus() != R.Status.Success.getCode()) {
            throw new RuntimeException(
                String.format("%s failed: %s", operation, resp.getMessage()));
        }
    }
}

// ProductEntity.java — 数据实体
@Data
@AllArgsConstructor
@NoArgsConstructor
public class ProductEntity {
    private Long id;
    private String title;
    private Double price;
    private String category;
    private Boolean inStock;
}

// ProductSearchResult.java — 搜索结果
@Data
@AllArgsConstructor
public class ProductSearchResult {
    private Long id;
    private Float score;
    private String title;
    private Double price;
    private String category;
    private Boolean inStock;
}
```

```java
// ProductSearchController.java
@RestController
@RequestMapping("/api/products")
public class ProductSearchController {
    
    @Autowired
    private MilvusProductRepository repo;
    
    @Autowired
    private EmbeddingService embeddingService;  // 假设已有 Embedding 服务封装
    
    @GetMapping("/search")
    public ResponseEntity<List<ProductSearchResult>> search(
            @RequestParam String q,
            @RequestParam(defaultValue = "10") int k,
            @RequestParam(required = false) String category,
            @RequestParam(required = false) Double minPrice,
            @RequestParam(required = false) Double maxPrice) {
        
        List<Float> queryVec = embeddingService.encode(q);
        
        // 构造过滤表达式
        StringBuilder exprBuilder = new StringBuilder();
        if (category != null) exprBuilder.append("category == '").append(category).append("'");
        if (minPrice != null) appendAnd(exprBuilder).append("price >= ").append(minPrice);
        if (maxPrice != null) appendAnd(exprBuilder).append("price <= ").append(maxPrice);
        String expr = exprBuilder.length() > 0 ? exprBuilder.toString() : null;
        
        List<ProductSearchResult> results = repo.search(
            "product_search", queryVec, k, expr,
            Arrays.asList("title", "price", "category", "in_stock")
        );
        return ResponseEntity.ok(results);
    }
    
    private StringBuilder appendAnd(StringBuilder sb) {
        if (sb.length() > 0) sb.append(" and ");
        return sb;
    }
}
```

#### 步骤 2：Go Gin 实现

```go
// repository.go
package repository

import (
    "context"
    "fmt"
    "time"

    "github.com/milvus-io/milvus-sdk-go/v2/client"
    "github.com/milvus-io/milvus-sdk-go/v2/entity"
)

type MilvusProductRepo struct {
    client client.Client
}

func NewMilvusProductRepo(ctx context.Context, addr string) (*MilvusProductRepo, error) {
    c, err := client.NewClient(ctx, client.Config{
        Address:  addr,
        DialTimeout: 10 * time.Second,
    })
    if err != nil {
        return nil, fmt.Errorf("连接 Milvus 失败: %w", err)
    }
    return &MilvusProductRepo{client: c}, nil
}

func (r *MilvusProductRepo) Close() error {
    return r.client.Close()
}

// 创建 Collection
func (r *MilvusProductRepo) CreateCollection(ctx context.Context, name string, dim int) error {
    has, err := r.client.HasCollection(ctx, name)
    if err != nil {
        return fmt.Errorf("检查 Collection 失败: %w", err)
    }
    if has {
        return nil
    }

    schema := entity.NewSchema().
        WithField(entity.NewField().WithName("id").WithDataType(entity.FieldTypeInt64).WithIsPrimaryKey(true).WithAutoID(false)).
        WithField(entity.NewField().WithName("title").WithDataType(entity.FieldTypeVarChar).WithMaxLength(512)).
        WithField(entity.NewField().WithName("title_vec").WithDataType(entity.FieldTypeFloatVector).WithDim(dim)).
        WithField(entity.NewField().WithName("price").WithDataType(entity.FieldTypeFloat)).
        WithField(entity.NewField().WithName("category").WithDataType(entity.FieldTypeVarChar).WithMaxLength(64)).
        WithField(entity.NewField().WithName("in_stock").WithDataType(entity.FieldTypeBool)).
        WithDescription("商品语义搜索（Go SDK）")

    return r.client.CreateCollection(ctx, name, schema, 2)
}

// 创建索引
func (r *MilvusProductRepo) CreateIndex(ctx context.Context, collection, field string) error {
    idx, err := entity.NewIndexHNSW(entity.COSINE, 16, 200)
    if err != nil {
        return fmt.Errorf("创建索引参数失败: %w", err)
    }
    return r.client.CreateIndex(ctx, collection, field, idx, false)
}

// Load
func (r *MilvusProductRepo) LoadCollection(ctx context.Context, name string) error {
    return r.client.LoadCollection(ctx, name, false)
}

// 批量插入
func (r *MilvusProductRepo) InsertProducts(ctx context.Context, collection string,
    products []ProductEntity, embeddings [][]float32, batchSize int) error {
    
    for i := 0; i < len(products); i += batchSize {
        end := i + batchSize
        if end > len(products) {
            end = len(products)
        }
        batch := products[i:end]
        embBatch := embeddings[i:end]

        ids := make([]int64, len(batch))
        titles := make([]string, len(batch))
        prices := make([]float32, len(batch))
        categories := make([]string, len(batch))
        stocks := make([]bool, len(batch))
        vecs := make([][]float32, len(batch))

        for j, p := range batch {
            ids[j] = p.ID
            titles[j] = p.Title
            prices[j] = float32(p.Price)
            categories[j] = p.Category
            stocks[j] = p.InStock
            vecs[j] = embBatch[j]
        }

        columns := []entity.Column{
            entity.NewColumnInt64("id", ids),
            entity.NewColumnVarChar("title", titles),
            entity.NewColumnFloatVector("title_vec", dim, vecs),
            entity.NewColumnFloat("price", prices),
            entity.NewColumnVarChar("category", categories),
            entity.NewColumnBool("in_stock", stocks),
        }

        _, err := r.client.Insert(ctx, collection, "", columns...)
        if err != nil {
            return fmt.Errorf("batch[%d] 写入失败: %w", i/batchSize, err)
        }
    }
    return r.client.Flush(ctx, collection, false)
}

// 向量搜索
func (r *MilvusProductRepo) Search(ctx context.Context, collection string,
    queryVec []float32, topK int, expr string,
    outputFields []string) ([]ProductSearchResult, error) {

    sp, err := entity.NewIndexHNSWSearchParam(64)
    if err != nil {
        return nil, err
    }

    sr, err := r.client.Search(ctx, collection, nil, expr, outputFields,
        []entity.Vector{entity.FloatVector(queryVec)},
        "title_vec", entity.COSINE, topK, sp)
    if err != nil {
        return nil, fmt.Errorf("搜索失败: %w", err)
    }

    var results []ProductSearchResult
    for _, r := range sr {
        var (
            idCol    *entity.ColumnInt64
            titleCol *entity.ColumnVarChar
            priceCol *entity.ColumnFloat
            catCol   *entity.ColumnVarChar
            stockCol *entity.ColumnBool
        )
        for _, f := range r.Fields {
            switch f.Name() {
            case "id":    idCol = f.(*entity.ColumnInt64)
            case "title": titleCol = f.(*entity.ColumnVarChar)
            case "price": priceCol = f.(*entity.ColumnFloat)
            case "category": catCol = f.(*entity.ColumnVarChar)
            case "in_stock": stockCol = f.(*entity.ColumnBool)
            }
        }
        for i := 0; i < r.ResultCount; i++ {
            results = append(results, ProductSearchResult{
                ID:       idCol.Data()[i],
                Score:    r.Scores[i],
                Title:    titleCol.Data()[i],
                Price:    float64(priceCol.Data()[i]),
                Category: catCol.Data()[i],
                InStock:  stockCol.Data()[i],
            })
        }
    }
    return results, nil
}

// 数据模型
type ProductEntity struct {
    ID       int64
    Title    string
    Price    float64
    Category string
    InStock  bool
}

type ProductSearchResult struct {
    ID       int64   `json:"id"`
    Score    float32 `json:"score"`
    Title    string  `json:"title"`
    Price    float64 `json:"price"`
    Category string  `json:"category"`
    InStock  bool    `json:"in_stock"`
}
```

```go
// handler.go — Gin HTTP 接口
package handler

import (
    "net/http"
    "strconv"

    "github.com/gin-gonic/gin"
)

type SearchHandler struct {
    repo *repository.MilvusProductRepo
    emb  *EmbeddingService
}

func (h *SearchHandler) Search(c *gin.Context) {
    q := c.Query("q")
    k, _ := strconv.Atoi(c.DefaultQuery("k", "10"))
    category := c.Query("category")
    minPriceStr := c.Query("min_price")
    maxPriceStr := c.Query("max_price")

    // Embedding
    queryVec, err := h.emb.Encode(c.Request.Context(), q)
    if err != nil {
        c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
        return
    }

    // 构造过滤表达式
    var conditions []string
    if category != "" {
        conditions = append(conditions, fmt.Sprintf("category == '%s'", category))
    }
    if minPriceStr != "" {
        conditions = append(conditions, fmt.Sprintf("price >= %s", minPriceStr))
    }
    if maxPriceStr != "" {
        conditions = append(conditions, fmt.Sprintf("price <= %s", maxPriceStr))
    }
    expr := ""
    if len(conditions) > 0 {
        expr = strings.Join(conditions, " and ")
    }

    results, err := h.repo.Search(c.Request.Context(),
        "product_search", queryVec, k, expr,
        []string{"title", "price", "category", "in_stock"})
    if err != nil {
        c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
        return
    }

    c.JSON(http.StatusOK, gin.H{"results": results, "count": len(results)})
}
```

#### 步骤 3：跨语言一致性验证

```python
# verify_cross_lang.py
"""验证 Java/Go/Python 三种 SDK 搜索结果的一致性"""
from pymilvus import connections, Collection
from sentence_transformers import SentenceTransformer

connections.connect(host="localhost", port="19530")
model = SentenceTransformer("all-MiniLM-L6-v2")
collection = Collection("product_search")
collection.load()

query_text = "户外露营帐篷"
query_vec = model.encode([query_text]).tolist()

results = collection.search(
    data=query_vec, anns_field="title_vec",
    param={"metric_type": "COSINE", "params": {"ef": 64}},
    limit=5, output_fields=["id", "title", "distance"]
)

print("Python SDK 搜索结果:")
for i, hit in enumerate(results[0]):
    print(f"  #{i+1}: id={hit.id}, title={hit.entity.get('title')[:30]}, "
          f"distance={hit.distance:.6f}")

print("\n请与 Java/Go SDK 的搜索结果对比，验证:")
print("  1. TopK 结果 ID 序列是否一致")
print("  2. Distance 分值是否一致（允许 ±1e-4 浮点误差）")
print("  3. output_fields 返回的字段是否完整")
```

---

## 4. 项目总结

### 4.1 优缺点对比

| 维度 | Java SDK | Go SDK | Python SDK |
|------|----------|--------|------------|
| 并发性能 | 高（连接池 + NIO） | 最高（goroutine + gRPC） | 中（GIL 限制，但 gRPC 有 C 扩展） |
| 生态集成 | Spring Boot 完美整合 | Gin/K8s 天然适配 | FastAPI/Flask 灵活 |
| 内存效率 | 中（JVM GC 波动） | 高（无 GC 停顿） | 中 |
| 类型安全 | 强（Builder + 泛型） | 强（编译期 + 接口） | 弱（运行时） |
| 编译/部署 | 重（JAR/WAR） | 轻（单二进制） | 无需编译 |
| 学习曲线 | 中（注解 + DI） | 中（Context + errgroup） | 低 |

### 4.2 适用场景

- **Java**：企业级后端服务（Spring Boot 微服务架构），已有 JVM 基础设施和监控体系
- **Go**：搜索网关、实时数据管道、高性能中间件，K8s 原生部署
- **Python**：数据分析、AI 模型集成、快速原型验证

**不适用场景**：移动端直接调用（应通过 API 网关）、前端浏览器直接调用。

### 4.3 注意事项

- **跨语言 metric 一致性**：确保三种 SDK 使用相同的 Metric Type 和搜索参数（ef/nprobe）。
- **序列化精度**：Java 的 float 与 Go 的 float32 在极端精度下可能有差异，设置合理容差。
- **版本对齐**：Java/Go/Python SDK 的版本应与 Milvus 服务端版本对齐。

### 4.4 常见踩坑经验

1. **Java 的 Vector 类型**：Java SDK 的向量类型是 `List<Float>`，但底层序列化要求的是连续内存，大量小批次的 List 创建会引发 GC。建议预分配 ArrayList 或使用 float[]。
2. **Go 的 entity.Column**：Go SDK 在 Search 结果解析时，字段类型必须在编译期确定（`*entity.ColumnInt64`），类型断言失败会 panic。建议包一层安全的类型转换。
3. **gRPC 消息大小限制**：三种 SDK 默认的 gRPC 消息大小限制是 4MB，大批量 Insert 时可能超过。需要增大 Proxy 和 SDK 的 gRPC 消息限制。

### 4.5 思考题

1. 如果需要在 Java 服务中实现"基于用户 ID 哈希的路由"（同一用户始终访问同一个 QueryNode），应该如何与 Milvus 的 Resource Group 配合？
2. Go 的 `context.Background()` 和 `context.WithTimeout()` 在分布式 Trace 传播时有什么区别？如何在 gRPC interceptor 中自动注入 TraceID？

---

> **下一章预告**：第9章我们将深入 Milvus 的删除、更新与数据生命周期管理。读完本章，你应该能在 Java 和 Go 项目中独立接入 Milvus，并与第7章的 Python 代码形成跨语言统一方案。
