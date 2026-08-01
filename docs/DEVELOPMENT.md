# CJDB 开发设计

> 更新日期：2026-07-27

## 1. 核心决策

CJDB 的扩展核心只有两个：

1. **Data Provider**：连接数据来源，负责采集、解析、清洗和转写。
2. **Store Provider**：连接数据去向，负责把统一的作品或账号数据写入外部存储。

Provider 只做协议适配和数据转换。业务状态、数据库事务、任务状态与同步关系由
Services 管理。

删除以下旧概念：

- 不再保留 Spider Service；采集能力由 Data Provider 表达。
- 不再保留 Connector 大目录；具体实现放在各自 Provider 的 `providers/` 目录。
- 不再保留单独的 TikHub 配置 Service；统一使用 Provider setup/status。

## 2. 分层

```text
CLI / API / WebUI / Worker
            │
            ▼
         Services
       ┌────┴────┐
       ▼         ▼
Data Provider  Store Provider
       │         │
       ▼         ▼
外部 API/本地模型  外部存储
```

### 2.1 外部入口

- CLI、API、WebUI 和 Worker 不直接写业务逻辑。
- 所有入口调用同一个 Service 方法。
- API 遇到安装、setup 等长耗时动作时，通过 CLI 派生独立进程并立即返回。
- CLI 面向用户使用 `aweme`、`account`、`transcription`、`provider`、`store`
  等业务名，不暴露内部 Data 模型。

### 2.2 Services

Services 负责：

- 查询与写入本地数据库。
- 作品、账号、转写和同步的状态流水线。
- 获取当前选中的 Data Provider。
- 获取具体 Store 实例并执行写入。
- 建立和对账默认 Store、分组 Store 的同步关系。

每次 `fetch_data()` 只执行一次，不在内部自动重试。失败时机械地记录失败状态和
尝试次数；Worker 或其他调用者根据状态决定是否再次调用。

### 2.3 CLI 输出边界

Services 不处理终端文案、表格、字段裁剪或 `text/json` 格式。每个 CLI 命令把
Service 的标准返回转换为：

```python
CLIResult(
    text="面向终端用户的摘要",
    json={"面向程序的": "结构化结果"},
)
```

`@output_command` 是统一输出边界。它读取命令的 `--format=text|json`，并且只在
命令完成后选择 `CLIResult.text` 或 `CLIResult.json`。领域命令不直接
`json.dumps()`，也不会为了 CLI 展示而修改 Service 返回结构。

Provider 列表是典型例子：Service 的 catalog 仍包含完整参数声明，供 API 和
WebUI 使用；`cli/providers.py` 只在 CLI 内部整理出名称、类型、当前选择和状态。

## 3. Data Provider

目录：

```text
data_provider/
├── base.py
├── types.py
└── providers/
    ├── AGENTS.md
    ├── tikhub/
    ├── http_collector/
    ├── faster_whisper/
    └── funasr/
```

### 3.1 抽象

- `BaseDataProvider`：声明 `setup()`、`status()` 和通用元数据。
- `AwemeProviderMixin`：声明作品采集和视频地址解析。
- `AccountProviderMixin`：声明账号采集。
- `CommentProviderMixin`：声明评论采集。
- `VideoTranscriptionProviderMixin`：声明视频转写。

具体实现采用多继承：

```python
class TikHubProvider(
    BaseDataProvider,
    AwemeProviderMixin,
    CommentProviderMixin,
):
    ...
```

抽象方法必须完整实现，否则 Python 无法实例化该类。Registry 还会检查声明的
Provider Type 与实际 Mixin 是否一致，并拒绝 namespace 冲突。

### 3.2 Provider Type

当前能力类型：

- `douyin_aweme_collect`
- `xiaohongshu_aweme_collect`
- `wechat_channels_aweme_collect`
- `wechat_mp_aweme_collect`
- `xiaohongshu_comment_collect`
- `account_collect`
- `video_transcription`

Provider Type 是用户操作参数；namespace 只负责配置隔离。

```bash
cjdb provider status video_transcription
cjdb provider setup douyin_aweme_collect api_key=...
```

`provider status tikhub` 是无效用法，因为 `tikhub` 是 namespace，不是 Type。

### 3.3 生命周期

- 具体 Provider 必须可以零参数初始化。
- `setup(values)` 是配置输入入口。
- setup 参数由具体 Provider 声明，WebUI 根据声明渲染表单。
- setup 值持久化到 `providers.<namespace>`。
- 当前选择持久化到 `providers.selected.<provider_type>`。
- `status()` 每次动态检查实际可用性，并返回标准 `ProviderStatus`。

一个 Provider 可以实现多个 Type。TikHub 只需配置一次，同一 namespace 下的配置
会被它支持的所有服务复用。

## 4. Store Provider

目录：

```text
store/
├── base.py
├── types.py
├── registry.py
└── providers/
    └── notion_store_provider.py
```

### 4.1 抽象

- `BaseStoreProvider`：声明 setup 参数和 `status()`。
- `AwemeStoreProviderMixin`：声明 `store_aweme()`。
- `AccountStoreProviderMixin`：声明 `store_account()`。
- `Storer`：一个 Store 实例、具体 Provider 与该实例配置的运行时组合。

核心调用：

```python
storer = services.store_providers.get_storer(store_id)
services.stores.store_aweme(aweme, storer)
services.stores.store_account(account, storer)
```

Store 实例记录在数据库中；敏感配置写入 `config.yaml` 的
`stores.<store_id>`，不会由状态接口返回明文。

## 5. 默认 Store 与分组 Store

- 默认 Store：接收所有未删除作品和账号。
- 分组 Store：只接收当前属于该分组的作品和账号。
- 同一个数据可以同时匹配多个默认或分组 Store。
- 同步关系使用唯一约束防止重复。

绑定变化时执行关系对账：

- 新匹配的关系被创建或重新启用。
- 不再匹配的关系被禁用。
- 历史状态和远端记录 ID 保留，不自动删除远端数据。
- 如果同一个 Store 仍由其他分组或默认规则匹配，关系保持启用。

## 6. CLI

主要命令树：

```text
cjdb
├── aweme
├── account
├── transcription
├── group
├── provider
├── store
├── worker
├── webui
└── settings
```

以下两个入口必须调用同一个方法：

```bash
cjdb aweme transcription AWEME_ID
cjdb transcription aweme AWEME_ID
```

Provider 的 `setup`、`status` 和 `logs` 都接收 Provider Type。Store 的默认绑定和
分组绑定分别由 `store default`、`group store` 管理。

## 7. Worker

Worker 主进程只负责：

1. 轮换选择下一类任务。
2. 每轮最多领取一条数据。
3. 通过隐藏 CLI 子命令派生一个短进程。
4. 维护并发上限、心跳和超时。

短进程调用对应 Service。任务失败只更新状态；重试属于 Worker 的外部调度策略。

## 8. 配置

运行配置统一保存在 `config.yaml`：

- `providers.selected`：Type 到 Provider ID 的选择关系。
- `providers.<namespace>`：Provider setup 参数。
- `stores.<store_id>`：Store 实例参数。
- `worker`、`worker_tasks`：调度参数。
- `app.logs_dir`：日志目录。

Pydantic 负责基础结构校验，同时允许第三方 Provider 使用新的 namespace。
`ConfigurationService` 的 getter 使用缓存读取；一次批量读取不会产生重复文件查询。
setter 在完整校验后原子替换 YAML 文件。

## 9. 验证要求

每次后端改动至少执行：

```bash
uv run ruff check src tests
uv run pytest -q
```

重点测试：

- 抽象方法和 Registry 冲突检查。
- Provider Type 与 namespace 的边界。
- 两个转写 CLI 别名调用同一个 Service 方法。
- 默认 Store、分组 Store及解绑后的关系对账。
- API、Services、SQLModel 和临时 SQLite 的端到端流程。
