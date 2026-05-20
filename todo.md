# MissAV-Downloader 重构计划

## 已完成的修复

- [x] 域名迁移：`missav.ai` → `missav.live`
- [x] 添加 `Referer` 头解决 surrit.com CDN 403 问题
- [x] 改用 `curl_cffi.requests.Session` 解决 TLS 兼容性（impersonate="chrome131"）
- [x] 分辨率优先级：CLI 参数 > 环境变量 `MIYUKI_QUALITY` > 默认 720

## Phase 1: 模块拆分

将当前单文件 `miyuki/miyuki.py`（~950行）拆分为职责清晰的模块：

```
miyuki/
├── __init__.py
├── client.py       ← HTTP 客户端封装（Session、反爬、重试）
├── models.py       ← Pydantic 数据模型（MovieInfo、DownloadTask、DownloadResult）
├── core.py         ← 核心服务层（MiyukiService 类，无全局状态）
├── cli.py          ← CLI 入口（argparse，保留原有用法兼容）
└── api.py          ← FastAPI 服务（Phase 2）
```

### client.py

- 封装 `curl_cffi.requests.Session`
- 管理 headers / Referer / impersonate 配置
- 提供 `get(url)` / `get_with_retry(url, retry, delay, timeout)` 方法
- 单一职责：处理反爬和网络请求

### models.py

```python
class MovieInfo(BaseModel):
    url: str
    uuid: str
    title: str
    available_qualities: list[str]   # ["360p", "480p", "720p"]
    segment_count: int
    cover_url: str | None

class DownloadTask(BaseModel):
    movie_url: str
    quality: str = "720"
    output_dir: str = "./downloads"
    use_ffmpeg: bool = True
    download_cover: bool = True

class DownloadResult(BaseModel):
    movie_url: str
    title: str
    output_path: str
    quality: str
    segment_total: int
    segment_downloaded: int
    status: Literal["completed", "failed", "in_progress"]
    error: str | None = None
```

### core.py

```python
class MiyukiService:
    def __init__(self, output_dir: str = "./downloads", quality: str = "720"):
        self.client = MiyukiClient()
        self.output_dir = output_dir
        self.quality = quality

    def get_movie_info(self, url: str) -> MovieInfo:
        """获取视频信息，不下载"""

    def download(self, task: DownloadTask, progress_callback=None) -> DownloadResult:
        """执行下载，支持进度回调"""

    def search(self, keyword: str) -> list[str]:
        """搜索返回 URL 列表"""
```

关键改动：
- 所有路径通过参数传入，不再硬编码
- 返回结构化结果（Pydantic model），不再只打日志
- 支持 progress_callback 用于 API 推送进度
- 消除全局状态，实例化时传入配置

### cli.py

- 保留原有 argparse 参数兼容
- 内部调用 `MiyukiService`
- 新增 `-output` 参数指定保存目录（环境变量 `MIYUKI_OUTPUT` 回退）

## Phase 2: FastAPI 服务化

### 端点设计

```
POST   /tasks              ← 提交下载任务，返回 task_id
GET    /tasks              ← 列出所有任务
GET    /tasks/{task_id}    ← 查询任务状态/进度
DELETE /tasks/{task_id}    ← 取消任务

GET    /search?q=keyword   ← 搜索视频，返回 MovieInfo 列表
GET    /info?url=xxx       ← 获取单个视频详情（不下载）
```

### 任务管理

- 内存 dict 存储任务状态（后续可换 SQLite/Redis）
- 后台用 `asyncio.create_task` + `run_in_executor` 执行下载
- 支持并发多任务，每个任务内部用线程池下载片段

### 并发模型

- 外层：asyncio 管理多个下载任务的调度
- 内层：threading 线程池并发下载单个视频的片段（curl_cffi 不支持 async）
- FastAPI 中通过 `run_in_executor` 桥接同步下载逻辑

### ffmpeg 处理

- 启动时检测 ffmpeg 是否可用
- 可用则默认使用 ffmpeg 合并（质量更好）
- 不可用则 fallback 到二进制拼接
- 返回结果中标注合并方式

### 配置

所有配置支持三级优先级：API 请求参数 > 环境变量 > 默认值

| 配置项 | 环境变量 | 默认值 |
|--------|----------|--------|
| 分辨率 | `MIYUKI_QUALITY` | 720 |
| 输出目录 | `MIYUKI_OUTPUT` | ./downloads |
| 并发线程数 | `MIYUKI_THREADS` | CPU 核心数 |
| 重试次数 | `MIYUKI_RETRY` | 5 |
| 重试延迟 | `MIYUKI_DELAY` | 2 |
| 请求超时 | `MIYUKI_TIMEOUT` | 10 |

## Phase 3: 未来可选

- [ ] MCP Tool 化（搜索/信息/下载三个 tool，接入 AI Agent 工作流）
- [ ] WebSocket 推送下载进度
- [ ] 下载历史持久化（SQLite）
- [ ] Docker 部署方案
- [ ] 前端 UI（可选）
