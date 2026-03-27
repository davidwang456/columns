# 背景

创建Elasticsearch索引时，往往先设置索引的映射。如下所示。

```
curl --location --request PUT 'http://192.168.217.129:9200/twitter?include_type_name=true' \
--header 'Content-Type: application/json' \
--data-raw '{
  "mappings": {
    "_doc": {
      "properties": {
        "type": { "type": "keyword" }, 
        "name": { "type": "text" },
        "user_name": { "type": "keyword" },
        "email": { "type": "keyword" },
        "content": { "type": "text" },
        "tweeted_at": { "type": "date" }
      }
    }
  }
}'
```

返回信息

```
{
    "acknowledged": true,
    "shards_acknowledged": true,
    "index": "twitter"
}
```

**获取mapping信息**

```
curl --location --request GET 'http://192.168.217.129:9200/twitter/_mapping'
```

结果如下

```
{
    "twitter": {
        "mappings": {
            "properties": {
                "content": {
                    "type": "text"
                },
                "email": {
                    "type": "keyword"
                },
                "name": {
                    "type": "text"
                },
                "tweeted_at": {
                    "type": "date"
                },
                "type": {
                    "type": "keyword"
                },
                "user_name": {
                    "type": "keyword"
                }
            }
        }
    }
}
```

其实在 ElasticSearch中可以不需要事先定义映射（Mapping），文档写入ElasticSearch时，会根据文档字段自动识别类型，但是通过这种自动识别的字段不是很精确，对于一些复杂的需要分词的就不适合了。

根据是否自动识别映射类型，我们可以将映射分为动态映射和静态映射。

动态映射，即不事先指定映射类型(Mapping)，文档写入ElasticSearch时，ES会根据文档字段自动识别类型，这种机制称之为动态映射。

静态映射，即人为事先定义好映射，包含文档的各个字段及其类型等，这种方式称之为静态映射，亦可称为显式映射。

那Elasticsearch是如何支持类型映射的呢？

# ElasticSearch支持的映射类型

**早期版本的ElasticSearch**

早期的ElasticSearch映射类型定义在ValueType.java

```
/**
 * @deprecated We are in the process of replacing this class with {@link ValuesSourceType}, so new uses or entries to the enum are
 * discouraged.  There is currently no migration path for existing uses, notably parsing user value type hints and Composite aggregation,
 * should continue to use this for now. Most importantly DO NOT ADD NEW PLACES WE SERIALIZE THIS ENUM!
 */
@Deprecated
public enum ValueType implements Writeable {

    STRING((byte) 1, "string", "string", CoreValuesSourceType.KEYWORD, DocValueFormat.RAW),

    LONG((byte) 2, "byte|short|integer|long", "long", CoreValuesSourceType.NUMERIC, DocValueFormat.RAW),
    DOUBLE((byte) 3, "float|double", "double", CoreValuesSourceType.NUMERIC, DocValueFormat.RAW),
    NUMBER((byte) 4, "number", "number", CoreValuesSourceType.NUMERIC, DocValueFormat.RAW),
    DATE(
        (byte) 5,
        "date",
        "date",
        CoreValuesSourceType.DATE,
        new DocValueFormat.DateTime(DateFieldMapper.DEFAULT_DATE_TIME_FORMATTER, ZoneOffset.UTC, DateFieldMapper.Resolution.MILLISECONDS)
    ),
    IP((byte) 6, "ip", "ip", CoreValuesSourceType.IP, DocValueFormat.IP),
    NUMERIC((byte) 7, "numeric", "numeric", CoreValuesSourceType.NUMERIC, DocValueFormat.RAW),
    GEOPOINT((byte) 8, "geo_point", "geo_point", CoreValuesSourceType.GEOPOINT, DocValueFormat.GEOHASH),
    BOOLEAN((byte) 9, "boolean", "boolean", CoreValuesSourceType.BOOLEAN, DocValueFormat.BOOLEAN),
    RANGE((byte) 10, "range", "range", CoreValuesSourceType.RANGE, DocValueFormat.RAW);
}
```

支持10中类型，但这个类已经@Deprecated，转而用ValuesSourceType来代替。ValuesSourceType有三个实现类，代表了Elasticsearch支持的三大分类：

核心数据类型

- CoreValuesSourceType

它包含了NUMERIC、KEYWORD、GEOPOINT、RANGE、IP、DATE、BOOLEAN等。

   NUMERIC对应IndexNumericFieldData

```
/**
 * Base class for numeric field data.
 */
public abstract class IndexNumericFieldData implements IndexFieldData<LeafNumericFieldData> {

    /**
     * The type of number.
     */
    public enum NumericType {
        BOOLEAN(false, SortField.Type.LONG, CoreValuesSourceType.BOOLEAN),
        BYTE(false, SortField.Type.LONG, CoreValuesSourceType.NUMERIC),
        SHORT(false, SortField.Type.LONG, CoreValuesSourceType.NUMERIC),
        INT(false, SortField.Type.LONG, CoreValuesSourceType.NUMERIC),
        LONG(false, SortField.Type.LONG, CoreValuesSourceType.NUMERIC),
        DATE(false, SortField.Type.LONG, CoreValuesSourceType.DATE),
        DATE_NANOSECONDS(false, SortField.Type.LONG, CoreValuesSourceType.DATE),
        HALF_FLOAT(true, SortField.Type.LONG, CoreValuesSourceType.NUMERIC),
        FLOAT(true, SortField.Type.FLOAT, CoreValuesSourceType.NUMERIC),
        DOUBLE(true, SortField.Type.DOUBLE, CoreValuesSourceType.NUMERIC);

        private final boolean floatingPoint;
        private final ValuesSourceType valuesSourceType;
        private final SortField.Type sortFieldType;

        NumericType(boolean floatingPoint, SortField.Type sortFieldType, ValuesSourceType valuesSourceType) {
            this.floatingPoint = floatingPoint;
            this.sortFieldType = sortFieldType;
            this.valuesSourceType = valuesSourceType;
        }

        public final boolean isFloatingPoint() {
            return floatingPoint;
        }
        public final ValuesSourceType getValuesSourceType() {
            return valuesSourceType;
        }
    }
```

KEYWORD对应IndexOrdinalsFieldData

GEOPOINT对应IndexGeoPointFieldData

RANGE对应indexFieldData

Date对应DateFieldType

- AnalyticsValuesSourceType对应数据IndexHistogramFieldData
- AggregateMetricsValuesSourceType对应数据IndexAggregateDoubleMetricFieldData

# 总结

Mapping.java

```
/**
 * Wrapper around everything that defines a mapping, without references to
 * utility classes like MapperService, ...
 */
```

MappingLookup.java

```
 /**
     * Creates a new {@link MappingLookup} instance given the provided mappers and mapping.
     * Note that the provided mappings are not re-parsed but only exposed as-is. No consistency is enforced between
     * the provided mappings and set of mappers.
     * This is a commodity method to be used in tests, or whenever no mappings are defined for an index.
     * When creating a MappingLookup through this method, its exposed functionalities are limited as it does not
     * hold a valid {@link DocumentParser}, {@link IndexSettings} or {@link IndexAnalyzers}.
     *
     * @param mapping the mapping
     * @param mappers the field mappers
     * @param objectMappers the object mappers
     * @param aliasMappers the field alias mappers
     * @return the newly created lookup instance
     */
    public static MappingLookup fromMappers(Mapping mapping,
                                            Collection<FieldMapper> mappers,
                                            Collection<ObjectMapper> objectMappers,
                                            Collection<FieldAliasMapper> aliasMappers) {
        return new MappingLookup(mapping, mappers, objectMappers, aliasMappers);
    }

    private MappingLookup(Mapping mapping,
                         Collection<FieldMapper> mappers,
                         Collection<ObjectMapper> objectMappers,
                         Collection<FieldAliasMapper> aliasMappers) {
        this.mapping = mapping;
        Map<String, Mapper> fieldMappers = new HashMap<>();
        Map<String, ObjectMapper> objects = new HashMap<>();

        boolean hasNested = false;
        for (ObjectMapper mapper : objectMappers) {
            if (objects.put(mapper.fullPath(), mapper) != null) {
                throw new MapperParsingException("Object mapper [" + mapper.fullPath() + "] is defined more than once");
            }
            if (mapper.isNested()) {
                hasNested = true;
            }
        }
        this.hasNested = hasNested;

        for (FieldMapper mapper : mappers) {
            if (objects.containsKey(mapper.name())) {
                throw new MapperParsingException("Field [" + mapper.name() + "] is defined both as an object and a field");
            }
            if (fieldMappers.put(mapper.name(), mapper) != null) {
                throw new MapperParsingException("Field [" + mapper.name() + "] is defined more than once");
            }
            indexAnalyzersMap.putAll(mapper.indexAnalyzers());
            if (mapper.hasScript()) {
                indexTimeScriptMappers.add(mapper);
            }
            if (mapper instanceof CompletionFieldMapper) {
                completionFields.add(mapper.name());
            }
        }

        for (FieldAliasMapper aliasMapper : aliasMappers) {
            if (objects.containsKey(aliasMapper.name())) {
                throw new MapperParsingException("Alias [" + aliasMapper.name() + "] is defined both as an object and an alias");
            }
            if (fieldMappers.put(aliasMapper.name(), aliasMapper) != null) {
                throw new MapperParsingException("Alias [" + aliasMapper.name() + "] is defined both as an alias and a concrete field");
            }
        }

        this.shadowedFields = new HashSet<>();
        for (RuntimeField runtimeField : mapping.getRoot().runtimeFields()) {
            runtimeField.asMappedFieldTypes().forEach(mft -> shadowedFields.add(mft.name()));
        }

        this.fieldTypeLookup = new FieldTypeLookup(mappers, aliasMappers, mapping.getRoot().runtimeFields());
        this.indexTimeLookup = new FieldTypeLookup(mappers, aliasMappers, Collections.emptyList());
        this.fieldMappers = Collections.unmodifiableMap(fieldMappers);
        this.objectMappers = Collections.unmodifiableMap(objects);
    }
```
