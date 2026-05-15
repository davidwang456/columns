# 第37章：Panel面板插件开发实战

## 1. 项目背景

"微服务有50+个，每次线上排查问题需要知道谁在调用谁。架构师画了一张依赖拓扑图用的是draw.io——静态的。能不能在Grafana里动态展示服务间调用关系，健康状态用颜色标记，点击能下钻？"

这是典型的"标准面板无法满足"的需求。Grafana内置了Time series、Stat、Table等面板，但面对"网络拓扑图"、"组织架构图"、"自定义流程图"等特殊可视化需求时，必须开发自定义Panel插件。

本章将从零开始开发一个"服务拓扑图"Panel插件，用SVG渲染节点和连线，从数据源动态获取服务依赖关系，支持节点着色和点击下钻。这是Grafana插件开发能力的终极体现。

## 2. 项目设计

**小胖**：大师，我想做一个"服务拓扑图"面板——把微服务之间的调用关系画成图，健康的绿色、异常的红色。用Grafana的现有面板做不到！

**大师**：这正是Panel插件的用武之地。Panel插件的本质是一个React组件，接收DataFrame数据，自己决定怎么渲染——可以用SVG、Canvas、D3.js任何你想要的渲染引擎。

**小白**：Panel插件需要实现哪些东西？

**大师**：一个最简Panel插件包含三部分：

1. **plugin.json**：声明面板类型、名称、版本
2. **Panel组件**（React）：接收数据→渲染可视化
3. **module.ts**：将Panel组件注册为Grafana Plugin

核心API是`PanelProps`——你的React组件收到的Props：
```typescript
interface PanelProps<T = any> {
    data: PanelData;             // 查询返回的数据
    options: T;                  // 面板选项（Threshold/Color等）
    width: number;               // 面板宽度
    height: number;              // 面板高度
    fieldConfig: FieldConfigSource; // 字段配置
    timeRange: TimeRange;        // 时间范围
    onChangeTimeRange: (range: TimeRange) => void;
}
```

你的任务就是根据`data`中的DataFrame，用SVG/Canvas画出自定义的图形。

**小胖**：能具体一点吗？拓扑图怎么做？

**大师**：把DataFrame的两列——`source`和`target`——作为图的节点和边。比如：
```
source | target | status | qps
order  | user   | 1      | 500
order  | payment| 1      | 300
user   | mysql  | 0      | 200
```

你的Panel组件解析这些数据：
1. 提取所有不重复的服务名作为节点
2. 每行数据是一条连线
3. 用简单的力学布局算法给节点定位（或固定位置）
4. SVG渲染圆形节点+文字标签+带箭头的连线
5. 根据status字段着色（1=绿，0=红）

**小白**：那Panel如何与Grafana的主题系统适配？

**大师**：通过`useTheme2()` hook获取当前主题色——自动适配Dark/Light模式。

**技术映射**：Panel插件 = 自定义画布（给你原料和画笔，你自由创作），PanelProps = 工具箱（提供数据、尺寸、配置），SVG渲染 = 不受分辨率限制的矢量绘制，useTheme2 = 自动换肤（跟随系统深色/浅色模式）。

## 3. 项目实战

**步骤一：创建Panel插件项目**

```bash
npx @grafana/create-plugin@latest
# 选择：panel
# 名称：service-topology-panel
```

核心文件结构：
```
src/
├── components/
│   └── ServiceTopologyPanel.tsx    # 主面板组件
├── types.ts                         # 类型定义
├── module.ts                        # 插件注册
├── plugin.json                      # 元数据
└── README.md
```

**步骤二：实现Panel组件**

`src/components/ServiceTopologyPanel.tsx`：

```tsx
import React, { useMemo } from 'react';
import { PanelProps, getFieldDisplayName } from '@grafana/data';
import { useTheme2 } from '@grafana/ui';

interface NodeData {
    name: string;
    x: number;
    y: number;
    status: number;
}

interface EdgeData {
    source: string;
    target: string;
    qps: number;
}

export const ServiceTopologyPanel: React.FC<PanelProps> = ({ data, width, height }) => {
    const theme = useTheme2();
    
    // 从DataFrame解析节点和边
    const { nodes, edges } = useMemo(() => {
        if (!data.series.length) return { nodes: [], edges: [] };
        
        const frame = data.series[0];
        const sourceIdx = frame.fields.findIndex(f => f.name === 'source');
        const targetIdx = frame.fields.findIndex(f => f.name === 'target');
        const statusIdx = frame.fields.findIndex(f => f.name === 'status');
        const qpsIdx = frame.fields.findIndex(f => f.name === 'qps');
        
        const nodeMap = new Map<string, number>();
        const edges: EdgeData[] = [];
        let nodeIdx = 0;
        
        for (let i = 0; i < frame.length; i++) {
            const source = frame.fields[sourceIdx].values[i];
            const target = frame.fields[targetIdx].values[i];
            const status = frame.fields[statusIdx]?.values[i] ?? 1;
            const qps = frame.fields[qpsIdx]?.values[i] ?? 0;
            
            if (!nodeMap.has(source)) {
                nodeMap.set(source, nodeIdx++);
            }
            if (!nodeMap.has(target)) {
                nodeMap.set(target, nodeIdx++);
            }
            
            edges.push({ source, target, qps });
        }
        
        // 简单的圆形布局
        const centerX = width / 2;
        const centerY = height / 2;
        const radius = Math.min(width, height) * 0.35;
        const nodes: NodeData[] = [];
        let i = 0;
        
        nodeMap.forEach((idx, name) => {
            const angle = (2 * Math.PI * i) / nodeMap.size;
            nodes.push({
                name,
                x: centerX + radius * Math.cos(angle),
                y: centerY + radius * Math.sin(angle),
                status: 1, // 默认健康
            });
            i++;
        });
        
        return { nodes, edges };
    }, [data, width, height]);
    
    const getColor = (status: number) => {
        return status === 1 ? theme.colors.success.main : theme.colors.error.main;
    };
    
    return (
        <svg width={width} height={height} style={{ background: theme.colors.background.canvas }}>
            {/* 连线 */}
            {edges.map((edge, i) => {
                const srcNode = nodes.find(n => n.name === edge.source);
                const tgtNode = nodes.find(n => n.name === edge.target);
                if (!srcNode || !tgtNode) return null;
                
                return (
                    <g key={`edge-${i}`}>
                        <line
                            x1={srcNode.x} y1={srcNode.y}
                            x2={tgtNode.x} y2={tgtNode.y}
                            stroke={theme.colors.border.medium}
                            strokeWidth={Math.min(edge.qps / 100, 5)}
                        />
                        {/* 箭头 */}
                        <polygon
                            points={`${tgtNode.x},${tgtNode.y} ${tgtNode.x-5},${tgtNode.y-3} ${tgtNode.x-5},${tgtNode.y+3}`}
                            fill={theme.colors.border.medium}
                        />
                    </g>
                );
            })}
            
            {/* 节点 */}
            {nodes.map((node) => (
                <g key={node.name}>
                    <circle
                        cx={node.x}
                        cy={node.y}
                        r={25}
                        fill={getColor(node.status)}
                        stroke={theme.colors.border.strong}
                        strokeWidth={2}
                    />
                    <text
                        x={node.x}
                        y={node.y + 5}
                        textAnchor="middle"
                        fill={theme.colors.text.primary}
                        fontSize={11}
                        fontWeight="bold"
                    >
                        {node.name.slice(0, 10)}
                    </text>
                </g>
            ))}
        </svg>
    );
};
```

**步骤三：注册插件**

`src/module.ts`：

```typescript
import { PanelPlugin } from '@grafana/data';
import { ServiceTopologyPanel } from './components/ServiceTopologyPanel';

export const plugin = new PanelPlugin(ServiceTopologyPanel)
    .setPanelOptions(builder => {
        return builder
            .addNumberInput({
                path: 'nodeRadius',
                name: '节点半径',
                defaultValue: 25,
            });
    });
```

`src/plugin.json`：

```json
{
  "type": "panel",
  "name": "服务拓扑图",
  "id": "service-topology-panel",
  "info": {
    "description": "展示微服务间调用关系的拓扑图",
    "author": { "name": "MyCompany" },
    "version": "1.0.0"
  }
}
```

**步骤四：测试面板**

```json
// 在TestData DB中创建测试数据
{
  "scenarioId": "csv_content",
  "csvContent": "source,target,status,qps\norder-service,user-service,1,500\norder-service,payment-service,1,300\norder-service,inventory-service,0,200\nuser-service,mysql,1,200\npayment-service,redis,1,100"
}
```

在Grafana中：
1. 创建Dashboard → Add panel → 面板类型选择"服务拓扑图"
2. 数据源选择TestData DB
3. 粘贴CSV数据
4. 面板上应显示圆形节点图和连线

**步骤五：进阶——添加交互**

```tsx
// 点击节点跳转到对应服务Dashboard
const onNodeClick = (nodeName: string) => {
    // 使用Grafana的LocationService跳转
    window.location.href = `/d/service-dashboard?var-service=${nodeName}`;
};

// 在circle上添加onClick
<circle onClick={() => onNodeClick(node.name)} style={{ cursor: 'pointer' }} />

// 悬停Tooltip
<title>{`${node.name}\nStatus: ${node.status === 1 ? 'Healthy' : 'Critical'}`}</title>
```

**步骤六：打包与签名**

```bash
# 编译
yarn build
mage -v build:linux

# 打包
npx @grafana/plugin-validator@latest -sourcePath ./dist

# 签名（如需发布到Grafana插件市场）
npx @grafana/plugin-sign@latest
```

**常见坑点**
1. **SVG坐标溢出**：节点坐标超出SVG视口 → 设置`viewBox`或缩放。
2. **主题不响应**：没有用`useTheme2()`而是用了硬编码颜色 → Light模式下白字白底看不清。
3. **数据更新后面板不刷新**：React组件依赖的props没包含在依赖数组中 → 检查`useMemo`的依赖列表。

## 4. 项目总结

**Panel插件核心能力**

| 层级 | 内容 |
|------|------|
| 基础 | 接收DataFrame、渲染SVG/Canvas |
| 进阶 | 支持Panel Options配置、响应主题 |
| 高级 | 交互（点击/悬停）、下钻、动画 |

**Panel插件最佳实践**
1. 用SVG做静态图（节点少时性能好），Canvas做大数据量图（10000+节点）
2. 始终使用`useTheme2()`获取颜色，不硬编码
3. 面板尺寸变化时（`width`/`height`变化）需要重新布局

**思考题**
1. 如果服务拓扑图有1000+个节点和5000+条连线，SVG渲染会卡顿。如何用Canvas或WebGL优化？
2. 如何为这个拓扑图面板添加"拖拽节点重新布局"的功能？
