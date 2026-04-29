# 第 29 章：EmbeddingModel —— 向量从哪来

## 1. 项目背景

### 业务场景（拟真）

RAG 检索效果的上限很大程度上由 **嵌入模型（EmbeddingModel）** 决定。不同嵌入模型在语言支持（中文 vs 英文）、维度（384 vs 768 vs 1536）、量化精度（FP32 vs FP16 vs INT8）上的差异直接影响召回质量。团队需要决定：用本地 ONNX 模型（省成本、数据不出域）还是用云端 Embedding API（精度更高但按量计费）？

### 痛点放大

最容易踩的坑是：**线上用的嵌入模型和索引用的是同一版吗？** 切换嵌入模型通常意味着 **全量重建索引**——因为不同模型生成的向量不在同一「语义空间」中。如果存的时候用的是模型 A、搜的时候用的是模型 B——结果基本不可用。此外，量化模型省资源但影响召回率，多语言模型的选择直接影响中文文档的检索质量。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：嵌入模型是不是就像给每句话拍一张「身份证照片」——不同照相馆拍出来的照片风格不一样，但都是同一个人？

**大师**：身份证的比喻很妙——关键就在于「不同照相馆拍出来风格不一样」这一句。如果你用 A 照相馆（模型 A）拍了 100 张照片（向量），然后用 B 照相馆（模型 B）的相机来识别新来的人——因为光线、角度、修图风格不同，识别率大打折扣。这就是为什么 **换嵌入模型必须重建索引**。所以 embedding 模型版本要写入每个 segment 的 metadata——线上出了问题你才能说「这些向量是 v1.0 模型算的，那个是 v2.0 的，我们得先确认是不是模型版本不一致导致的问题」。

**小白**：量化模型（INT8）比全精度模型（FP32）省内存、算得快，但牺牲多少召回率？怎么判断值不值得？

**大师**：量化的召回损失 **不能用感觉判断，要靠离线评测**。拿一个黄金测试集——500 条 query + 对应的正确答案文档——分别用全精度和量化模型跑一遍检索，对比 Recall@K 的差距。如果 Recall@K 下降了不到 1%，但推理速度提升了 2 倍、内存用量降了 75%——那量化就是值得的。如果下降了 5% 以上，你的业务不能接受这个精度损失——那就用全精度。**技术映射**：**embedding 模型版本和索引版本必须写进 segment 的 metadata——这是线上排障时回答「这批向量是用哪个模型算的」的唯一凭据；没有版本号，出了问题就只能全量重建**。

---

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/rag-examples
export OPENAI_API_KEY="sk-your-key-here"
```

### 分步实现

#### 步骤 1：比较本地与云端嵌入模型

```java
// 本地 ONNX 模型（Quantized，维度 384）
EmbeddingModel localModel = new BgeSmallEnV15QuantizedEmbeddingModel();

// 云端 API（全文精度，维度 1536）
EmbeddingModel cloudModel = new OpenAiEmbeddingModel.builder()
        .apiKey(System.getenv("OPENAI_API_KEY"))
        .modelName("text-embedding-3-small")
        .build();

String testQuery = "return policy for damaged items";
String testDoc = "Our return policy allows returns within 30 days of purchase.";

// 对比维度
Embedding localEmbed = localModel.embed(testQuery).content();
Embedding cloudEmbed = cloudModel.embed(testQuery).content();

System.out.println("Local model dimension:  " + localEmbed.dimension());   // 384
System.out.println("Cloud model dimension: " + cloudEmbed.dimension());     // 1536

// 对比编码同一段文档的向量
Embedding localDocEmbed = localModel.embed(testDoc).content();
Embedding cloudDocEmbed = cloudModel.embed(testDoc).content();

// 计算余弦相似度（验证两个模型的语义空间是否一致）
double localSimilarity = cosineSimilarity(localEmbed, localDocEmbed);
double cloudSimilarity = cosineSimilarity(cloudEmbed, cloudDocEmbed);

System.out.println("Local model similarity:  " + localSimilarity);
System.out.println("Cloud model similarity: " + cloudSimilarity);
```

#### 步骤 2：量化对比实验

```java
// 全精度 vs 量化：用黄金测试集算 Recall@K
// 伪代码逻辑
List<Query> goldenSet = loadGoldenSet();  // 500 条 query + 对应 doc

int recallAt5_FP32 = evaluate(localModelFP32, goldenSet, 5);
int recallAt5_INT8 = evaluate(localModelINT8, goldenSet, 5);

System.out.println("Recall@5 FP32: " + recallAt5_FP32 + "%");
System.out.println("Recall@5 INT8: " + recallAt5_INT8 + "%");
System.out.println("Recall drop:   " + (recallAt5_FP32 - recallAt5_INT8) + "%");
```

#### 步骤 3：版本号写入 metadata

```java
// ingest 时写入 embedding 版本
String EMBEDDING_MODEL_VERSION = "bge-small-en-v1.5-quantized:v2";

segment.metadata().put("embeddingModelVersion", EMBEDDING_MODEL_VERSION);
// 后续排障时，可以查某条 segment 是用哪个版本的模型编码的
```

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 存搜模型不一致 | 召回的结果几乎随机 | 索引 metadata 中记下模型版本，上线前校验一致性 |
| 维度不匹配 | 启动报错 | 换模型必须重建索引 |
| 多语言嵌入用错 | 中文文档召回率极低 | 用多语言嵌入模型 |

### 测试验证

```bash
# 黄金测试集：500 条 query + 标准答案
# 对比两个模型的 Recall@K、延迟、成本
```

### 完整代码清单

`embeddings/` 目录下各模块 README、`Naive_RAG_Example` 中的 `EmbeddingModel` 构造。

---

## 4. 项目总结

### 优点与缺点

| 维度 | 本地 ONNX embed | 云端 embed API | 与 chat 同厂商 |
|------|----------------|---------------|--------------|
| 成本/延迟 | 可控（一次投入） | 按量计费 | 视套餐 |
| 合规 | 易（数据不出域） | 难（数据出境评估） | 视区域 |
| 召回精度 | 中（受限于量化） | 高 | 中 |

### 适用场景

- 大批量历史索引（本地 embed 省 API 费用）
- 数据不出域要求（本地模型）

### 不适用场景

- 极小文档量（只需少量 embed，用更快）
- 要求最高召回精度（云端模型通常更优）

### 常见踩坑

1. 中英混用错选纯英文模型 → 中文文档召回差
2. embed 与 chat 语言不一致 → 检索好但生成差
3. 无版本号 → 线上问题无法追溯

### 进阶思考题

1. embedding 与 chat 不同厂商时，幻觉风险在哪？
2. CPU pinning 在 K8s 内对批嵌入吞吐的影响？

### 推广计划提示

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 19 章 → 本章 → 第 30 章 | 模型版本入 metadata |
| 运维 | 镜像内模型文件缓存 | CPU limit 与批任务 |
| 测试 | 黄金集 | 阈值回归 |