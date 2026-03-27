# 背景

“要想Elasticsearch学的好，数据类型少不了！”，数据结构是Elasticsearch的基础功能，理解数据结构及其用法，非常重要。

# Elasticsearch数据结构

根据官方文档做了归类，如下表所示。

![](http://p3.toutiaoimg.com/large/tos-cn-i-qvj2lq49k0/44c08637eec44f2c8f6190293ef11159)

# Elasticsearch数据结构用法

**文本**

文本，全文内容的传统字段类型，如电子邮件正文或产品描述。使用示例：

```
curl -X PUT "localhost:9200/my-index-000001?pretty" -H 'Content-Type: application/json' -d'
{
  "mappings": {
    "properties": {
      "full_name": {
        "type":  "text"
      }
    }
  }
}
'
```

Match_only_text，一种空间优化的文本变体，它禁止评分，并且在需要位置的查询上执行得更慢。它最适合于为日志消息建立索引。

有时，同时拥有同一个字段的全文(text)和关键字(keyword)版本是很有用的:一个用于全文搜索，另一个用于聚合和排序。这可以通过多字段实现。

**关键词**

keyword用于结构化内容，如id、电子邮件地址、主机名、状态码、邮政编码或标记。

```
curl -X PUT "localhost:9200/my-index-000001?pretty" -H 'Content-Type: application/json' -d'
{
  "mappings": {
    "properties": {
      "tags": {
        "type":  "keyword"
      }
    }
  }
}
'
```

Constant_keyword用于始终包含相同值的关键字字段。

```
curl -X PUT "localhost:9200/logs-debug?pretty" -H 'Content-Type: application/json' -d'
{
  "mappings": {
    "properties": {
      "@timestamp": {
        "type": "date"
      },
      "message": {
        "type": "text"
      },
      "level": {
        "type": "constant_keyword",
        "value": "debug"
      }
    }
  }
}
'
```

wildcard用于非结构化机器生成内容。通配符类型针对具有大值或高基数的字段进行优化。

```
curl -X PUT "localhost:9200/my-index-000001?pretty" -H 'Content-Type: application/json' -d'
{
  "mappings": {
    "properties": {
      "my_wildcard": {
        "type": "wildcard"
      }
    }
  }
}
'
curl -X PUT "localhost:9200/my-index-000001/_doc/1?pretty" -H 'Content-Type: application/json' -d'
{
  "my_wildcard" : "This string can be quite lengthy"
}
'
curl -X GET "localhost:9200/my-index-000001/_search?pretty" -H 'Content-Type: application/json' -d'
{
  "query": {
    "wildcard": {
      "my_wildcard": {
        "value": "*quite*lengthy"
      }
    }
  }
}
'
```

注意：

并非所有数值数据都应映射为数值字段数据类型。Elasticsearch为范围查询优化数字字段，例如整数或长字段关键字字段更适合术语和其他术语级查询。

标识符，例如ISBN或产品标识符，很少用于范围查询。然而，它们通常使用术语级查询来检索。

考虑将数字标识符映射为关键字，如果:

- 您不打算使用范围查询搜索标识符数据。
- 快速检索很重要。在关键字字段上的术语查询搜索通常比在数字字段上的术语搜索更快。
- 如果不确定使用哪一种，可以使用多字段将数据映射为关键字和数字数据类型。

其它的类型都可以参考官方的使用。

# 拓展

数据结构一般都是FieldMapper的实现类，想深入了解其内部原理的可以读读源码。

_server的org.elasticsearch.index.mapper包目录下

![](http://p26.toutiaoimg.com/large/tos-cn-i-qvj2lq49k0/6b62204c73dd47adb11dff7bafaff077)

# 参考资料

【1】https://www.elastic.co/guide/en/elasticsearch/reference/current/mapping-types.html#aggregated-data-types
