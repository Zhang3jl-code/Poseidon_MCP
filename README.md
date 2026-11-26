# Poseidon 日志 MCP（本地最小版本）

使用账号密码登录波塞冬，取得并在内存中维护 token，以 stdio 提供只读应用日志工具。基于 2026-11-26 当前站点的前端协议实现；已在原工作目录验证真实账号登录和 MCP 认证；日志查询及生产分页仍待联调。

## 已提供的工具

| 工具 | 用途 |
| --- | --- |
| `check_connection` | 登录并验证认证状态，只返回成功标志及平台地址 |
| `list_apps` | 分页返回范围内应用 ID、名称 |
| `list_instances` | 返回指定时间范围内应用的日志实例 |
| `search_logs` | 时间、关键词、排除词、实例、子集筛选 |
| `get_log_context` | 原始纳秒时间锚点、流标签、前后上下文及分页 |

traceId 查询：把完整 ID 作为 `search_logs.keywords` 的唯一元素。上下文工具默认不保留关键词，以便看到同实例相邻日志；继续筛选翻页时显式传入原关键词。多个关键词的组合语义由后台决定，尚需实测。

## 安装

此仓库不包含虚拟环境和凭据。首次使用时，使用 Python 3.10 或以上安装依赖：

```powershell
Set-Location -LiteralPath 'D:\Java\Project\Poseidon_MCP'
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
```

## 先在本机验证账号密码

在交互式 PowerShell 中运行：

```powershell
& 'D:\Java\Project\Poseidon_MCP\start.ps1' -CheckLogin
```

按提示输入用户名和密码。密码输入不回显，不写入文件；只传入 Python 子进程的环境，结束后恢复原环境。成功时输出 `{"authenticated": true}`。此命令只验证登录和 `/api/self/`，不查询应用日志。

默认地址是已观察到的平台根地址 `http://steel.psd.hbjxgt.com`。可通过 `POSEIDON_BASE_URL` 改成其他根地址。该站点当前入口为 HTTP，前端的 AES 格式不替代 HTTPS；若平台提供 HTTPS，应配置其正式地址。客户端保持证书校验，不跟随重定向。

如果 PowerShell 禁止运行本地脚本，可在终端直接为当前进程设置环境变量并运行 `server.py --check-login`；不要把密码写入命令历史。

## 接到 MCP 客户端

如果希望 Codex 自动启动服务，可先在本机交互式终端运行：

```powershell
& 'D:\Java\Project\Poseidon_MCP\configure-credentials.ps1'
& 'D:\Java\Project\Poseidon_MCP\start.ps1' -CheckLogin
```

按用户要求，第一条命令直接把用户名和密码明文保存到 `credentials.local.json`，不加密。密码输入时仍不回显。此文件已在 `.gitignore` 中排除；可以直接用文本编辑器修改，但不要提交或分享该文件。只有你手动运行该脚本时才会保存，本次开发没有保存任何凭据。删除该文件即可移除这份本地保存的凭据。

之后把 `codex-config.example.toml` 的配置块加入客户端配置并重新连接。示例通过 `start.ps1 -NonInteractive` 启动，读取本地 JSON 凭据后进入 stdio，不会等待交互输入。如果系统策略禁止脚本运行，请按组织政策使用下方 Python 直接启动和环境变量方式，不需降低执行策略。

也可以完全不保存凭据，使用下面的环境变量方式。

客户端启动命令：

```text
D:\Java\Project\Poseidon_MCP\.venv\Scripts\python.exe
```

参数：

```text
D:\Java\Project\Poseidon_MCP\server.py
```

通过客户端的本机进程环境提供 `POSEIDON_USERNAME`、`POSEIDON_PASSWORD`；可额外提供 `POSEIDON_BASE_URL`。无人值守启动脚本时应带 `-NonInteractive`，否则凭据缺失时可能等待输入。

下面是 Codex 的接入命令（只添加启动信息，不包含密码；本项目没有自动执行它）：

```powershell
codex mcp add poseidon-logs -- 'D:\Java\Project\Poseidon_MCP\.venv\Scripts\python.exe' 'D:\Java\Project\Poseidon_MCP\server.py'
```

在客户端 MCP 配置中设置/传递上述环境变量后，启动新的客户端连接，再调用 `check_connection`。仅手动验证成功不会把凭据传给已经运行的 Codex。若客户端支持凭据存储，优先使用其凭据存储；不要把账号密码作为工具调用参数发送给模型。

## 查询范围与示例

每次查询要求明确 `project`、`product`、`env` 三个 ID；也可通过 `POSEIDON_PROJECT`、`POSEIDON_PRODUCT`、`POSEIDON_ENV` 提供默认值。程序没有默认生产环境。ID 来自当前网页 URL 的 `project=...&product=...&env=...`。

以下只是本次已观察页面的示例，请确认目标范围后使用：

```json
{
  "app": "app-steel-cc3-server",
  "project": "63",
  "product": "56",
  "env": "100",
  "start": "2026-11-26T15:00:00+08:00",
  "end": "2026-11-26T15:10:00+08:00",
  "keywords": ["完整的 traceId"],
  "limit": 200
}
```

时间不带时区时按北京时间解释。单次最多 7 天、1000 条，工具返回总量有 200 KB 限制。日志按时间正序展示，原始纳秒时间始终作为字符串保留。检查 `possibly_truncated`、`locally_truncated`、`has_next`、`has_previous`；空结果不证明该时段没有日志。

上下文查询把返回行的 `time_ns` 原样传入，并传该行标签中的 `pod`、`node` 等（去掉 `app/project/product/env`）；`forward` 查之后，`backward` 查之前。相同时间戳可能出现多条日志，跨页边界可能重叠；本版本不保证去重或完整链路。结果截断时优先缩小时间范围。

## 认证实现和边界

- 按前端代码，登录密码使用固定 AES-128-ECB / PKCS7 格式，提交 `POST /api/auth/login/`，读取 `data.access` 和 `data.refresh`。
- 请求使用 Bearer token，附带三个范围请求头；客户端保留自己登录产生的会话 Cookie，不读取浏览器 Cookie。
- 第一次 HTTP 401 后刷新并最多重放一次；刷新被拒绝时最多重新登录一次。HTTP 403 不触发刷新。
- 账号登录失败后本进程不再重复尝试，避免错误密码导致锁号。检查凭据后重启。
- Python 服务本身不把密码或 token 写入文件、不作为 MCP 参数或返回值；错误不回显服务端原始响应。凭据仍存在当前进程及其子进程环境中。可选的配置脚本按用户要求明文保存用户名和密码，不保存 token。
- 只开放固定读取接口，认证 POST 仅用于登录/刷新；没有部署、重启、配置修改、删除或容器执行工具。
- Cookie 依赖、权限、关键词语义及生产分页尚待进一步联调。若账号走 SSO、验证码或额外认证，此最小版本会明确报错，需另行适配正常登录流程。

## 测试

```powershell
Set-Location -LiteralPath 'D:\Java\Project\Poseidon_MCP'
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
```

测试用虚拟 HTTP 响应验证登录加密、token 刷新、权限错误、纳秒精度、重复关键词编码、上下文参数与截断标志；另有真实子进程的 MCP stdio 握手和工具调用测试。测试不使用真实账号，不访问生产日志。

协议依据：本站 `/assets/index-DJWwy6Z6.js` 与日志模块 `/assets/index-BnxLPkvy.js`；MCP 使用[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)。
