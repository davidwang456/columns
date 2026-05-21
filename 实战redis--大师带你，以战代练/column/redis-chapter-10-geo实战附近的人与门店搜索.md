# 第10章：GEO 实战：附近的人与门店搜索

## 1. 项目背景

电商系统准备接入线下门店。用户打开 App 首页时，希望看到“附近 3 公里可自提门店”；外卖业务还想做“附近骑手”；社交业务则想展示“附近的人”。这些需求都有共同点：给定用户当前位置，快速找出一定范围内的对象，并按距离排序。

如果把门店经纬度放在 MySQL 里，每次查询都用经纬度公式计算距离，再排序分页，数据量一大就会很吃力。专业 GIS 数据库当然能做得更完整，但对轻量门店搜索来说，引入复杂系统的成本也不低。Redis GEO 正好适合这种“规模中等、实时性高、查询条件相对简单”的地理位置场景。

Redis GEO 底层基于 ZSet，把经纬度编码成 geohash 分值存储。它提供 `GEOADD`、`GEOSEARCH`、`GEODIST`、`GEOPOS` 等命令，可以完成半径搜索、矩形搜索、距离返回和排序。本章我们实现附近门店搜索，并讨论附近的人为什么要更谨慎。

## 2. 项目设计

小胖开场：“这不就是地图上找奶茶店吗？我站在公司楼下，搜 3 公里内谁最近。”

小白问：“门店还好，位置不太变。附近的人位置一直变化，如果每次都写 Redis，会不会压力很大？隐私和过期也要考虑。”

大师说：“所以 GEO 的第一个决策是对象类型。门店、仓库、充电桩这类位置稳定的数据，很适合 Redis GEO；骑手、司机、用户这类移动对象也能用，但必须设计上报频率、过期清理、隐私授权和离线删除。”

技术映射：`geo:store:city:{cityId}` 存门店位置，member 是门店 ID；移动对象可以按城市、业务线或 geohash 前缀拆 key。

小胖追问：“为什么要按城市拆 key？全部门店放一个 key 不行吗？”

大师回答：“能跑，但不一定好。Redis GEO 查询会在一个集合里找附近对象。全国门店放一个 key，集合变大、管理困难；按城市或区域拆分，可以缩小查询范围，也方便冷热数据隔离。”

小白补充：“但用户在城市边界附近，可能漏掉隔壁城市门店。”

大师点头：“是的，所以拆分维度要配合业务。门店搜索可以根据定位城市加周边城市兜底；骑手调度可能要按更细网格，并在边界查相邻网格。”

技术映射：GEO key 的粒度影响查询性能和边界召回，需要结合业务地理范围设计。

小胖又问：“我还想按门店类型过滤，比如只看支持自提的店。”

大师说：“Redis GEO 只负责位置召回，不擅长复杂属性过滤。常见做法是先用 GEO 找附近 N 个候选，再回数据库或缓存查门店类型、营业状态、库存，再二次过滤排序。不要把所有业务条件都塞进 GEO。”

技术映射：GEO 做空间候选召回，业务服务做属性过滤和最终排序。

## 3. 项目实战

启动 Redis：

```bash
docker run --name redis-lab-10 -p 6379:6379 -d redis:8.6
docker exec -it redis-lab-10 redis-cli
```

### 3.1 写入门店位置

以北京部分位置做演示，实际经纬度请以业务数据为准：

```bash
GEOADD geo:store:city:bj 116.397128 39.916527 store:1001
GEOADD geo:store:city:bj 116.407526 39.904030 store:1002
GEOADD geo:store:city:bj 116.384799 39.949402 store:1003
GEOADD geo:store:city:bj 116.455100 39.941000 store:1004
```

查看门店坐标：

```bash
GEOPOS geo:store:city:bj store:1001 store:1002
```

计算两个门店距离：

```bash
GEODIST geo:store:city:bj store:1001 store:1002 km
```

注意 `GEOADD` 参数顺序是经度、纬度、member。很多线上错误都来自把纬度和经度写反。

### 3.2 查询附近门店

以用户当前位置为中心，查 3 公里内门店，按距离从近到远返回：

```bash
GEOSEARCH geo:store:city:bj FROMLONLAT 116.397500 39.908700 BYRADIUS 3 km ASC WITHDIST COUNT 10
```

如果业务要矩形范围，例如地图视窗内门店：

```bash
GEOSEARCH geo:store:city:bj FROMLONLAT 116.397500 39.908700 BYBOX 5 4 km ASC WITHDIST COUNT 20
```

业务伪代码：

```text
nearbyStores(lon, lat, cityId, radiusKm, pageSize):
  key = "geo:store:city:" + cityId
  candidates = GEOSEARCH key FROMLONLAT lon lat BYRADIUS radiusKm km ASC WITHDIST COUNT pageSize * 3
  storeIds = extractMembers(candidates)
  storeInfo = batchGetStoreInfo(storeIds)
  filtered = filter storeInfo by open=true and supportPickup=true
  return first pageSize with distance
```

这里 `COUNT pageSize * 3` 是为了给后续属性过滤留余量。如果只取 10 个候选，过滤掉 8 个，页面就只剩 2 个结果。

### 3.3 门店类型过滤

GEO 本身不存复杂属性，可以把门店属性放在 Hash 或数据库：

```bash
HSET store:1001 name "王府井自提店" type "pickup" open 1
HSET store:1002 name "前门体验店" type "experience" open 1
HSET store:1003 name "西城仓店" type "pickup" open 0
```

查询流程是先 GEO 召回，再 `HMGET` 或批量查数据库。对于高频属性，也可以维护辅助 Set：

```bash
SADD store:type:pickup store:1001 store:1003
SADD store:open store:1001 store:1002
SINTER store:type:pickup store:open
```

但不要对每次附近查询都做大规模集合交集。一般做法是在应用层对 GEO 候选结果过滤，候选数量控制在几十到几百。

### 3.4 附近的人

移动对象要持续更新位置：

```bash
GEOADD geo:user:city:bj 116.401000 39.910000 user:1001
GEOADD geo:user:city:bj 116.402000 39.912000 user:1002
GEOSEARCH geo:user:city:bj FROMLONLAT 116.400000 39.910000 BYRADIUS 1 km ASC WITHDIST COUNT 20
```

用户关闭定位或离线时要删除：

```bash
ZREM geo:user:city:bj user:1001
```

因为 GEO 底层是 ZSet，所以删除成员使用 `ZREM`。如果要避免长期保存移动轨迹，不要给每次上报创建历史 key，只保存最新位置，并在用户退出或超过心跳时间后清理。Redis GEO 没有成员级 TTL，可以额外维护心跳 ZSet：

```bash
ZADD geo:user:heartbeat:bj 1714470000 user:1001
```

后台扫描过期心跳，再 `ZREM` 位置集合。

### 3.5 常见坑

第一，经纬度顺序容易写反。Redis 使用 longitude、latitude，也就是经度在前、纬度在后。

第二，GEO 适合轻量地理查询，不适合路线规划、行政区多边形判断、复杂空间索引。这些场景应交给专业 GIS、PostGIS 或搜索引擎。

第三，分页不是简单的深分页。附近搜索通常按半径返回前 N 个，用户加载更多时可以扩大半径或用业务游标，不建议在巨大结果集上深翻页。

第四，移动对象要考虑隐私授权、上报频率、离线清理和防刷。附近的人不是单纯技术功能，必须和产品、法务、安全一起设计。

## 4. 项目总结

Redis GEO 适合把“经纬度附近搜索”快速接入业务，尤其是门店、仓库、充电桩、骑手位置等轻量场景。它的落地套路是：按区域拆 key，GEO 做候选召回，业务服务做属性过滤，数据库保留权威资料。

它的边界也要记牢：GEO 不做复杂 GIS，不做精细路线距离，不天然支持成员过期，也不适合承载隐私策略。把它当成高性能附近候选索引，而不是完整地图系统，才是稳妥用法。

本章思考题：

1. 如果用户在城市边界附近，按城市拆分 GEO key 可能漏召回，你会如何补偿？
2. “附近的人”功能为什么比“附近门店”更需要心跳、过期和隐私设计？
