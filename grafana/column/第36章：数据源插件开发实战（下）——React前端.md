# 第36章：数据源插件开发实战（下）——React前端

## 1. 项目背景

"Go后端能跑通了，在Grafana的Explore里写JSON查询也能出数据。但用户不能每次去写JSON吧？需要一个可视化的查询编辑器界面——选指标、设过滤条件、选时间范围。"

第35章实现了数据源插件的Go后端，但前端还是一个空壳。用户需要点击选指标而不是手写JSON。本章将完成数据源插件的React前端——包括数据源配置编辑器（ConfigEditor）、查询编辑器（QueryEditor）、变量查询（MetricFindValue）和E2E测试。

## 2. 项目设计

**小胖**：大师，后端QueryData返回数据了，但前端不知道怎么写。ConfigEditor和QueryEditor是啥？

**大师**：Grafana数据源插件有两个核心前端组件：

**ConfigEditor**：数据源配置页面。用户在这里填写服务器URL、认证Token等。Grafana首次添加数据源时展示。

**QueryEditor**：查询编辑器。用户在Dashboard/Explore中编辑查询条件时展示。每个面板的查询都需要一个QueryEditor。

**小白**：它们和后端怎么通信？

**大师**：通过DataSourceWithBackend类。前端调用`this.query()`或`this.testDatasource()`，自动通过gRPC调用后端的`QueryData()`和`CheckHealth()`。

```typescript
// datasource.ts
export class CompanyDBDataSource extends DataSourceWithBackend<MyQuery, MyDataSourceOptions> {
    constructor(instanceSettings: DataSourceInstanceSettings<MyDataSourceOptions>) {
        super(instanceSettings);
    }
    
    async query(options: DataQueryRequest<MyQuery>): Promise<DataQueryResponse> {
        // DataSourceWithBackend自动调用后端QueryData
        return super.query(options);
    }
    
    async testDatasource(): Promise<TestDataSourceResponse> {
        // DataSourceWithBackend自动调用后端CheckHealth
        return super.testDatasource();
    }
}
```

**小胖**：那一些不需要查后端的功能呢？比如变量下拉框的Metric列表？

**大师**：MetricFindValue（变量查询）需要前端实现。因为Grafana通过`datasource.metricFindQuery()`来获取变量选项：

```typescript
async metricFindQuery(query: string): Promise<MetricFindValue[]> {
    const response = await this.fetchMetrics(); // 自定义方法
    return response.metrics.map(m => ({ text: m }));
}
```

**技术映射**：ConfigEditor = 安装向导（首次配置时填写连接信息），QueryEditor = 遥控器面板（日常使用时操作），DataSourceWithBackend = 自动翻译机（前端操作自动转为gRPC调用）。

## 3. 项目实战

**环境准备**：基于第35章的插件项目，已生成Go后端。

**步骤一：定义类型**

`src/types.ts`：

```typescript
export interface MyQuery extends DataQuery {
    metricName: string;
    aggregation: 'avg' | 'max' | 'min';
    filters: Record<string, string>;
}

export interface MyDataSourceOptions extends DataSourceJsonData {
    serverURL: string;
}

export const DEFAULT_QUERY: Partial<MyQuery> = {
    metricName: '',
    aggregation: 'avg',
    filters: {},
};
```

**步骤二：实现ConfigEditor**

`src/ConfigEditor.tsx`：

```typescript
import React, { ChangeEvent } from 'react';
import { InlineField, InlineFieldRow, Input, SecretInput } from '@grafana/ui';
import { DataSourcePluginOptionsEditorProps } from '@grafana/data';
import { MyDataSourceOptions } from './types';

interface Props extends DataSourcePluginOptionsEditorProps<MyDataSourceOptions> {}

export function ConfigEditor(props: Props) {
    const { onOptionsChange, options } = props;
    
    const onServerURLChange = (event: ChangeEvent<HTMLInputElement>) => {
        onOptionsChange({
            ...options,
            jsonData: {
                ...options.jsonData,
                serverURL: event.target.value,
            },
        });
    };
    
    const onAuthTokenChange = (event: ChangeEvent<HTMLInputElement>) => {
        onOptionsChange({
            ...options,
            secureJsonData: {
                authToken: event.target.value,
            },
        });
    };
    
    const onResetAuthToken = () => {
        onOptionsChange({
            ...options,
            secureJsonFields: {
                ...options.secureJsonFields,
                authToken: false,
            },
            secureJsonData: {
                ...options.secureJsonData,
                authToken: '',
            },
        });
    };
    
    return (
        <div>
            <InlineFieldRow>
                <InlineField label="Server URL" labelWidth={14} tooltip="数据库服务器地址">
                    <Input
                        width={40}
                        value={options.jsonData.serverURL || ''}
                        onChange={onServerURLChange}
                        placeholder="https://companydb.internal:9090"
                    />
                </InlineField>
            </InlineFieldRow>
            
            <InlineFieldRow>
                <InlineField label="Auth Token" labelWidth={14}>
                    <SecretInput
                        width={40}
                        value={options.secureJsonData?.authToken || ''}
                        isConfigured={options.secureJsonFields?.authToken ?? false}
                        onChange={onAuthTokenChange}
                        onReset={onResetAuthToken}
                        placeholder="输入认证Token"
                    />
                </InlineField>
            </InlineFieldRow>
        </div>
    );
}
```

**步骤三：实现QueryEditor**

`src/QueryEditor.tsx`：

```typescript
import React, { useState, useEffect, useCallback } from 'react';
import { QueryEditorProps, SelectableValue } from '@grafana/data';
import { InlineField, Select, Input } from '@grafana/ui';
import { DataSource } from './datasource';
import { MyDataSourceOptions, MyQuery } from './types';

type Props = QueryEditorProps<DataSource, MyQuery, MyDataSourceOptions>;

const AGGREGATION_OPTIONS = [
    { label: '平均值', value: 'avg' },
    { label: '最大值', value: 'max' },
    { label: '最小值', value: 'min' },
];

export function QueryEditor({ query, onChange, onRunQuery, datasource }: Props) {
    const [metrics, setMetrics] = useState<Array<SelectableValue<string>>>([]);
    const [isLoading, setIsLoading] = useState(false);
    
    // 加载可用的指标列表
    const loadMetrics = useCallback(async () => {
        setIsLoading(true);
        try {
            const result = await datasource.metricFindQuery?.('metrics');
            setMetrics(result?.map(m => ({ label: m.text, value: m.text })) || []);
        } finally {
            setIsLoading(false);
        }
    }, [datasource]);
    
    useEffect(() => {
        loadMetrics();
    }, [loadMetrics]);
    
    const onMetricChange = (value: SelectableValue<string>) => {
        onChange({ ...query, metricName: value.value || '' });
        onRunQuery(); // 自动执行查询
    };
    
    const onAggregationChange = (value: SelectableValue<string>) => {
        onChange({ ...query, aggregation: (value.value as any) || 'avg' });
        onRunQuery();
    };
    
    return (
        <div>
            <InlineField label="指标" labelWidth={10}>
                <Select
                    width={40}
                    value={query.metricName}
                    options={metrics}
                    onChange={onMetricChange}
                    isLoading={isLoading}
                    placeholder="选择指标"
                    isClearable
                />
            </InlineField>
            
            <InlineField label="聚合方式" labelWidth={10}>
                <Select
                    width={20}
                    value={query.aggregation}
                    options={AGGREGATION_OPTIONS}
                    onChange={onAggregationChange}
                />
            </InlineField>
        </div>
    );
}
```

**步骤四：实现DataSource类**

`src/datasource.ts`：

```typescript
import { 
    DataSourceWithBackend, 
    MetricFindValue, 
    getBackendSrv,
} from '@grafana/runtime';
import { 
    DataSourceInstanceSettings, 
    CoreApp,
    ScopedVars,
} from '@grafana/data';
import { MyQuery, MyDataSourceOptions, DEFAULT_QUERY } from './types';
import { Observable, from } from 'rxjs';

export class DataSource extends DataSourceWithBackend<MyQuery, MyDataSourceOptions> {
    private serverURL: string;
    
    constructor(instanceSettings: DataSourceInstanceSettings<MyDataSourceOptions>) {
        super(instanceSettings);
        this.serverURL = instanceSettings.jsonData.serverURL || '';
    }
    
    // 变量查询（MetricFindValue）
    async metricFindQuery(query: string): Promise<MetricFindValue[]> {
        if (query === 'metrics') {
            // 调用后端CallResource获取指标列表
            // 或者直接HTTP请求
            const response = await this.getResource('metrics');
            return response?.metrics?.map((m: string) => ({
                text: m,
            })) || [];
        }
        return [];
    }
    
    // 调用自定义资源端点
    async getResource(path: string): Promise<any> {
        const response = await getBackendSrv().get(
            `api/datasources/${this.id}/resources/${path}`
        );
        return response;
    }
}
```

**步骤五：注册插件**

`src/plugin.json`：

```json
{
  "id": "companydb-datasource",
  "name": "CompanyDB",
  "type": "datasource",
  "metrics": true,
  "annotations": false,
  "alerting": true,
  "backend": true,
  "executable": "gpx_companydb-datasource",
  "queryOptions": {
    "minInterval": true
  },
  "info": {
    "description": "CompanyDB 数据源插件",
    "author": { "name": "MyCompany" },
    "version": "1.0.0"
  }
}
```

**步骤六：module.ts入口**

`src/module.ts`：

```typescript
import { DataSourcePlugin } from '@grafana/data';
import { DataSource } from './datasource';
import { ConfigEditor } from './ConfigEditor';
import { QueryEditor } from './QueryEditor';
import { MyQuery, MyDataSourceOptions } from './types';

export const plugin = new DataSourcePlugin<DataSource, MyQuery, MyDataSourceOptions>(DataSource)
    .setConfigEditor(ConfigEditor)
    .setQueryEditor(QueryEditor);
```

**步骤七：添加后端Resource端点（支持变量查询）**

在Go后端添加`CallResource`接口：

```go
func (d *CompanyDBDatasource) CallResource(ctx context.Context, req *backend.CallResourceRequest, sender backend.CallResourceResponseSender) error {
    switch req.Path {
    case "metrics":
        metrics, err := d.client.ListMetrics(ctx)
        if err != nil {
            return sender.Send(&backend.CallResourceResponse{
                Status: http.StatusInternalServerError,
                Body:   []byte(err.Error()),
            })
        }
        
        body, _ := json.Marshal(map[string][]string{"metrics": metrics})
        return sender.Send(&backend.CallResourceResponse{
            Status: http.StatusOK,
            Body:   body,
        })
    default:
        return sender.Send(&backend.CallResourceResponse{
            Status: http.StatusNotFound,
        })
    }
}
```

**步骤八：测试**

```bash
# 启动Grafana
docker compose up

# 在Grafana中：
# 1. 添加CompanyDB数据源 → 填写URL和Token → Save & test → 确认连接成功
# 2. 创建Dashboard → Add panel → 选择CompanyDB数据源
# 3. QueryEditor应显示指标下拉框 → 选择指标 → 数据正确显示

# E2E测试
cd companydb-datasource
npx @grafana/plugin-e2e
```

**常见坑点**
1. **ConfigEditor的onChange没有触发后端重新连接**：修改ServerURL后需要点击Save & test才能验证新地址。
2. **SecretInput的isConfigured状态**：已经配置过的Token在编辑页面不显示原始值（只显示"configured"）。`onReset`回调用于清除。
3. **QueryEditor的onRunQuery不生效**：只有在Explore中`onRunQuery`会自动执行。在Dashboard中需要用户手动点击刷新。

## 4. 项目总结

**前端开发关键文件**

| 文件 | 作用 |
|------|------|
| `src/types.ts` | TypeScript类型定义 |
| `src/datasource.ts` | DataSource类（核心） |
| `src/ConfigEditor.tsx` | 数据源配置页面 |
| `src/QueryEditor.tsx` | 查询编辑器 |
| `src/module.ts` | 插件注册入口 |
| `plugin.json` | 插件元数据声明 |

**常用API**
| API | 用途 |
|-----|------|
| `super.query(options)` | 执行查询（调用后端QueryData） |
| `super.testDatasource()` | 测试连接（调用后端CheckHealth） |
| `this.getResource(path)` | 调用后端CallResource |
| `this.metricFindQuery()` | 变量查询 |
| `this.getTagKeys/Values()` | 标签查询 |

**思考题**
1. 如果QueryEditor需要支持"查询历史"功能（记录用户最近选择的10个指标），应该把历史数据存在哪里？
2. 如何为这个插件设计E2E测试，验证用户从选择指标到看到图表的完整流程？
