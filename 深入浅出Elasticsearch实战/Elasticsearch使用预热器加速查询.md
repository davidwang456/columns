# 背景

在使用Elasticsearch时，您可能已经听说过或使用过预热器（warmers）API。预热器允许我们添加一些查询，这些查询会在索引段被激活用于搜索之前运行，以此来“预热”索引段。具体来说，预热器是一组在Elasticsearch中注册的查询，用于准备索引以供搜索使用。在本节中，我们将回顾如何添加预热器、如何管理它们以及它们的用途。

**使用预热器的原因**

您可能会问，预热器真的有那么有用吗？答案实际上取决于您的数据和查询，但总体上，它们是有用的。正如我们之前提到的（例如，在第五章的“理解Elasticsearch缓存”部分中讨论缓存时），为了使用某些特性，如父子关系、分面或基于字段的排序，Elasticsearch需要将一些数据预加载到缓存中。这个预加载过程可能需要时间和资源，这会使您的查询在一段时间内变慢。如果您的索引变化很快，缓存将需要频繁刷新，查询性能会进一步受到影响。

这就是为什么Elasticsearch 0.20引入了预热器API。预热器是标准的查询，它们会在Elasticsearch允许对冷段（尚未使用）进行搜索之前运行。这不仅在启动时发生，而且在每次提交新段时也会发生。因此，通过适当的预热查询，我们可以将所有需要的数据预加载到缓存中，还可以预热操作系统的I/O缓存（通过读取冷段）。通过这样做，当段最终暴露给查询时，我们可以确保获得最佳的搜索性能，并且所有需要的数据都将准备就绪。

在预热器部分的末尾，我们将向您展示一个简单的示例，说明预热器如何改善初始查询性能，并让您自己感受到差异。

# 操作预热器

Elasticsearch允许我们创建、检索和删除预热器。每个预热器都与特定的索引或索引和类型相关联。我们可以在索引创建请求中包含预热器，将它们包含在我们的模板中，或者使用PUT预热器API来创建它们。最后，我们可以完全禁用预热器，而无需删除它们。因此，如果我们不希望它们只运行一段时间，我们可以轻松做到。

**使用PUT预热器API**

向索引或索引和类型添加预热器的最简单方法是使用PUT预热器API。为此，我们需要向_warmer REST端点发送一个PUT HTTP请求，并在请求体中包含查询。例如，如果我们想为名为mastering的索引和名为doc的类型添加一个简单的match_all查询和一些术语分面作为预热器，我们可以使用以下命令：

```
curl -XPUT 'localhost:9200/mastering/doc/_warmer/testWarmer' -d '{
  "query" : {
    "match_all" : {}
  },
  "facets" : {
    "nameFacet" : {
      "terms" : {
        "field" : "name"
      }
    }
  }
}'
```

如您所见，每个预热器都有自己的名称，应该是唯一的（在前面的示例中是testWarmer），我们可以使用它来检索或删除预热器。如果我们想为整个mastering索引添加相同的预热器，我们将不得不省略类型名称，命令将如下所示：

```
curl -XPUT 'localhost:9200/mastering/_warmer/testWarmer' -d '{
  ...
}'
```

**在索引创建期间添加预热器**

除了使用PUT预热器API，我们还可以在索引创建期间定义预热器。为此，我们需要在同一级别上添加一个额外的预热器部分，就像我们在映射中做的那样。例如，如果我们想创建带有doc文档类型和我们在PUT预热器API中使用的相同预热器的mastering索引，我们会发送以下请求：

```
curl -XPUT 'localhost:9200/mastering' -d '{
  "warmers" : {
    "testWarmer" : {
      "types" : ["doc"],
      "source" : {
        "query" : {
          "match_all" : {}
        },
        "facets" : {
          "nameFacet" : {
            "terms" : {
              "field" : "name"
            }
          }
        }
      }
    },
    "mappings" : {
      "doc" : {
        "properties" : {
          "name": { "type": "string", "store": "yes", "index": "analyzed" }
        }
      }
    }
  }'
```

如您所见，除了映射部分，我们还包含了一个预热器部分，用于为我们正在创建的索引提供预热器。每个预热器都通过其名称（在本例中为testWarmer）标识，并具有两个属性：类型和来源。类型属性是索引中文档类型的数组，预热器应该用于这些类型。如果我们希望预热器用于所有文档类型，我们应该将该数组留空。来源属性应该包含我们的查询来源。我们可以在单个索引创建请求中包含多个预热器。

**向模板添加预热器**

Elasticsearch还允许我们以与创建索引时相同的方式为模板包含预热器。例如，如果我们想为一个示例模板包含一个预热器，我们会运行以下命令：

```
curl -XPUT 'localhost:9200/_template/templateone' -d '{
  "warmers" : {
    "testWarmer" : {
      "types" : ["doc"],
      "source" : {
        "query" : {
          "match_all" : {}
        },
        "facets" : {
          "nameFacet" : {
            "terms" : {
              "field" : "name"
            }
          }
        }
      }
    },
    "template" : "test*"
  }
}'
```

**检索预热器**

所有定义的预热器都可以使用GET HTTP方法，并发送请求到_warmer REST端点（后跟名称）来检索。我们可以通过使用它的名称来检索单个预热器，例如，像这样：

```
curl -XGET 'localhost:9200/mastering/_warmer/warmerOne'
```

或者我们可以使用通配符字符来检索所有以给定短语开头的预热器的名称。例如，如果我们想获取所有以字母w开头的预热器，我们可以使用以下命令：

```
curl -XGET 'localhost:9200/mastering/_warmer/w*'
```

最后，我们可以使用以下命令获取给定索引的所有预热器：

```
curl -XGET 'localhost:9200/mastering/_warmer/'
```

当然，我们也可以在所有前面的命令中包含文档类型，以便不操作整个索引的预热器，而只操作所需类型的预热器。

**删除预热器**

与Elasticsearch允许我们检索预热器的方式类似，我们可以通过使用DELETE HTTP方法和_warmer REST端点来删除它们。例如，如果我们想从mastering索引中删除名为warmerOne的预热器，我们会运行以下命令：

```
curl -XDELETE 'localhost:9200/mastering/_warmer/warmerOne'
```

我们也可以删除所有以给定短语开头的预热器的名称。例如，如果我们想删除所有以字母w开头并属于mastering索引的预热器，我们会运行以下命令：

```
curl -XDELETE 'localhost:9200/mastering/_warmer/w*'
```

我们还可以删除给定索引的所有预热器。我们通过发送DELETE HTTP方法到_warmer REST端点而不提供预热器名称来做到这一点。例如，删除mastering索引的所有预热器可以通过以下命令完成：

```
curl -XDELETE 'localhost:9200/mastering/_warmer/'
```

当然，我们也可以像检索预热器时一样在所有前面的命令中包含类型，以便不操作整个索引的预热器，而只操作所需类型的预热器。

**禁用预热器**

如果您不想使用您的预热器，但又不想删除它们，您可以使用index.warmer.enabled属性并将其设置为false。您可以在elasticsearch.yml文件中设置它，或者使用更新设置API，例如，像这样：

```
curl -XPUT 'localhost:9200/mastering/_settings' -d '{
  "index.warmer.enabled": false
}'
```

如果您想再次使用预热器，您所需要做的只是更改index.warmer.enabled属性并将其设置为true。

# 测试预热器

为了测试预热器，我们来运行一个简单的测试。我使用以下命令创建了一个简单的索引：

```
curl -XPUT localhost:9200/docs -d '{
  "mappings" : {
    "doc" : {
      "properties" : {
        "name": { "type": "string", "store": "yes", "index": "analyzed" }
      }
    }
  }
}'
```

除此之外，我还创建了第二种类型，称为child，它作为先前创建的doc类型文档的子文档。为此，我使用了以下命令：

```
curl -XPUT 'localhost:9200/docs/child/_mapping' -d '{
  "child" : {
    "_parent": {
      "type" : "doc"
    },
    "properties" : {
      "name": { "type": "string", "store": "yes", "index": "analyzed" }
    }
  }
}'
```

在此之后，我索引了一个doc类型的单个文档和大约80,000个指向该文档的child类型文档，使用父请求参数。

**在没有预热器的情况下查询**

索引过程结束后，重新启动Elasticsearch并运行以下查询：

```
{
  "query" : {
    "has_child" : {
      "type" : "child",
      "query" : {
        "term" : {
          "name" : "document"
        }
      }
    }
  }
}
```

如您所见，这是一个简单的查询，它返回至少在一个子文档中具有给定术语的父文档。Elasticsearch返回的响应如下：

```
{
  "took" : 479,
  "timed_out" : false,
  "_shards" : {
    "total" : 1,
    "successful" : 1,
    "failed" : 0
  },
  "hits" : {
    "total" : 1,
    "max_score" : 1.0,
    "hits" : [ {
      "_index" : "docs",
      "_type" : "doc",
      "_id" : "1",
      "_score" : 1.0, "_source" : {"name":"Test 1234"}
    } ]
  }
}
```

执行时间为479毫秒。听起来相当高，对吧？如果我们再次运行相同的查询，执行时间会下降。

**在预热器存在的情况下查询**

为了提高初始查询性能，我们需要引入一个简单的预热器，它不仅会预热I/O缓存，还会强制Elasticsearch将父文档标识符加载到内存中，以允许更快的父子查询。正如我们所知，Elasticsearch在给定关系的第一次查询期间会这样做。因此，有了这些信息，我们可以使用以下命令添加我们的预热器：

```
curl -XPUT 'localhost:9200/docs/_warmer/sampleWarmer' -d '{
  "query" : {
    "has_child" : {
      "type" : "child",
      "query" : {
        "match_all" : {}
      }
    }
  }
}'
```

现在，如果我们重新启动Elasticsearch并运行与没有预热器时相同的查询，我们将得到或多或少以下结果：

```
{
  "took" : 38,
  "timed_out" : false,
  "_shards" : {
    "total" : 1,
    "successful" : 1,
    "failed" : 0
  },
  "hits" : {
    "total" : 1,
    "max_score" : 1.0,
    "hits" : [ {
      "_index" : "docs",
      "_type" : "doc",
      "_id" : "1",
      "_score" : 1.0, "_source" : {"name":"Test 1234"}
    } ]
  }
}
```

现在我们可以看到，在Elasticsearch重新启动后，预热器如何改善了查询的执行时间。没有预热器的查询几乎需要半秒钟，而存在预热器的查询执行时间不到40毫秒。

# 总结

当然，性能提升不仅仅是因为Elasticsearch能够将文档标识符加载到内存中，还因为操作系统能够缓存索引段。尽管如此，性能提升是显著的，如果您使用的查询可以被预热（例如，使用重过滤、父子关系、分面等），那么使用预热器是一个很好的选择。
