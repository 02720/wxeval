# 天气预报API准确度评测程序 设计文档

日期: 2026-08-24
状态: 已批准

## 1. 目标

从第一性原理构建天气预报准确度持续评测系统：预报在起报时刻才存在，必须**捕获发布时刻的预报**，待验证时刻过后与观测对比，按提前量(lead time)分桶量化各模式技巧。

## 2. 需求（已确认）

1. 多预报源，先支持 Open-Meteo 三模式：`ecmwf_ifs`、`ncep_gfs_global`、`dwd_icon_global`；架构可扩展
2. 观测真值：Open-Meteo Historical Weather API (`/v1/archive`, best_match)
3. 评估框架：cyeva，仅温度、降水两要素
4. lead time 分桶：0–24h / 24–72h / 72–168h / 168–384h（左闭右开）
5. 多地点：初始 梧州(23.477,111.279)、博白(22.271,109.978)、平南(23.546,110.393)、万宁(18.795,110.389)，配置文件可改
6. GitHub Actions 每6小时定时自动运行
7. 评估粒度分小时级、日级（按当地时区自然日聚合）

输出形态：累积CSV + Markdown报告 + PNG图表。

方法论（用户选定方案A）：纯实时捕获+延迟评分，不做历史回填；backfill 仅预留接口。

## 3. 核心循环

```
每6小时 cron (UTC 20 */6 * * *):
  ① capture  拉取 3模型 × N城市 最新16天逐小时预报(temp/precip) → data/forecasts/
  ② score    拉取最新可得观测 → 对所有未完全评分的capture增量评分 → data/results/*.csv 追加
  ③ report   生成 reports/latest.md + PNG 图表
  ④ prune    完全评分且过保留期的capture删除（滚动窗口≈21天），仓库体积有界
```

## 4. 关键语义

- **issue/起报识别**: Open-Meteo `/v1/forecast` 始终返回最新完整run。以 `(model, location, 预报首小时UTC时间)` 为run身份去重；重复捕获跳过，容忍cron延迟/漏跑。
- **lead time** = valid_time_utc − issue_utc。分桶见上。
- **观测延迟**: ERA5约5天。每次评分尝试获取 `[now−10d, now−2d]` 观测窗口，能评多少评多少；`data/results/state.json` 记录每个capture的 `scored_through`(UTC)，保证幂等增量。
- **对齐**: 双方均转UTC时间戳索引后 inner join，dropna成对样本再评。
- **日级聚合**: 按 `Asia/Shanghai` 自然日：日均温(cyeva kind='24h'对应累计降水)、日累计降水。
- **指标**:
  - 温度（小时&日）: MAE, RMSE, ±1℃准确率, ±2℃准确率 (`TemperatureComparison`)
  - 降水小时: 晴雨准确率, TS, 空报率, 漏报率 (kind='1h')
  - 降水日: 晴雨准确率, TS, ETS, BIAS (kind='24h')
- **最小样本**: 配对样本<24 跳过该次评分（记 skipped）。

## 5. 架构

```
wxeval/
├── src/wxeval/
│   ├── cli.py            # argparse: run|capture|score|report|backfill(预留NotImplemented)
│   ├── config.py         # YAML加载+校验
│   ├── sources/base.py   # ForecastSource抽象: fetch(location)->ForecastFrame
│   ├── sources/open_meteo.py
│   ├── observations.py   # ObsClient: fetch(loc,start,end)->DataFrame(time,temp,precip)
│   ├── store.py          # run去重存储/state.json读写/prune
│   ├── evaluate.py       # 编排: 遍历未完成capture→分桶→调metrics→追加CSV
│   ├── metrics.py        # cyeva封装, 输出统一metric行
│   ├── report.py         # Markdown生成
│   └── charts.py         # matplotlib三图
├── config/locations.yaml
├── data/
│   ├── forecasts/{issue_utc}/{model}/{location}.csv.gz   # time,temperature_2m,precipitation
│   ├── observations/{location}.csv.gz                    # 滚动观测缓存
│   └── results/{hourly.csv,daily.csv,state.json}
├── reports/latest.md + charts/*.png
├── .github/workflows/eval.yml    # permissions: contents:write; commit+push
├── tests/                        # pytest; requests mock; @network标记冒烟
└── pyproject.toml                # py3.11+, ruff, pytest
```

依赖: `requests, pandas, numpy, pyyaml, cyeva, matplotlib`

## 6. 结果CSV schema

公共键列: `issue_utc, model, source_model, location, bucket, granularity`
指标列: 温度 `temp_mae, temp_rmse, temp_acc1, temp_acc2, n_pairs`
降水 `precip_binary_acc, precip_ts, precip_far, precip_mar, precip_ets, precip_bias, n_pairs`
- hourly.csv 与 daily.csv 分文件；同一唯一键重复出现时先删后插（幂等）。

## 7. 报告结构

运行元信息 → 跨城市平均的模式×分桶总表 → 分城市明细表 → 三图:
(a) 温度MAE 分桶×模式柱状 (b) 日降水TS 分桶×模式柱状 (c) 近30天日均温MAE趋势线 → 错误日志节。

## 8. 错误处理

- 单模型/单城市失败隔离，错误收集进报告"Errors"节，进程退出码非0仅当全部失败。
- HTTP: 重试3次指数退避；超时30s。
- API返回null值/缺变量: 解析为NaN，不致命。
- cyeva对全NaN/无降水样本: TS等记空字符串，不抛异常中断。

## 9. 测试策略

- 单测（无网络）: mock responses——API解析、lead分桶边界、日聚合时区、run去重、state幂等、prune、metrics已知答案小数组。
- 冒烟: `@pytest.mark.network` 手动本地跑真实API，CI跳过。
- CI(push): pytest + ruff check。

## 10. GitHub Actions

- cron `20 */6 * * *` UTC + workflow_dispatch手动触发。
- 步骤: checkout → setup-python 3.12 → pip install . → `wxeval run` → git add data/reports → commit `[data] eval YYYY-MM-DDTHH` → push。
- 允许失败策略: 步骤②③失败不影响commit已捕获数据。

## 11. 未来扩展（不在本期）

backfill（Open-Meteo Historical Forecast API回填）、新预报源适配器、风要素、Pages仪表盘。
