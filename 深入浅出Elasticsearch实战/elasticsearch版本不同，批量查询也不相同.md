# 背景

     因历史代码原因，一个接口的性能不佳。为了优化这个接口，分析了代码，发现对elasticsearch的查询是单次查询，这严重影响了接口的性能。最简单的优化方式当然是批量的方式了，但在改成批量的时候，遇到了一些坑，特此写下来作为笔记。

# ElasticSearch批量查询

网上搜到批量查询可以通过TransportClient实现，但官方推荐使用RestHighLevelClient实现。

```
We plan on deprecating the TransportClient in Elasticsearch 7.0 and removing it completely
in 8.0. Instead, you should be using the Java High Level REST Client, 
  which executes HTTP requests rather than serialized Java requests.
```

查找elasticsearch对应版本的官方文档Multi-Search API | Java REST Client [6.5] | Elastic

给出了示例：

```
MultiSearchRequest request = new MultiSearchRequest();    
SearchRequest firstSearchRequest = new SearchRequest();   
SearchSourceBuilder searchSourceBuilder = new SearchSourceBuilder();
searchSourceBuilder.query(QueryBuilders.matchQuery("user", "kimchy"));
firstSearchRequest.source(searchSourceBuilder);
request.add(firstSearchRequest);                          
SearchRequest secondSearchRequest = new SearchRequest();  
searchSourceBuilder = new SearchSourceBuilder();
searchSourceBuilder.query(QueryBuilders.matchQuery("user", "luca"));
secondSearchRequest.source(searchSourceBuilder);
request.add(secondSearchRequest);
MultiSearchResponse response = client.msearch(request, RequestOptions.DEFAULT);
```

但尝试发现，client没有msearch这个方法，然后发现client对应的版本为6.1；而版本为6.1的 不支持这个方法MultiSearch API | Java API [6.1] | Elastic。

```
SearchRequestBuilder srb1 = client
    .prepareSearch().setQuery(QueryBuilders.queryStringQuery("elasticsearch")).setSize(1);
SearchRequestBuilder srb2 = client
    .prepareSearch().setQuery(QueryBuilders.matchQuery("name", "kimchy")).setSize(1);

MultiSearchResponse sr = client.prepareMultiSearch()
        .add(srb1)
        .add(srb2)
        .get();

// You will get all individual responses from MultiSearchResponse#getResponses()
long nbHits = 0;
for (MultiSearchResponse.Item item : sr.getResponses()) {
    SearchResponse response = item.getResponse();
    nbHits += response.getHits().getTotalHits();
}
```

而升级client要评估其影响，不敢轻易升级，只能使用目前的客户端。只能使用TransportClient来实现：

```
import java.io.IOException;
import java.net.InetAddress;

import org.elasticsearch.action.search.MultiSearchResponse;
import org.elasticsearch.action.search.MultiSearchResponse.Item;
import org.elasticsearch.action.search.SearchRequest;
import org.elasticsearch.client.transport.TransportClient;
import org.elasticsearch.common.settings.Settings;
import org.elasticsearch.common.transport.TransportAddress;
import org.elasticsearch.search.builder.SearchSourceBuilder;
import org.elasticsearch.transport.client.PreBuiltTransportClient;

public class App4 {
    @SuppressWarnings("resource")
    public static void main(String[] args) throws IOException {

            TransportClient client=new PreBuiltTransportClient(Settings.EMPTY)
                    .addTransportAddress(new TransportAddress(InetAddress.getByName("127.0.0.1"), 9300)); //1st ES Node host and port
            SearchRequest req=new SearchRequest("person");
             SearchSourceBuilder builder=new SearchSourceBuilder();
             builder.size(10);
             req.source(builder);

             SearchRequest req1=new SearchRequest("posts");
             SearchSourceBuilder builder1=new SearchSourceBuilder();
             builder.size(10);
             req.source(builder1);
            MultiSearchResponse bulkResponse=client.prepareMultiSearch()
                    .add(req)
                    .add(req1)
                    .get();

            for (Item item : bulkResponse.getResponses()) {
                if (item.isFailure()) { 
                    System.out.println(item.getFailureMessage());
                }else {
                    System.out.println("-----------------------------------------------------------------------");
                    System.out.println(item.getResponse().toString());
                }
            }

        /**** Done ****/
        System.out.println("Done");

    }

}
```

# 总结

    使用elasticsearch服务时，尽量保持服务端和客户端一致；不一致的话，可能会导致有些功能实现更复杂。
