# 背景

    Elasticsearch 是一个分布式的 RESTful 搜索和分析引擎，可用来集中存储您的数据，以便您对形形色色、规模不一的数据进行搜索、索引和分析。Elasticsearch特性：

![](http://p26.toutiaoimg.com/large/tos-cn-i-jcdsk5yqko/4b928e72770a4a53a7c67c4fcf292e05)

其强大的功能，想必听到它名称的人都如雷贯耳。

Elasticsearch不仅具有强大的数据查询功能，还具有超强的数据聚合功能，常常和Kibana，logstash等一起使用，也就是著名的ELK。

# Elasticsearch聚合功能

Elasticsearch的聚合功能集中在AggregationBuilders中，包含了常用的max，min，avg，sum，count等。

```
/**
 * Utility class to create aggregations.
 */
public class AggregationBuilders {

    private AggregationBuilders() {
    }

    /**
     * Create a new {@link ValueCount} aggregation with the given name.
     */
    public static ValueCountAggregationBuilder count(String name) {
        return new ValueCountAggregationBuilder(name, null);
    }

    /**
     * Create a new {@link Avg} aggregation with the given name.
     */
    public static AvgAggregationBuilder avg(String name) {
        return new AvgAggregationBuilder(name);
    }

    /**
     * Create a new {@link Avg} aggregation with the given name.
     */
    public static WeightedAvgAggregationBuilder weightedAvg(String name) {
        return new WeightedAvgAggregationBuilder(name);
    }

    /**
     * Create a new {@link Max} aggregation with the given name.
     */
    public static MaxAggregationBuilder max(String name) {
        return new MaxAggregationBuilder(name);
    }

    /**
     * Create a new {@link Min} aggregation with the given name.
     */
    public static MinAggregationBuilder min(String name) {
        return new MinAggregationBuilder(name);
    }

    /**
     * Create a new {@link Sum} aggregation with the given name.
     */
    public static SumAggregationBuilder sum(String name) {
        return new SumAggregationBuilder(name);
    }

    /**
     * Create a new {@link Stats} aggregation with the given name.
     */
    public static StatsAggregationBuilder stats(String name) {
        return new StatsAggregationBuilder(name);
    }

    /**
     * Create a new {@link ExtendedStats} aggregation with the given name.
     */
    public static ExtendedStatsAggregationBuilder extendedStats(String name) {
        return new ExtendedStatsAggregationBuilder(name);
    }

    /**
     * Create a new {@link Filter} aggregation with the given name.
     */
    public static FilterAggregationBuilder filter(String name, QueryBuilder filter) {
        return new FilterAggregationBuilder(name, filter);
    }

    /**
     * Create a new {@link Filters} aggregation with the given name.
     */
    public static FiltersAggregationBuilder filters(String name, KeyedFilter... filters) {
        return new FiltersAggregationBuilder(name, filters);
    }

    /**
     * Create a new {@link Filters} aggregation with the given name.
     */
    public static FiltersAggregationBuilder filters(String name, QueryBuilder... filters) {
        return new FiltersAggregationBuilder(name, filters);
    }

    /**
     * Create a new {@link AdjacencyMatrix} aggregation with the given name.
     */
    public static AdjacencyMatrixAggregationBuilder adjacencyMatrix(String name, Map<String, QueryBuilder> filters) {
        return new AdjacencyMatrixAggregationBuilder(name, filters);
    }

    /**
     * Create a new {@link AdjacencyMatrix} aggregation with the given name and separator
     */
    public static AdjacencyMatrixAggregationBuilder adjacencyMatrix(String name, String separator,  Map<String, QueryBuilder> filters) {
        return new AdjacencyMatrixAggregationBuilder(name, separator, filters);
    }

    /**
     * Create a new {@link Sampler} aggregation with the given name.
     */
    public static SamplerAggregationBuilder sampler(String name) {
        return new SamplerAggregationBuilder(name);
    }

    /**
     * Create a new {@link Sampler} aggregation with the given name.
     */
    public static DiversifiedAggregationBuilder diversifiedSampler(String name) {
        return new DiversifiedAggregationBuilder(name);
    }

    /**
     * Create a new {@link Global} aggregation with the given name.
     */
    public static GlobalAggregationBuilder global(String name) {
        return new GlobalAggregationBuilder(name);
    }

    /**
     * Create a new {@link Missing} aggregation with the given name.
     */
    public static MissingAggregationBuilder missing(String name) {
        return new MissingAggregationBuilder(name, null);
    }

    /**
     * Create a new {@link Nested} aggregation with the given name.
     */
    public static NestedAggregationBuilder nested(String name, String path) {
        return new NestedAggregationBuilder(name, path);
    }

    /**
     * Create a new {@link ReverseNested} aggregation with the given name.
     */
    public static ReverseNestedAggregationBuilder reverseNested(String name) {
        return new ReverseNestedAggregationBuilder(name);
    }

    /**
     * Create a new {@link GeoDistance} aggregation with the given name.
     */
    public static GeoDistanceAggregationBuilder geoDistance(String name, GeoPoint origin) {
        return new GeoDistanceAggregationBuilder(name, origin);
    }

    /**
     * Create a new {@link Histogram} aggregation with the given name.
     */
    public static HistogramAggregationBuilder histogram(String name) {
        return new HistogramAggregationBuilder(name);
    }

    /**
     * Create a new {@link InternalGeoHashGrid} aggregation with the given name.
     */
    public static GeoHashGridAggregationBuilder geohashGrid(String name) {
        return new GeoHashGridAggregationBuilder(name);
    }

    /**
     * Create a new {@link InternalGeoTileGrid} aggregation with the given name.
     */
    public static GeoTileGridAggregationBuilder geotileGrid(String name) {
        return new GeoTileGridAggregationBuilder(name);
    }

    /**
     * Create a new {@link SignificantTerms} aggregation with the given name.
     */
    public static SignificantTermsAggregationBuilder significantTerms(String name) {
        return new SignificantTermsAggregationBuilder(name, null);
    }


    /**
     * Create a new {@link SignificantTextAggregationBuilder} aggregation with the given name and text field name
     */
    public static SignificantTextAggregationBuilder significantText(String name, String fieldName) {
        return new SignificantTextAggregationBuilder(name, fieldName);
    }


    /**
     * Create a new {@link DateHistogramAggregationBuilder} aggregation with the given
     * name.
     */
    public static DateHistogramAggregationBuilder dateHistogram(String name) {
        return new DateHistogramAggregationBuilder(name);
    }

    /**
     * Create a new {@link Range} aggregation with the given name.
     */
    public static RangeAggregationBuilder range(String name) {
        return new RangeAggregationBuilder(name);
    }

    /**
     * Create a new {@link DateRangeAggregationBuilder} aggregation with the
     * given name.
     */
    public static DateRangeAggregationBuilder dateRange(String name) {
        return new DateRangeAggregationBuilder(name);
    }

    /**
     * Create a new {@link IpRangeAggregationBuilder} aggregation with the
     * given name.
     */
    public static IpRangeAggregationBuilder ipRange(String name) {
        return new IpRangeAggregationBuilder(name);
    }

    /**
     * Create a new {@link Terms} aggregation with the given name.
     */
    public static TermsAggregationBuilder terms(String name) {
        return new TermsAggregationBuilder(name, null);
    }

    /**
     * Create a new {@link Percentiles} aggregation with the given name.
     */
    public static PercentilesAggregationBuilder percentiles(String name) {
        return new PercentilesAggregationBuilder(name);
    }

    /**
     * Create a new {@link PercentileRanks} aggregation with the given name.
     */
    public static PercentileRanksAggregationBuilder percentileRanks(String name, double[] values) {
        return new PercentileRanksAggregationBuilder(name, values);
    }

    /**
     *
```

```
 Create a new {@link MedianAbsoluteDeviation} aggregation with the given name
     */
    public static MedianAbsoluteDeviationAggregationBuilder medianAbsoluteDeviation(String name) {
        return new MedianAbsoluteDeviationAggregationBuilder(name);
    }

    /**
     * Create a new {@link Cardinality} aggregation with the given name.
     */
    public static CardinalityAggregationBuilder cardinality(String name) {
        return new CardinalityAggregationBuilder(name, null);
    }

    /**
     * Create a new {@link TopHits} aggregation with the given name.
     */
    public static TopHitsAggregationBuilder topHits(String name) {
        return new TopHitsAggregationBuilder(name);
    }

    /**
     * Create a new {@link GeoBounds} aggregation with the given name.
     */
    public static GeoBoundsAggregationBuilder geoBounds(String name) {
        return new GeoBoundsAggregationBuilder(name);
    }

    /**
     * Create a new {@link GeoCentroid} aggregation with the given name.
     */
    public static GeoCentroidAggregationBuilder geoCentroid(String name) {
        return new GeoCentroidAggregationBuilder(name);
    }

    /**
     * Create a new {@link ScriptedMetric} aggregation with the given name.
     */
    public static ScriptedMetricAggregationBuilder scriptedMetric(String name) {
        return new ScriptedMetricAggregationBuilder(name);
    }

    /**
     * Create a new {@link CompositeAggregationBuilder} aggregation with the given name.
     */
    public static CompositeAggregationBuilder composite(String name, List<CompositeValuesSourceBuilder<?>> sources) {
        return new CompositeAggregationBuilder(name, sources);
    }
}
```

# 日期聚合实例

Elasticsearch中，使用日期进行聚合数据在开发中非常常用，本章以日期聚合为例进行展示。

日期聚合常用两种聚合函数：

- DateRangeAggregationBuilder：时间范围分组聚合。
- DateHistogramAggregationBuilder：使用上面的date_range的分组，我们可以实现将一个长的时间段分成多个小的时间段然后实现分时段的数据分析。当然实际业务中很多时候我们可能只需要展示一年每个月的数据分析、或者一个月每天的数据分析。这个时候可以使用date_histogram的方法。其interval参数接收month和day的参数，它可以将数据根据每月或者每天的区间自动完成数据的分组。

**时间范围分组聚合DateRangeAggregationBuilder实例**

```
 //  时间范围分组聚合
    public void dateRange() throws IOException {
                RestHighLevelClient client = new RestHighLevelClient(
                RestClient.builder(new HttpHost("10.203.10.98", 9200, "http")));
        ElasticsearchRestTemplate elasticsearchTemplate =new ElasticsearchRestTemplate(client);
        Map<String, Object> termConditions =new HashMap<>();
        termConditions.put("status", 2);
        Map<String, Object> termNotConditons =new HashMap<>();

        DateRangeAggregationBuilder agg =
            AggregationBuilders
                .dateRange("aggQuery")
                .field("endTime")
                .format("yyyy-MM-dd")
                .addUnboundedTo("2019-05-01")
                .addRange("2019-05-01","2019-07-01")
                .addUnboundedFrom("2019-07-01");
      //根据手机号去重
            agg.subAggregation(AggregationBuilders.cardinality("phone").field("phone"));
              TimeRange tr=new TimeRange();
               tr.setStart(LocalDateTime.of(2021, 12, 30, 0, 0));
              tr.setEnd(LocalDateTime.now());

        SearchHits<CallLogEntity> result=exeAggQuery(elasticsearchTemplate,1,termConditions,termNotConditons,tr,agg);
        if (result.getTotalHits()>0) {
            ParsedDateHistogram parsed = result.getAggregations().get("aggQuery");
            for (Bucket item : parsed.getBuckets()) {
                ParsedCardinality ca=item.getAggregations().get("phone");
                System.out.println(
                    item.getKeyAsString()+":" +
                    ca.getValue());
            }
            System.out.println("do something");
        }
    }
  private static SearchHits<LogEntity> exeAggQuery(ElasticsearchRestTemplate elasticsearchTemplate,Integer tenantId, Map<String, Object> termConditions,Map<String, Object> termNotConditons, TimeRange range, AbstractAggregationBuilder<?>... aggs) {
        String start = EsUtils.DTF.format(range.start);
        String end = EsUtils.DTF.format(range.end);
        Instant now = Instant.now();
        BoolQueryBuilder query = boolQuery()
                .must(QueryBuilders.rangeQuery("endTime").from(start).to(end)));
        for (Map.Entry<String, Object> c : termConditions.entrySet()) {
            if (c.getValue() != null) {
                if (c.getValue() instanceof Collection)
                    query.must(QueryBuilders.termsQuery(c.getKey(), (Collection<?>) c.getValue()));
                else if (c.getValue().getClass().isArray())
                    query.must(QueryBuilders.termsQuery(c.getKey(), (Object[]) c.getValue()));
                else
                    query.must(QueryBuilders.termQuery(c.getKey(), c.getValue()));
            }
        }

        for (Map.Entry<String, Object> c : termNotConditons.entrySet()) {
            if (c.getValue() != null) {
                if (c.getValue() instanceof Collection)
                    query.mustNot(QueryBuilders.termsQuery(c.getKey(), (Collection<?>) c.getValue()));
                else if (c.getValue().getClass().isArray())
                    query.mustNot(QueryBuilders.termsQuery(c.getKey(), (Object[]) c.getValue()));
                else
                    query.mustNot(QueryBuilders.termQuery(c.getKey(), c.getValue()));
            }
        }

        String[] indices = genIndices(range);
        if (indices.length == 0)
            return null;
        NativeSearchQueryBuilder builder = new NativeSearchQueryBuilder()
                .withQuery(query)
                .withSearchType(QUERY_THEN_FETCH)
                .withPageable(PageRequest.of(0, 1));
//                .withIndices(indices);
        if(aggs!=null) {
            for (AbstractAggregationBuilder<?> agg : aggs)
                if (agg != null) builder.addAggregation(agg);
        }
        Query searchQuery = builder.build();
        SearchHits<CallLogEntity> result = elasticsearchTemplate.search(searchQuery, LogEntity.class, IndexCoordinates.of(indices));
        return result;
    }

    private static String[] genIndices(TimeRange range) {
        Set<String> idx = new HashSet<>();
        idx.add("log_test");
        return idx.toArray(new String[]{});
    }
```

**时间柱状图DateHistogramAggregationBuilder聚合分组**

```
          DateHistogramAggregationBuilder agg = AggregationBuilders
          .dateHistogram("aggQuery") .field("startTime") .format("yyyy-MM-dd")
          .dateHistogramInterval(DateHistogramInterval.DAY);

          agg.subAggregation(AggregationBuilders.cardinality("phone").field("phone"));
```

可能会出现的报错：

```
          DateHistogramAggregationBuilder agg = AggregationBuilders
          .dateHistogram("aggQuery") .field("startTime") .format("yyyy-MM-dd")
          .dateHistogramInterval(DateHistogramInterval.DAY);

          agg.subAggregation(AggregationBuilders.cardinality("phone").field("phone"));
```

原因是聚合的filed需是keyword，解决方式

```
"Text fields are not optimised for operations that require per-document field data like aggregations and sorting, so these operations are disabled by default. Please use a keyword field instead. Alternatively, set fielddata=true on [interests] in order to load field data by uninverting the inverted index. Note that this can use significant memory.
```

当然，Elasticsearch也支持使用sql实现。示例如下：

```
curl -X PUT "localhost:9200/call_log_test/_mapping?pretty" -H 'Content-Type: application/json' -d'
{
  "properties": {
    "phone": { 
      "type":     "text",
      "fielddata": true
    }
  }
}
'
```

# 总结

      聚合框架可以基于搜索查询帮助提供聚合后的数据。其基础是名为聚合的简单构建基块，人们可以对这些构建基块进行编辑以生成有关数据的复杂汇总。可以将聚合看做一个工作单元，用来针对一组文档创建分析信息。聚合分类：指标聚合、桶聚合、管道聚合、矩阵聚合、累计基数聚合。

参考资料

【1】https://blog.csdn.net/qq330983778/article/details/102889663

【2】https://www.elastic.co/guide/cn/elasticsearch/guide/current/cardinality.html

【3】https://www.elastic.co/guide/en/elasticsearch/reference/current/sql-functions-datetime.html#sql-functions-datetime

【4】https://www.elastic.co/cn/blog/an-introduction-to-elasticsearch-sql-with-practical-examples-part-2
