# 背景

      因历史原因，当前系统中有单机版的elasticsearch服务器做数据聚合统计，数据来源于业务数据的双写。为了系统稳定性，要升级到集群。因Elasticsearch单机变集群期间服务不能停止。迁移要尽量减少对业务的影响或者对业务无影响，考虑了几种方案，通过一一甄选，找到了一种最简单的方式。

# Elasticsearch单机变集群方案

**方案一：新建一个elasticsearch集群**

新搭建一个集群，将单机的数据先全量后增量导入到新集群中，然后通过切换域名指向新的ES集群IP地址。可选的技术：

**阿里的Canal**

- 步骤一：准备MySQL数据源

在RDS MySQL中，准备待同步的数据。

- 步骤二：创建索引和mapping

在阿里云Elasticsearch实例中，创建索引和Mapping。要求Mapping中定义的字段名称和类型与待同步数据保持一致。

- 步骤三：安装JDK

在使用Canal前，必须先安装JDK，要求版本大于等于1.8.0。

- 步骤四：安装并启动Canal-server

安装Canal-server，然后修改配置文件关联RDS MySQL。Canal-server模拟MySQL集群的一个slave，获取MySQL集群Master节点的二进制日志（binary log），并将日志推送给Canal-adapter。

- 步骤五：安装并启动Canal-adapter

安装Canal-adapter，然后修改配置文件关联RDS MySQL和Elasticsearch，以及定义MySQL数据到Elasticsearch数据的映射字段，用来将数据同步到Elasticsearch。

- 步骤六：验证增量数据同步

在RDS MySQL中新增、修改或删除数据，查看数据同步结果。

**logstash方案**

使用 Logstash 和 JDBC 输入插件来让 Elasticsearch 与 MySQL 保持同步。从概念上讲，Logstash 的 JDBC 输入插件会运行一个循环来定期对 MySQL 进行轮询，从而找出在此次循环的上次迭代后插入或更改的记录。如要让其正确运行，必须满足下列条件：

1. 在将 MySQL 中的文档写入 Elasticsearch 时，Elasticsearch 中的 "_id" 字段必须设置为 MySQL 中的 "id" 字段。这可在 MySQL 记录与 Elasticsearch 文档之间建立一个直接映射关系。如果在 MySQL 中更新了某条记录，那么将会在 Elasticsearch 中覆盖整条相关记录。请注意，在 Elasticsearch 中覆盖文档的效率与更新操作的效率一样高，因为从内部原理上来讲，更新便包括删除旧文档以及随后对全新文档进行索引。
2. 当在 MySQL 中插入或更新数据时，该条记录必须有一个包含更新或插入时间的字段。通过此字段，便可允许 Logstash 仅请求获得在轮询循环的上次迭代后编辑或插入的文档。Logstash 每次对 MySQL 进行轮询时，都会保存其从 MySQL 所读取最后一条记录的更新或插入时间。在下一次迭代时，Logstash 便知道其仅需请求获得符合下列条件的记录：更新或插入时间晚于在轮询循环中的上一次迭代中所收到的最后一条记录。

如果满足上述条件，我们便可配置 Logstash，以定期请求从 MySQL 获得新增或已编辑的全部记录，然后将它们写入 Elasticsearch 中。

**方案二：改造单机ES为集群方式**

改造单机ES为集群方式，新增服务器加到这个集群中。

- 修改单机ES为集群(修改elasticsearch.yml配置)，修改hosts文件；
- 新增ES服务器加入到集群中，使用相同的elasticsearch.yml配置，同时修改hosts文件
- ES集群内部自动同步数据到新的服务器上。

**方案对比**

- 方案一是比较通用的方案，对原有业务没有影响；缺点是：需要新增服务器，新增组件，学习成本也有
- 方案二方案简单，但有可能短暂影响业务，避免影响业务的方式是业务低谷时操作

**方案选择**

   通过监控系统发现，目前的系统有明显的时段性，特别是晚上8点之后，业务量明显下降，且存在短时间（超过一分钟）内无业务量的情况，根据这种情况，选择了方案二，提前准备好配置文件，修改好hosts文件，选择一个无业务的时间段重启了单机elasticsearch，同时启动新增的es服务器，此时查看日志，发现组成集群成功，且在同步数据。

# Elasticsearch单机变集群经验

- 全量+增量还是全量？

最初的时候考虑方案二，先通过拷贝一份全量索引文件到新的服务器，测试也验证可行的(保证elasticsearch的版本是一致的)，增量的索引文件由集群慢慢同步。

但最后发现，elasticsearch的集群内同步文件很快，拷贝全量索引文件到新服务器就放弃了。

- 分片不均衡

经过一夜的同步后，发现索引文件差距不小:两个接近1.8G，而另外一个则多达2.4G

```
du -sb /var/lib/elasticsearch/no
```

```
des/0/indices/
```

 通过查看分片信息，可以看到因分片的数量不均衡，产生了索引文件的差异。

```
#curl 'localhost:9200/_cat/shards/xx_log_prod?v'
index         shard prirep state      docs   store ip           node
xx_log_prod 4     p      STARTED 2996816 594.6mb 168.192.1.110 node-3
xx_log_prod 4     r      STARTED 2996816 594.7mb 168.192.1.111 node-2
xx_log_prod 2     p      STARTED 2992131 593.2mb 168.192.1.110 node-3
xx_log_prod 2     r      STARTED 2992131 593.1mb 168.192.1.111 node-2
xx_log_prod 1     r      STARTED 2994651 594.9mb 168.192.1.111 node-2
xx_log_prod 1     p      STARTED 2994651   595mb 168.1921.99  node-1
xx_log_prod 3     r      STARTED 2993818 595.1mb 168.1921.110 node-3
xx_log_prod 3     p      STARTED 2993818 595.1mb 168.192.1.99  node-1
xx_log_prod 0     r      STARTED 2990233 593.5mb 168.192.1.111 node-2
xx_log_prod 0     p      STARTED 2990233 593.6mb 168.192.1.99  node-1
```

可以发现，在分片分布中，总共10个分片。master(99)分到了3片(0,1,3);follower(110)分到了3片(2,3,4)；follower(111)分到了4片(0,1,2,4)，故索引文件大于其它两个。

# 常用elasticsearch命令

查看集群健康状态

```
# curl 'localhost:9200/_cluster/health?pretty'
{
  "cluster_name" : "bahasa-es",
  "status" : "green",
  "timed_out" : false,
  "number_of_nodes" : 3,
  "number_of_data_nodes" : 3,
  "active_primary_shards" : 5,
  "active_shards" : 10,
  "relocating_shards" : 0,
  "initializing_shards" : 0,
  "unassigned_shards" : 0,
  "delayed_unassigned_shards" : 0,
  "number_of_pending_tasks" : 0,
  "number_of_in_flight_fetch" : 0,
  "task_m
```

```
ax_waiting_in_queue_millis" : 0,
  "active_shards_percent_as_number" : 100.0
}
```

查看集群状态

```
# curl 'localhost:9200/_cluster/stats?pretty'
{
  "_nodes" : {
    "total" : 3,
    "successful" : 3,
    "failed" : 0
  }.....
```

查看elasticsearch是否安装成功

```
# curl -XGET localhost:9200
{
  "name" : "node-1",
  "cluster_name" : "xx-es",
  "cluster_uuid" : "_2fd8D34QzSAg8Hit5X9sA",
  "version" : {
    "number" : "7.7.0",
    "build_flavor" : "default",
    "build_type" : "rpm",
    "build_hash" : "81a1e9eda8e6183f5237786246f6dced26a10eaf",
    "build_date" : "2020-05-12T02:01:37.602180Z",
    "build_snapshot" : false,
    "lucene_version" : "8.5.1",
    "minimum_wire_compatibility_version" : "6.8.0",
    "minimum_index_compatibility_version" : "6.0.0-beta1"
  },
  "tagline" : "You Know, for Search"
}
```

查看elastisearch是否健康

```
# curl 'localhost:9200/_cat/health?v'
epoch      timestamp cluster   status node.total node.data shards pri relo init unassign pending_tasks max_task_wait_time active_shards_percent
1632288992 05:36:32  xx-es green           3         3     10   5    0    0        0             0                  -                100.0%
```

查看节点情况

```
# curl 'localhost:9200/_cat/nodes?v'
ip           heap.percent ram.percent cpu load_1m load_5m load_15m node.role master name
168.192.1.110           61          57   0    0.15    0.10     0.07 dilmrt    -      node-3
168.192.1.111           32          60   0    0.04    0.09     0.07 dilmrt    -      node-2
168.192.1.99            50          98   1    0.11    0.08     0.05 dilmrt    *      node-1
```

其中，“*”表示master，“-”表示follower

查看索引情况

```
# curl 'localhost:9200/_cat/indices?v'
health status index         uuid                   pri rep docs.count docs.deleted store.size pri.store.size
green  open   xx_log_prod 9R4BIWuhQzCHUi13D1bBJg   5   1   14974644          250      5.8gb          2.9gb
```

查看分片情况

```
#curl 'localhost:9200/_cat/shards/xx_log_prod?v'
index         shard prirep state      docs   store ip           node
xx_log_prod 4     p      STARTED 2996816 594.6mb 168.192.1.110 node-3
xx_log_prod 4     r      STARTED 2996816 594.7mb 168.192.1.111 node-2
xx_log_prod 2     p      STARTED 2992131 593.2mb 168.192.1.110 node-3
xx_log_prod 2     r      STARTED 2992131 593.1mb 168.192.1.111 node-2
xx_log_prod 1     r      STARTED 2994651 594.9mb 168.192.1.111 node-2
xx_log_prod 1     p      STARTED 2994651   595mb 168.1921.99  node-1
xx_log_prod 3     r      STARTED 2993818 595.1mb 168.1921.110 node-3
xx_log_prod 3     p      STARTED 2993818 595.1mb 168.192.1.99  node-1
xx_log_prod 0     r      STARTED 2990233 593.5mb 168.192.1.111 node-2
xx_log_prod 0     p      STARTED 2990233 593.6mb 168.192.1.99  node-1
```

查看segment情况

```
curl 'localhost:9200/_cat/segments/xx_log_prod?v'
```

查看索引字段情况

```
curl -XGET 'http://localhost:9200/xx_log_prod/_mapping?pretty'
```

# 总结

关于Elasticsearch中集群出现负载不均的情况，有以下两种问题场景：

- 节点间磁盘使用率差距不大，监控中节点CPU使用率或load_1m参数呈现明显的负载不均衡现象。
- 节点间磁盘使用率差距很大，监控中节点CPU使用率或load_1m参数呈现明显的负载不均衡现象。

很多人认为Elasticsearch，同一个分片的主分片和副本分片文档数量肯定是一样的，数据大小也是一样的。这个其实只说对了一半，文档数量是一样的，但是数据大小不一定一样。产生这种现象的原因在于，主分片和副本分片的segment数量可能不一样。

以下两种方法其中一种解决方式可以解决Segment数据不一致：

- 在业务低峰期进行强制合并操作，具体请参见force merge，将缓存中的delete.doc彻底删除，将小segment合并成大segment。
- 重启主shard所在节点，触发副shard升级为主shard。并且重新生成副shard，副shard复制新的主shard中的数据，保持主副shard的segment一致。

**参考资料**

【1】https://help.aliyun.com/document_detail/135297.html

【2】https://www.elastic.co/cn/blog/how-to-keep-elasticsearch-synchronized-with-a-relational-database-using-logstash

【3】https://stackoverflow.com/questions/33028085/how-to-migrate-mysql-data-to-elasticsearch-realtime

【4】https://help.aliyun.com/document_detail/160455.html
