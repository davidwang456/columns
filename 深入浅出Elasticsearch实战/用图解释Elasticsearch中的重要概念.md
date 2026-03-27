# 背景

Elasticsearch中的术语有点多，如Index、Document、Mapping、Type、Node、Cluster 、Shard、Replica、Gateway等，这些术语从名称上不太好理解，下面让我们用图来一个个解释这些概念

# 用图解释Elasticsearch中的重要概念

**Index（索引）**

索引是相似文档的集合。在Elasticsearch中，你可以将索引视为一个数据库，其中包含了多个文档。Elasticsearch 索引与关系数据库中的索引不同。如果将 Elasticsearch 集群视为一个数据库，它可以包含许多索引，您可以将它们看作是一个表，在每个索引内，有许多文档。   

- RDBMS => 数据库 => 表 => 列/行
- Elasticsearch => 集群 => 索引 => 分片 => 带有键值对的文档

**Document（文档）**

文档是Elasticsearch中的基础信息单元，类似于关系型数据库中的行。每个文档都是一个JSON对象，包含多个字段。

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/35ad8caf1b4f41ebb8912aed91ea4eec~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600513&x-signature=NO0O7TpArqxxcGNe4G3wZuszqYQ%3D)

**Mapping（映射）**

映射定义了索引中的每个字段的名称、类型、属性和其他设置。它描述了文档的结构。

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/c2e6b15b0f8249698232acef6ac8b608~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600513&x-signature=yi4xbmoJ2tuoVbPvHdtB9xencdE%3D)

**Type**

注意：在Elasticsearch 7.x及更高版本中，类型（Type）的概念已被弃用，每个索引只能有一个映射。

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/3191eaf863f244448e2f666f5fe65474~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600513&x-signature=%2FQDwTQJNjVHRZfGekwWMagRk0DE%3D)

**Node（节点）**

节点是Elasticsearch集群中的一个实例。它存储了数据（作为索引的一部分），并参与集群的搜索和索引操作。

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/90de80eb61b34a7e9661a0899b4aca80~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600513&x-signature=DAE6PrmA4xjaGMYcn3%2Bh9U8hyKY%3D)

**Cluster（集群）**

集群是一组Elasticsearch节点的集合，它们协同工作，共享数据并提供搜索和索引功能。集群中的所有节点都通过相同的集群名称进行标识。

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/5de87b2986734dc98b2e7f68de9a9e7a~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600513&x-signature=cRLuQbmgdI%2FWzo9q0YI57WR%2BY10%3D)

**Shard（分片）**

当索引的数据量变得非常大时，为了提高性能和可扩展性，Elasticsearch会将索引划分为多个分片。每个分片都是一个完整的Lucene索引，可以独立地存储、搜索和索引数据。

一个索引可以由一个或多个主分片（Primary Shard）组成，并且可以有零个或多个副本分片（Replica Shard）。

![](https://p26-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/a95335fad1aa4860b146f2df99980be1~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600513&x-signature=wNFiQfvq852XjuH0KK3kPkFW70M%3D)

**Replica（副本）**

副本是主分片的复制品。它们提供了数据冗余和容错性，因为如果一个主分片所在的节点出现故障，可以从其副本中恢复数据。

在默认情况下，Elasticsearch会为每个主分片创建一个副本，但你可以根据需要调整副本的数量。

![](https://p3-sign.toutiaoimg.com/tos-cn-i-pyjprfzmqi/7111532ca44343f4b9802f9e1eb232b0~tplv-tt-origin.webp?_iz=30575&lk3s=eb9271ed&x-expires=1774600513&x-signature=ZqSc4ccBEsR0rxkMeLg9r%2FdP4kc%3D)

**Gateway（网关）**

在Elasticsearch中，网关是一个负责数据持久化和集群恢复的组件。它确保在节点故障或整个集群重新启动后，数据能够重新加载到集群中。

Elasticsearch提供了多种类型的网关，如本地文件系统网关、分布式文件系统网关（如HDFS）和云存储网关（如S3）。

# 总结

下面是一张图，展示了这些概念之间的关系：

```
Cluster
  |
  +-- Node 1 --+-- Document 1 --+-- Mapping
  |             |               |
  |             |               +-- Field 1 (Type: Text)
  |             |               +-- Field 2 (Type: Date)
  |             |
  |             +-- Index 1 ----+
  |             |               |
  |             |               +-- Shard 1 (Primary)
  |             |               +-- Shard 1 (Replica)
  |             |
  |             +-- Index 2 ----+-- Shard 2 (Primary)
  |             |               +-- Shard 2 (Replica)
  |
  +-- Node 2 --+-- Document 2 --+
```

在这个图中，集群由两个节点（Node 1和Node 2）组成。每个节点可以存储多个索引（Index 1和Index 2），每个索引可以包含多个文档（Document 1和Document 2）。每个文档都有其映射（Mapping），定义了文档的结构。索引被分成多个分片（Shard 1和Shard 2），并且每个分片可以有多个副本（Replica）。
