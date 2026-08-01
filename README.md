# CJDB Collectors

CJDB 是一个本地优先的对标作品管理工具。它通过可扩展的 Data Provider 获取数据，
通过可扩展的 Store Provider 把作品和账号同步到外部存储。

项目提供 FastAPI WebUI、Typer CLI、SQLite/SQLModel 数据层，以及负责后台轮询的
Worker。

## 安装

用户不需要预先安装 Python。安装 `uv` 后，`uv` 会下载项目所需的 Python、创建
`.venv` 并安装依赖。

macOS / Linux：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv --version
```

在项目目录初始化：

```bash
cp config.yaml.example config.yaml
uv python install 3.12
uv python pin 3.12
uv sync --no-dev
```

`uv sync` 会自动创建项目根目录下的 `.venv`，无需手动激活。

## 启动

项目根目录提供 `cjdb` 启动脚本。macOS / Linux：

```bash
./cjdb webui
```

Windows PowerShell：

```powershell
.\cjdb.ps1 webui
```

访问：

- WebUI：<http://127.0.0.1:8000/>
- OpenAPI：<http://127.0.0.1:8000/docs>
- 存活检查：<http://127.0.0.1:8000/health/live>

WebUI 和 Worker 都支持后台运行：

```bash
./cjdb webui start -d
./cjdb webui logs -f
./cjdb webui restart -d
./cjdb webui stop

./cjdb worker start -d
./cjdb worker logs -f
./cjdb worker restart -d
./cjdb worker stop
```

日志默认写入 `./logs`，可通过 `config.yaml` 的 `app.logs_dir` 修改。

## CLI 输出格式

CLI 默认使用适合终端阅读的 `text` 格式，只展示当前命令需要的信息。需要交给脚本、
AI 或其他程序处理时，使用 `--format=json`：

```bash
./cjdb aweme list
./cjdb aweme list --format=json
./cjdb provider status --format=json
```

所有公开命令都支持 `--format=text|json`。JSON 只写入标准输出，并保持可直接解析；
日志命令的 `--format=json` 返回 `path` 和 `lines`。由于 `-f` 是持续输出流，
不能与单个 JSON 文档格式同时使用。

列表命令的 JSON 使用 `{items, pagination/count}`，并排除运行令牌、原始响应等内部
字段；`show` 命令才返回完整的单条资源。

输出格式只属于 CLI：Services 始终返回统一的领域对象或标准数据。CLI 将结果转换为
`CLIResult(text=..., json=...)` 后，再由统一输出器根据 `--format` 选择最终表示；
API 和 WebUI 分别完成自己的序列化与界面渲染。

## Provider

Provider Type 表示一类业务能力，例如 `douyin_aweme_collect` 或
`video_transcription`。namespace 只用于隔离同一个 Provider 的配置，不作为 CLI
操作参数。

```bash
./cjdb provider list
./cjdb provider status
./cjdb provider status douyin_aweme_collect
./cjdb provider select video_transcription faster_whisper
./cjdb provider setup douyin_aweme_collect \
  api_key=YOUR_KEY \
  base_url=https://api.tikhub.dev
./cjdb provider logs douyin_aweme_collect -f
```

`provider list` 每个具体实现只显示一次，并汇总它支持的服务类型；它用于找到
`provider select TYPE PROVIDER_ID` 所需的 Provider ID。`provider status [TYPE]`
则动态检查服务类型当前所选 Provider 的状态，不按 namespace 查询。

`setup` 根据具体 Provider 声明的参数进行校验，并写入它自己的
`providers.<namespace>`。Provider 切换结果写入
`providers.selected.<provider_type>`。状态通过 Provider 的 `status()` 动态检查，
不会把一次 setup 成功永久当成可用。

TikHub 服务地址：

- 中国大陆：`https://api.tikhub.dev`，也是默认值。
- 中国大陆以外：`https://api.tikhub.io`。

配置输出会遮蔽密码、Token、API Key 等敏感值。

## 作品与账号

创建作品或账号时只需 URL 和平台；平台数据 ID 和详情由采集过程补齐。

```bash
./cjdb aweme add "https://www.douyin.com/video/731234567890" \
  --platform douyin
./cjdb aweme list
./cjdb aweme search 关键词
./cjdb aweme fetch AWEME_ID
./cjdb aweme fetch-comments AWEME_ID
./cjdb aweme download-video AWEME_ID
./cjdb aweme download-images AWEME_ID

./cjdb account add "https://www.douyin.com/user/..." --platform douyin
./cjdb account list
./cjdb account fetch ACCOUNT_ID
./cjdb account awemes ACCOUNT_ID --page 1 --size 50
```

`fetch` 直接调用业务 Service，并在本次调用中记录 running、succeeded 或 failed。
Service 本身不做重试；是否再次调用由 Worker 或其他外部调用者决定。

## 视频转写

下面两条命令功能相同，都会调用
`TranscriptionService.transcribe_aweme(aweme_id)`：

```bash
./cjdb aweme transcription AWEME_ID
./cjdb transcription aweme AWEME_ID
```

独立视频只接受本地文件或 URL：

```bash
./cjdb transcription add --file /path/to/video.mp4
./cjdb transcription add --url https://example.com/video.mp4
./cjdb transcription list
./cjdb transcription run TRANSCRIPTION_ID
```

安装本地转写依赖：

```bash
uv sync --no-dev --extra transcription
./cjdb provider setup video_transcription \
  model=turbo
```

`model_dir` 可以留空；留空时使用 faster-whisper、ModelScope 或 Hugging Face 的官方默认缓存。

## Store 与分组

Store Provider 定义配置、状态检查、作品写入和账号写入方法。Store 是一个已配置的
具体实例。

```bash
./cjdb store types
./cjdb store add notion --name "Notion 主库" \
  token=YOUR_TOKEN \
  database_id=DATABASE_ID
./cjdb store status STORE_ID
./cjdb store aweme AWEME_ID --to STORE_ID
./cjdb store account ACCOUNT_ID --to STORE_ID
```

默认 Store 接收所有作品和账号：

```bash
./cjdb store default set STORE_ID
./cjdb store default list
./cjdb store default unset STORE_ID
```

分组 Store 只接收该分组中的作品和账号：

```bash
./cjdb group add "竞品账号"
./cjdb group store add GROUP_ID STORE_ID
./cjdb group store list GROUP_ID
./cjdb group store remove GROUP_ID STORE_ID
```

绑定发生变化时，系统会对账同步关系。历史同步记录会保留，但不再匹配默认或分组
范围的关系会被禁用，不会继续被 Worker 调度。

## 代码结构

```text
src/cjdb_collectors/
├── data_provider/
│   ├── base.py
│   ├── types.py
│   ├── registry.py
│   └── providers/
├── store/
│   ├── base.py
│   ├── types.py
│   ├── registry.py
│   └── providers/
├── services/
├── api/
├── routes/
├── cli/
├── models/
└── worker/
```

调用方向固定为：

```text
CLI / API / WebUI / Worker
            ↓
         Services
       ↙          ↘
Data Provider    Store Provider
```

核心调用：

```python
aweme = services.awemes.get(aweme_id)
services.awemes.fetch_data(aweme)

storer = services.store_providers.get_storer(store_id)
services.stores.store_aweme(aweme, storer)
```

具体 Provider 必须零参数初始化。setup 参数是它与外部配置交互的入口；新增实现时，
继承对应的抽象 Mixin，并注册到 Registry。

## 验证

```bash
uv sync
uv run ruff check src tests
uv run pytest -q
```

更完整的架构约定见[开发设计文档](docs/DEVELOPMENT.md)。
