# 专栏配套示例约定

为避免每章重复一套脚手架，示例代码建议：

1. **单仓库多模块**（或单模块多包）：公共父 POM 锁定 [VERSION_MATRIX.md](../VERSION_MATRIX.md) 中的 JDK / Spring Boot / Flowable。  
2. **按章分包**：`com.neuratech.column.ch01` … `ch36`，仅新增本章流程与委托类。  
3. **流程资源**：`src/main/resources/processes/chXX-*.bpmn20.xml`，与章节编号一致。  
4. **联跑**：每章 `README` 一段说明 `mvn -pl chXX test` 或与全量测试的切换方式。

本仓库若未提交具体实现，专栏读者可在自有空项目按各章「项目实战」粘贴；编辑器请以章内版本脚注为准。
