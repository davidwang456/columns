# 第 16 章 · Geo：附近的人

本故事纯属虚构，如有雷同，纯属巧合。

> **版本说明**：Geo 基于 Sorted Set；与 **JSON + 索引** 联用时，职责边界在应用层或 Query Engine，勿混为一谈。

## 本话目标

- 正确 **`GEOADD` 经纬度顺序**（经度在前）。  
- 用 **`GEOSEARCH`** 拉一圈附近的店，并说出与 **Vector 近邻** 的差异。

## 步步引导：先别 for 循环算地球

**小白**：我把所有店铺拉回 Java，算距离排序……

**大师**：数据量一上来，**带宽与 CPU** 先哭。Redis Geo 用 **geohash + ZSet** 做**范围候选**，再精确距离。

**小白**：经纬度写反了咋办？

**大师**：能查，但结果**跑到海里**——上线前用已知坐标**写单元测试**。

**小白**：复杂多边形区域呢？

**大师**：Redis **轻量 LBS**；复杂 GIS 请专业引擎，别硬怼。

## 小剧场：三里地茶楼

「三里内哪家分号最近？」——Geo 擅长；「沿省界内所有门店」——换大堂（专业 GIS）。

---

## LBS 三件套：坐标、距离、范围

**大师**：**`GEOADD`/`GEORADIUS`/`GEOSEARCH`**——骑手、门店、社交 LBS。Redis 用 **geohash 编码 + ZSet** 搞定范围查询，**别在内存里 for 循环算球面距离**当主路径。

**小白**：能当「真·PostGIS」吗？

**大师**：**轻量 LBS** 可以；复杂多边形、投影变换、空间联合查询，请专业 GIS 或专用引擎。**Redis 是快刀，不是测绘总局**。

**趣味比方**：Geo 像**茶楼排位**：「方圆三里内的分号」一嗓子就够；你要画**省界多边形**，得换大堂。

---

## 最小示例

```text
GEOADD venues 121.4737 31.2304 shop:9001
GEOADD venues 121.4800 31.2350 shop:9002
GEOSEARCH venues FROMLONLAT 121.47 31.23 BYRADIUS 5 km WITHDIST COUNT 10 ASC
```

member 用业务主键，坐标顺序是 **`经度 纬度`**，别写反——写反了能查，但**跑到海里去**。

---

## 实战要点

- 地球模型：`GEOSEARCH` 参数 **BYRADIUS/BYBOX**、**M**/`KM`/`FT`/`MI`。  
- 与业务 ID：`GEOADD venues 121.5 31.2 shop:9001`，查询结果再回表补详情（Redis 存索引，不必存整本黄页）。  
- 大规模：**分片键设计**见 [27-集群与高可用.md](27-集群与高可用.md)；**同城多副本**按城市拆 key 常见。  
- 与 [20-VectorSet与RAG迷你管道.md](20-VectorSet与RAG迷你管道.md)：一个是**地理近邻**，一个是**语义近邻**，别混为一谈。

---

## 踩坑

- 跨 dateline 与极点特殊 case，读文档。  
- 超高密度点集：**查询复杂度**与 **member 数量**要压测。  
- 把 Geo 当「通用搜索」：全文/标签请 JSON/Query Engine 或搜索引擎。

---

## 动手试一试

```text
GEOADD demo:shops 121.47 31.23 shop:a 121.48 31.24 shop:b
GEOSEARCH demo:shops FROMLONLAT 121.47 31.23 BYRADIUS 3 km WITHDIST COUNT 5
```

换你自己的城市坐标试一遍；故意 **经纬度对调** 再试，观察结果多离谱。

## 实战锦囊

- member 用 **业务主键**，详情 **回源 DB**。  
- 超大规模按 **城市/区域** 拆 key，结合 [27-集群与高可用.md](27-集群与高可用.md)。  
- 与 [20-VectorSet与RAG迷你管道.md](20-VectorSet与RAG迷你管道.md)：**地理近邻 ≠ 语义近邻**。

---

## 收式

**小白**：弟子先去画三里地的圈。

**大师**：下一章：[17-TimeSeries模块篇.md](17-TimeSeries模块篇.md)。
