# 天气预报准确度评测程序 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline)

**Goal:** 构建实时捕获预报、延迟按lead-time分桶评分的持续评测系统（Open-Meteo 3模式 × 4城市起步）。

**Architecture:** 每6小时GH Action执行 capture→score→report→prune 循环；预报源通过 ForecastSource 抽象适配；观测来自 Archive API；结果累积CSV幂等追加；仓库滚动保留≈21天捕获。

**Tech Stack:** Python ≥3.10,<3.13；requests/pandas/numpy/pyyaml/matplotlib；cyeva==0.2.3 + flexparser==0.3.1（修复传递依赖）；pytest/ruff。

## Global Constraints

- Python `>=3.10,<3.13`；cyeva `0.2.3` 需 `flexparser==0.3.1`
- 无网络单测：所有HTTP经注入session mock；真实网络测试标 `@pytest.mark.network` 默认跳过
- 代码不加注释；时区统一UTC内部存储，日聚合用地点本地时区
- 唯一键：`(issue_utc, model, location, bucket)`；粒度分文件 hourly.csv/daily.csv

## Task 1: 项目骨架

Files: `pyproject.toml`, `src/wxeval/__init__.py`, `config/locations.yaml`, `.gitignore`

pyproject要点: hatchling构建；`[project.scripts] wxeval="wxeval.cli:main"`；deps见上。locations.yaml含4城市+models列表+buckets配置。
验证: `uv pip install -e .` 成功，`wxeval --help` 可用。Commit.

## Task 2: config模块

Files: `src/wxeval/config.py`; Test: `tests/test_config.py`

```python
@dataclass(frozen=True)
class Location: name:str; latitude:float; longitude:float; timezone:str
def load_locations(path)->list[Location]   # 校验必填字段,纬度[-90,90],经度[-180,180]
def load_models(path)->list[str]           # 默认3模式
BUCKETS=((0,24,"0-24h"),(24,72,"24-72h"),(72,168,"72-168h"),(168,384,"168-384h"))
```
Test: 正常解析4城市；缺字段/越界报 ValueError。

## Task 3: 预报源抽象 + Open-Meteo

Files: `src/wxeval/sources/base.py`, `src/wxeval/sources/open_meteo.py`, `tests/test_open_meteo.py`

```python
# base.py
class SourceError(Exception): ...
class ForecastSource(Protocol):
    model: str
    def fetch(self, loc: Location, *, forecast_days: int = 16) -> pd.DataFrame:
        """返回DataFrame: time(UTC DatetimeIndex), temperature_2m, precipitation; 
        df.attrs={"issue_utc": iso, "model": str}"""
def get_with_retry(fn, retries=3, base_delay=2.0)  # 指数退避,网络类异常重试

# open_meteo.py
BASE="https://api.open-meteo.com/v1/forecast"
class OpenMeteoForecastSource:
    def __init__(self, model:str, session=requests.Session): ...
    # params: hourly=temperature_2m,precipitation; models=<model>; timezone=GMT;
    #         forecast_days=16; temperature_unit=celsius; precipitation_unit=mm
```
解析规则: `hourly.time`为ISO无时区(GMT)→`pd.to_datetime(..., utc=True)`；null→NaN。API错误响应含`error:true`与`reason`→抛SourceError。
Test: fixture小payload(48h×2变量含null)→断言列/索引/attrs/null处理；error响应抛SourceError；retry逻辑(monkeypatch sleep计数)。

## Task 4: 观测客户端

Files: `src/wxeval/observations.py`, `tests/test_observations.py`

```python
ARCHIVE="https://archive-api.open-meteo.com/v1/archive"
class ObsClient:
    def __init__(self, root: Path, session=...): ...   # root=data目录
    def fetch_update(self, loc, start, end) -> pd.DataFrame:
        # GET best_match(不传models即best_match)；合并进缓存 data/observations/{name}.csv.gz
        # 合并后按time去重排序返回完整缓存
    def load(self, loc) -> pd.DataFrame                 # 读缓存,无则空DF
```
Test: mock响应→缓存写入；二次fetch不同区间→并集去重；NaN保留。

## Task 5: 存储/状态/清理

Files: `src/wxeval/store.py`, `tests/test_store.py`

```python
@dataclass(frozen=True)
class Capture: issue_utc:pd.Timestamp; model:str; location:str; path:Path
def capture_key(issue, model, location) -> str          # f"{issue:%Y%m%dT%H%M}|{model}|{loc}"
def save_capture(root, df, model, location) -> bool     # data/forecasts/{issue:%Y%m%dT%H}Z/{model}/{loc}.csv.gz
                                                        # 已存在→False跳过(run去重核心)
def list_captures(root) -> list[Capture]
def load_capture(c:Capture) -> pd.DataFrame
def load_state(root) -> dict[str,str]; save_state(root,state)   # data/results/state.json, 原子写(tmp+rename)
def prune(root, state, *, retention=pd.Timedelta(days=21))      # 删除 issue<now-retention 的capture及其state项
```
Test: 存取往返；同key二次save返回False；prune只删过期且同步清理state。

## Task 6: cyeva指标封装

Files: `src/wxeval/metrics.py`, `tests/test_metrics.py`

```python
def temp_metrics(obs,fcst)->dict    # temp_mae,temp_rmse,temp_acc1,temp_acc2 (TemperatureComparison.calc_diff_accuracy_ratio(limit=n))
def precip_hourly_metrics(obs,fcst)->dict  # precip_binary_acc,precip_ts,precip_far,precip_mar (kind='1h')
def precip_daily_metrics(obs,fcst)->dict   # 上者+precip_ets,precip_bias (kind='24h')
# 全部round4; 异常/全NaN→对应键填nan不抛出
```
Test: 手算已知答案数组(如obs=[0,1,5],fcst=[0,0,4]: hit=1,miss=1,fa=0→TS=.5,FAR=0,MAR=.5)；全NaN→nan字典；无降水→ts=nan。

## Task 7: 评估编排

Files: `src/wxeval/evaluate.py`, `tests/test_evaluate.py`

```python
def bucket_label(lead_hours)->str|None       # 边界左闭右开; >384或<0→None
def score_capture(cap, obs_df, state, writer, min_pairs=24):
    # fcst join obs on UTC小时(inner,dropna成对)
    # hourly: 每桶一组→temp+precip指标行(key=issue|model|loc|bucket)
    # 仅处理 valid<=max_obs_time 且 > state[scored_through] 的行
    # daily: 两序列转地点本地自然日(temp日均mean,precip日累计sum),
    #        日桶由该地当日local午夜lead判定; precip_daily_metrics(kind='24h'语义天然匹配日累计)
    # 更新 state[key].scored_through=max(valid processed)
def run_scoring(root, locations, sources, obs, *, now=None) -> ScoringSummary
    # 遍历未完成capture(scored_through < issue+384h),逐个score,收集errors
def append_rows(csv_path, rows, keys)  # 幂等upsert: 删旧同key行→concat→sort→write
```
Test: 构造跨边界数据(lead恰24h入24-72桶)；本地日聚合(+08:00跨UTC日界)；跑两遍行数不变(幂等)；部分观测→state推进且后续补评剩余。

## Task 8: 报告与图表

Files: `src/wxeval/report.py`, `src/wxeval/charts.py`, `tests/test_report.py`

```python
def build_report(root,out_md): 
  # 读hourly/daily.csv→按model×bucket跨城市均值透视表→markdown表格
  # 图: charts.temp_mae_by_bucket(),charts.precip_ts_by_bucket(),charts.temp_mae_trend(30d)
  # 尾部Errors节读data/results/lastrun_errors.json
# charts: matplotlib.use("Agg"); PNG存reports/charts/
```
Test: 小CSV→md含预期表头与模型名；3个PNG生成非空。

## Task 9: CLI整合

Files: `src/wxeval/cli.py`, `tests/test_cli.py`

```python
# argparse子命令: capture|score|report|run|backfill(NotImplementedError退出2)
# run=capture→score→report, 单点失败隔离记入errors; 全部失败exit1否则0
main(argv=None)->int
```
Test: tmp根目录+FakeSource/FakeObs注入→run产生 forecasts/results/reports全套产物；重复run幂等。

## Task 10: GitHub Actions

Files: `.github/workflows/eval.yml`, `.github/workflows/ci.yml`

eval.yml: cron `20 */6 * * *` + workflow_dispatch; concurrency组防重叠; py3.12; `pip install .`; `wxeval run`; `if: always()` 提交data/reports(github-actions[bot]); 失败仍commit已捕获数据。
ci.yml: push/PR→ruff check + `pytest -m "not network"`。

## Task 11: README + 收尾

README: 原理、快速开始、CLI用法、配置说明、Actions说明、指标口径、扩展新源指南。`.gitignore`: .venv/__pycache__/pytest_cache/ruff; data与reports**不忽略**(设计如此)。加data/.gitkeep占位。

## Task 12: 全面验证

pytest全绿; ruff clean; 离线端到端smoke; 本地network冒烟(`-m network`)真实拉一次数据; 之后进入对抗式审查阶段。
