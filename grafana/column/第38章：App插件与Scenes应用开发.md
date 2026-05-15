# 第38章：App插件与Scenes应用开发

## 1. 项目背景

"运维团队需要一个'发布日历'功能——在Grafana里展示各服务的发布时间窗口，和监控指标放在同一个平台。但这不是一个面板能解决的——它需要独立页面、独立路由、自己的数据逻辑。"

这是App插件的典型场景。Panel插件解决的是"一种新的可视化方式"，DataSource插件解决的是"一种新的数据接入方式"，而App插件解决的是"一个完整的子应用"——它可以有自己的多个页面、独立路由、甚至可以包含自己的Panel和DataSource。Grafana的Alerting、Explore等核心功能本质就是App插件。

最新的Scenes框架（`@grafana/scenes`）更是将App开发提升到新高度——用声明式API构建Dashboard级别的复杂应用，而不需要深入编写React组件。本章将带你开发一个"发布日历"App插件，深入Scenes框架。

## 2. 项目设计

**小胖**：大师，什么是App插件？和Panel插件听起来差不多。

**大师**：区别很大。Panel插件只是Dashboard上的一个"图表"。App插件可以：
1. 注册独立的左侧导航菜单项
2. 拥有自己的页面（可以有多个路由）
3. 在页面中内嵌Dashboard、Panel、自定义React组件
4. 整个插件的权限管理

Grafana的Alerting、Connections、Administration本质上都是App插件。

**小白**：Scenes框架又是什么？

**大师**：Scenes是Grafana的新一代Dashboard运行时框架。传统Dashboard开发需要直接操作React组件树、Redux Store。Scenes提供了更高层的声明式抽象：

```typescript
const scene = new EmbeddedScene({
    $timeRange: new SceneTimeRange({ from: 'now-1h', to: 'now' }),
    $data: new SceneQueryRunner({
        datasource: { type: 'prometheus', uid: 'prometheus' },
        queries: [{ expr: 'up' }],
    }),
    body: new SceneFlexLayout({
        children: [
            new SceneFlexItem({
                body: PanelBuilders.timeseries().setTitle('QPS').build(),
            }),
        ],
    }),
});
```

你描述"我要一个Dashboard，里面有一个Time series面板，数据源是Prometheus，查询up"，Scenes帮你生成对应的React组件和Redux state。不需要写JSX。

**小胖**：那App + Scenes能做什么？

**大师**：比如做一个"发布日历"App：
- 左侧导航多一个"发布日历"菜单
- 主页面用Scenes构建
- 上方是一个自定义React组件（日历控件）
- 下方是Scenes Dashboard（监控指标）
- 选择日期后，下方的监控指标自动更新为当天的发布窗口数据

**技术映射**：App插件 = 自建商场里的品牌店（独立店面、独立管理），Scenes = 预制装配式建筑（用构件搭房子，不用一块块搬砖），Panel插件 = 店内的一个货架。

## 3. 项目实战

**步骤一：创建App插件**

```bash
npx @grafana/create-plugin@latest
# 选择：app
# 名称：release-calendar-app
# 勾选：Include Scenes support
```

**步骤二：配置plugin.json**

```json
{
  "type": "app",
  "name": "发布日历",
  "id": "release-calendar-app",
  "includes": [
    {
      "type": "page",
      "name": "发布日历",
      "path": "/a/release-calendar-app",
      "role": "Viewer",
      "action": "release-calendar-app:read"
    }
  ],
  "roles": [
    {
      "role": { "name": "Reader", "description": "查看发布日历" },
      "permissions": [
        { "action": "release-calendar-app:read" }
      ]
    }
  ]
}
```

**步骤三：实现Scenes页面**

`src/pages/ReleaseCalendarPage.tsx`：

```typescript
import React, { useState } from 'react';
import {
    EmbeddedScene,
    SceneFlexLayout,
    SceneFlexItem,
    SceneTimeRange,
    SceneQueryRunner,
    PanelBuilders,
    SceneAppPage,
    SceneAppPageState,
    SceneVariableSet,
    TestVariable,
} from '@grafana/scenes';
import { Button, DatePicker, HorizontalGroup } from '@grafana/ui';

// 自定义组件包裹Scenes
function ReleaseCalendarScene() {
    const [selectedDate, setSelectedDate] = useState<Date>(new Date());
    
    const queryRunner = new SceneQueryRunner({
        datasource: { type: 'prometheus', uid: 'prometheus' },
        queries: [
            {
                refId: 'A',
                expr: `deployment_frequency_total{date="${selectedDate.toISOString().split('T')[0]}"}`,
            },
        ],
    });
    
    const scene = new EmbeddedScene({
        $timeRange: new SceneTimeRange({
            from: new Date(selectedDate.getFullYear(), selectedDate.getMonth(), selectedDate.getDate()).toISOString(),
            to: new Date(selectedDate.getFullYear(), selectedDate.getMonth(), selectedDate.getDate() + 1).toISOString(),
        }),
        $data: queryRunner,
        body: new SceneFlexLayout({
            direction: 'column',
            children: [
                new SceneFlexItem({
                    height: '40%',
                    body: PanelBuilders.stat()
                        .setTitle('今日发布次数')
                        .setUnit('short')
                        .build(),
                }),
                new SceneFlexItem({
                    height: '60%',
                    body: PanelBuilders.table()
                        .setTitle('发布详情')
                        .build(),
                }),
            ],
        }),
    });
    
    return (
        <div>
            <div style={{ padding: '16px', background: 'var(--background-secondary)' }}>
                <HorizontalGroup>
                    <DatePicker
                        value={selectedDate}
                        onChange={(date) => setSelectedDate(date)}
                    />
                    <Button variant="primary">刷新数据</Button>
                </HorizontalGroup>
            </div>
            <scene.Component model={scene} />
        </div>
    );
}
```

**步骤四：注册App页面与路由**

`src/module.ts`：

```typescript
import { AppPlugin } from '@grafana/data';
import { ReleaseCalendarPage } from './pages/ReleaseCalendarPage';

export const plugin = new AppPlugin()
    .setRootPage(ReleaseCalendarPage)
    .configureExtensionLink({
        title: '发布日历',
        description: '查看服务发布日历和窗口状态',
        path: '/a/release-calendar-app',
    });
```

**步骤五：Scenes高级——变量联动**

```typescript
const scene = new EmbeddedScene({
    $variables: new SceneVariableSet({
        variables: [
            new TestVariable({
                name: 'service',
                label: '服务',
                query: 'label_values(http_requests_total, service)',
                datasource: { type: 'prometheus', uid: 'prometheus' },
            }),
        ],
    }),
    body: new SceneFlexLayout({
        children: [
            new SceneFlexItem({
                body: PanelBuilders.timeseries()
                    .setTitle('${service} - QPS')
                    .build(),
            }),
        ],
    }),
});
```

变量变化时，面板的查询自动更新——和Dashboard的变量行为完全一致。

**步骤六：Scenes Panel Builder**

便利的PanelBuilder API：
```typescript
// Time series
PanelBuilders.timeseries()
    .setTitle('CPU使用率')
    .setUnit('percent')
    .setDecimals(1)
    .setCustomFieldConfig('fillOpacity', 15)
    .build();

// Stat
PanelBuilders.stat()
    .setTitle('QPS')
    .setColorMode(FieldColorModeId.Thresholds)
    .setThresholds({ steps: [{ value: 0, color: 'green' }, { value: 100, color: 'red' }] })
    .build();

// Table
PanelBuilders.table()
    .setTitle('服务列表')
    .setFooterOptions({ show: true, countRows: true })
    .build();

// 自定义面板（你开发的Panel插件）
PanelBuilders.custom(MyPanelComponent)
    .setTitle('自定义视图')
    .build();
```

**常见坑点**
1. **Scenes版本兼容**：`@grafana/scenes`要求Grafana版本≥10.0。旧版本Grafana无法运行Scenes应用。
2. **组件生命周期**：Scenes对象不是React组件——它们需要在`useEffect`中管理生命周期。使用`scene.Component`渲染。
3. **导航菜单不出现在左侧**：`plugin.json`中`includes`配置的`type: "page"`必须正确，路径必须以`/a/`开头。

**步骤七：实战——Scenes自定义布局与动态数据**

实现一个完整的仪表盘视图，包含多个动态面板。

```typescript
import {
    EmbeddedScene,
    SceneFlexLayout,
    SceneFlexItem,
    SceneTimeRange,
    SceneQueryRunner,
    SceneVariableSet,
    TestVariable,
    SceneTimePicker,
    SceneRefreshPicker,
    SceneByFrameRepeater,
    PanelBuilders,
    VizPanel,
} from '@grafana/scenes';

function buildReleaseDashboard() {
    // 变量系统
    const serviceVar = new TestVariable({
        name: 'service',
        label: '服务',
        query: 'label_values(http_requests_total, service)',
        datasource: { type: 'prometheus', uid: 'prometheus' },
    });

    // 查询执行器
    const qpsQuery = new SceneQueryRunner({
        datasource: { type: 'prometheus', uid: 'prometheus' },
        queries: [{
            refId: 'A',
            expr: 'sum(rate(http_requests_total{service="${service}"}[5m]))',
            legendFormat: '${service}',
        }],
    });

    const errorQuery = new SceneQueryRunner({
        datasource: { type: 'prometheus', uid: 'prometheus' },
        queries: [{
            refId: 'B',
            expr: 'sum(rate(http_requests_total{service="${service}",status=~"5.."}[5m])) / sum(rate(http_requests_total{service="${service}"}[5m])) * 100',
        }],
    });

    return new EmbeddedScene({
        $variables: new SceneVariableSet({
            variables: [serviceVar],
        }),
        $timeRange: new SceneTimeRange({ from: 'now-6h', to: 'now' }),
        controls: [
            new SceneTimePicker({}),
            new SceneRefreshPicker({}),
        ],
        body: new SceneFlexLayout({
            direction: 'column',
            children: [
                // 顶部KPI行
                new SceneFlexLayout({
                    direction: 'row',
                    height: '30%',
                    children: [
                        new SceneFlexItem({
                            body: new VizPanel({
                                title: 'QPS',
                                pluginId: 'stat',
                                $data: qpsQuery,
                                fieldConfig: {
                                    defaults: {
                                        unit: 'short',
                                        color: { mode: 'thresholds' },
                                        thresholds: {
                                            steps: [
                                                { value: 0, color: 'green' },
                                                { value: 1000, color: 'red' },
                                            ],
                                        },
                                    },
                                },
                            }),
                        }),
                        new SceneFlexItem({
                            body: new VizPanel({
                                title: 'Error Rate %',
                                pluginId: 'stat',
                                $data: errorQuery,
                            }),
                        }),
                    ],
                }),
                // 趋势图行
                new SceneFlexLayout({
                    direction: 'row',
                    height: '70%',
                    children: [
                        new SceneFlexItem({
                            body: new VizPanel({
                                title: 'QPS Trend',
                                pluginId: 'timeseries',
                                $data: qpsQuery,
                            }),
                        }),
                    ],
                }),
            ],
        }),
    });
}

// 导出页面
export const releaseCalendarPage = new SceneAppPage({
    title: '发布日历',
    url: '/a/release-calendar-app',
    getScene: buildReleaseDashboard,
});
```

**Scenes事件系统**：

```typescript
// 订阅变量变化
scene.state.$variables?.subscribeToState((state) => {
    const service = state.variables[0].getValue();
    console.log('Service changed to:', service);
});

// 订阅时间范围变化
scene.state.$timeRange?.subscribeToState((state) => {
    console.log('Time range:', state.value.from, state.value.to);
});

// 编程式触发刷新
scene.state.$data?.runQueries();
```

**Scenes调试**：
```typescript
// 启用Scenes调试插件
import { ScenesDebugger } from '@grafana/scenes-debugger';

// 在开发模式下
const scene = new EmbeddedScene({
    $debug: new ScenesDebugger(),
    // ...其他配置
});

// 在浏览器Console中查看Scenes对象树
// 输入：window.__scenesDebugger.getSceneTree()
```

**Grafana插件类型对比**

| 类型 | 用途 | 复杂 | 典型场景 |
|------|------|------|---------|
| Panel | 新可视化图表 | 中 | 拓扑图、流程图、3D图 |
| DataSource | 新数据接入 | 高 | 自研数据库、私有协议 |
| App | 完整子应用 | 高 | 发布日历、合规报表 |

**App + Scenes开发流程**
1. `create-plugin`脚手架
2. 定义`plugin.json`（注册页面和权限）
3. 用Scenes构建页面（声明式Dashboard）
4. 用React开发自定义交互组件
5. 注册导航和路由

**思考题**
1. Scenes框架生成的Dashboard和用户手动创建的Dashboard，底层使用的是同一套渲染引擎吗？性能有差异吗？
2. App插件如何实现"多Tab"页面——一个App有"概览"和"详情"两个子页面，共享顶部的变量？
