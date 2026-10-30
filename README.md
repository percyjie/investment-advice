# investment-advice

个人持仓再平衡助手：每周给出"买什么、买多少"，每天扫描风险，推送到飞书/钉钉。

**不接券商、不自动下单、不动资金**。所有买卖由你看完建议后手动执行。

## 工作方式

- **规则引擎算仓位**：目标权重、偏离、交易金额全部由确定性代码计算，可解释、可单测。
- **LLM 只做解读**：DeepSeek 读取新闻标题和板块涨跌，总结行业热点；它拿不到你的资产数字，也改不了任何交易清单。
- **保守配置**：权益 30%、黄金 20%、债券 20%、现金 30%（`config/rules.yaml` 可改）。
- **数据兜底**：行情优先东财，失败自动切新浪/腾讯；数据不新鲜时拒绝给建议，只提示"数据异常"。

## 快速开始

```bash
cd investment-advice
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"

mkdir -p ~/.config/ia
cp .env.example ~/.config/ia/env
chmod 600 ~/.config/ia/env
# 编辑 ~/.config/ia/env，填入 DeepSeek key 和飞书/钉钉 webhook
```

检查环境与数据源：

```bash
.venv/bin/ia check          # 每个数据源的连通性、耗时、最新日期
.venv/bin/ia portfolio      # 持仓估值、权重、与中性目标的差距
```

生成但不推送（先看版式）：

```bash
.venv/bin/ia weekly --dry-run          # 周报，含热点
.venv/bin/ia weekly --no-llm --no-push # 不调模型、不推送，终端看全文
.venv/bin/ia daily --force --no-push   # 日报（--force 让非交易日也跑）
```

测试推送：

```bash
.venv/bin/ia notify-test --channel feishu
.venv/bin/ia notify-test --channel dingtalk
```

报告落盘在 `data/reports/`，`ia show --kind weekly` 查看最近一份。

## 配置

三个 YAML，都在 `config/`：

| 文件 | 作用 |
|---|---|
| `portfolio.yaml` | 你的持仓与现金。**示例数据，必须替换** |
| `rules.yaml` | 目标权重、阈值、风控参数 |
| `universe.yaml` | 可投标的白名单与各桶默认加仓标的 |

持仓格式：

```yaml
meta:
  is_example: false          # 换成真实持仓后改为 false
cash: 50000.00
holdings:
  - code: "510300"
    name: 沪深300ETF
    type: etf                # stock | etf | fund
    bucket: equity_core      # equity_core | equity_satellite | stock | gold | bond
    qty: 10000               # 股/份
    cost: 3.85               # 成本单价
    bought_at: 2025-06-12    # 可选，场外基金 7 天赎回费判断
```

要点：

- 场外基金净值 T+1，报告一律使用最新可得净值并标注日期。
- 买入只买桶内已有标的；桶内没有持仓时买该桶 `default: true` 的标的。热点里出现的未持仓个股只标注"仅观察"，不会被建议买入。
- 加仓标的默认是 ETF；想换成场外基金，改 `universe.yaml` 并确保 `type: fund`。

## 定时任务

见 [ops/install_launchd.md](ops/install_launchd.md)。

- 每日 08:30（工作日）：风险扫描，基于上一交易日收盘。
- 每周六 09:00：周报，周五场外基金净值已更新。

电脑睡眠错过的任务，launchd 唤醒后会补跑一次；报告页脚会标"补发"。

## 目录

```
config/      持仓、规则、白名单
prompts/     LLM 系统提示词与用户模板
src/ia/      market（数据） / features（指标） / rules（决策）
             llm（热点） / report（渲染） / notify（推送）
ops/         launchd 模板与安装说明
tests/       纯函数单测
data/        缓存、快照、报告（gitignore）
logs/        运行日志（gitignore）
```

## 测试

```bash
.venv/bin/python -m pytest
```

覆盖：市场状态投票与急跌保护、目标权重归一化与上下限、再平衡先卖后买/换手上限/现金底线/单票上限/止损/禁追涨/基金赎回费锁定/整手取整、每日 12 条告警、LLM 输出四道校验闸、三种报告渲染。

## 免责

本工具只做信息整理与纪律执行，不构成投资建议。规则参数是用户自行设定的个人偏好，历史数据不代表未来收益。
