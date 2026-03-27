# 背景

Suggesters基本的运作原理是将输入的文本分解为token，然后在索引的字典里查找相似的term并返回。 根据使用场景的不同，Elasticsearch里设计了4种类别的Suggester，分别是:

•Term Suggester  
•Completion Suggester  
•Phrase Suggester  
•Context Suggester

suggest（提示）：completion suggest，常叫做自动完成（auto completion）， 其他叫法搜索推荐、搜索提示

比如说我们在百度，搜索，你现在搜索“大话西游”，百度自动给你提示“大话西游电影”、“大话西游小说”、 “大话西游手游”，不用你把所有你想要输入的文本都输入完，搜索引擎会自动提示你可能是你想要搜索的那个文本。

# Elasticsearch提供的 completion suggest

**定义数据结构**

示例：

```
PUT /news_website
{
  "mappings": {
    "news" : {
      "properties" : {
        "title" : {
          "type": "text",
          "analyzer": "ik_max_word",
          "fields": {
            "suggest" : {
              "type" : "completion",
              "analyzer": "ik_max_word"
            }
          }
        },
        "content": {
          "type": "text",
          "analyzer": "ik_max_word"
        }
      }
    }
  }
}
```

> suggest.type=completion

一种用于前缀搜索的特殊数据结构，不是我们之前利用的倒排索引，会全部放在内存中，所以 auto completion 进行的前缀搜索提示，性能是非常高的。

**Mock数据**

> 插入数据

```
PUT /news_website/news/1
{
  "title": "大话西游电影",
  "content": "大话西游的电影时隔20年即将在2017年4月重映"
}
PUT /news_website/news/2
{
  "title": "大话西游小说",
  "content": "某知名网络小说作家已经完成了大话西游同名小说的出版"
}
PUT /news_website/news/3
{
  "title": "大话西游手游",
  "content": "网易游戏近日出品了大话西游经典IP的手游，正在火爆内测中"
}
```

> 搜索

```
GET /news_website/news/_search
{
  "suggest": {
    "my-suggest" : {
      "prefix" : "大话西游",
      "completion" : {
        "field" : "title.suggest"
      }
    }
  }
}

        Copied!
```

响应数据

```
{
  "took": 3,
  "timed_out": false,
  "_shards": {
    "total": 5,
    "successful": 5,
    "failed": 0
  },
  "hits": {
    "total": 0,
    "max_score": 0,
    "hits": []
  },
  "suggest": {
    "my-suggest": [
      {
        "text": "大话西游",
        "offset": 0,
        "length": 4,
        "options": [
          {
            "text": "大话西游小说",
            "_index": "news_website",
            "_type": "news",
            "_id": "2",
            "_score": 1,
            "_source": {
              "title": "大话西游小说",
              "content": "某知名网络小说作家已经完成了大话西游同名小说的出版"
            }
          },
          {
            "text": "大话西游手游",
            "_index": "news_website",
            "_type": "news",
            "_id": "3",
            "_score": 1,
            "_source": {
              "title": "大话西游手游",
              "content": "网易游戏近日出品了大话西游经典IP的手游，正在火爆内测中"
            }
          },
          {
            "text": "大话西游电影",
            "_index": "news_website",
            "_type": "news",
            "_id": "1",
            "_score": 1,
            "_source": {
              "title": "大话西游电影",
              "content": "大话西游的电影时隔20年即将在2017年4月重映"
            }
          }
        ]
      }
    ]
  }
}
```

**代码实现**

```
        // 构建SearchRequest、SearchSourceBuilder 指定查询的库
        // SearchRequest searchRequest = new SearchRequest(ESConst.ES_INDEX);
        SearchRequest searchRequest = new SearchRequest("testdate");
        SearchSourceBuilder searchSourceBuilder = new SearchSourceBuilder();

        // 控制显示内容 (优化查询效率将所有无关查询提示字段都不显示)
        String[] excludeFields = new String[] {"doc_number","doc_type","attachment","doc_keywords","id","pubdate","doc_name"};
        String[] includeFields = new String[] {""};
        searchSourceBuilder.fetchSource(includeFields, excludeFields);

        // 构建completionSuggestionBuilder传入查询的参数
        CompletionSuggestionBuilder completionSuggestionBuilder = SuggestBuilders.completionSuggestion(suggestField).prefix(suggestValue).size(10);
        SuggestBuilder suggestBuilder = new SuggestBuilder();
        // 定义查询的suggest名称
        suggestBuilder.addSuggestion(suggestField+"_suggest", completionSuggestionBuilder);
        searchSourceBuilder.suggest(suggestBuilder);
        searchRequest.source(searchSourceBuilder);

        // 执行查询
        SearchResponse searchResponse = restHighLevelClient.search(searchRequest, RequestOptions.DEFAULT);
        // 获取查询的结果
        Suggest suggest = searchResponse.getSuggest();
```

# 总结

Completion Suggester——**只能用于前缀查询**，速度很快，性能要求高

需求场景是：输入一个字符，即时发送一个请求查询匹配项•数据结构：并非是倒排索引实现的，而是将分词的数据编码成FST和索引一起存放；FST会被加载进内存，速度很快•限制：需要对查询字段指定为Completion

参考资料

【1】https://www.jianshu.com/p/3718d05ffea4

【2】https://zq99299.github.io/note-book/elasticsearch-senior/es-high/76-completion-suggest.html#es-%E6%8F%90%E4%BE%9B%E7%9A%84-completion-suggest
