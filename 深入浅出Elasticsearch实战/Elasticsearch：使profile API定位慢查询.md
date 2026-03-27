# 背景

有时候查询的响应速度很慢，查询缓慢可能会有多种原因，如何查找原因呢？Elasticsearch 的 Profile API 允许您获取有关搜索请求执行的详细信息，包括查询的各个阶段所花费的时间。这对于性能调优和理解查询的内部工作机制非常有用。

# 开启Profile API

 **开启实例**

request请求格式示例

```
GET /my-index-000001/_search
{
  "profile": true,
  "query" : {
    "match" : { "message" : "GET /search" }
  }
}
```

或者curl请求

```
curl -X GET "localhost:9200/my-index-000001/_search?pretty" -H 'Content-Type: application/json' -d'
{
  "profile": true,
  "query" : {
    "match" : { "message" : "GET /search" }
  }
}
'
```

response响应示例

```
{
  "took": 25,
  "timed_out": false,
  "_shards": {
    "total": 1,
    "successful": 1,
    "skipped": 0,
    "failed": 0
  },
  "hits": {
    "total": {
      "value": 5,
      "relation": "eq"
    },
    "max_score": 0.17402273,
    "hits": [...] 
  },
  "profile": {
    "shards": [
      {
        "id": "[q2aE02wS1R8qQFnYu6vDVQ][my-index-000001][0]",
        "node_id": "q2aE02wS1R8qQFnYu6vDVQ",
        "shard_id": 0,
        "index": "my-index-000001",
        "cluster": "(local)",
        "searches": [
          {
            "query": [
              {
                "type": "BooleanQuery",
                "description": "message:get message:search",
                "time_in_nanos" : 11972972,
                "breakdown" : {
                  "set_min_competitive_score_count": 0,
                  "match_count": 5,
                  "shallow_advance_count": 0,
                  "set_min_competitive_score": 0,
                  "next_doc": 39022,
                  "match": 4456,
                  "next_doc_count": 5,
                  "score_count": 5,
                  "compute_max_score_count": 0,
                  "compute_max_score": 0,
                  "advance": 84525,
                  "advance_count": 1,
                  "score": 37779,
                  "build_scorer_count": 2,
                  "create_weight": 4694895,
                  "shallow_advance": 0,
                  "create_weight_count": 1,
                  "build_scorer": 7112295,
                  "count_weight": 0,
                  "count_weight_count": 0
                },
                "children": [
                  {
                    "type": "TermQuery",
                    "description": "message:get",
                    "time_in_nanos": 3801935,
                    "breakdown": {
                      "set_min_competitive_score_count": 0,
                      "match_count": 0,
                      "shallow_advance_count": 3,
                      "set_min_competitive_score": 0,
                      "next_doc": 0,
                      "match": 0,
                      "next_doc_count": 0,
                      "score_count": 5,
                      "compute_max_score_count": 3,
                      "compute_max_score": 32487,
                      "advance": 5749,
                      "advance_count": 6,
                      "score": 16219,
                      "build_scorer_count": 3,
                      "create_weight": 2382719,
                      "shallow_advance": 9754,
                      "create_weight_count": 1,
                      "build_scorer": 1355007,
                      "count_weight": 0,
                      "count_weight_count": 0
                    }
                  },
                  {
                    "type": "TermQuery",
                    "description": "message:search",
                    "time_in_nanos": 205654,
                    "breakdown": {
                      "set_min_competitive_score_count": 0,
                      "match_count": 0,
                      "shallow_advance_count": 3,
                      "set_min_competitive_score": 0,
                      "next_doc": 0,
                      "match": 0,
                      "next_doc_count": 0,
                      "score_count": 5,
                      "compute_max_score_count": 3,
                      "compute_max_score": 6678,
                      "advance": 12733,
                      "advance_count": 6,
                      "score": 6627,
                      "build_scorer_count": 3,
                      "create_weight": 130951,
                      "shallow_advance": 2512,
                      "create_weight_count": 1,
                      "build_scorer": 46153,
                      "count_weight": 0,
                      "count_weight_count": 0
                    }
                  }
                ]
              }
            ],
            "rewrite_time": 451233,
            "collector": [
              {
                "name": "QueryPhaseCollector",
                "reason": "search_query_phase",
                "time_in_nanos": 775274,
                "children" : [
                  {
                    "name": "SimpleTopScoreDocCollector",
                    "reason": "search_top_hits",
                    "time_in_nanos": 775274
                  }
                ]
              }
            ]
          }
        ],
        "aggregations": [],
        "fetch": {
          "type": "fetch",
          "description": "",
          "time_in_nanos": 660555,
          "breakdown": {
            "next_reader": 7292,
            "next_reader_count": 1,
            "load_stored_fields": 299325,
            "load_stored_fields_count": 5,
            "load_source": 3863,
            "load_source_count": 5
          },
          "debug": {
            "stored_fields": ["_id", "_routing", "_source"]
          },
          "children": [
            {
              "type": "FetchSourcePhase",
              "description": "",
              "time_in_nanos": 20443,
              "breakdown": {
                "next_reader": 745,
                "next_reader_count": 1,
                "process": 19698,
                "process_count": 5
              },
              "debug": {
                "fast_path": 5
              }
            },
            {
              "type": "StoredFieldsPhase",
              "description": "",
              "time_in_nanos": 5310,
              "breakdown": {
                "next_reader": 745,
                "next_reader_count": 1,
                "process": 4445,
                "process_count": 5
              }
            }
          ]
        }
      }
    ]
  }
}
```

**解析响应**

当 Profile API 被启用时，响应将包含一个 "profile" 字段，其中包含有关查询性能的详细信息。这些信息包括：

- shards: 包含每个分片的分析信息。
- searches: 每个分片中的搜索请求分析。
- query: 有关查询执行的详细信息，包括类型、描述、耗时等。
- rewrite_time: 查询重写阶段的耗时。
- collector: 结果收集阶段的信息。

# 小结

- Profile API 可能会引入额外的性能开销，因此不建议在生产环境中频繁使用。
- 性能分析结果不包括网络延迟或请求排队时间。
- Profile API 是一个实验性功能，其行为在未来版本中可能会有所变化

参考资料

【1】https://www.elastic.co/guide/en/elasticsearch/reference/current/search-profile.html
