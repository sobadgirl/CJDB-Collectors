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
- 建立和对账默认 Store、项目 Store 的同步关系。

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
domains/data_provider/
├── base.py
├── types.py
└── providers/
    ├── AGENTS.md
    ├── tikhub/
    ├── faster_whisper/
    └── funasr/
```

### 3.1 抽象

- `BaseDataProvider`：声明无默认实现的 `setup()`、`status()` 和通用元数据。
- `AwemeProviderMixin`：声明作品采集和视频地址解析。
- `DouyinAccountProviderMixin`：声明抖音账号采集。
- `XiaohongshuAccountProviderMixin`：声明小红书账号采集。
- `WeChatChannelsAccountProviderMixin`：声明视频号账号采集。
- `WeChatMpAccountProviderMixin`：声明公众号账号采集。
- `CommentProviderMixin`：声明评论采集。
- `VideoTranscriptionProviderMixin`：声明视频转写。

具体实现采用多继承：

```python
class TikHubProvider(
    BaseDataProvider,
    AwemeProviderMixin,
    CommentProviderMixin,
    DouyinAccountProviderMixin,
    XiaohongshuAccountProviderMixin,
    WeChatChannelsAccountProviderMixin,
    WeChatMpAccountProviderMixin,
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
- `douyin_comment_collect`
- `xiaohongshu_comment_collect`
- `wechat_channels_comment_collect`
- `wechat_mp_comment_collect`
- `douyin_account_collect`
- `xiaohongshu_account_collect`
- `wechat_channels_account_collect`
- `wechat_mp_account_collect`
- `video_transcription`

Provider Type 是用户操作参数；namespace 只负责配置隔离。

```bash
cjdb provider status video_transcription
cjdb provider setup douyin_aweme_collect api_key=...
```

`provider status tikhub` 是无效用法，因为 `tikhub` 是 namespace，不是 Type。

### 3.3 生命周期

- 具体 Provider 必须可以零参数初始化。
- Service 的 `setup(values)` 是参数输入入口；Provider 的 `setup(params)` 只执行自身
  初始化并返回标准 `SetupResult`。
- setup 参数由具体 Provider 声明，WebUI 根据声明渲染表单。
- setup params 只用于本次初始化；成功返回的 `setup_payload` 持久化到对应
  `Provider` 实例，后续通过 `Provider(setup_payload=...)` 注入。
- `ProjectProvider` 只保存 Project 与 Provider 的多对多可用关系。
- `ProjectProviderSelection(project_id, provider_type, provider_id)` 保存实际路由；
  三列组成联合主键。采集和转写类型写入时替换同类型旧记录，Store 类型允许
  同一类型存在多行，从而把一份数据同步到多个目标。
- `SetupResult` 包含 `success`、`message`、`logs` 和 Provider 私有
  `setup_payload`，不包含
  `ProviderStatus`。
- 只有 Provider setup 返回成功后，Service 才主动调用 `refresh_status()`，并保存标准
  `ProviderStatus`。

一个 Provider 可以实现多个 Type。TikHub 只需配置一次，同一 namespace 下的配置
会被它支持的所有服务复用。

## 4. Store Provider

Provider 框架本身不区分 Data Provider 和 Store Provider。所有实现共享
`BaseProvider`、`ProviderRegistry`、`ProviderType` 和数据库中的 `Provider` 实例表；
两个目录只用于按业务约定组织实现代码。旧的 `BaseDataProvider`、
`BaseStoreProvider` 和对应 Registry 是面向现有业务调用的兼容入口，底层都接入同一个
Base 与 Registry。

Provider Class 保存静态信息：`namespace`、`name`、`supported_types` 和
`parameters`。数据库 `providers` 表保存可复用配置实例：实例名称、`namespace`、成功
setup 返回的 `setup_payload_json`、status、status payload、检查时间和下次检查时间。
`supported_types` 不写入数据库。调用时先按 `ProviderType` 从 Registry 取得 Class
分组，再通过 `ProjectProviderSelection` 使用 `project_id + provider_type` 直接找到
Provider ID。读取 Provider 记录后，仅使用 namespace 定位 Python 实现类，不再用
namespace 猜测当前应该选择哪个实例。

`DataStorer`、`DefaultDataStorer` 和 `ProjectDataStorer` 已合并进 Provider 模型。同步关系
统一保存 `provider_id`。新建 Provider 会同时绑定当前项目；“从其他项目导入”只创建一条
新的 `ProjectProvider` 关系，不复制 setup payload。Provider 列表必须同时接受
`project_id` 与 `provider_type` 过滤。

Worker 对需要 Provider 的任务执行双门禁：先查询是否存在到期任务；没有任务时不读取
Provider。存在任务后，再根据任务和平台解析 `ProviderType`，只有被选中 Provider 的
持久化状态为 `ready` 才 claim 并调度任务。任一条件不满足都不会启动真实采集进程。

目录：

```text
domains/store/
├── base.py
├── types.py
├── registry.py
└── providers/
    └── notion_store_provider.py
```

### 4.1 抽象

- `BaseStoreProvider`：初始化时接收当前 Store 的配置，并声明无参数的
  `setup()`、`status()`。
- `AwemeStoreProviderMixin`：声明 `store_aweme()`。
- `AccountStoreProviderMixin`：声明 `store_account()`。

所有写入方法接收业务 DTO 和上一次成功的 `StoreResult`，并返回本次调用结果：

```python
StoreResult(
    success=True,
    message=None,
    success_payload={"provider_owned_key": "value"},
)
```

`success` 和 `message` 只用于 Service 更新同步关系的 `status` 与
`error_message`。核心系统不解释 `success_payload`；它只在调用成功时覆盖同步关系中
保存的 payload，并在下次调用时原样交还同一个 Provider。Provider 的预期失败应返回
`success=False`，不应抛出异常；Service 会把第三方实现意外抛出的异常统一转换成失败
结果。

Registry 保存 Provider 类而不是共享实例。运行时按同步关系中的 `provider_id` 从统一
`Provider` 表读取 namespace 与 `setup_payload_json` 后创建
`Provider(setup_payload=...)`。表单 params 只作为本次 setup 的过程输入，不长期
存储；只有成功返回的 `SetupResult.setup_payload` 会被持久化。因此 Provider 的
`status()` 和各类 `store_*()` 方法都不再接收 config，`setup(params)` 只接收本次
解析完成的临时参数。Store Provider
不定义 `close()` 生命周期；需要网络客户端的实现应在请求
作用域内释放资源。`get_visit_url()` 默认返回 `None`，需要访问地址的 Provider 自行
覆盖。

Store Provider 的 `setup()` 只执行自身初始化并返回标准 `SetupResult`，不调用
`status()`。只有 setup 成功后，Service 才主动检查并保存 Store 状态。Notion 当前没有
额外初始化动作，因此直接返回成功；配置、鉴权和目标 Database 的可用性由随后执行的
`status()` 检查。业务 Schema 不在 setup 中创建：Notion 写入时识别明确的缺字段错误，
补齐本次写入需要的 Schema 后最多重试一次；仍然失败则返回
`StoreResult(success=False, ...)`，不得无界递归。

可识别异常统一使用 `CJDBError(code=..., message=..., data=...)`。Store 域不再为鉴权、
不可用、Schema 等场景定义不同异常类，而是使用稳定的 `code` 区分。

核心调用：

```python
services.stores.store_aweme(aweme, provider_id)
services.stores.store_account(account, provider_id)
```

这里的 `services.stores` 只是旧业务调用的兼容 facade，不再对应独立 Store 实体；实例、
项目绑定和状态都属于 Provider。状态接口不会返回配置明文。

## 5. 默认 Store 与项目 Store

- 默认 Store：接收所有未删除作品和账号。
- 项目 Store：只接收当前属于该项目的作品和账号。
- 同一个数据可以同时匹配多个默认或项目 Store。
- 同步关系使用唯一约束防止重复。

绑定变化时执行关系对账：

- 新匹配的关系被创建或重新启用。
- 不再匹配的关系被禁用。
- 历史状态和最近一次成功的 Provider payload 保留，不自动删除远端数据。
- 如果同一个 Store 仍由其他项目或默认规则匹配，关系保持启用。

## 6. CLI

主要命令树：

```text
cjdb
├── aweme
├── account
├── transcription
├── project
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
项目绑定分别由 `store default`、`project store` 管理。

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
- 默认 Store、项目 Store 及解绑后的关系对账。
- API、Services、SQLModel 和临时 SQLite 的端到端流程。
