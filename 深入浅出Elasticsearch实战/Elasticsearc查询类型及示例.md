# 背景

Elasticsearch提供了多种查询类型，每种类型都适用于不同的搜索场景。

# 使用实例

以下是一些常见的查询类型及其使用场景：

**Match Query**:

使用场景：用于全文搜索，可以找到包含给定单词或短语的文档。它对文本进行分词，然后搜索每个分词，最后将结果合并。

```
{
  "query": {
    "match": {
      "content": "Elasticsearch search"
    }
  }
}
```

**Match Phrase Query**:

使用场景：当你需要找到包含特定顺序的词组时使用。它确保文档中的词与查询中词的顺序相匹配。

```
{
  "query": {
    "match_phrase": {
      "content": {
        "query": "Elasticsearch guide",
        "slop": 2
      }
    }
  }
}
```

**Term Query**:

使用场景：用于精确匹配，比如搜索特定单词的文档，它不进行分词，适合用于精确值字段，如数字、日期或未分词的字符串。

```
{
  "query": {
    "term": {
      "status": "active"
    }
  }
}
```

**Terms Query**:

使用场景：类似于Term Query，但它允许你搜索包含任意给定列表中的术语的文档。

```
{
  "query": {
    "terms": {
      "tags": ["search", "analytics"]
    }
  }
}
```

**Range Query**:

使用场景：用于基于数值或日期的范围搜索，比如找到价格在100到500之间的产品。

```
{
  "query": {
    "range": {
      "price": {
        "gte": 100,
        "lte": 500
      }
    }
  }
}
```

**Prefix Query**:

使用场景：用于搜索以特定前缀开始的文档，适合进行自动完成或搜索相似项。

```
{
  "query": {
    "prefix": {
      "product": "apple"
    }
  }
}
```

**Wildcard Query**:

使用场景：支持使用通配符（*和?）进行搜索，适用于当你知道搜索词的一部分但不知道全部时。

```
{
  "query": {
    "wildcard": {
      "product": "he??o*"
    }
  }
}
```

**Regexp Query**:

使用场景：使用正则表达式进行复杂模式的搜索，适合进行模式匹配。

```
{
  "query": {
    "regexp": {
      "email": ".*\\@example.*"
    }
  }
}
```

**Bool Query**:

使用场景：组合多个查询，可以同时执行多个查询条件，使用must, should, must_not和filter子句来精细控制查询逻辑。

```
{
  "query": {
    "bool": {
      "must": [
        { "match": { "content": "Elasticsearch" } }
      ],
      "filter": [
        { "term": { "status": "active" } }
      ],
      "must_not": [
        { "match": { "content": "buggy" } }
      ]
    }
  }
}
```

**Fuzzy Query**:

使用场景：当搜索拼写错误的单词时使用，允许一定量的拼写错误。

```
{
  "query": {
    "fuzzy": {
      "like_this": {
        "value": "Elasticsearch",
        "fuzziness": "AUTO"
      }
    }
  }
}
```

**Common Terms Query**:

使用场景：在文本中搜索常见词，同时考虑低频词，适合平衡效率和相关性。

```
{
  "query": {
    "common_terms": {
      "body": {
        "query": "Elasticsearch is powerful",
        "cutoff_frequency": 1
      }
    }
  }
}
```

**Constant Score Query**:

使用场景：用于在过滤条件下包装查询，以保持过滤的性能优势，同时应用查询条件。

```
{
  "query": {
    "constant_score": {
      "filter": {
        "term": {
          "status": "active"
        }
      }
    }
  }
}
```

**Function Score Query**:

使用场景：修改查询的得分，根据某些字段值或脚本对得分进行加权。

```
{
  "query": {
    "function_score": {
      "query": {
        "match_all": {}
      },
      "functions": [
        {
          "filter": { "match": { "status": "premium" }},
          "weight": 5
        }
      ],
      "score_mode": "sum",
      "boost_mode": "multiply"
    }
  }
}
```

**Span Queries**:

使用场景：用于更精细的短语搜索，如span_term, span_multi, span_near等，它们允许在更细的粒度上进行短语匹配。

```
{
  "query": {
    "span_term": {
      "field": {
        "value": "Elasticsearch"
      }
    }
  }
}
```

**Geo-Shape Queries**:

使用场景：用于地理空间搜索，比如根据地理位置过滤结果。

```
{
  "query": {
    "geo_shape": {
      "location": {
        "shape": {
          "type": "polygon",
          "coordinates": [
            [
              [100.0, 1.0],
              [101.0, 1.0],
              [101.0, 0.0],
              [100.0, 0.0],
              [100.0, 1.0]
            ]
          ]
        },
        "relation": "within"
      }
    }
  }
}
```

**More Like This Query**:

使用场景：基于一个或多个文档找到相似的文档。

```
{
  "query": {
    "more_like_this": {
      "fields": ["title", "description"],
      "like": ["Elasticsearch"],
      "min_term_freq": 1,
      "max_query_terms": 12
    }
  }
}
```

**Script Query**:

使用场景：使用脚本进行复杂的查询逻辑，可以基于自定义的脚本逻辑来搜索文档。

```
{
  "query": {
    "script": {
      "script": {
        "source": "doc['price'].value > 100"
      }
    }
  }
}
```

# 总结

每种查询类型都有其特定的应用场景，选择正确的查询类型对于实现高效的搜索至关重要。在实际应用中，你可能需要根据具体需求和数据的特点来选择最合适的查询方式。
