本故事纯属虚构，如有雷同，纯属巧合。

> **版本说明**：Geo 基于 Sorted Set；与 **JSON + 索引** 联用时，职责边界在应用层或 Query Engine，勿混为一谈。

---

## Geo：破掌式新绎——附近的人仍在，地图更大

**大师**：**`GEOADD`/`GEORADIUS`/`GEOSEARCH`**——骑手、门店、社交 LBS。Redis 用 **geohash 编码 + ZSet** 搞定范围查询，**别在内存里for循环算球面距离**当主路径。

**小白**：能当「真·PostGIS」吗？

**大师**：**轻量 LBS** 可以；复杂多边形、投影变换，请专业 GIS 或引擎。

---

## 实战要点

- 地球模型：`GEOSEARCH` 参数 **BYRADIUS/BYBOX**、**M**/`KM`/`FT`/`MI`。  
- 与业务 ID：`GEOADD venues 121.5 31.2 shop:9001`，member 用业务主键。  
- 大规模：**分片键设计**见 [卷七-27](卷七-27-集群与高可用.md)。

---

## 收式

下一篇：[卷四-17-TimeSeries模块篇.md](卷四-17-TimeSeries模块篇.md)。
