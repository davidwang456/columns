# 背景

分词器是专门处理分词的组件，分词器由以下三部分组成：

- **Character Filters**：针对原始文本处理，比如去除 html 标签
- **Tokenizer**：按照规则切分为单词，比如按照空格切分
- **Token Filters**：将切分的单词进行加工，比如大写转小写，删除 stopwords，增加同义语  
  Lucence自带了很多分词器Analyzer，针对不同语言有不同的Analyzer。

```
<a href="{@docRoot}/../analysis/common/overview-summary.html">Common</a>: Analyzers for
 indexing content in different languages and domains.
<a href="{@docRoot}/../analysis/icu/overview-summary.html">ICU</a>: Exposes functionality
 from ICU to Apache Lucene.
<a href="{@docRoot}/../analysis/kuromoji/overview-summary.html">Kuromoji</a>: Morphological
 analyzer for Japanese text.
<a href="{@docRoot}/../analysis/morfologik/overview-summary.html">Morfologik</a>:
 Dictionary-driven lemmatization for the Polish language.
<a href="{@docRoot}/../analysis/phonetic/overview-summary.html">Phonetic</a>: Analysis for
 indexing phonetic signatures (for sounds-alike search).
<a href="{@docRoot}/../analysis/smartcn/overview-summary.html">Smart Chinese</a>: Analyzer
 for Simplified Chinese, which indexes words.
<a href="{@docRoot}/../analysis/stempel/overview-summary.html">Stempel</a>: Algorithmic
 Stemmer for the Polish Language.
```

Elasticsearch基于Lucence，内置了许多分词器：

- **Standard Analyzer** - 默认分词器，按词切分，小写处理
- **Simple Analyzer** - 按照非字母切分（符号被过滤），小写处理
- **Stop Analyzer** - 小写处理，停用词过滤（the ，a，is）
- **Whitespace Analyzer** - 按照空格切分，不转小写
- **Keyword Analyzer** - 不分词，直接将输入当做输出
- **Pattern Analyzer** - 正则表达式，默认 \W+
- **Language** - 提供了 30 多种常见语言的分词器
- **Customer Analyzer** - 自定义分词器支持第三方的分词器。

目前针对中文分词的效果不太好，一般都是使用第三方的分词器，如IK，*JIEBA等。*

# 安装第三方分词器IK，来实现分词。

1.插件的下载安装

![](http://p9.toutiaoimg.com/large/tos-cn-i-qvj2lq49k0/a3591acf92e843e583781fc5c9a47226)

从 github 上找到和本次 es 版本匹配上的分词器

```
##本文使用的elasticsearch版本为7.12.0，对应的插件地址
https://github.com/medcl/elasticsearch-analysis-ik/releases/download/v7.12.0/elasticsearch-analysis-ik-7.12.0.zip
##到elasticsearch安装根目录
cd elasticsearch根目录/bin
./elasticsearch-plugin -v install https://github.com/medcl/elasticsearch-analysis-ik/releases/download/v7.12.0/elasticsearch-analysis-ik-7.12.0.zip
##检查是否安装成功，也可以先下载插件，然后本地安装
./elasticsearch-plugin list
```

2.插件的测试

- 标准分词器

使用系统自带的标准分词器

```
curl --location --request GET 'http://192.168.217.131:9200/_analyze' \
--header 'Content-Type: application/json' \
--data-raw '{
  "analyzer": "standard",
  "text":"软件工程师"
}'
```

分词结果：

```
curl --location --request GET 'http://192.168.217.131:9200/_analyze' \
--header 'Content-Type: application/json' \
--data-raw '{
  "analyzer": "standard",
  "text":"软件工程师"
}'
```

- 使用IK分词

Analyzer: ik_smart , ik_max_word ,

Tokenizer: ik_smart , ik_max_word

IK分词器提供了2种分词的模式

1. ik_max_word: 将需要分词的文本做最小粒度的拆分，尽量分更多的词。

```
curl --location --request GET 'http://192.168.217.131:9200/_analyze' \
--header 'Content-Type: application/json' \
--data-raw '{
  "analyzer": "ik_max_word",
  "text":"软件工程师"
}'
```

分词结果

```
{
    "tokens": [
        {
            "token": "软件工程",
            "start_offset": 0,
            "end_offset": 4,
            "type": "CN_WORD",
            "position": 0
        },
        {
            "token": "软件",
            "start_offset": 0,
            "end_offset": 2,
            "type": "CN_WORD",
            "position": 1
        },
        {
            "token": "工程师",
            "start_offset": 2,
            "end_offset": 5,
            "type": "CN_WORD",
            "position": 2
        },
        {
            "token": "工程",
            "start_offset": 2,
            "end_offset": 4,
            "type": "CN_WORD",
            "position": 3
        },
        {
            "token": "师",
            "start_offset": 4,
            "end_offset": 5,
            "type": "CN_CHAR",
            "position": 4
        }
    ]
}
```

2. ik_smart: 将需要分词的文本做最大粒度的拆分。

```
curl --location --request GET 'http://192.168.217.131:9200/_analyze' \
--header 'Content-Type: application/json' \
--data-raw '{
  "analyzer": "ik_smart",
  "text":"软件工程师"
}'
```

分词结果

```
{
    "tokens": [
        {
            "token": "软件",
            "start_offset": 0,
            "end_offset": 2,
            "type": "CN_WORD",
            "position": 0
        },
        {
            "token": "工程师",
            "start_offset": 2,
            "end_offset": 5,
            "type": "CN_WORD",
            "position": 1
        }
    ]
}
```

# IK词库扩展

在elasticsearch-analysis-ik-7.12.0\config 目录下定义了IK的配置文件及词库（以.dict结尾）。IKAnalyzer.cfg.xml

```
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">
<properties>
    <comment>IK Analyzer 扩展配置</comment>
    <!--用户可以在这里配置自己的扩展字典 -->
    <entry key="ext_dict"></entry>
     <!--用户可以在这里配置自己的扩展停止词字典-->
    <entry key="ext_stopwords"></entry>
    <!--用户可以在这里配置远程扩展字典 -->
    <!-- <entry key="remote_ext_dict">words_location</entry> -->
    <!--用户可以在这里配置远程扩展停止词字典-->
    <!-- <entry key="remote_ext_stopwords">words_location</entry> -->
</properties>
```

IK词库扩展主要有两种方式：

- 在IKAnalyzer.cfg.xml使用自定义词库，以.dict为后缀，示例：

```
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">
<properties>
    <comment>IK Analyzer 扩展配置</comment>
    <!--用户可以在这里配置自己的扩展字典 -->
    <entry key="ext_dict">custom-ext.dic</entry>
     <!--用户可以在这里配置自己的扩展停止词字典-->
    <entry key="ext_stopwords">custom-stop.d
```

```
ic</entry>
    <!--用户可以在这里配置远程扩展字典 -->
    <!-- <entry key="remote_ext_dict">words_location</entry> -->
    <!--用户可以在这里配置远程扩展停止词字典-->
    <!-- <entry key="remote_ext_stopwords">words_location</entry> -->
</properties>
```

其中custom-ext.dic 和 custom-stop.dic 是自定义的词库，定义分词的文件必须是UTF-8的编码。

- 在IKAnalyzer.cfg.xml使用自定义词库

目前该插件支持热更新 IK 分词，通过上文在 IK 配置文件中提到的如下配置

```
     <!--用户可以在这里配置远程扩展字典 -->
    <entry key="remote_ext_dict">location</entry>
     <!--用户可以在这里配置远程扩展停止词字典-->
    <entry key="remote_ext_stopwords">location</entry>
```

其中 location 是指一个 url，比如 http://yoursite.com/getCustomDict，该请求只需满足以下两点即可完成分词热更新。

1. 该 http 请求需要返回两个头部(header)，一个是 Last-Modified，一个是 ETag，这两者都是字符串类型，只要有一个发生变化，该插件就会去抓取新的分词进而更新词库。
2. 该 http 请求返回的内容格式是一行一个分词，换行符用 \n 即可。

满足上面两点要求就可以实现热更新分词了，不需要重启 ES 实例。

可以将需自动更新的热词放在一个 UTF-8 编码的 .txt 文件里，放在 nginx 或其他简易 http server 下，当 .txt 文件修改时，http server 会在客户端请求该文件时自动返回相应的 Last-Modified 和 ETag。可以另外做一个工具来从业务系统提取相关词汇，并更新这个 .txt 文件。

# IK远程词库原理

IK词库的原理

(1)AnalysisIkPlugin获取Analyzer和Tokenizer

```
public class AnalysisIkPlugin extends Plugin implements AnalysisPlugin {

    public static String PLUGIN_NAME = "analysis-ik";

    @Override
    public Map<String, AnalysisModule.AnalysisProvider<TokenizerFactory>> getTokenizers() {
        Map<String, AnalysisModule.AnalysisProvider<TokenizerFactory>> extra = new HashMap<>();


        extra.put("ik_smart", IkTokenizerFactory::getIkSmartTokenizerFactory);
        extra.put("ik_max_word", IkTokenizerFactory::getIkTokenizerFactory);

        return extra;
    }

    @Override
    public Map<String, AnalysisModule.AnalysisProvider<AnalyzerProvider<? extends Analyzer>>> getAnalyzers() {
        Map<String, AnalysisModule.AnalysisProvider<AnalyzerProvider<? extends Analyzer>>> extra = new HashMap<>();

        extra.put("ik_smart", IkAnalyzerProvider::getIkSmartAnalyzerProvider);
        extra.put("ik_max_word", IkAnalyzerProvider::getIkAnalyzerProvider);

        return extra;
    }

}
```

(2)加载配置

```
  public IkTokenizerFactory(IndexSettings indexSettings, Environment env, String name, Settings settings) {
      super(indexSettings, settings,name);
      configuration=new Configuration(env,settings);
  }
    @Inject
    public Configuration(Environment env,Settings settings) {
        this.environment = env;
        this.settings=settings;

        this.useSmart = settings.get("use_smart", "false").equals("true");
        this.enableLowercase = settings.get("enable_lowercase", "true").equals("true");
        this.enableRemoteDict = settings.get("enable_remote_dict", "true").equals("true");

        Dictionary.initial(this);

    }
```

(3) 加载字典

```
    /**
     * 词典初始化 由于IK Analyzer的词典采用Dictionary类的静态方法进行词典初始化
     * 只有当Dictionary类被实际调用时，才会开始载入词典， 这将延长首次分词操作的时间 该方法提供了一个在应用加载阶段就初始化字典的手段
     * 
     * @return Dictionary
     */
    public static synchronized void initial(Configuration cfg) {
        if (singleton == null) {
            synchronized (Dictionary.class) {
                if (singleton == null) {

                    singleton = new Dictionary(cfg);
                    singleton.loadMainDict();
                    singleton.loadSurnameDict();
                    singleton.loadQuantifierDict();
                    singleton.loadSuffixDict();
                    singleton.loadPrepDict();
                    singleton.loadStopWordDict();

                    if(cfg.isEnableRemoteDict()){
                        // 建立监控线程
                        for (String location : singleton.getRemoteExtDictionarys()) {
                            // 10 秒是初始延迟可以修改的 60是间隔时间 单位秒
                            pool.scheduleAtFixedRate(new Monitor(location), 10, 60, TimeUnit.SECONDS);
                        }
                        for (String location : singleton.getRemoteExtStopWordDictionarys()) {
                            pool.scheduleAtFixedRate(new Monitor(location), 10, 60, TimeUnit.SECONDS);
                        }
                    }

                }
            }
        }
    }
```

(4) 远程词库Monitor线程

```
    public void run() {
        SpecialPermission.check();
        AccessController.doPrivileged((PrivilegedAction<Void>) () -> {
            this.runUnprivileged();
            return null;
        });
    }

    /**
     * 监控流程：
     *  ①向词库服务器发送Head请求
     *  ②从响应中获取Last-Modify、ETags字段值，判断是否变化
     *  ③如果未变化，休眠1min，返回第①步
     *     ④如果有变化，重新加载词典
     *  ⑤休眠1min，返回第①步
     */

    public void runUnprivileged() {

        //超时设置
        RequestConfig rc = RequestConfig.custom().setConnectionRequestTimeout(10*1000)
                .setConnectTimeout(10*1000).setSocketTimeout(15*1000).build();

        HttpHead head = new HttpHead(location);
        head.setConfig(rc);

        //设置请求头
        if (last_modified != null) {
            head.setHeader("If-Modified-Since", last_modified);
        }
        if (eTags != null) {
            head.setHeader("If-None-Match", eTags);
        }

        CloseableHttpResponse response = null;
        try {

            response = httpclient.execute(head);

            //返回200 才做操作
            if(response.getStatusLine().getStatusCode()==200){

                if (((response.getLastHeader("Last-Modified")!=null) && !response.getLastHeader("Last-Modified").getValue().equalsIgnoreCase(last_modified))
                        ||((response.getLastHeader("ETag")!=null) && !response.getLastHeader("ETag").getValue().equalsIgnoreCase(eTags))) {

                    // 远程词库有更新,需要重新加载词典，并修改last_modified,eTags
                    Dictionary.getSingleton().reLoadMainDict();
                    last_modified = response.getLastHeader("Last-Modified")==null?null:response.getLastHeader("Last-Modified").getValue();
                    eTags = response.getLastHeader("ETag")==null?null:response.getLastHeader("ETag").getValue();
                }
            }else if (response.getStatusLine().getStatusCode()==304) {
                //没有修改，不做操作
                //noop
            }else{
                logger.info("remote_ext_dict {} return bad code {}" , location , response.getStatusLine().getStatusCode() );
            }

        } catch (Exception e) {
            logger.error("remote_ext_dict {} error!",e , location);
        }finally{
            try {
                if (response != null) {
                    response.close();
                }
            } catch (IOException e) {
                logger.error(e.getMessage(), e);
            }
        }
    }
```

参考资料

【1】https://github.com/medcl/elasticsearch-analysis-ik

【2】https://www.cnblogs.com/mrwhite2020/p/14716102.html

【3】https://www.cnblogs.com/davidwang456/articles/14713395.html

【4】https://segmentfault.com/a/1190000039854381
