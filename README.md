# WeSwitch

**在本机浏览器里，审阅并安全修改 Codex Mac 模型配置。**

简体中文 · [English](README.en.md) · [MIT License](LICENSE)

WeSwitch 是独立社区项目，不隶属于 OpenAI。**默认中文，可随时切换 English**，切换语言不清空表单或重复执行配置。

[下载应用](https://github.com/susunola/WeSwitch/releases/latest) · [查看源码](https://github.com/susunola/WeSwitch)

## 先看清，再修改

- 编辑提供方、模型 ID 与默认模型设置，先预览差异，再明确确认。
- **确认 → 原配置逐字节备份并校验 → 原子写入配置**。原配置不存在时记录该状态，不伪造旧文件。
- 新输入的托管 API Key 保存到 macOS 钥匙串，不把密钥值写进配置或进程命令行。
- 可选模型发现：只有明确同意后，才向所选基础地址发送 `GET <base>/models`。不发送生成请求，不把模型列表请求当成兼容性测试。

## 开始使用

### 推荐：Apple Silicon 独立应用

初始二进制发布目标为 **macOS Apple Silicon（arm64）**；CI 构建使用 macOS 14，本地构建已在 macOS 27 验证。其他系统版本尚需实际验证。独立应用内置 Python，不需要安装 Python、Homebrew 或 WorkBuddy。

发布后，下载 `WeSwitch-v0.1.0-macos-arm64.zip` 及其 `.zip.sha256` 校验文件；在下载目录运行：

```bash
shasum -a 256 -c WeSwitch-v0.1.0-macos-arm64.zip.sha256
```

解压后打开 `WeSwitch.app`，默认浏览器会显示本地界面。校验和用于检查文件完整性，不等于发行者身份认证。

**此发布流程不做 Developer ID 签名或 Apple 公证。** PyInstaller 可能使用 ad-hoc 签名，但这不是官方发行者签名；Gatekeeper 仍可能提示或阻止运行。仅在核对来源并信任应用后，使用 macOS 针对该应用的打开确认流程；不要全局关闭 Gatekeeper。若不愿运行未经公证的应用，可先审阅源码，再按下方运行。

### 源码方式：Python 3.11+

适用于 Apple Silicon，也作为 Intel Mac 的初始使用路径。完整解压源码后，双击 `Start WeSwitch.command`，或在源码目录运行：

```bash
bash "Start WeSwitch.command"
```

也可以向 `bash` 传入该文件的完整路径，不需要切换工作目录；包含空格的路径必须加引号。

启动器在 PATH 和常见 Homebrew 目录寻找 Python 3.13、3.12、3.11 或其他满足 3.11+ 的 `python3`。首次安装前会显示中英双语确认；仅在同意后创建专属 venv，并用 `requirements.txt` 安装固定依赖，不全局安装，不执行 Homebrew/curl 安装器。已有其他环境不会被覆盖。

后续启动会复用同一配置目录的有效本地服务，并再次打开浏览器。源码服务所在终端按 `Ctrl+C` 停止服务；关闭网页不等于停止服务。环境创建中断时，启动器不会直接覆盖目录，请先检查并移走不完整的专属 venv，再重试。

## 使用边界

1. 填写提供方地址与模型 ID。`https://api.example.com/v1` 和 `model-id` **仅是示例**，不是推荐服务或可用模型声明。
2. 如需读取模型列表，先核对目标地址与将使用的凭据，再单独确认。请求可能把认证信息发送给该提供方；取消则不请求。返回的列表只是元数据，**不证明 Responses API 兼容性、模型可用性、账号权限或 Codex 模型选择器可见性**。
3. 预览配置变更，明确确认后应用。WeSwitch 不写自定义模型目录文件，也不保证 Codex 模型选择器会出现该模型。
4. 保存工作，完全退出 Codex 后重新打开，并在新会话中检查。项目配置、profile、启动参数或当前线程选择可能覆盖全局默认值；本工具不强制覆盖它们。

**环境变量模式：** 配置中只保存变量名；WeSwitch 不配置系统环境变量，Finder 启动的应用也不保证继承终端变量。模型发现只可读取与**已保存的同一提供方、同一地址、同一变量名**匹配的进程环境变量，不提供任意环境变量读取。新提供方可手填模型 ID，或使用表单临时 Key 进行经确认的列表请求；获取列表本身不会保存 Key。复用的服务保留原来的环境，改变环境变量后需先停止旧服务。

## 安全与本地数据

| 项目 | 默认位置 / 约束 |
| --- | --- |
| Codex 配置 | `~/.codex/config.toml`；支持 `CODEX_HOME`，须与 Codex 实际使用的目录一致 |
| 配置备份 | `~/.codex/model-ui-backups/`；使用 `CODEX_HOME` 时位于该目录下，保留旧目录名以兼容既有备份 |
| 托管凭据 | macOS Keychain，service 为 `local.codex-model-ui`；保留旧名称以兼容已有条目 |
| 源码专属环境 | `~/Library/Application Support/WeSwitch/venv/` |
| 运行状态 | `~/Library/Application Support/WeSwitch/runtime.json`；含会话 token，不可分享 |
| 浏览器服务 | 仅监听 `127.0.0.1`，API 需要会话 token，并校验 Host / Origin |

钥匙串写入通过系统安全接口完成；读取由 `/usr/bin/security` helper 按 service/account 查询，命令行只包含条目标识，不包含密钥值。配置仅引用 helper。已有配置与逐字节备份仍可能包含原先存在的敏感内容，请按秘密文件保管。

不要将端口转发到公网，不要把本地服务发布成远程站点。不要提交真实配置、Key、运行状态、备份、个人截图或未经脱敏的日志。`.gitignore` 只是辅助，发布前仍需检查文件白名单与内容。旧启动入口不属于公开分发内容。恢复配置前先停止 Codex 与 WeSwitch，核对备份内容，并保留当前文件；工具不会自动删除旧钥匙串条目。

## 开发与构建

以下命令在源码目录中运行；先确保 `python3` 为 3.11+。使用一个新的专属开发环境，不复用其他项目的 venv。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s . -p 'test_*.py'
bash -n "Start WeSwitch.command"
```

CI 在 Ubuntu 上分别使用 Python 3.11 / 3.13。测试应只使用临时配置、测试凭据与模拟钥匙串；`test_browser.py` 是单独运行的可选浏览器测试，不应在 unittest 导入时启动浏览器。

在本机 macOS 上构建当前架构的应用：

```bash
.venv/bin/python scripts/build_macos.py --version v0.1.0
# Optional: choose a fresh distribution directory.
.venv/bin/python scripts/build_macos.py --version v0.1.0 --output "$HOME/WeSwitch release"
```

构建脚本不安装依赖。入口为 `launch_desktop.py`；打包 `index.html`、`i18n.js` 以及存在时的 `assets/WeSwitch.icns`。工作文件与 spec 留在 `build/`，默认输出在 `dist/`；已有应用或同版本压缩包不会被覆盖，重复构建请指定新的输出目录。脚本校验 Python 与应用可执行文件架构，再用 `ditto` 保留 bundle 元数据生成 zip 和 SHA-256 文件。初始 GitHub Release 工作流只发布 arm64 应用。

Release 工作流接受 `v*` 标签推送，或从默认分支手动指定**已存在**的稳定版本标签；PR 不发布，只有发布 job 拥有 `contents: write` 权限，不使用长期密钥。请先完成代码审查和测试，再创建、推送版本标签。手动重试命令：

```bash
gh workflow run release.yml --repo susunola/WeSwitch -f version=v0.1.0
```

## 许可证

[MIT](LICENSE) · Copyright (c) 2026 susunola
