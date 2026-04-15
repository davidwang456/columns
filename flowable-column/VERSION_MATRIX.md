# 版本矩阵（专栏前置说明）

全专栏示例代码、依赖坐标与命令行片段，**默认**以下组合；若某章需例外，必须在章首声明。

---

## 推荐矩阵 A（默认，写作与样章）

| 组件 | 版本 | 说明 |
|------|------|------|
| JDK | **17** 或 **21**（LTS） | 与 Spring Boot 3 对齐 |
| Spring Boot | **3.2.x** 或 **3.3.x** | 团队任选其一锁死小版本 |
| Flowable | **7.0.x**（与 Boot 3 官方 starter 对齐） | 专栏主线索 |
| 构建 | Maven **3.9+** 或 Gradle 8.x | 文内以 Maven 为主 |

** starters（Maven 示意）**

```xml
<dependency>
  <groupId>org.flowable</groupId>
  <artifactId>flowable-spring-boot-starter</artifactId>
  <!-- 版本由 BOM 或与 Spring Boot 兼容的 Flowable 发布说明锁定 -->
</dependency>
```

> 具体 `<version>` 以选用的 Flowable 发行说明为准；团队应在项目父 POM 中**集中锁定**，避免各模块漂移。

**数据库（本地/联调）**

| 用途 | 推荐 |
|------|------|
| 本地快速体验 | **H2**（内存或文件），配合 `spring.jpa` / Flowable 自动建表 |
| 团队共享/准生产形态 | **PostgreSQL 14+** 或 **MySQL 8.0+** |

---

## 备选矩阵 B（存量系统仍在 JDK 8 / Spring Boot 2）

| 组件 | 版本 |
|------|------|
| JDK | **8** 或 **11** |
| Spring Boot | **2.7.x** |
| Flowable | **6.8.x**（与 Boot 2.7 兼容的稳定线） |

**说明**：部分 API 包名、自动配置类名在 Boot 2/3 间有差异；专栏正文默认矩阵 A，涉及差异处会用脚注标「B 矩阵读者注意…」。

---

## 不支持作为专栏默认基线

- **极老**的 Spring Boot 1.x + Flowable 5/6 早期：仅历史维护项目可能遇到，不展开。
- **无 LTS 规划的中间版本 JDK**：不作默认。

---

## 验证清单（每章作者在合并前自检）

- [ ] 标明 JDK / Boot / Flowable 三者至少到**次版本号**  
- [ ] 数据库方言（H2 / PG / MySQL）与 DDL 脚本策略一致  
- [ ] REST 示例若使用 `/flowable-rest` 或内嵌 REST，与依赖模块一致  
- [ ] 若使用 Docker 镜像，镜像 tag 与引擎小版本一致  

---

## 与本仓库示例的关系

路径 `flowable-examples/spring-boot-example` 等可能使用**较旧**的 Boot 与 Flowable 版本，**仅供对照**，不等同于专栏默认矩阵。迁移时请参考官方 Release Notes：**Flowable 6 → 7** 与 **Spring Boot 2 → 3** 需分别评估。
