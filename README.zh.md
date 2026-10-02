# WeSwitch

**在本机浏览器里，审阅并安全修改 Codex Mac 模型配置。**

简体中文 · [English](README.md) · [MIT License](LICENSE)

WeSwitch 是独立社区项目，不隶属于 OpenAI。**默认中文，可随时切换 English**，切换语言不清空表单或重复执行配置。

[下载应用](https://github.com/susunola/WeSwitch/releases/latest) · [查看源码](https://github.com/susunola/WeSwitch)

## 先看清，再修改

- 编辑提供方、模型 ID 与默认模型设置，先预览差异，再明确确认。
- **确认 → 原配置逐字节备份并校验 → 原子写入配置**。原配置不存在时记录该状态，不伪造旧文件。
- 新输入的托管 API Key 仍保存到 macOS 钥匙串。为了让桌面端能带上 Key，界面确认后还会把它写入当前提供方的 `experimental_bearer_token`。`auth.json` 里的官方登录不改。
- 可选模型发现：只有明确同意后，才向所选基础地址发送 `GET <base>/models`。不发送生成请求，不把模型列表请求当成兼容性测试。
- **一次添加多个模型**：同一个提供方的地址与密钥只填一次，多个模型 ID（如 Pro、Flash）一起保存，各自成为可切换的 profile，不需要为每种模型重复新建提供方。
- **桌面端一次只认当前提供方**：填地址和 Key 后用 Key 拉取模型，勾选「写入模型目录」。不要勾「强制 API Key 登录」。应用后完全退出 Codex 再开新对话；不要在下拉里改回官方 GPT，否则请求仍发到 OpenAI。
- **可删除本工具加过的模型**：界面里的「删除自定义模型」只移除本工具记录的条目。默认 GPT 模型不会出现，也不能删。
- **可选连接与协议检查**：确认后向该地址发送**一次最短请求**（最多 16 个输出 token），判断它是否接受 Codex 唯一的 Responses 协议；可能产生少量费用，只报告结果，不保存密钥或配置。
- **每次应用都可回滚**：每次应用都会生成校验过的备份；界面里可选择任一已应用的备份还原，还原前会把当前配置再存一份新备份，因此还原本身也可撤销。备份目录保留最近 5 份与最早 1 份，超出的在应用或还原后自动清理。
- **先看清 Codex 现状，再动手**：套餐、账号、套餐有效期与登录令牌状态**离线**读自本机登录信息；已有模型列表读自本机目录。已加过的模型会被标记并**默认不勾选**，避免同一个模型加两次。

## 开始使用

### 推荐：Apple Silicon 独立应用

初始二进制发布目标为 **macOS Apple Silicon（arm64）**；CI 构建使用 macOS 14，本地构建已在 macOS 27 验证。其他系统版本尚需实际验证。独立应用内置 Python，不需要安装 Python、Homebrew 或 WorkBuddy。

发布后，下载 `WeSwitch-v0.6.4-macos-arm64.zip` 及其 `.zip.sha256` 校验文件；在下载目录运行：

```bash
shasum -a 256 -c WeSwitch-v0.6.4-macos-arm64.zip.sha256
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

## 一次添加多个模型

同一个网关、同一份凭据只需配置一个提供方：在模型 ID 框里**每行填写一个模型 ID**，或获取列表后多选并点击「加入模型列表」。预览时会显示将写入的模型、默认模型以及其余模型的 profile 名称。

- Codex 的配置里**没有**「一个供应商挂多个模型」的字段；多个模型保存为多个命名 profile（`profiles.<名称>`），源码依据见 [PROFILES.md](PROFILES.md)。
- 默认模型写入根级 `model` / `model_provider`；其余只写入 `model`、`model_provider`、`model_reasoning_effort`，不动其他 profile 设置。
- 名称由「提供方 ID + 模型 ID」生成，例如 `deepseek-deepseek-chat`。已存在且含本工具不写入之设置的同名 profile 会被拒绝，避免覆盖你的配置。
- 切换方式取决于 Codex 自身支持的 profile 能力（如配置 `profile`、命令行 `--profile`）。**本工具不保证桌面端模型下拉列表一定显示这些模型**；请以实际 Codex 版本为准，必要时手动输入模型 ID。

## 先看清 Codex 现状：套餐、已有模型与用量

右侧「Codex 现状」面板分两部分，**默认只读、默认不联网**：

**套餐与已有模型（离线）。** 套餐类型、账号、套餐有效期与登录令牌状态，从本机 `~/.codex/auth.json` 的登录信息中解析；已有模型列表从本机模型目录（`models_cache.json`，或你配置的 `model_catalog_json`）读取。这两项都不发网络请求 —— 打开页面就能看到。本工具只读取，不修改登录信息，也**不会把令牌返回给页面**。

**避免重复添加。** 模型选择器里，已经存在的模型会标注为「已加过」（本工具写入的）或「已在目录中」（目录里本来就有），并**默认不选中**。想重复添加必须自己手动勾选 —— 误加不会顺手发生。

**用量（点击才查询一次）。** 用量不随页面加载请求。只有你点「查询用量」并在确认框中同意后，才会用本机保存的 ChatGPT 登录令牌，向 `chatgpt.com` 发送**一次只读请求**。这一点会写在按钮旁，也会写在确认框里：**令牌会发给 chatgpt.com**。查询不修改任何文件、不上传配置、不保存令牌；取消则完全不发请求。非 ChatGPT 登录（例如只填了 API Key）时该按钮不可用。

查询结果按窗口分组（5 小时 / 7 天），显示已用百分比与重置时间，并可展开按维度（模型、触发方式、会话来源、界面）的细分。用量数值来自服务端，**仅为参考**：窗口是滚动窗口，数据可能不完整，四舍五入与统计口径以 Codex 自身显示为准。

## 接口协议：只有一个

Codex 只有一种线协议：**Responses API**。旧版 `wire_api = "chat"` 已从 codex-cli 移除（Chat Completions 不再受支持），配置里也没有协议开关。所以界面不提供协议下拉选择 —— 真正待验证的只有一件事：这个地址是否接受一次最短的 Responses 请求。

连接测试会真的发出这次请求并读取响应结构：返回 `response` 对象或 `output` 数组视为兼容；返回 `chat.completion` 结构则明确不兼容，且**不能改协议绕过**。列表成功不证明协议兼容，配置成功也不代表模型可用；工具调用、长上下文与流式输出仍需在 Codex 新会话里验证。

## 模型目录与回滚

**模型目录**：勾选「写入模型目录」后，WeSwitch 把现有目录条目与你新增的模型**合并**写入 `~/.codex/models.json`（原目录始终先备份），并把 `model_catalog_json` 指向它；同时把同样的条目合并进登录后桌面选择器实际渲染的 `models_cache.json`，并刷新缓存时间，避免立刻被远程「推荐模型集」盖掉。这两件事都不做，选择器就会退回「默认 推荐模型集」。本机还没有目录时，合并会先从 **Codex 已有的模型**起步 —— 本机缓存的 `models_cache.json`，或安装包里自带的目录（`codex debug models --bundled`）—— 因此新建目录绝不会让官方模型消失。只有两者都读不到时，才不提供勾选。

生成的条目只写入 Codex 必需的字段，不多写。描述**源模型自身能力**的字段 —— 上下文窗口、service tier、speed tier、tool mode —— 一律**不**复制，因为它们描述的是另一个模型。写入之前，候选文件会由本机安装的 Codex 在一次性 `CODEX_HOME` 里重新解析一遍；只要 Codex 会拒绝，就一个文件都不写，并把 Codex 的原话显示出来。这一点很关键：**只要有一个条目不合规，Codex 就会拒绝整份文件**，代价是选择器里所有模型一起消失，而不只是自定义的那一个。

目录条目只决定桌面端的显示名称。实际请求走当前 `model_provider` 的地址。桌面下拉只改模型名、不改提供方，所以不要在下拉里改回官方 GPT。官方登录留在 `auth.json`；Key 由界面写入当前提供方，不需要手改配置文件。目录写入后 Codex 的远程目录更新不再生效；把 `model_catalog_json` 改回原路径即可恢复。

**回滚**：`~/.codex/model-ui-backups/` 下每个已应用的备份都可在界面里选择还原；还原前当前配置会另存为新备份。还原只覆盖本工具写过的文件（配置，以及应用时写过的目录文件），不删除任何钥匙串条目。

**备份保留**：目录最多保留最近 **5** 个备份，外加**最早那一份** —— 即本工具首次改动配置之前的那份。最早那份始终保留，好让连续多次应用之后仍然回得去。因此上限为 6 份，超出的会在**应用或还原成功之后**于写锁内清理，界面会报出本次清理了几个。仅打开界面、切换语言、预览、读取状态都不会删除任何备份；清理只发生在真正写入配置时。清理失败的备份会原地保留，并且不会让这次应用失败。

## 使用边界

1. 填写提供方地址与模型 ID。`https://api.example.com/v1` 和 `model-id` **仅是示例**，不是推荐服务或可用模型声明。
2. 如需读取模型列表，先核对目标地址与将使用的凭据，再单独确认。请求可能把认证信息发送给该提供方；取消则不请求。返回的列表只是元数据，**不证明 Responses API 兼容性、模型可用性、账号权限或 Codex 模型选择器可见性**。
3. 预览配置变更，明确确认后应用。是否写入模型目录由你勾选决定；未勾选时目录文件与 `model_catalog_json` 均不变，模型也不会出现在桌面端选择器里。
4. 保存工作，完全退出 Codex 后重新打开，并在新会话中检查。项目配置、profile、启动参数或当前线程选择可能覆盖全局默认值；本工具不强制覆盖它们。

**环境变量模式：** 配置中只保存变量名；WeSwitch 不配置系统环境变量，Finder 启动的应用也不保证继承终端变量。模型发现只可读取与**已保存的同一提供方、同一地址、同一变量名**匹配的进程环境变量，不提供任意环境变量读取。新提供方可手填模型 ID，或使用表单临时 Key 进行经确认的列表请求；获取列表本身不会保存 Key。复用的服务保留原来的环境，改变环境变量后需先停止旧服务。

## 安全与本地数据

| 项目 | 默认位置 / 约束 |
| --- | --- |
| Codex 配置 | `~/.codex/config.toml`；支持 `CODEX_HOME`，须与 Codex 实际使用的目录一致 |
| 配置备份 | `~/.codex/model-ui-backups/`；使用 `CODEX_HOME` 时位于该目录下，保留旧目录名以兼容既有备份 |
| 备份保留 | 最近 5 份 + 最早 1 份，上限 6 份；应用或还原成功后清理，仅打开界面不删除任何备份 |
| 托管凭据 | macOS Keychain，service 为 `local.codex-model-ui`；保留旧名称以兼容已有条目 |
| 源码专属环境 | `~/Library/Application Support/WeSwitch/venv/` |
| 运行状态 | `~/Library/Application Support/WeSwitch/runtime.json`；含会话 token，不可分享 |
| 浏览器服务 | 仅监听 `127.0.0.1`，API 需要会话 token，并校验 Host / Origin |
| ChatGPT 登录信息 | `~/.codex/auth.json`；只读解析套餐与令牌状态，令牌不回传页面 |

钥匙串写入通过系统安全接口完成；读取由 `/usr/bin/security` helper 按 service/account 查询。桌面端另外需要当前提供方里的 `experimental_bearer_token`，所以确认应用后 Key 会写入 `config.toml`。已有配置与逐字节备份按秘密文件保管。

WeSwitch 自身的网络出站只有两处，都由你逐次确认：模型列表请求（`GET <base>/models`，发给**你填写的服务商**）和用量查询（发给 **chatgpt.com**，携带本机 ChatGPT 登录令牌）。用量查询是本工具唯一一个会把该令牌发往外部地址的操作，且只在你点击并在确认框同意后发生一次；查询失败时不会读取错误正文，以免在日志或界面中泄露令牌。

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
.venv/bin/python scripts/build_macos.py --version v0.6.4
# Optional: choose a fresh distribution directory.
.venv/bin/python scripts/build_macos.py --version v0.6.4 --output "$HOME/WeSwitch release"
```

构建脚本不安装依赖。入口为 `launch_desktop.py`；打包 `index.html`、`i18n.js` 以及存在时的 `assets/WeSwitch.icns`。工作文件与 spec 留在 `build/`，默认输出在 `dist/`；已有应用或同版本压缩包不会被覆盖，重复构建请指定新的输出目录。脚本校验 Python 与应用可执行文件架构，再用 `ditto` 保留 bundle 元数据生成 zip 和 SHA-256 文件。初始 GitHub Release 工作流只发布 arm64 应用。

Release 工作流接受 `v*` 标签推送，或从默认分支手动指定**已存在**的稳定版本标签；PR 不发布，只有发布 job 拥有 `contents: write` 权限，不使用长期密钥。请先完成代码审查和测试，再创建、推送版本标签。手动重试命令：

```bash
gh workflow run release.yml --repo susunola/WeSwitch -f version=v0.6.4
```

## 许可证

[MIT](LICENSE) · Copyright (c) 2026 susunola
