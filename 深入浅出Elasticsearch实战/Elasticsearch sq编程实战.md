# 背景

es的查询虽然功能很强大，但是查询语言(DSL)很麻烦，不管是封装json还是通过python/java的api进行封装，都不方便。而elasticsearch-SQL可以用sql查询es，对于不熟悉es的DSL的人来说，更为简便和易读。

Elasticsearch-sql支持的功能：

**（1）插件式的安装**

**（2）SQL查询**

**（3）超越SQL之外的查询**

**（4）对JDBC方式的支持**

# Elasticsearch sq编程实战

**1.安装Elasticsearch**

- 第一步：安装elasticsearch7.12.0，如果不指定版本号则默认安装最新版本

```
docker pull elasticsearch:7.12.0
```

- 第二步：创建外部映射文件

```
#在主机创建es的配置文件
mkdir -p /elasticsearch/config
#在主机上创建es的数据文件
mkdir -p /elasticsearch/data
#在主键上创建插件文件夹
mkdir -p /elasticsearch/plugins
#配置es可以被远程的任何机器访问 --可根据实际业务需求进行设定
echo "http.host: 0.0.0.0">>/elasticsearch/config/elasticsearch.yml
```

- 第三步:创建启动脚本

```
cat docker-compose.yml 
version: '2'
services:
  elasticsearch:
    container_name: elasticsearch
    image: elasticsearch:7.12.0
    ports:
      - "9200:9200"
    volumes:
      - /elasticsearch/config/elasticsearch.yml:/usr/share/elasticsearch/config/elasticsearch.yml
      - /elasticsearch/data:/usr/share/elasticsearch/data
      - /elasticsearch/plugins:/usr/share/elasticsearch/plugins
    environment:
      - "ES_JAVA_OPTS=-Xms64m -Xmx512m"
      - "discovery.type=single-node"
      - "COMPOSE_PROJECT_NAME=elasticsearch-server"
    restart: always
```

- 第四步：启动elasticsearch

```
docker-compose up -d
```

验证elasticsearch是否启动成功。

浏览器输入：http://192.168.217.129:9200/

```
name    "f2c1fe79233d"
cluster_name    "elasticsearch"
cluster_uuid    "Gi2vnW9IRn-dJtUdIEc75A"
version    
number    "7.12.0"
build_flavor    "default"
build_type    "docker"
build_hash    "78722783c38caa25a70982b5b042074cde5d3b3a"
build_date    "2021-03-18T06:17:15.410153305Z"
build_snapshot    false
lucene_version    "8.8.0"
minimum_wire_compatibility_version    "6.8.0"
minimum_index_compatibility_version    "6.0.0-beta1"
tagline    "You Know, for Search"
```

**2.准备数据**

- 使用Spring Initializr创建项目、添加依赖

![](http://p26.toutiaoimg.com/large/tos-cn-i-qvj2lq49k0/81201d7c021247f48618f5f41943ca6f)

- 配置上面的elasticsearch客户端及属性文件

```
package com.david.springboot.elasticsearchintegrate.config;

import org.elasticsearch.client.RestHighLevelClient;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.elasticsearch.client.ClientConfiguration;
import org.springframework.data.elasticsearch.client.RestClients;
import org.springframework.data.elasticsearch.config.AbstractElasticsearchConfiguration;
@Configuration("esConfig")
public class RestClientConfig extends AbstractElasticsearchConfiguration{
    @Value("${es.host:localhost}")
    private String host;

    @Override
    public RestHighLevelClient elasticsearchClient() {
        final ClientConfiguration clientConfiguration = ClientConfiguration.builder()
                .connectedTo(host.split(","))
                .build();
        return RestClients.create(clientConfiguration).rest();
    }

}
```

其中es.host配置在application.properties.

```
es.host=192.168.217.129:9200
```

- 批量生成100w数据

```
    @GetMapping("/setBatchStudent")
    String setBatchStudent(){
        List<Student> batch=null;
        for(int i=0;i<BATCH_NUM;i++) {
            batch=new ArrayList<>();
            for(int k=0;k<10000;k++) {
                int seqNo=10000*i+k;
                Student std=new Student();
                std.setId(seqNo+"");
                std.setFirstName("david"+seqNo);
                std.setLastName("www"+seqNo);
                std.setAge(k%100);   
                batch.add(std);
            }
            studentRepository.saveAll(batch);
        }
         return "ok";
    }
```

**3.安装elasticsearch-sql插件**

进入elasticsearch容器内：

```
docker exec -it elasticsearch /bin/bash
```

执行安装步骤，注意：elasticsearch的版本和elasticsearch-sql版本要对应上。

```
./bin/elasticsearch-plugin install https://github.com/NLPchina/elasticsearch-sql/releases/download/7.12.0.0/elasticsearch-sql-7.12.0.0.zip
```

验证，进入plugins目录，查看是否出现sql目录。

然后退出elasticsearch容器，并重启elasticsearch

```
docker restart elasticsearch
```

**3.chrome安装浏览器插件**

- github上下载elasticsearch-sql-site-chrome。
- 解压zip文件
- 在chrome浏览器输入：chrome://extensions/  
  先打开开发者模式，然后出现“加载已解压的扩展程序”按钮，单击该按钮，加载刚才已解压的扩展程序

![](http://p3.toutiaoimg.com/large/tos-cn-i-jcdsk5yqko/ef4b3c490571450cadf50a3909f183d4)

- 打开插件

![](http://p9.toutiaoimg.com/large/tos-cn-i-qvj2lq49k0/bbef2aa48bc44ef0ab47f16d1fda9a51)

进入SQL可视化界面，执行SQL命令 （首先在右上角填写对应的ES集群地址，此处填写了本地地址：http://192.168.217.129:9200/）

- SQL可视化查询

```
SELECT * FROM student where lastName like 'www50999%' limit 10
```

![](http://p26.toutiaoimg.com/large/tos-cn-i-qvj2lq49k0/4440123c42e34975aed850d6436f2031)

也可以使用postman或者cURL

Postman

![](http://p6.toutiaoimg.com/large/tos-cn-i-jcdsk5yqko/2bf5fc7fdf144585abe2c4545ed3dbcc)

cURL：

```
curl --location --request POST 'http://192.168.217.129:9200/_nlpcn/sql' \
--header 'Content-Type: application/json' \
--data-raw 'SELECT * FROM student where lastName like '\''www50999%'\'' limit 10'
```

# 总结

es-sql也支持jdbc方式的查询，实例如下：

```
public void testJDBC() throws Exception {
        Properties properties = new Properties();
        properties.put("url", "jdbc:elasticsearch://127.0.0.1:9300/" + TestsConstants.TEST_INDEX);
        DruidDataSource dds = (DruidDataSource) ElasticSearchDruidDataSourceFactory.createDataSource(properties);
        Connection connection = dds.getConnection();
        PreparedStatement ps = connection.prepareStatement("SELECT  gender,lastname,age from  " + TestsConstants.TEST_INDEX + " where lastname='Heath'");
        ResultSet resultSet = ps.executeQuery();
        List<String> result = new ArrayList<String>();
        while (resultSet.next()) {
              System.out.println(resultSet.getString("lastname") + "," + resultSet.getInt("age") + "," + resultSet.getString("gender"))
        }
        ps.close();
        connection.close();
        dds.close();
    }
```
