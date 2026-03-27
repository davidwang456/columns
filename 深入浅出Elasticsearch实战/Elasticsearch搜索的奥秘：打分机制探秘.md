# 1.背景

要理解Elasticsearch的内部机制，了解Lucene是第一步。Elasticsearch是Lucene之上的抽象层，提供了一个分布式搜索引擎，它具有水平可扩展性，并提供基于JSON的API与Lucene交互。Lucene是一个由Apache基金会维护的开源项目。我们通常不需要直接与Lucene交互，因为Elasticsearch已经代表我们工作了。可以将这种关系想象成开车而不需要直接命令引擎启动。

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/df013d7a26d34307bc9cf93947558689~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600593&x-signature=nZ%2FSqYXDiNdej%2BR6JvcQzPh1Hek%3D)

Lucene索引：索引用于在大型数据集中快速搜索文本。首先需要索引数据，将其转换成应用程序可访问的格式。Lucene索引被划分为多个段（segments），每个段本身由不完全独立的索引组成。

Lucene段（Segment）：每个段是一组或多组Lucene文档的集合。段是不可变的。在删除文档时，文档被标记为已删除，并且文档的新版本被添加到段中。之后，在某个时间点，这些段会被合并成一个单独的段，不包括那些被标记为已删除的旧版本。合并段的优势：

丢弃旧版本的文档，减少索引在磁盘上的空间。

旧的段被移除，创建更大的段，这增加了搜索速度。

# 2.打分机制

**TFIDF算法**

老版本的Lucene的默认打分算法是TFIDF算法，其公式如下：

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/f0a17abf10064fe2b8b3b4a8a4c8b23a~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600593&x-signature=7iTP2uPKzDSnn3%2FM8%2FzaR9Lxejs%3D)

其中

**BM25算法**

新版的lucene使用了BM25Similarity作为默认打分实现。这里显式使用了BM25Similarity。算法公式如下：

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/e87d3e66f640407695370f5c715d0f6f~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600593&x-signature=3YNu%2BRxI2SqWGjdCCpGF7zsmZqg%3D)

其中:

　　 D即文档(Document),Q即查询语句(Query),score(D,Q)指使用Q的查询语句在该文档下的打分函数。

　　IDF即倒排文件频次(Inverse Document Frequency)指在倒排文档中出现的次数，qi是Q分词后term

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/c00b8a3106694a9da70fb433446fd329~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600593&x-signature=S90doI2oXL2Kcj05ZE9JJeI4z%2Fw%3D)

其中，N是总的文档数目，n(qi)是出现分词qi的文档数目。

　　f(qi,D)是qi分词在文档Document出现的频次

　　 k1和b是可调参数，默认值为1.2，0.75

　　|D|是文档的单词的个数，avgdl 指库里的平均文档长度。

BM25算法实现

1.IDF实现

　　单个IDF实现

```
  /** Implemented as <code>log(1 + (docCount - docFreq + 0.5)/(docFreq + 0.5))</code>. */
  protected float idf(long docFreq, long docCount) {
    return (float) Math.log(1 + (docCount - docFreq + 0.5D)/(docFreq + 0.5D));
  }
```

 　　IDF的集合实现

```
  @Override
  public final SimWeight computeWeight(float boost, CollectionStatistics collectionStats, TermStatistics... termStats) {
    Explanation idf = termStats.length == 1 ? idfExplain(collectionStats, termStats[0]) : idfExplain(collectionStats, termStats);
    float avgdl = avgFieldLength(collectionStats);

    float[] oldCache = new float[256];
    float[] cache = new float[256];
    for (int i = 0; i < cache.length; i++) {
      oldCache[i] = k1 * ((1 - b) + b * OLD_LENGTH_TABLE[i] / avgdl);
      cache[i] = k1 * ((1 - b) + b * LENGTH_TABLE[i] / avgdl);
    }
    return new BM25Stats(collectionStats.field(), boost, idf, avgdl, oldCache, cache);
  }

  /**
   * Computes a score factor for a phrase.
   * 
   * <p>
   * The default implementation sums the idf factor for
   * each term in the phrase.
   * 
   * @param collectionStats collection-level statistics
   * @param termStats term-level statistics for the terms in the phrase
   * @return an Explain object that includes both an idf 
   *         score factor for the phrase and an explanation 
   *         for each term.
   */
  public Explanation idfExplain(CollectionStatistics collectionStats, TermStatistics termStats[]) {
    double idf = 0d; // sum into a double before casting into a float
    List<Explanation> details = new ArrayList<>();
    for (final TermStatistics stat : termStats ) {
      Explanation idfExplain = idfExplain(collectionStats, stat);
      details.add(idfExplain);
      idf += idfExplain.getValue();
    }
    return Explanation.match((float) idf, "idf(), sum of:", details);
```

2.k1和b参数实现

```
  public BM25Similarity(float k1, float b) {
    if (Float.isFinite(k1) == false || k1 < 0) {
      throw new IllegalArgumentException("illegal k1 value: " + k1 + ", must be a non-negative finite value");
    }
    if (Float.isNaN(b) || b < 0 || b > 1) {
      throw new IllegalArgumentException("illegal b value: " + b + ", must be between 0 and 1");
    }
    this.k1 = k1;
    this.b  = b;
  }

  /** BM25 with these default values:
   * <ul>
   *   <li>{@code k1 = 1.2}</li>
   *   <li>{@code b = 0.75}</li>
   * </ul>
   */
  public BM25Similarity() {
    this(1.2f, 0.75f);
  }
```

  3.平均文档长度avgdl 计算

```
  /** The default implementation computes the average as <code>sumTotalTermFreq / docCount</code> */
  protected float avgFieldLength(CollectionStatistics collectionStats) {
    final long sumTotalTermFreq;
    if (collectionStats.sumTotalTermFreq() == -1) {
      // frequencies are omitted (tf=1), its # of postings
      if (collectionStats.sumDocFreq() == -1) {
        // theoretical case only: remove!
        return 1f;
      }
      sumTotalTermFreq = collectionStats.sumDocFreq();
    } else {
      sumTotalTermFreq = collectionStats.sumTotalTermFreq();
    }
    final long docCount = collectionStats.docCount() == -1 ? collectionStats.maxDoc() : collectionStats.docCount();
    return (float) (sumTotalTermFreq / (double) docCount);
  }
```

4.参数Weigh的计算

```
  /** Cache of decoded bytes. */
  private static final float[] OLD_LENGTH_TABLE = new float[256];
  private static final float[] LENGTH_TABLE = new float[256];

  static {
    for (int i = 1; i < 256; i++) {
      float f = SmallFloat.byte315ToFloat((byte)i);
      OLD_LENGTH_TABLE[i] = 1.0f / (f*f);
    }
    OLD_LENGTH_TABLE[0] = 1.0f / OLD_LENGTH_TABLE[255]; // otherwise inf

    for (int i = 0; i < 256; i++) {
      LENGTH_TABLE[i] = SmallFloat.byte4ToInt((byte) i);
    }
  }

  @Override
  public final SimWeight computeWeight(float boost, CollectionStatistics collectionStats, TermStatistics... termStats) {
    Explanation idf = termStats.length == 1 ? idfExplain(collectionStats, termStats[0]) : idfExplain(collectionStats, termStats);
    float avgdl = avgFieldLength(collectionStats);

    float[] oldCache = new float[256];
    float[] cache = new float[256];
    for (int i = 0; i < cache.length; i++) {
      oldCache[i] = k1 * ((1 - b) + b * OLD_LENGTH_TABLE[i] / avgdl);
      cache[i] = k1 * ((1 - b) + b * LENGTH_TABLE[i] / avgdl);
    }
    return new BM25Stats(collectionStats.field(), boost, idf, avgdl, oldCache, cache);
  }
```

相当于 

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/eaec1653640b40d5adc459cb52ea9e3a~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600593&x-signature=FIDnApuoNrtGzh6VcVtIUpjxUJw%3D)

5.WeightValue计算

```
    BM25Stats(String field, float boost, Explanation idf, float avgdl, float[] oldCache, float[] cache) {
      this.field = field;
      this.boost = boost;
      this.idf = idf;
      this.avgdl = avgdl;
      this.weight = idf.getValue() * boost;
      this.oldCache = oldCache;
      this.cache = cache;
    }

    BM25DocScorer(BM25Stats stats, int indexCreatedVersionMajor, NumericDocValues norms) throws IOException {
      this.stats = stats;
      this.weightValue = stats.weight * (k1 + 1);
      this.norms = norms;
      if (indexCreatedVersionMajor >= 7) {
        lengthCache = LENGTH_TABLE;
        cache = stats.cache;
      } else {
        lengthCache = OLD_LENGTH_TABLE;
        cache = stats.oldCache;
      }
    }
```

 相当于

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/5140310576a44ef7a71f2b872bf9bc8b~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600593&x-signature=y1XG6VHhI%2BK37LvudYt4GbFf2K0%3D)

红色部分相乘

6.总的得分计算

```
    @Override
    public float score(int doc, float freq) throws IOException {
      // if there are no norms, we act as if b=0
      float norm;
      if (norms == null) {
        norm = k1;
      } else {
        if (norms.advanceExact(doc)) {
          norm = cache[((byte) norms.longValue()) & 0xFF];
        } else {
          norm = cache[0];
        }
      }
      return weightValue * freq / (freq + norm);
    }
```

其中norm是从cache里取的，cache是放入了

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/58d9aca9159a47eea5904a34adc35eb7~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600593&x-signature=iGZ97OplZsDFlaFvzxW1d03xqfs%3D)

那么整个公式就完整的出来了

# 查询过程

![](https://p26-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/c3446f7cadbf4d2c8b9440e99f05eab8~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600593&x-signature=MRofF%2F1cVyfMwQ38IrjLAsYzdhw%3D)

# 总结

BM25算法的全称是 Okapi BM25，是一种二元独立模型的扩展，也可以用来做搜索的相关度排序。本文通过和lucene的BM25Similarity的实现来深入理解整个打分公式。
