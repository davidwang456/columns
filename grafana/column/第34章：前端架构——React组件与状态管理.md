# 第34章：前端架构——React组件与状态管理

## 1. 项目背景

"我想修改Grafana面板的Time picker组件，加一个'本周'的快捷选项。但Grafana前端是React+Redux+TypeScript堆栈——组件嵌套至少5层，状态管理贯穿整个应用，改一个组件可能影响10个页面。"

前端工程师小周收到了产品需求——在时间选择器中增加常用快捷选项。虽然React的经验丰富，但Grafana的前端是一个运行了8年的大型SPA，代码架构和约定的学习曲线很高。理解它的组件树、状态管理（Redux Toolkit）、核心SDK包，是进行任何前端二次开发的前提。

本章将深入Grafana前端的React架构、Redux状态管理、Grafana UI组件库和插件加载机制，让你能自信地修改前端代码。

## 2. 项目设计

**小胖**（被VSCode里一片TypeScript代码淹没）：大师，Grafana前端的代码量是后端的5倍以上！public/app/目录下面有features、core、plugins、angular……光看名字完全不知道什么是什么。React组件之间的关系像蜘蛛网。

**大师**：Grafana前端确实庞大，但它有一个清晰的架构层次。

**第一层：入口和路由**

`public/app/app.tsx`是React应用根组件。它初始化Redux store、设置路由、渲染主布局。

路由使用React Router（v5），定义在`public/app/routes/routes.tsx`。顶级路由包括：
```
/ → 首页
/d/:uid → Dashboard页面
/explore → Explore页面
/alerting → Alerting页面
/datasources → 数据源管理
/admin → 系统管理
```

**第二层：核心包（Packages）**

Grafana前端代码分两部分：`packages/`（发布的NPM包）和`public/`（应用代码）。

核心SDK包：
- `@grafana/data`：数据类型、DataFrame、面板Plugin API
- `@grafana/ui`：UI组件库（Button/Modal/Select/TimePicker等）
- `@grafana/runtime`：运行时API（DataSource、TemplateSrv、BackendSrv）
- `@grafana/schema`：Dashboard/Panel的JSON Schema

**第三层：Redux状态管理**

Grafana使用Redux Toolkit（新标准）管理全局状态。核心Store结构：

```typescript
{
  user: { ... },           // 当前用户信息
  navIndex: { ... },       // 导航菜单
  dataSources: { ... },    // 数据源列表（缓存）
  dashboard: { ... },      // 当前Dashboard状态
  templating: { ... },     // 模板变量状态
  explore: { ... },        // Explore页面状态
  panels: { ... },         // 面板编辑器状态
}
```

每个模块用一个`slice`管理自己的状态。比如Dashboard的状态在`public/app/features/dashboard/state/reducers.ts`。

**小白**：那组件之间怎么通信？比如TimePicker时间变化后，面板怎么知道要重新查询？

**大师**：Grafana通过`@grafana/runtime`的`TimeSrv`服务实现跨组件通信。这不是React Props传递，而是通过单例Service：

```typescript
import { getTimeSrv } from '@grafana/runtime';

// TimePicker修改时间
getTimeSrv().setTime({ from: 'now-1w', to: 'now' });

// 面板订阅时间变化
getTimeSrv().timeRangeChanged.subscribe((timeRange) => {
    // 重新查询数据
    runQueries();
});
```

这种"全局Service总线"模式是Grafana前端架构的精髓——组件不需要知道彼此，通过Service通信。

**小胖**：那插件是怎么加载的？Grafana怎么知道一个插件是Panel还是DataSource？

**大师**：插件加载通过`plugin.json`声明。Grafana启动时扫描插件目录，读取每个插件的`plugin.json`：

```json
{
  "id": "my-custom-panel",
  "name": "My Custom Panel",
  "type": "panel",
  "info": { ... }
}
```

根据`type`字段，Grafana将插件注册到对应的Registry。前端通过`@grafana/runtime`的`getPluginImportUtils()`动态加载插件的JS Bundle。

现代Grafana使用SystemJS或Module Federation来动态加载插件代码——这是一种微前端架构。

**技术映射**：Redux Store = 全局公告板（任何人都能读但不能直接改），Service总线 = 公司内线电话（各部门通过电话通信），Plugins = 外接设备（通过标准接口接入系统），Slices = 部门档案柜（各部门独立管理自己的文件）。

## 3. 项目实战

**环境准备**：基于第32章的开发环境。

**步骤一：修改TimePicker添加快捷选项**

需求：在时间选择器中增加"本周"的快捷选项。

文件：`packages/grafana-ui/src/components/TimePicker/TimePickerContent.tsx`

找到快捷选项定义，添加：
```typescript
const quickOptions: TimeOption[] = [
  // 现有选项...
  { from: 'now-1w/w', to: 'now-1w/w+1w', display: '本周', section: 1 },
];
```

验证：`yarn start`启动开发模式 → 打开Grafana → 时间选择器中应出现"本周"选项。

**步骤二：理解Redux Slice**

以Dashboard Slice为例，添加一个自定义状态：

文件：`public/app/features/dashboard/state/reducers.ts`

添加一个新的action和reducer：
```typescript
// Action
export const dashboardViewCountUpdated = createAction<number>('dashboard/viewCountUpdated');

// Reducer中处理
builder.addCase(dashboardViewCountUpdated, (state, action) => {
  state.viewCount = action.payload;
});
```

在组件中使用：
```typescript
import { useDispatch, useSelector } from 'react-redux';

function MyComponent() {
    const viewCount = useSelector((state: StoreState) => state.dashboard.viewCount);
    const dispatch = useDispatch();
    
    useEffect(() => {
        dispatch(dashboardViewCountUpdated(42));
    }, []);
    
    return <div>Viewed: {viewCount} times</div>;
}
```

**步骤三：创建自定义Grafana UI组件**

在`packages/grafana-ui/src/components/`下创建新组件：

```typescript
// packages/grafana-ui/src/components/StatusBadge/StatusBadge.tsx
import React from 'react';
import { useTheme2 } from '../../themes';

interface StatusBadgeProps {
    status: 'healthy' | 'warning' | 'critical';
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
    const theme = useTheme2();
    
    const colors = {
        healthy: theme.colors.success.main,
        warning: theme.colors.warning.main,
        critical: theme.colors.error.main,
    };
    
    return (
        <span style={{ 
            backgroundColor: colors[status],
            color: '#fff',
            padding: '2px 8px',
            borderRadius: '4px',
            fontSize: '12px',
        }}>
            {status.toUpperCase()}
        </span>
    );
};
```

**步骤四：理解插件加载过程**

以Stat面板为例，跟踪插件从注册到渲染：

1. `plugin.json`声明面板类型→Grafana扫描注册→`PanelPlugin`对象创建

2. Dashboard加载时→读取`panel.type: "stat"`→查找对应的`PanelPlugin`→获取React组件→渲染

3. 面板数据更新时→Redux更新panelData→React重新渲染

插件生命周期：
```
registerPlugin → loadPlugin → PanelPlugin.sync → Panel.setState → Panel.render
```

**步骤五：开发模式下的调试技巧**

**React DevTools**：
1. 安装React DevTools浏览器插件
2. 打开Grafana → 点击DevTools → Components标签 → 查看组件树和Props

**Redux DevTools**：
1. 安装Redux DevTools插件
2. 查看每个action对state的改变
3. Time-travel调试（回退到之前的状态）

**追踪渲染性能**：
```typescript
import { Profiler } from 'react';

<Profiler id="DashboardPanel" onRender={(id, phase, actualDuration) => {
    console.log(`${id} ${phase}: ${actualDuration}ms`);
}}>
    <DashboardPanel />
</Profiler>
```

**常见坑点**
1. **修改`packages/`后应用不更新**：`packages/`编译为独立的NPM包，需要重新构建（`yarn build`）才能被`public/`引用。
2. **Redux State不可变**：必须用`createSlice`的Immer API或展开运算符修改state，直接修改会导致React不重新渲染。
3. **样式不生效**：Grafana使用Emotion CSS-in-JS，不是普通CSS文件。需用`useStyles2()`或`css={{ color: '...' }}`。

**步骤六：实战——添加Dashboard使用统计面板到首页**

在前端实现一个显示"最常访问Dashboard"的组件。

**1. 创建React组件**（`public/app/features/dashboard/components/MostAccessedDashboards.tsx`）：
```tsx
import React, { useEffect, useState } from 'react';
import { getBackendSrv } from '@grafana/runtime';
import { useStyles2, Card, Spinner } from '@grafana/ui';
import { css } from '@emotion/css';

interface DashboardStat {
    dashboard_uid: string;
    title: string;
    access_count: number;
    last_access: number;
}

export const MostAccessedDashboards: React.FC = () => {
    const [dashboards, setDashboards] = useState<DashboardStat[]>([]);
    const [loading, setLoading] = useState(true);
    const styles = useStyles2(getStyles);

    useEffect(() => {
        const fetchStats = async () => {
            try {
                const result = await getBackendSrv().get(
                    '/api/dashboards/stats/most-accessed?limit=10'
                );
                setDashboards(result as DashboardStat[]);
            } finally {
                setLoading(false);
            }
        };
        fetchStats();
    }, []);

    if (loading) return <Spinner />;

    return (
        <div className={styles.container}>
            <h3>常用Dashboard</h3>
            {dashboards.map(d => (
                <a
                    key={d.dashboard_uid}
                    href={`/d/${d.dashboard_uid}`}
                    className={styles.link}
                >
                    {d.title}
                    <span className={styles.count}>
                        访问: {d.access_count}次
                    </span>
                </a>
            ))}
        </div>
    );
};

const getStyles = (theme: GrafanaTheme2) => ({
    container: css`
        padding: ${theme.spacing(2)};
    `,
    link: css`
        display: flex;
        justify-content: space-between;
        padding: ${theme.spacing(1)};
        border-bottom: 1px solid ${theme.colors.border.weak};
        &:hover {
            background: ${theme.colors.background.secondary};
        }
    `,
    count: css`
        color: ${theme.colors.text.secondary};
        font-size: ${theme.typography.size.sm};
    `,
});
```

**2. 修改Redux Slice添加状态管理**（`public/app/features/dashboard/state/reducers.ts`）：
```typescript
export interface DashboardState {
    // ...现有字段
    mostAccessed: DashboardStat[];
    mostAccessedLoading: boolean;
}

const initialState: DashboardState = {
    mostAccessed: [],
    mostAccessedLoading: false,
};

const dashboardSlice = createSlice({
    name: 'dashboard',
    initialState,
    reducers: {
        mostAccessedLoaded(state, action: PayloadAction<DashboardStat[]>) {
            state.mostAccessed = action.payload;
            state.mostAccessedLoading = false;
        },
        mostAccessedLoading(state) {
            state.mostAccessedLoading = true;
        },
    },
});
```

**3. 注册到首页**：

在`public/app/routes/routes.tsx`找到首页组件，添加`<MostAccessedDashboards />`到首页渲染树中。

**4. 编译验证**：
```bash
yarn start  # 开发模式验证
# 打开 http://localhost:3001 查看首页是否显示"常用Dashboard"区域
```

**设计规范遵循**：

Grafana前端有严格的设计规范：
- 组件Props必须继承`HTMLAttributes`（如果渲染为原生元素）
- 样式必须使用`useStyles2()` + Emotion CSS
- 数据获取使用`getBackendSrv()`而不是`fetch()`
- 组件必须支持`data-testid`属性（用于E2E测试）
- 使用`@grafana/ui`的组件而不是直接写原生HTML元素

示例：
```tsx
// ✅ 正确
import { Button, Field, Input } from '@grafana/ui';

// ❌ 错误
<button className="my-btn">Click</button>
<input type="text" />
```

**Grafana前端关键技术栈**

| 技术 | 用途 | 文件分布 |
|------|------|---------|
| React 18 | 组件框架 | `public/app/` |
| Redux Toolkit | 状态管理 | `public/app/features/*/state/` |
| Emotion | CSS-in-JS | 各组件内联 |
| RxJS | 响应式事件流 | TemplateSrv, TimeSrv |
| React Router v5 | 路由 | `public/app/routes/` |
| SystemJS | 插件动态加载 | `public/app/features/plugins/` |
| Turborepo | Monorepo管理 | 根目录 |

**推荐学习路线**
1. 先看`public/app/app.tsx` → 了解整体入口
2. 再看`@grafana/data`包的types → 理解核心数据类型
3. 追溯一个面板的完整渲染链路（从Dashboard加载到Panel显示）
4. 尝试修改一个简单组件（如TimePicker文本）

**思考题**
1. Grafana前端Redux Store中有哪些不同的Slice？如果新增一个"全局公告"功能（顶部横幅通知），应该在哪个Slice管理状态？
2. 一个Panel组件接收到新数据后，Grafana如何保证React只重渲染必要的部分？用到了哪些优化手段？
