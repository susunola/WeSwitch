"""Field-scoped localization of backend responses; no I/O or process-wide locale.

Pass the X-WeSwitch-Language header value to translate_response before JSON
serialization. CLI callers may use translate_text independently. Only an explicit
'en' (case-insensitive, with surrounding whitespace ignored) selects English.
Unknown messages and all captured parameters are preserved verbatim.
"""
from __future__ import annotations

import re

__all__ = ["normalize_language", "translate_text", "translate_response"]


# Whole messages only. Keep these grouped by their source module so additions are
# easy to review; test_i18n.py checks the source AST without importing the backend.
_EXACT_TRANSLATIONS = {
    # config_core.py: validation and configuration reads.
    "请填写 API 基础地址，不要包含 /responses、/chat/completions 或 /messages。":
        "Enter the API base URL without /responses, /chat/completions, or /messages.",
    "API 地址必须为 HTTPS，或本机 localhost / 127.0.0.1 的 HTTP；不允许内嵌账号、查询参数或片段。":
        "The API URL must use HTTPS, or HTTP on localhost / 127.0.0.1; embedded credentials, query parameters, and fragments are not allowed.",
    "请求格式必须为 JSON 对象。":
        "The request must be a JSON object.",
    "供应商 ID 请以小写字母开头，仅使用小写字母、数字、下划线和短横线。":
        "The provider ID must start with a lowercase letter and contain only lowercase letters, digits, underscores, and hyphens.",
    "请使用独立供应商 ID，例如 my-gateway；不要覆盖内置供应商。":
        "Use a separate provider ID, such as my-gateway; do not overwrite a built-in provider.",
    "模型 ID 为空、过长或包含空格；请填写服务商提供的精确 ID。":
        "The model ID is empty, too long, or contains whitespace; enter the exact ID supplied by the provider.",
    "请至少添加一个模型 ID，可一次添加多个。":
        "Add at least one model ID; several can be added at once.",
    "模型 ID 必须是文本。":
        "The model ID must be text.",
    "模型 ID 重复，请删除重复项后重试。":
        "A model ID is duplicated; remove the duplicate and try again.",
    "默认模型必须是已添加的模型之一。":
        "The default model must be one of the models that were added.",
    "模型 ID 过长，无法生成合规的 profile 名称；请缩短模型 ID 或供应商 ID。":
        "The model ID is too long to build a valid profile name; shorten the model ID or the provider ID.",
    "字段 save_profiles 必须为布尔值。":
        "Field save_profiles must be a boolean.",
    "现有 profiles 配置格式异常，未做修改。":
        "The existing profiles configuration has an invalid format. No changes were made.",
    "生成的 profile 校验失败，停止写入。":
        "The generated profile failed validation. Writing has been stopped.",
    "不支持的认证方式。":
        "Unsupported authentication method.",
    "无密钥模式仅限本机服务；远程服务请选择钥匙串或环境变量。":
        "Keyless mode is only available for local services; use Keychain or an environment variable for remote services.",
    "环境变量名仅使用大写字母、数字和下划线，且不能以数字开头。":
        "Environment variable names may contain only uppercase letters, digits, and underscores, and cannot start with a digit.",
    "不支持的推理强度。":
        "Unsupported reasoning effort.",
    "config.toml 是符号链接。为避免改错目标，本工具不自动写入，请先确认实际配置位置。":
        "config.toml is a symbolic link. To avoid changing the wrong target, this tool will not write to it automatically; confirm the actual configuration location first.",
    "配置不是普通文件或超过 4 MB，停止操作。":
        "The configuration is not a regular file or exceeds 4 MB. The operation has been stopped.",
    "现有 config.toml 不是有效 UTF-8 TOML。未做修改，请先修复原文件。":
        "The existing config.toml is not valid UTF-8 TOML. No changes were made; repair the original file first.",
    "模型目录无法读取或格式异常；本工具不会覆盖它。":
        "The model catalog cannot be read or has an invalid format; this tool will not overwrite it.",
    "仅在你确认获取列表时请求服务商 /models；不会发送模型生成请求。获取列表与配置写入是独立操作。":
        "The provider's /models endpoint is requested only when you confirm fetching the list; no model generation requests are sent. Fetching the list and writing the configuration are separate operations.",

    # config_core.py: preview warnings, credentials, and changes.
    "此供应商使用已有的外部认证方式。为避免移除认证，请新建独立供应商 ID。":
        "This provider already uses external authentication. Create a separate provider ID to avoid removing that authentication.",
    "该供应商含额外认证、请求头或高级设置。为避免误覆盖，请换一个新的供应商 ID。":
        "This provider has additional authentication, request headers, or advanced settings. Use a new provider ID to avoid overwriting them.",
    "不修改 auth.json、MCP、项目权限、历史记录或应用本体。":
        "auth.json, MCP, project permissions, history, and the application itself are not modified.",
    "只修改用户级配置；项目配置或已有会话仍可能覆盖 model。请完全退出应用后开启新会话验证。":
        "Only user-level configuration is modified; project configuration or existing sessions may still override model. Fully quit the application, then start a new session to verify.",
    "API 必须兼容 Responses 的流式输出与工具调用；列表获取不验证这些能力。":
        "The API must support Responses streaming and tool calls; fetching the list does not verify these capabilities.",
    "本环境不支持 macOS 钥匙串；请使用环境变量方式。":
        "macOS Keychain is not supported in this environment; use an environment variable instead.",
    "新供应商或地址变更需要填写 API Key，不能把原凭据静默发送到新地址。":
        "A new provider or a changed URL requires an API Key; existing credentials cannot be silently sent to a new URL.",
    "原钥匙串凭据不存在或未解锁，请重新输入 API Key。":
        "The existing Keychain credential is missing or locked; enter the API Key again.",
    "API Key 仅保存在 macOS 钥匙串；桌面端首次读取时可能请求钥匙串授权。":
        "The API Key is stored only in macOS Keychain; the desktop application may request Keychain access when first reading it.",
    "只有钥匙串模式可以接收 API Key。":
        "Only Keychain mode can accept an API Key.",
    "环境变量必须在桌面应用进程中可见；本工具不写 shell 配置，也不注入 Dock 环境。":
        "The environment variable must be available to the desktop application process; this tool neither writes shell configuration nor injects variables into the Dock environment.",
    "无密钥模式只适用于本机无需认证的服务。":
        "Keyless mode is only for local services that require no authentication.",
    "保留现有模型目录和其他供应商":
        "Preserve the existing model catalog and other providers",
    "清除旧的推理强度，避免向新模型发送不支持的选项":
        "Clear the previous reasoning effort to avoid sending an unsupported option to the new model",
    "只保存供应商；不切换当前模型（模型 ID 与推理强度不会写入默认设置）":
        "Save only the provider; do not switch the current model (the model ID and reasoning effort will not be written to the defaults)",
    "推理强度由你指定；请确认上游模型支持该值。":
        "You specified the reasoning effort; confirm that the upstream model supports this value.",
    "合并写入新的模型目录；原始目录文件保留在备份中":
        "Merge into a new model catalog; the original catalog file is preserved in the backup",
    "设置 model_catalog_json 指向新目录文件；原目录文件已备份。":
        "Point model_catalog_json at the new catalog file; the original catalog file has been backed up.",
    "目录写入后，桌面端选择器改用这个文件；Codex 之后的远程目录更新不会再生效。把 model_catalog_json 改回原路径即可恢复。":
        "After the catalog is written, the desktop picker uses this file; later remote catalog updates from Codex will no longer take effect. Point model_catalog_json back at the original path to restore that.",
    "目录条目只决定桌面端的显示名称与推理选项；实际请求仍使用你填写的模型 ID 和 API 地址。":
        "Catalog entries only determine the desktop display name and reasoning options; requests still use the model ID and API URL you entered.",
    "在 [desktop] 中启用全部推理强度选项，避免所选强度被界面隐藏。":
        "Enable all reasoning effort options under [desktop] so the selected effort is not hidden by the UI.",
    "配置中没有 [desktop] 表，未写入推理强度列表；若桌面端隐藏了所选强度，请手动添加。":
        "The configuration has no [desktop] table, so the reasoning effort list was not written; if the desktop hides the selected effort, add it manually.",
    "写入 preferred_auth_method 与 forced_login_method，启动后直接使用 API Key 登录。":
        "Write preferred_auth_method and forced_login_method so the app signs in with the API Key directly on launch.",
    "改为 API Key 登录后，此前用 ChatGPT 账号登录的会话历史会归到另一种登录方式下而暂时看不到；改回即可恢复，不会被删除。":
        "After switching to API Key sign-in, sessions created with a ChatGPT account are grouped under another sign-in method and are temporarily hidden; switching back restores them, and nothing is deleted.",
    "请先确认，才会还原备份。":
        "Confirm first; only then will the backup be restored.",
    "备份标识无效。":
        "The backup identifier is invalid.",
    "备份不存在。":
        "The backup does not exist.",
    "备份目录不存在或是符号链接，已停止还原。":
        "The backup directory does not exist or is a symbolic link. The restore has been stopped.",
    "备份清单或配置文件无法读取，已停止还原。":
        "The backup manifest or configuration file cannot be read. The restore has been stopped.",
    "这个备份不完整或不是已应用的备份，已停止还原。":
        "This backup is incomplete or was never applied. The restore has been stopped.",
    "备份内容与清单记录不一致，已停止还原。":
        "The backup contents do not match the manifest record. The restore has been stopped.",
    "这个备份记录的是“原本没有配置文件”。为避免删除文件，本工具不自动删除；请手动处理。":
        "This backup records that no configuration file existed before. To avoid deleting files, this tool does not delete automatically; handle it manually.",
    "还原前的安全副本校验失败，未修改任何文件。":
        "Verification of the safety copy failed before restoring. No file has been modified.",
    "还原后文件再次发生变化，请检查备份目录，不要重复还原。":
        "The file changed again after restoring. Check the backup directory and do not restore repeatedly.",
    "已还原该备份。还原前的配置已另存为新备份，可再次还原；请完全退出应用（⌘Q）后重新打开。":
        "The backup has been restored. The configuration as it was before restoring has been saved as a new backup, so the restore itself can be reversed; fully quit the application (⌘Q) and reopen it.",
    "强制 API Key 登录需要钥匙串或环境变量认证；无认证模式不支持。":
        "Forcing API Key sign-in requires Keychain or environment-variable authentication; keyless mode is not supported.",
    "未找到现有模型目录：model_catalog_json 未设置，也读不到 Codex 内置模型列表。为避免让官方模型从选择器中消失，本工具不新建只包含自定义模型的目录。":
        "No existing model catalog was found, and Codex's built-in model list could not be read either. To avoid making the built-in models disappear from the picker, this tool does not create a catalog containing only custom models.",
    "现有模型目录为空或无法解析，为保证官方模型仍可选，未生成新目录。":
        "The existing model catalog is empty or cannot be parsed, so no new catalog was generated; this keeps the built-in models selectable.",
    "本机原先没有模型目录；底稿取自本机缓存的模型列表 models_cache.json。":
        "This machine had no model catalog; the starting point is the locally cached model list, models_cache.json.",
    "本机原先没有模型目录；底稿取自 Codex 内置模型目录 codex debug models --bundled。":
        "This machine had no model catalog; the starting point is Codex's built-in catalog, codex debug models --bundled.",
    "目录写入后，桌面端选择器只读取这个文件；Codex 之后的远程目录更新不会自动生效。重新运行本工具会用当时的内置列表重建底稿。":
        "After the catalog is written, the desktop picker reads only this file, so later remote catalog updates from Codex will not take effect on their own. Running this tool again rebuilds the starting point from the built-in list of that moment.",
    "生成的配置校验失败，停止写入。":
        "The generated configuration failed validation. Writing has been stopped.",
    "检测到无关设置发生变化，已阻止写入。":
        "Changes to unrelated settings were detected. Writing has been blocked.",

    # config_core.py: confirmation, conflicts, backups, and apply results.
    "请先预览并明确确认修改。":
        "Preview the changes and explicitly confirm them first.",
    "API Key 格式不正确。":
        "Invalid API Key format.",
    "API Key 不能包含空格或换行。":
        "The API Key cannot contain spaces or line breaks.",
    "密钥输入状态已改变，请重新预览。":
        "The key input state has changed; generate a new preview.",
    "预览已过期，请重新预览。":
        "The preview has expired; generate a new preview.",
    "表单与预览不一致，请重新预览。":
        "The form no longer matches the preview; generate a new preview.",
    "Codex 配置已被其他程序修改；为避免覆盖，请刷新并重新预览。":
        "Another program has modified the Codex configuration; refresh and generate a new preview to avoid overwriting it.",
    "备份目录是符号链接，已停止操作。":
        "The backup directory is a symbolic link. The operation has been stopped.",
    "无法创建安全备份目录。":
        "Unable to create a secure backup directory.",
    "配置已变化，请刷新并重新预览。":
        "The configuration has changed; refresh and generate a new preview.",
    "备份校验失败，未修改配置。":
        "Backup verification failed. The configuration was not modified.",
    "保存期间配置发生变化，已停止写入；新钥匙串条目可能已建立但未启用。":
        "The configuration changed while saving, so writing was stopped; a new Keychain item may have been created but is not active.",
    "配置写入失败；备份保留在 model-ui-backups。新钥匙串条目可能存在但未启用。":
        "Writing the configuration failed; backups remain in model-ui-backups. A new Keychain item may exist but is not active.",
    "写入后文件再次发生变化。请检查备份与配置，不要继续重复应用。":
        "The file changed again after writing. Check the backup and configuration; do not keep applying the changes.",
    "配置已备份并写入。请保存工作，完全退出应用（⌘Q）后重新打开，再用新会话验证模型。":
        "The configuration has been backed up and written. Save your work, fully quit the application (⌘Q), reopen it, and verify the model in a new session.",

    # model_discovery.py: consent and endpoint-bound credential reuse.
    "API Key 为空、过长或包含空格/换行/非 ASCII 字符。请核对后重试。":
        "The API Key is empty, too long, or contains spaces, line breaks, or non-ASCII characters. Check it and try again.",
    "请确认目标地址并点击获取模型列表，才会发送请求。":
        "Confirm the destination URL and click Fetch model list before a request is sent.",
    "请填写 API 基础地址，不要包含 /models；本工具会自动拼接。":
        "Enter the API base URL without /models; this tool appends it automatically.",
    "请选择钥匙串、环境变量或无认证模式。":
        "Select Keychain, environment variable, or no authentication mode.",
    "API Key 必须为文本。":
        "The API Key must be text.",
    "只有钥匙串模式允许提供 API Key。":
        "An API Key may only be supplied in Keychain mode.",
    "无认证模式仅限本机服务。远程服务请选择认证方式。":
        "No authentication mode is only available for local services. Select an authentication method for remote services.",
    "要复用凭据，请填写有效的已保存提供方 ID。":
        "To reuse credentials, enter a valid saved provider ID.",
    "无法将已保存凭据用于新地址。请选钥匙串模式并在表单中填写此服务的 API Key；获取列表不会保存它。":
        "Saved credentials cannot be used for a new URL. Select Keychain mode and enter this service's API Key in the form; fetching the list will not save it.",
    "此提供方没有由本工具托管的凭据，请填写 API Key。":
        "This provider has no credentials managed by this tool; enter an API Key.",
    "钥匙串读取失败或未获授权。请解锁钥匙串，或直接填写 API Key 后重试。":
        "Keychain could not be read or access was not authorized. Unlock Keychain, or enter the API Key directly and try again.",
    "环境变量名无效。":
        "Invalid environment variable name.",
    "获取列表仅可复用同一已保存提供方的环境变量。不读取其他系统变量；新服务请用钥匙串模式临时填写 Key。":
        "Fetching the list can only reuse the environment variable of the same saved provider. Other system variables are not read; for a new service, temporarily enter a Key in Keychain mode.",
    "配置工具进程中没有这个环境变量。可切换钥匙串模式临时填写 Key，或继续手动填写模型 ID。":
        "This environment variable is not available to the configuration tool process. Switch to Keychain mode to temporarily enter a Key, or continue by entering the model ID manually.",
    "读取凭据期间配置发生变化，未发出请求；请刷新状态后重试。":
        "The configuration changed while reading credentials, so no request was sent; refresh the state and try again.",

    # model_discovery.py: response validation and list limitations.
    "模型列表响应超过 2 MB，已停止读取；请手动填写模型 ID。":
        "The model list response exceeds 2 MB. Reading has been stopped; enter the model ID manually.",
    "服务未返回有效 JSON 模型列表，可能是网页或网关错误；请核对基础地址，或手动填写。":
        "The service did not return a valid JSON model list; it may be a web page or gateway error. Check the base URL or enter the model manually.",
    "接口未返回兼容的 data 模型数组；可继续手动填写服务商给出的模型 ID。":
        "The endpoint did not return a compatible data array of models; you can still enter the provider's model ID manually.",
    "模型数量超过 5000，已停止解析；请缩小服务商列表范围或手动填写。":
        "The model count exceeds 5000. Parsing has been stopped; narrow the provider's list or enter the model manually.",
    "列表中没有合法的模型 ID；请手动填写，未显示上游原始响应。":
        "The list contains no valid model IDs; enter the model manually. The raw upstream response has not been displayed.",
    "列表来自服务商，只证明该接口列出了模型；未验证 Responses、流式输出或工具调用能力。":
        "The list comes from the provider and only shows that the endpoint listed these models; Responses, streaming, and tool-call support have not been verified.",
    "服务商返回空列表，可能没有模型权限。仍可手动填写模型 ID。":
        "The provider returned an empty list, possibly because model access is not permitted. You can still enter the model ID manually.",
    "服务商标记还有后续页，本工具仅显示本次响应，不自动跟随分页地址。":
        "The provider indicates that more pages are available. This tool only displays the current response and does not automatically follow pagination URLs.",

    # model_discovery.py: HTTP, network, timeout, and TLS failures.
    "模型列表接口未返回成功结果；请核对基础地址。":
        "The model list endpoint did not return a successful result; check the base URL.",
    "模型列表使用了不支持的压缩响应，请手动填写模型 ID。":
        "The model list uses an unsupported compressed response; enter the model ID manually.",
    "模型列表响应长度无效或超过 2 MB，已停止读取。":
        "The model list response length is invalid or exceeds 2 MB. Reading has been stopped.",
    "模型列表响应超过 2 MB，已停止读取。":
        "The model list response exceeds 2 MB. Reading has been stopped.",
    "服务商拒绝认证或无模型列表权限。请检查 API Key；也可继续手动填写模型 ID。":
        "The provider rejected authentication or denied access to the model list. Check the API Key; you can also continue by entering the model ID manually.",
    "服务商不支持此地址的 /models 列表接口。请核对基础地址，或手动填写模型 ID。":
        "The provider does not support the /models list endpoint at this URL. Check the base URL or enter the model ID manually.",
    "接口要求跳转。为避免密钥泄露，未跟随跳转；请核对并直接填写最终 API 基础地址。":
        "The endpoint requested a redirect. It was not followed to avoid leaking the key; check and enter the final API base URL directly.",
    "服务商限制了请求频率，请稍后手动重试或直接填写模型 ID。":
        "The provider has rate-limited requests. Retry manually later or enter the model ID directly.",
    "获取模型列表超时，请检查网络或手动填写模型 ID。":
        "Fetching the model list timed out; check the network or enter the model ID manually.",
    "无法连接模型列表接口。请检查地址、网络或 TLS 证书；本工具不会跳过证书校验。":
        "Unable to connect to the model list endpoint. Check the URL, network, or TLS certificate; this tool does not skip certificate verification.",

    # connection_test.py: consent, cost disclosure, and protocol verdicts.
    "请确认目标地址并点击测试连接，才会发送请求。":
        "Confirm the destination URL and click Test connection before a request is sent.",
    "这次测试向服务商真实发送了一次最短请求（最多输出 16 个 token），可能产生少量费用。":
        "This test sends one real minimal request to the provider (at most 16 output tokens), which may cost a small amount of quota.",
    "服务端接受了 Responses 请求并返回响应对象；协议兼容，凭据可用。":
        "The service accepted the Responses request and returned a response object; the protocol is compatible and the credential works.",
    "测试成功只说明这个地址接受了一次最短的 Responses 请求；工具调用、长上下文和流式输出仍需在 Codex 新会话中验证。":
        "A successful test only shows that this URL accepted one minimal Responses request; tool calls, long context, and streaming still need to be verified in a new Codex session.",
    "地址返回了成功状态，但响应结构不是 Responses（缺少 response 或 output）。Codex 按 Responses 解析，可能无法使用。":
        "The URL returned a success status, but the response is not a Responses structure (no response or output). Codex parses Responses and may not be able to use it.",
    "地址返回的是 Chat Completions 结构。Codex 只支持 Responses 协议，不能切换协议。":
        "The URL returned a Chat Completions structure. Codex supports only the Responses protocol and cannot switch protocols.",
    "地址返回成功状态，但响应不是有效 JSON；可能是网页或网关。Codex 无法使用它。":
        "The URL returned a success status, but the response is not valid JSON; it may be a web page or gateway. Codex cannot use it.",
    "这个地址没有可用的 /responses 接口。Codex 只支持 Responses 协议，不能改为其他协议。":
        "This URL has no usable /responses endpoint. Codex supports only the Responses protocol and cannot be switched to another one.",
    "认证被拒绝。请核对 API Key 或环境变量；未读取上游错误正文。":
        "Authentication was rejected. Check the API Key or environment variable; the upstream error body was not read.",
    "请求被拒绝（HTTP 400）。常见原因是模型 ID 不受支持；未读取上游错误正文。":
        "The request was rejected (HTTP 400). A common cause is an unsupported model ID; the upstream error body was not read.",
    "服务商限流，请稍后重试。":
        "The provider is rate-limiting requests. Retry later.",
    "接口要求跳转。为避免密钥泄露，未跟随跳转；请填写最终 API 地址。":
        "The endpoint requested a redirect. It was not followed to avoid leaking the key; enter the final API URL directly.",
    "接口发生了跳转。为避免密钥泄露，未跟随跳转；请填写最终 API 地址。":
        "The endpoint redirected. It was not followed to avoid leaking the key; enter the final API URL directly.",
    "连接测试响应过大，已停止读取，无法判断协议兼容性。":
        "The connection test response is too large. Reading has been stopped, so protocol compatibility cannot be determined.",
    "连接测试响应使用了不支持的压缩方式，无法判断协议兼容性。":
        "The connection test response uses an unsupported compression method, so protocol compatibility cannot be determined.",
    "连接测试超时。请检查网络或服务商状态；未保存任何凭据或配置。":
        "The connection test timed out. Check the network or provider status; no credential or configuration was saved.",
    "无法连接该地址。请检查地址、网络或 TLS 证书；本工具不会跳过证书校验。":
        "Unable to connect to this URL. Check the URL, network, or TLS certificate; this tool does not skip certificate verification.",

    # keychain.py: read, authorization, and availability errors.
    "无法读取这个钥匙串条目，请在表单中重新填写 API Key。":
        "Unable to read this Keychain item; enter the API Key again in the form.",
    "钥匙串读取未获授权或凭据不存在。请解锁钥匙串，或在表单中重新填写 API Key。":
        "Keychain access was not authorized or the credential does not exist. Unlock Keychain or enter the API Key again in the form.",
    "钥匙串中的凭据为空，请重新填写 API Key。":
        "The credential in Keychain is empty; enter the API Key again.",
    "钥匙串读取失败或等待授权超时；请重试，或直接在表单中填写 API Key。":
        "Keychain could not be read or waiting for authorization timed out; try again or enter the API Key directly in the form.",
    "此环境无法使用 macOS 钥匙串。":
        "macOS Keychain is unavailable in this environment.",

    # account_info.py: local sign-in facts and the on-demand usage query. These
    # strings reach the browser only through error/message fields or the consent
    # prompt; no plan, window or model identifier is ever localized.
    "将读取 ~/.codex/auth.json 里的 ChatGPT 访问令牌，并用它向 chatgpt.com 查询用量。这是一次只读请求：不修改配置，不上传你的配置文件，也不保存令牌。是否继续？":
        "The ChatGPT access token in ~/.codex/auth.json will be read and used to query usage from chatgpt.com. This is a read-only request: no configuration is changed, your configuration file is not uploaded, and no token is saved. Continue?",
    "没有找到 ~/.codex/auth.json：请先用 ChatGPT 账号登录 Codex，再查询用量。":
        "~/.codex/auth.json was not found. Sign in to Codex with your ChatGPT account first, then query usage.",
    "本机保存的 ChatGPT 登录令牌已过期。请在 Codex 里发一条消息让它自动刷新，然后重试。":
        "The ChatGPT token saved on this Mac has expired. Send a message in Codex so it refreshes automatically, then retry.",
    "当前 Codex 使用 API Key 登录，没有 ChatGPT 套餐用量可查。":
        "Codex is signed in with an API key, so there is no ChatGPT plan usage to query.",
    "无法读取本机登录信息，因此无法查询用量。":
        "The local sign-in information could not be read, so usage cannot be queried.",
    "查询用量超时。请检查网络后重试；未发送任何配置内容。":
        "The usage query timed out. Check the network and retry; no configuration content was sent.",
    "无法连接 chatgpt.com 查询用量。请检查网络、代理或 TLS 证书；本工具不会跳过证书校验。":
        "Unable to reach chatgpt.com to query usage. Check the network, proxy, or TLS certificate; this tool never skips certificate verification.",
    "用量接口发生了跳转。为避免令牌泄露，未跟随跳转。":
        "The usage endpoint redirected. The redirect was not followed, to avoid leaking the token.",
    "用量接口拒绝了这次请求：登录已过期或未授权。请在 Codex 里重新登录后重试。":
        "The usage endpoint rejected this request: the sign-in has expired or is not authorized. Sign in again in Codex, then retry.",
    "用量接口限流，请稍后重试。":
        "The usage endpoint is rate limiting requests. Retry later.",
    "用量接口返回了成功状态，但响应不是有效 JSON，无法解析。":
        "The usage endpoint returned a success status, but the response is not valid JSON and cannot be parsed.",
    "用量响应使用了不支持的压缩方式，无法解析。":
        "The usage response used an unsupported content encoding and cannot be parsed.",
    "用量响应过大，已停止读取，未显示任何数据。":
        "The usage response was too large, so reading stopped and no data is shown.",
    "这个账号暂时没有可用的用量数据（接口返回 404）。":
        "This account has no usage data available yet (the endpoint returned 404).",
    "用量数据来自 chatgpt.com 的官方接口；仅供参考，以 Codex 界面显示为准。":
        "The usage data comes from the official chatgpt.com endpoint. It is for reference only; what the Codex UI shows is authoritative.",

    # server.py: API authorization, request validation, and safe errors.
    "拒绝不匹配的本机请求地址。":
        "The request was rejected because the local host address does not match.",
    "拒绝来自其他网页的请求。":
        "Requests from other web pages are not allowed.",
    "拒绝跨站请求。":
        "Cross-site requests are not allowed.",
    "会话凭据失效，请重新双击启动入口。":
        "The session credential is invalid; double-click the launcher again.",
    "无法读取配置；请确认文件权限。未输出原文件内容或密钥。":
        "Unable to read the configuration; check file permissions. Neither the original file contents nor keys have been exposed.",
    "页面不存在。":
        "Page not found.",
    "接口不存在。":
        "Endpoint not found.",
    "仅接受 JSON 请求。":
        "Only JSON requests are accepted.",
    "请求长度无效或超过限制。":
        "The request length is invalid or exceeds the limit.",
    "请求不是有效 JSON。":
        "The request is not valid JSON.",
    "预览请求不得包含 API Key。":
        "Preview requests must not contain an API Key.",
    "已有模型列表请求正在处理，请等待完成后重试。":
        "A model list request is already being processed; wait for it to finish before trying again.",
    "已有连接测试正在进行，请等待完成后重试。":
        "A connection test is already running; wait for it to finish before trying again.",
    "已有用量查询正在进行，请等待完成后重试。":
        "A usage query is already running; wait for it to finish before trying again.",
    "操作未能完成。请检查本机权限和备份目录；不要重复提交。详细异常已隐藏以保护凭据。":
        "The operation could not be completed. Check local permissions and the backup directory; do not submit repeatedly. Exception details have been hidden to protect credentials.",
    "此工具不允许跨域访问。":
        "This tool does not allow cross-origin access.",

    # server.py: CLI descriptions and runtime messages (translate_text callers).
    "本地配置界面已在运行。":
        "The local configuration UI is already running.",
    "Codex 本地模型配置界面；真实配置仅在界面确认后修改。":
        "Codex local model configuration UI; the real configuration is modified only after confirmation in the UI.",
    "在默认浏览器打开界面":
        "Open the UI in the default browser",
    "仅显示测试标识；请同时传入专用 --config-home":
        "Only display a test indicator; also specify a dedicated --config-home",
    "演示模式必须指定独立的 --config-home，不能使用真实配置。":
        "Demo mode requires a separate --config-home and cannot use the real configuration.",
    "运行状态文件不能为符号链接。":
        "The runtime state file cannot be a symbolic link.",
    "真实配置尚未修改。使用启动入口打开带会话凭据的界面；Ctrl+C 可停止服务。":
        "The real configuration has not been modified. Use the launcher to open the UI with session credentials; press Ctrl+C to stop the server.",
}


# Anchored whole-message templates, never substring replacements. Named captures
# are inserted once, without trimming, interpreting braces, or translating IDs.
_TEMPLATES = tuple((re.compile(pattern), english) for pattern, english in (
    (r"\A字段 (?P<field>[^\r\n]+) 必须是文本。\Z",
     "Field {field} must be text."),
    (r"\A字段 (?P<field>[^\r\n]+) 包含控制字符。\Z",
     "Field {field} contains control characters."),
    (r"\A字段 (?P<field>[^\r\n]+) 为空、过长或包含控制字符。\Z",
     "Field {field} is empty, too long, or contains control characters."),
    (r"\A字段 (?P<field>[^\r\n]+) 必须为布尔值。\Z",
     "Field {field} must be a boolean."),
    (r"\A更新供应商：(?P<provider>[^\r\n]+)\Z", "Update provider: {provider}"),
    (r"\A添加供应商：(?P<provider>[^\r\n]+)\Z", "Add provider: {provider}"),
    (r"\A一次添加 (?P<count>[0-9]+) 个模型：(?P<models>[^\r\n]+)\Z",
     "Add {count} models at once: {models}"),
    (r"\A为其余模型写入可切换 profile：(?P<names>[^\r\n]+)\Z",
     "Write switchable profiles for the remaining models: {names}"),
    (r"\A写入可切换 profile：(?P<names>[^\r\n]+)\Z",
     "Write a switchable profile: {names}"),
    (r"\A一次最多添加 (?P<limit>[0-9]+) 个模型；请分批添加。\Z",
     "At most {limit} models can be added at once; add them in batches."),
    (r"\A移除不再选择的托管 profile：(?P<name>[^\r\n]+)（原值完整保留在备份中）\Z",
     "Remove the managed profile that is no longer selected: {name} (the original value is fully preserved in the backup)"),
    (r"\A这些模型不在现有本地目录中：(?P<models>[^\r\n]+)。桌面端选择器只渲染目录条目，未列入时选择器会退回默认推荐模型集；勾选写入模型目录可让它们出现在列表中。\Z",
     "These models are not in the existing local catalog: {models}. The desktop picker renders catalog entries only, so an unlisted model makes the picker fall back to the default recommended set. Enable 'write model catalog' to make them appear in the list."),
    (r"\A写入模型目录：(?P<path>[^\r\n]+)\Z", "Write the model catalog: {path}"),
    (r"\A合并现有目录 (?P<total>[0-9]+) 条记录，新增 (?P<count>[0-9]+) 个模型条目\Z",
     "Merge {total} entries from the existing catalog and add {count} model entries"),
    # The report is Codex's own error text, kept verbatim. model_catalog collapses
    # it onto one line so it matches here like every other captured parameter.
    (r"\ACodex 拒绝这份模型目录，未生成任何文件：(?P<report>[^\r\n]+)\Z",
     "Codex rejected this model catalog, so no file was written: {report}"),
    (r"\A现有模型目录本身就无法被 Codex 解析，未生成任何文件：(?P<report>[^\r\n]+)\Z",
     "The existing model catalog itself cannot be parsed by Codex, so no file was written: {report}"),
    (r"\A这些模型 ID 已在目录中，保留原有条目：(?P<models>[^\r\n]+)\Z",
     "These model IDs are already in the catalog; the existing entries are kept: {models}"),
    (r"\Aprofile 名称 (?P<name>[^\r\n]+) 已被占用且含本工具不写入的设置。请换个供应商 ID，或手动改名/删除该 profile 后重试。\Z",
     "The profile name {name} is already used and contains settings this tool does not write. Choose a different provider ID, or rename or remove that profile manually before retrying."),
    # '未设置' may be the fallback OR an actual model ID. The rendered message
    # cannot distinguish them, so it too must remain verbatim inside a capture.
    (r"\A默认模型：(?P<old>[^\r\n]*) → (?P<new>[^\r\n]+)\Z",
     "Default model: {old} → {new}"),
    (r"\A移除旧模型专属参数：(?P<parameter>[^\r\n]+)（原值完整保留在备份中）\Z",
     "Remove previous model-specific parameter: {parameter} (the original value is fully preserved in the backup)"),
    (r"\A忽略了 (?P<count>[0-9]+) 条格式不正确或包含敏感内容的记录。\Z",
     "Ignored {count} records with an invalid format or sensitive content."),
    (r"\A服务商模型列表接口返回 HTTP (?P<code>[0-9]{3})；未读取错误正文，请稍后重试或手动填写。\Z",
     "The provider's model list endpoint returned HTTP {code}; the error body was not read. Retry later or enter the model manually."),
    (r"\A服务端返回 HTTP (?P<status>[0-9]{3})；未读取错误正文，请稍后重试。\Z",
     "The service returned HTTP {status}; the error body was not read. Retry later."),
    (r"\A用量接口返回 HTTP (?P<status>[0-9]{3})。为避免泄露令牌，未读取错误正文；请稍后重试。\Z",
     "The usage endpoint returned HTTP {status}. The error body was not read, to avoid leaking the token. Retry later."),
    (r"\A无法检查钥匙串（系统状态 (?P<status>-?[0-9]+)）。请先解锁登录钥匙串。\Z",
     "Unable to check Keychain (system status {status}). Unlock the login Keychain first."),
    (r"\A钥匙串保存未成功（系统状态 (?P<status>-?[0-9]+)）。未写入 Codex 配置。\Z",
     "Keychain could not be saved (system status {status}). The Codex configuration was not written."),
    (r"\A本地配置界面已启动：(?P<origin>[^\r\n]+)；仅监听 127\.0\.0\.1。\Z",
     "The local configuration UI has started: {origin}; listening only on 127.0.0.1."),
))

_MESSAGE_FIELDS = frozenset({"error", "message", "warnings", "changes"})
# Treat these as opaque even if an unexpected value contains nested message keys.
_OPAQUE_FIELDS = frozenset({
    "config_path", "revision", "id", "ids", "name", "provider_id", "model_id",
    "model", "models", "model_provider", "plan_id", "backup_id", "snippet", "snippets",
    "endpoint", "base_url", "path", "paths", "files", "backup_path", "code",
})


def normalize_language(language=None):
    """Normalize an explicit UI header to 'en'; everything else defaults to 'zh'.

    This is not Accept-Language negotiation: 'en-US' and language lists default
    to Chinese, just like missing, malformed, and unsupported header values.
    """
    return "en" if isinstance(language, str) and language.strip().lower() == "en" else "zh"


def translate_text(value, language=None):
    """Translate one known human message, preserving unknowns and non-string values."""
    if not isinstance(value, str) or normalize_language(language) != "en":
        return value
    if value in _EXACT_TRANSLATIONS:
        return _EXACT_TRANSLATIONS[value]
    for pattern, english in _TEMPLATES:
        match = pattern.fullmatch(value)
        if match is not None:
            return english.format_map(match.groupdict())
    return value


def translate_response(data, language=None):
    """Return a new dict/list JSON structure with only human fields localized.

    Translate error/message/warnings/changes, including string elements of their
    nested lists, and warning directly within catalog. Other string leaves are
    opaque. Dictionaries within message lists are still field-scoped, not blanket
    translated. Protected identifier/path/snippet subtrees are always opaque.

    Iterative copying avoids recursion limits for deeply nested arrays. Memo keys
    include scope so an aliased list used as both warnings and IDs stays separate.
    No metadata, fields, or process-wide language state are added or changed.
    """
    language = normalize_language(language)
    memo = {}
    pending = []

    def clone(value, scope):
        if isinstance(value, (dict, list)):
            identity = (id(value), scope)
            if identity not in memo:
                result = {} if isinstance(value, dict) else []
                memo[identity] = result
                pending.append((value, result, scope))
            return memo[identity]
        return translate_text(value, language) if scope == "text" else value

    result = clone(data, "object")
    while pending:
        source, target, scope = pending.pop()
        if isinstance(source, list):
            target.extend(clone(value, scope) for value in source)
            continue
        for key, value in source.items():
            if scope == "opaque" or key in _OPAQUE_FIELDS:
                child_scope = "opaque"
            elif key in _MESSAGE_FIELDS or (scope == "catalog" and key == "warning"):
                child_scope = "text"
            elif key == "catalog":
                child_scope = "catalog"
            else:
                child_scope = "object"
            target[key] = clone(value, child_scope)
    return result
