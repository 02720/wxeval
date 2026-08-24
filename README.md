# wxeval — 天气预报API准确度评测

从第一性原理出发的预报准确度持续评测系统：**预报只在起报时刻存在一次**，因此系统在起报时捕获各模式原始预报，待验证时刻过后与观测对比，按提前量(lead time)分桶量化每个模式的真实技巧。

## 工作原理

```
每6小时 (GitHub Actions cron):
  ① capture  拉取 3模式 × N城市 最新16天逐小时预报 → data/forecasts/
  ② score    拉取最新观测(ERA5 best_match, 约5天延迟) → 对历史捕获按 lead 分桶增量评分
  ③ prune    完全评分且超过21天保留期的旧捕获删除（仓库体积有界）
  ④ report   生成 reports/latest.md + PNG 图表，连同数据 commit 回仓库
```

- **起报去重**：以 `(模式, 地点, 预报起始时刻)` 为run身份，重复捕获自动跳过，容忍定时任务延迟或漏跑
- **增量幂等评分**：`state.json` 记录每个capture已评分到的时刻；结果CSV按唯一键 `(issue_utc, model, location, bucket)` 先删后插，重复运行不产生重复行
- **lead time 分桶**（左闭右开）: `0-24h / 24-72h / 72-168h / 168-384h`
- **双粒度**：小时级直接逐时配对；日级按当地时区自然日聚合（日均温/日累计降水），日桶由当日本地零点的lead决定（起报日本地日的负lead部分不计入）

## 指标口径（cyeva）

| 要素 | 粒度 | 指标 |
|------|------|------|
| 温度 | 小时+日 | MAE、RMSE、±1℃准确率、±2℃准确率 |
| 降水 | 小时 | 晴雨准确率(%)、TS、空报率FAR(0-1)、漏报率MAR(0-1) |
| 降水 | 日累计 | 晴雨准确率(%)、TS、ETS、BIAS |

注：cyeva各指标单位不一致（命中率类为百分数、TS类为分数），本封装统一输出——准确率为百分数，TS/FAR/MAR/ETS/BIAS为0-1分数。配对样本少于 `min_pairs`(默认24) 的桶跳过。

## 快速开始

```bash
# Python >=3.10,<3.13 (cyeva限制)
pip install .

# 手动执行完整流程
wxeval run --config config/locations.yaml --data-root data

# 或分步执行
wxeval capture   # 仅拉取最新预报
wxeval score     # 仅对已存预报评分
wxeval report    # 仅重新生成报告

pytest           # 离线单元测试（网络测试默认跳过）
pytest -m network  # 真实API冒烟测试
```

## 配置

编辑 `config/locations.yaml`：

```yaml
models:
  - ecmwf_ifs        # ECMWF IFS 全球
  - ncep_gfs_global  # NCEP GFS 全球
  - dwd_icon_global  # DWD ICON 全球

locations:
  - name: 梧州
    latitude: 23.477
    longitude: 111.279
    timezone: Asia/Shanghai

min_pairs: 24         # 单桶最少配对样本
retention_days: 21    # 预报保留天数（需 > 16天预报期 + 观测延迟）
forecast_days: 16
```

## GitHub Actions

- `weather-eval` workflow 每6小时自动运行并提交数据；也可在 Actions 页面手动触发
- `ci` workflow 在 push/PR 时运行 ruff + pytest
- 报告入口：`reports/latest.md`

## 接入新的预报源

实现 `wxeval/sources/base.py` 的 `ForecastSource` 协议：

```python
class MySource:
    model = "my_model"
    def fetch(self, latitude, longitude, forecast_days=16) -> Forecast: ...
```

返回 `Forecast(model, issue_utc, frame)`，frame 为 UTC 时间索引、含 `temperature_2m` 与 `precipitation` 两列的 DataFrame。然后在 `cli.make_sources` 中注册即可复用全部评分/存储/报告设施。

## 目录结构

```
config/locations.yaml      地点与模式配置
src/wxeval/
  ├── cli.py               CLI入口(run/capture/score/report)
  ├── config.py            配置加载与lead分桶定义
  ├── sources/             预报源抽象 + Open-Meteo适配器
  ├── observations.py      Archive API客户端(滚动gzip缓存)
  ├── store.py             预报存储/run去重/state/清理
  ├── evaluate.py          分桶评分编排(幂等)
  ├── metrics.py           cyeva封装
  └── report.py            Markdown报告+图表
data/                      预报捕获、观测缓存、累积结果CSV(随仓库持久化)
reports/latest.md          最新评测报告+图表
```

## 已知边界

- Open-Meteo 免费档非商用，请遵守其 [使用条款](https://open-meteo.com/en/terms)
- ERA5 观测有约5天延迟，故 168-384h 桶的结果约在起报3周后才完整
- 日级评估中，起报当日的本地日因部分早于起报时刻而不参与评分
