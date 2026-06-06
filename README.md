# Delta Force Data Center - 三角洲行动数据分析中心

Delta Force Data Center 是一个面向《三角洲行动》WeGame 战绩的本地数据中心。它可以把你的战绩、对局详情、队友表现、带出物品和账号资产集中到本地 Web 面板里，方便查询、分析、导出和备份。

开源版不需要额外授权服务，所有本地功能默认可用。仓库内置了一份已脱敏的样例数据，首次启动后无需登录 WeGame 也能直接体验主要页面。

## 开源协议

本项目采用 `AGPL-3.0-only` 协议。

选择 AGPLv3 的原因是：本项目包含本地 Web 面板和数据服务能力。如果有人基于本项目二次开发，并将修改后的版本发布、分发，或作为可联网访问的服务提供给他人使用，需要继续开放对应源代码，并沿用同样的开源协议。

协议全文见 [LICENSE](LICENSE)。

## 功能亮点

### 战绩总览

- 查看历史对局、地图、干员、撤离结果、击杀、收益、时长等核心信息
- 按账号、地图、结果和时间范围筛选战绩
- 打开单局详情，查看本局表现、队友信息和带出物品
- 自动标记高光或异常对局，例如高价值撤离、连续失败、落地成盒等

### 带出物品

- 汇总每局带出的物品、数量、品质和价值
- 支持按自己、队友或全部玩家切换视角
- 支持按时间和物品价值排序，快速定位高价值对局

### 数据分析

- 统计总对局数、撤离率、击杀、收益、平均时长等指标
- 查看不同地图、干员和结果的分布
- 展示高收益、高击杀等代表性对局
- 支持按时间范围查看阶段表现

### 数据趋势

- 按日、周、月查看关键指标变化
- 观察撤离率、收益、击杀、平均时长等趋势
- 对比不同阶段的游戏状态变化

### 组队分析

- 选择 1 到 3 名玩家，分析共同对局表现
- 查看固定队友之间的胜率、收益、击杀和配合情况
- 支持导出 Excel 表格和图片报告
- 可生成 AI PDF 分析报告，用更直观的文字总结队伍表现

### 数据抓取与备份

- 登录 WeGame 后抓取战绩列表、对局详情、房间详情和账号资产
- 支持定量抓取和智能补全缺失详情
- 支持导出备份包，也可以导入旧备份恢复数据
- 数据保存在本机，适合作为个人长期战绩档案

## 快速开始

需要 Python 3.10 或更高版本。

首次运行前安装依赖：

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Windows 用户可以直接双击：

```text
dev-local.bat
```

也可以用命令行启动：

```powershell
python -m src web -p 18080
```

启动后打开：

```text
http://127.0.0.1:18080
```

如果不想自动打开浏览器：

```powershell
python -m src web -p 18080 --no-browser
```

## 样例数据

仓库内置的样例数据库位于：

```text
data/vault/store/db.bin
```

样例账号名为 `阿萨拉机枪兵`，包含 46 场经过公开发布处理的真实对局数据。玩家名、玩家 ID、房间号和队友标识都已替换为稳定随机值，游戏时间也已加入随机秒数偏移。

这份开源副本不包含个人本地游戏数据库。

## AI PDF 报告

AI 报告读取项目根目录的 `config.ai.json`：

```json
{
  "base_url": "https://api.openai.com/v1",
  "api_key": "请填写你的 API Key",
  "model": "gpt-4.1-mini",
  "timeout_seconds": 120,
  "temperature": 0.3
}
```

接口需要兼容 OpenAI Chat Completions。你也可以在 Web 面板的组队分析页面中编辑 AI 设置。

`config.ai.json` 可能保存你的私人 API Key，发布分支或打包前请确认不要提交真实密钥。

## 常用命令

```powershell
python -m src login
python -m src fetch -q sol -n 100
python -m src fetch -q mp -n 50
python -m src export -f all
python -m src stats
python -m src web -p 18080
```

队列说明：

- `sol`：烽火地带
- `mp`：全面战场

## 数据与隐私

- 所有数据默认保存在本机 `data/` 目录下
- 仓库中的 `data/vault/store/db.bin` 是脱敏样例数据库
- 登录自己的 WeGame 账号后，新数据只会写入本地数据库
- 不要提交运行时数据库、Cookie、原始请求、生成报告、进程号或私人 API Key
- 发布 fork 前，请重点检查 `config.ai.json` 和 `data/` 目录

## 说明

本项目是非官方的本地数据管理与分析工具。WeGame 页面和接口可能变化，登录和抓取功能后续可能需要维护适配。
