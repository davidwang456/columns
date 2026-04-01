# 第06章 Analyzer 入门：分词链路与中文检索

## 背景

当我们在 Elasticsearch 中执行一次 `match` 查询时，查询文本和索引中的文档都需要经过"分词"处理。分词（Analysis）是全文检索的基石——它决定了一段文本会被拆解成哪些词项（Term），而词项是倒排索引的最小单元。

对于英文，按空格和标点拆分通常就能得到不错的效果。但对于中文，"软件工程师"是一个词还是"软件"+"工程师"两个词？"中华人民共和国"应该怎么拆？这就需要专门的中文分词器来处理。

理解 Analyzer 的工作原理，对于写出正确的 Mapping、排查"搜不到"的问题、以及优化检索效果都至关重要。

## 本章目标

1. 理解 Analyzer 的三段式架构：Character Filters -> Tokenizer -> Token Filters
2. 掌握内置 Analyzer 的特点与适用场景
3. 学会使用 `_analyze` API 调试分词结果
4. 理解中文分词的必要性，掌握 IK 分词器的使用
5. 理解索引分词器与搜索分词器分离的策略
6. 能够自定义 Analyzer 满足业务需求

---

## 1. 分词链路三段架构

Elasticsearch 的 Analyzer 由三个阶段组成，每段各司其职：

```
原始文本
   |
   v
+-------------------+
| Character Filters  |  字符预处理（可选，0个或多个）
+-------------------+
   |
   v
+-------------------+
|    Tokenizer       |  分词器（必选，只能1个）
+-------------------+
   |
   v
+-------------------+
|  Token Filters     |  词元后处理（可选，0个或多个）
+-------------------+
   |
   v
词项列表 [term1, term2, term3, ...]
```

### 1.1 Character Filters（字符过滤器）

在分词之前对原始文本进行字符级别的预处理。

- **html_strip**：去除 HTML 标签，将 `<p>Hello</p>` 转为 `Hello`
- **mapping**：字符映射替换，如将 `:)` 替换为 `_happy_`
- **pattern_replace**：基于正则表达式的字符替换

### 1.2 Tokenizer（分词器）

将经过字符过滤的文本拆分为词元（Token）。每个 Analyzer 有且只能有一个 Tokenizer。

- **standard**：按 Unicode 文本分段算法拆词，去除标点。对英文效果好，对中文会拆成单字
- **whitespace**：仅按空白字符拆分，不做任何其他处理
- **keyword**：不分词，将整个输入作为一个词元输出
- **pattern**：基于正则表达式拆分
- **ik_smart / ik_max_word**：IK 中文分词器提供的两种模式（需安装插件）

### 1.3 Token Filters（词元过滤器）

对 Tokenizer 输出的词元进行后处理。

- **lowercase**：转为小写
- **stop**：移除停用词（如 "the", "a", "is"）
- **synonym**：同义词扩展
- **stemmer**：词干提取（如 "running" -> "run"）
- **ngram / edge_ngram**：生成子串词元，用于前缀搜索或自动补全

---

## 2. 内置 Analyzer 一览

Elasticsearch 提供了多个开箱即用的 Analyzer：

### 2.1 standard（默认）

组成：Standard Tokenizer + Lower Case Token Filter + Stop Token Filter（默认禁用）。

按 Unicode 文本分段算法拆词并转为小写。对英文效果好，对中文会逐字拆分。

```
输入: "The Quick Brown Fox"
输出: ["the", "quick", "brown", "fox"]

输入: "软件工程师"
输出: ["软", "件", "工", "程", "师"]  -- 逐字拆分，不理想
```

适用场景：英文或多语言混合文本的通用处理。

### 2.2 simple

组成：Lower Case Tokenizer（按非字母字符拆分并转为小写）。

```
输入: "Hello-World 123"
输出: ["hello", "world"]  -- 数字被丢弃
```

适用场景：只关心字母内容的简单场景。

### 2.3 whitespace

组成：Whitespace Tokenizer。

仅按空格拆分，不转小写、不去标点。

```
输入: "The Quick Brown-Fox"
输出: ["The", "Quick", "Brown-Fox"]
```

适用场景：对原始文本格式敏感的场景，如日志分析。

### 2.4 keyword

组成：Keyword Tokenizer。

不做任何拆分，将整个输入视为一个词元。

```
输入: "New York City"
输出: ["New York City"]
```

适用场景：不需要分词的字段（通常直接使用 `keyword` 字段类型更合适）。

---

## 3. _analyze API：调试分词结果

`_analyze` API 是排查分词问题的核心工具。

### 3.1 指定 Analyzer

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_analyze?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "analyzer": "standard",
  "text": "Elasticsearch 全文检索入门"
}'
```

### 3.2 指定 Tokenizer 和 Filter

可以单独指定 Tokenizer 和 Token Filters 的组合，不依赖预定义的 Analyzer：

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_analyze?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "tokenizer": "standard",
  "filter": ["lowercase", "stop"],
  "text": "The Quick Brown Fox Jumps"
}'
```

### 3.3 使用索引中已定义的 Analyzer

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/my_index/_analyze?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "field": "title",
  "text": "软件工程师招聘"
}'
```

这将使用 `my_index` 中 `title` 字段配置的 Analyzer 进行分词，是调试映射问题时最直接的方式。

---

## 4. 中文分词详解

### 4.1 为什么需要中文分词

英文文本有天然的空格边界，按空格拆分即可得到有意义的词。中文文本中字与字连续书写，没有空格分隔。`standard` Analyzer 对中文的处理是逐字拆分，"软件工程师"会变成 ["软", "件", "工", "程", "师"]，这五个单字几乎不携带任何语义信息。

中文分词的目标是将连续的汉字序列切分成有意义的词语："软件工程师" -> ["软件", "工程师"] 或 ["软件", "工程", "工程师"]。

### 4.2 IK 分词器

IK 分词器是最流行的 Elasticsearch 中文分词插件，提供两种分词模式：

- **ik_smart**：粗粒度分词，做最少切分。优先输出最长的词
- **ik_max_word**：细粒度分词，做最多切分。将文本中所有可能的词都切出来

### 4.3 其他中文分词器

- **smartcn**（analysis-smartcn）：Apache 官方提供的中文分词插件，基于隐马尔可夫模型
- **icu_analyzer**（analysis-icu）：基于 ICU 标准的分词和文本处理，支持多语言

### 4.4 分词效果对比

以"软件工程师"为例：

| Analyzer | 分词结果 |
|----------|---------|
| standard | ["软", "件", "工", "程", "师"] |
| ik_smart | ["软件工程师"] |
| ik_max_word | ["软件工程师", "软件", "工程师", "工程", "师"] |

以"中华人民共和国"为例：

| Analyzer | 分词结果 |
|----------|---------|
| standard | ["中", "华", "人", "民", "共", "和", "国"] |
| ik_smart | ["中华人民共和国"] |
| ik_max_word | ["中华人民共和国", "中华人民", "中华", "华人", "人民共和国", "人民", "共和国", "共和", "国"] |

可以看到 `ik_max_word` 会穷举所有可能的组合，召回率高但也更占用存储空间。

---

## 5. 索引分词器 vs 搜索分词器

### 5.1 核心思想

索引时和搜索时可以使用不同的 Analyzer。这是一种常用的优化策略：

- **索引时使用 ik_max_word**（细粒度）：将文本拆成尽可能多的词项，确保不遗漏任何可能的匹配
- **搜索时使用 ik_smart**（粗粒度）：将查询词做最少切分，提高搜索的精确性

举例：文档内容"中华人民共和国"，用 `ik_max_word` 索引后，无论用户搜"中华"、"人民"还是"共和国"都能命中。而搜索时用 `ik_smart`，用户输入"中华人民共和国"只会被切为一个词项，避免因过度切分导致的噪声。

### 5.2 配置方式

```json
{
  "mappings": {
    "properties": {
      "title": {
        "type": "text",
        "analyzer": "ik_max_word",
        "search_analyzer": "ik_smart"
      }
    }
  }
}
```

- `analyzer`：控制索引时使用的分词器
- `search_analyzer`：控制搜索时使用的分词器。如不指定，默认与 `analyzer` 相同

---

## 6. 自定义 Analyzer

当内置 Analyzer 无法满足业务需求时，可以在索引的 `settings` 中定义自定义 Analyzer。

### 6.1 基本结构

自定义 Analyzer 需要指定 `type: "custom"`，然后配置 Tokenizer 和 Token Filters：

```json
{
  "settings": {
    "analysis": {
      "analyzer": {
        "my_custom_analyzer": {
          "type": "custom",
          "tokenizer": "ik_max_word",
          "filter": ["lowercase", "my_stop", "my_synonym"]
        }
      },
      "filter": {
        "my_stop": {
          "type": "stop",
          "stopwords": ["的", "了", "在", "是", "我"]
        },
        "my_synonym": {
          "type": "synonym",
          "synonyms": [
            "手机,移动电话,手持电话",
            "笔记本,笔电,laptop"
          ]
        }
      }
    }
  }
}
```

### 6.2 带 Character Filter 的自定义 Analyzer

```json
{
  "settings": {
    "analysis": {
      "char_filter": {
        "html_cleaner": {
          "type": "html_strip"
        }
      },
      "analyzer": {
        "clean_html_analyzer": {
          "type": "custom",
          "char_filter": ["html_cleaner"],
          "tokenizer": "ik_smart",
          "filter": ["lowercase"]
        }
      }
    }
  }
}
```

---

## 7. 同义词

### 7.1 Synonym Token Filter

同义词通过 Token Filter 实现，有两种配置方式。

内联配置：

```json
{
  "filter": {
    "my_synonym": {
      "type": "synonym",
      "synonyms": [
        "手机,移动电话",
        "电脑,计算机,PC"
      ]
    }
  }
}
```

文件配置：

```json
{
  "filter": {
    "my_synonym": {
      "type": "synonym",
      "synonyms_path": "analysis/synonyms.txt"
    }
  }
}
```

### 7.2 同义词文件格式

同义词文件放在 Elasticsearch 的 `config` 目录下，每行一组同义词：

```
手机,移动电话,手持电话
电脑,计算机,PC,computer
笔记本,笔电,laptop
```

也支持单向映射格式：

```
laptop => 笔记本,笔电
PC => 电脑,计算机
```

### 7.3 注意事项

同义词的一个重要风险：如果同义词配置在索引时的 Analyzer 中，修改同义词规则后需要重建索引才能生效。为了避免这个问题，可以将同义词仅配置在 `search_analyzer` 中，这样修改同义词后只需要重启节点（或使用 `synonym_graph` filter 的 `updateable: true` 配置实现热更新），无需重建索引。

```json
{
  "mappings": {
    "properties": {
      "title": {
        "type": "text",
        "analyzer": "ik_max_word",
        "search_analyzer": "my_search_analyzer_with_synonyms"
      }
    }
  }
}
```

---

## 8. 总结

- Analyzer 由 Character Filters、Tokenizer、Token Filters 三部分组成
- 中文文本必须使用专门的中文分词器（如 IK），`standard` Analyzer 只会逐字拆分
- `ik_smart` 做最少切分（粗粒度），`ik_max_word` 做最多切分（细粒度）
- 推荐策略：索引时用 `ik_max_word` 提高召回，搜索时用 `ik_smart` 提高精确性
- `_analyze` API 是调试分词问题的核心工具
- 自定义 Analyzer 可以组合不同的 Tokenizer 和 Token Filters 满足业务需求
- 同义词建议配置在搜索分词器中，避免修改后需要重建索引

---

## 9. 练习题

1. Analyzer 的三段架构分别是什么？各自的作用是什么？
2. `standard` Analyzer 对中文文本"数据库管理系统"的分词结果是什么？为什么不理想？
3. `ik_smart` 和 `ik_max_word` 的区别是什么？分别在什么场景下使用？
4. 为什么推荐索引时用 `ik_max_word`、搜索时用 `ik_smart`？请举例说明。
5. 同义词配置在索引分词器 vs 搜索分词器中各有什么优缺点？

---

## 10. 实战（curl）

### 10.1 对比不同分词器的效果

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_analyze?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "analyzer": "standard",
  "text": "Elasticsearch是一个分布式搜索引擎"
}'

curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_analyze?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "analyzer": "ik_smart",
  "text": "Elasticsearch是一个分布式搜索引擎"
}'

curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_analyze?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "analyzer": "ik_max_word",
  "text": "Elasticsearch是一个分布式搜索引擎"
}'
```

### 10.2 创建自定义 Analyzer 索引

```bash
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/articles" \
  -H 'Content-Type: application/json' -d '
{
  "settings": {
    "analysis": {
      "filter": {
        "my_stop": {
          "type": "stop",
          "stopwords": ["的", "了", "在", "是"]
        },
        "my_synonym": {
          "type": "synonym",
          "synonyms": [
            "搜索引擎,检索引擎,search engine",
            "数据库,DB,database"
          ]
        }
      },
      "analyzer": {
        "my_ik_analyzer": {
          "type": "custom",
          "tokenizer": "ik_max_word",
          "filter": ["lowercase", "my_stop"]
        },
        "my_search_analyzer": {
          "type": "custom",
          "tokenizer": "ik_smart",
          "filter": ["lowercase", "my_stop", "my_synonym"]
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "title": {
        "type": "text",
        "analyzer": "my_ik_analyzer",
        "search_analyzer": "my_search_analyzer"
      },
      "content": {
        "type": "text",
        "analyzer": "my_ik_analyzer",
        "search_analyzer": "my_search_analyzer"
      },
      "category": {
        "type": "keyword"
      }
    }
  }
}'
```

### 10.3 验证自定义 Analyzer

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/articles/_analyze?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "field": "title",
  "text": "Elasticsearch是一个强大的搜索引擎"
}'
```

### 10.4 写入测试数据并搜索

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/articles/_bulk?refresh=true" \
  -H 'Content-Type: application/json' -d '
{"index":{"_id":"1"}}
{"title":"Elasticsearch搜索引擎入门","content":"Elasticsearch是当前最流行的分布式搜索引擎","category":"技术"}
{"index":{"_id":"2"}}
{"title":"数据库设计与优化","content":"关系型数据库的索引设计是性能优化的关键","category":"技术"}
{"index":{"_id":"3"}}
{"title":"全文检索技术概述","content":"全文检索引擎在信息检索领域有广泛应用","category":"综述"}
'

curl -u "$ES_USER:$ES_PASS" -X GET "$ES_URL/articles/_search?pretty" \
  -H 'Content-Type: application/json' -d '
{
  "query": {
    "match": {
      "content": "检索引擎"
    }
  }
}'
```

---

## 11. 实战（Java SDK）

```java
import co.elastic.clients.elasticsearch.ElasticsearchClient;
import co.elastic.clients.elasticsearch.indices.*;
import co.elastic.clients.elasticsearch.core.SearchResponse;

// 使用 _analyze API 调试分词
AnalyzeResponse analyzeResponse = client.indices().analyze(a -> a
    .analyzer("ik_smart")
    .text("Elasticsearch是一个分布式搜索引擎")
);

for (AnalyzeToken token : analyzeResponse.tokens()) {
    System.out.println("token=" + token.token()
        + " start=" + token.startOffset()
        + " end=" + token.endOffset()
        + " position=" + token.position());
}

// 创建带自定义 Analyzer 的索引
client.indices().create(c -> c
    .index("articles_java")
    .settings(s -> s
        .analysis(a -> a
            .filter("my_stop", f -> f.definition(d -> d
                .stop(st -> st.stopwords("的", "了", "在", "是"))))
            .analyzer("my_ik_analyzer", an -> an.custom(cu -> cu
                .tokenizer("ik_max_word")
                .filter("lowercase", "my_stop")))
            .analyzer("my_search_analyzer", an -> an.custom(cu -> cu
                .tokenizer("ik_smart")
                .filter("lowercase", "my_stop")))
        )
    )
    .mappings(m -> m
        .properties("title", p -> p.text(t -> t
            .analyzer("my_ik_analyzer")
            .searchAnalyzer("my_search_analyzer")))
        .properties("content", p -> p.text(t -> t
            .analyzer("my_ik_analyzer")
            .searchAnalyzer("my_search_analyzer")))
        .properties("category", p -> p.keyword(k -> k))
    )
);

// 搜索
SearchResponse<Map> searchResponse = client.search(s -> s
    .index("articles_java")
    .query(q -> q
        .match(m -> m
            .field("content")
            .query("检索引擎")
        )
    ),
    Map.class
);

for (var hit : searchResponse.hits().hits()) {
    System.out.println("id=" + hit.id() + " score=" + hit.score());
}
```
