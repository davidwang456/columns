# 背景

# 第06章 Analyzer 入门：分词链路与中文检索

中文检索最容易出现“看起来词很像却搜不到”。根因通常不在查询 API，而在分词链路。理解 Analyzer 才能解释召回率与准确率之间的取舍。

## 本章目标
- 理解分词如何决定检索效果。
- 掌握中文场景下的基础分词策略。

## 1. 分词链路是什么
Analyzer 通常包含三段：
- 字符过滤（Character Filters）
- 分词器（Tokenizer）
- 词元过滤（Token Filters）

最终产出的 token 决定“能搜到什么”。

## 2. 为什么中文更要重视分词
中文没有天然空格分词，错误分词会导致：
- 召回不足（搜不到）
- 误召回过多（噪声高）

常见方案是 IK、smartcn、icu 等组合。

## 3. 索引分词器与搜索分词器
- 索引时分词：决定存入索引的词元。
- 查询时分词：决定用户输入如何拆词。

两者不一致时，可能出现“看似合理却搜不到”。

## 4. 进阶关注点
- 同义词会显著影响召回与排序。
- 停用词策略会影响精度与结果解释性。
- 分词策略变更应配合灰度与回滚。

# 总结
- 分词是搜索质量的地基，不是后期小优化。
- 中文检索先做可解释，再做复杂策略。

## 练习题
1. 用 `_analyze` 比较两种中文分词器对同一句话的结果。  
2. 设计一个商品标题字段的索引分词与搜索分词方案。  
3. 给“苹果手机”设计同义词规则并说明风险。  

## 实战（curl）

```bash
# 标准分词
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_analyze?pretty" \
  -H "Content-Type: application/json" \
  -d '{"analyzer":"standard","text":"软件工程师"}'

# IK 分词（如已安装）
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_analyze?pretty" \
  -H "Content-Type: application/json" \
  -d '{"analyzer":"ik_max_word","text":"软件工程师"}'
```

## 实战（Java SDK）

```java
var analyzeResp = client.indices().analyze(a -> a
    .analyzer("standard")
    .text("软件工程师"));
analyzeResp.tokens().forEach(t -> System.out.println(t.token()));
```

