(() => {
  'use strict';
  const english = Object.freeze({
    '填写有效 API 地址后显示 GET …/models；不要在地址中包含凭据或查询参数。': 'Enter a valid API URL to see GET …/models. Do not include credentials or query parameters.',
    '请选择模型（不会自动选择）': 'Choose a model (no automatic selection)',
    '没有匹配项；可调整搜索或手动填写': 'No matches; adjust your search or enter a model manually',
    '显示 {shown} / {total} 个模型 ID': 'Showing {shown} / {total} model IDs',
    '已停止等待旧列表。已发送的请求无法撤回，旧结果会被忽略；新地址或凭据需重新确认。': 'Stopped waiting for the previous list. Sent requests cannot be recalled; old results will be ignored. Confirm again for a new URL or credentials.',
    '尚未请求当前设置的列表。仅点击获取并确认后发送请求；也可直接手动填写模型 ID。': 'No list requested for these settings. A request is sent only after you click Fetch and confirm. You can also enter a model ID manually.',
    '尚未发送请求。': 'No request has been sent. ',
    '提供方 ID 须以小写字母开头，最多 64 个字符，仅含小写字母、数字、_ 或 -。': 'Provider IDs must start with a lowercase letter and contain at most 64 lowercase letters, digits, underscores, or hyphens.',
    '请填写完整 HTTPS 地址或本机回环 HTTP 地址，不要包含账号密码、查询参数或片段。': 'Enter a full HTTPS URL or a loopback HTTP URL without a username, password, query parameters, or fragment.',
    '已有提供方使用外部认证，不能在此读取凭据。请新建独立提供方 ID。': 'This saved provider uses external authentication. Its credentials cannot be read here. Create a separate provider ID.',
    '尚未发送请求。请选择有效的认证方式。': 'No request has been sent. Choose a valid authentication method.',
    '请填写 API Key；仅相同提供方与 API 地址可复用已托管的密钥。': 'Enter an API key. A managed key can only be reused for the same provider and API URL.',
    'Keychain 不可用，无法读取已托管密钥。可临时填写新密钥获取列表，但不会保存。': 'Keychain is unavailable, so the managed key cannot be read. You can enter a temporary key to fetch the list; it will not be saved.',
    '请填写有效的环境变量名，不要填写密钥内容。': 'Enter a valid environment variable name, not the secret value.',
    '获取列表只能使用已保存且提供方 ID、API 地址和变量名均匹配的环境变量配置；变量须存在于本地服务进程环境中。可改用临时 API Key，或手动填写模型 ID。': 'Fetching requires a saved environment-variable configuration with the same provider ID, API URL, and variable name. The variable must exist in the local service process environment. Alternatively, use a temporary API key or enter a model ID manually.',
    '将不携带认证凭据向 {endpoint} 读取模型列表，不保存密钥、不修改配置、不调用模型生成。是否继续？': 'Fetch the model list from {endpoint} without authentication credentials? No keys will be saved, no configuration changed, and no model generation called. Continue?',
    '将使用你的凭据向 {endpoint} 读取模型列表，不保存密钥、不修改配置、不调用模型生成。是否继续？': 'Use your credentials to fetch the model list from {endpoint}? No keys will be saved, no configuration changed, and no model generation called. Continue?',
    '已取消，未发送模型列表请求。可手动填写模型 ID，或再次点击获取。': 'Cancelled. No model-list request was sent. Enter a model ID manually or click Fetch again.',
    '已开始读取模型列表。请完成选择后重新预览与确认。': 'Fetching the model list. Finish selecting a model, then preview and confirm again.',
    '正在读取模型列表… 不保存凭据或配置，不调用模型生成。': 'Fetching models… No credentials or configuration will be saved, and no model generation will be called.',
    '模型列表响应格式不完整。': 'The model-list response is incomplete.',
    '模型列表端点不一致。': 'The model-list endpoint does not match.',
    '已读取 {count} 个模型 ID，请自行选择或继续手动填写。': 'Fetched {count} model IDs. Choose one yourself or continue with manual entry.',
    '服务未返回模型 ID，可能为空或不支持模型列表。请手动填写，或再次点击获取重试。': 'No model IDs were returned. The list may be empty or unsupported. Enter a model manually or click Fetch to retry.',
    ' 列表可能不完整，可再次点击获取重试或手动填写。': ' The list may be incomplete. Click Fetch to retry or enter a model manually.',
    ' 列出模型不代表支持 Responses API；未测试模型生成。': ' A listed model does not imply Responses API support. Model generation has not been tested.',
    '服务不支持模型列表接口。': 'The provider does not support the model-list endpoint.',
    '读取模型列表超时。': 'Fetching the model list timed out.',
    '无法连接本地配置服务。': 'Cannot connect to the local configuration service.',
    '服务返回的模型列表或端点格式不正确。': 'The returned model list or endpoint is invalid.',
    '上游列表请求失败，请检查服务地址和认证凭据。': 'The upstream model-list request failed. Check the provider URL and credentials.',
    '未能读取模型列表，请检查地址、认证和本地服务状态。': 'Could not fetch the model list. Check the URL, authentication, and local service status.',
    ' 可手动填写模型 ID，或再次点击获取重试；未保存密钥或配置，未调用模型生成。': ' Enter a model ID manually or click Fetch to retry. No keys or configuration were saved, and no model generation was called.',
    '未设置': 'Not set',
    '本地会话不可用。请关闭此页面，从配置工具重新打开。': 'The local session is unavailable. Close this page and reopen it from the configuration tool.',
    '已阻止非本地 API 请求。': 'A non-local API request was blocked.',
    '会话已失效，请从配置工具重新打开页面。': 'The session has expired. Reopen this page from the configuration tool.',
    '会话需要重新打开': 'Reopen the session',
    '本地会话已失效或未获授权。请从配置工具重新打开此页面。': 'The local session has expired or is unauthorized. Reopen this page from the configuration tool.',
    '会话已失效。预览与应用已禁用，请重新打开本地工具。': 'The session has expired. Preview and apply are disabled. Reopen the local tool.',
    '请求等待超时，请检查本地工具是否仍在运行。': 'The request timed out. Check that the local tool is still running.',
    '本地服务返回了无法读取的响应。请刷新状态后重试。': 'The local service returned an unreadable response. Refresh the status and retry.',
    '本地服务未能完成请求，请刷新状态后重试。': 'The local service could not complete the request. Refresh the status and retry.',
    '本地服务返回的数据格式不正确。': 'The local service returned an invalid data format.',
    '请求已取消。': 'The request was cancelled.',
    '无法连接本地配置服务。请确认工具仍在运行，再刷新状态。': 'Cannot connect to the local configuration service. Check that the tool is still running, then refresh the status.',
    '留空以保留已托管的密钥': 'Leave blank to keep the managed key',
    '输入 API Key': 'Enter an API key',
    '可留空': 'Optional',
    '必填': 'Required',
    '相同提供方与地址可复用已托管凭据。确认获取列表时会发送给该服务商；填写新密钥仅在应用时保存。预览不发送密钥。': 'Managed credentials can be reused for the same provider and URL. Fetching sends them to that provider after confirmation. A new key is saved only when applying. Preview never sends keys.',
    '请填写 API Key。确认获取列表时经本地服务发送给你填写的服务商，但不保存；仅应用时保存。预览不发送密钥。': 'Enter an API key. After fetch confirmation, the local service sends it to your provider without saving it. It is saved only when applying. Preview never sends keys.',
    '等待状态': 'Awaiting status',
    '本机可用': 'Available locally',
    '本机不可用': 'Unavailable locally',
    '正在读取': 'Reading',
    '刷新状态': 'Refresh status',
    '正在获取…': 'Fetching…',
    '获取模型列表': 'Fetch models',
    '正在生成预览': 'Preparing preview',
    '预览配置更改': 'Preview changes',
    '正在备份并写入': 'Backing up and saving',
    '确认并应用配置': 'Confirm and apply',
    '表单已更改。请重新预览，并再次确认。': 'The form has changed. Preview and confirm again.',
    '需要重新预览': 'New preview required',
    '以小写字母开头，最多 64 个字符，仅使用小写字母、数字、下划线或短横线。': 'Start with a lowercase letter. Use at most 64 lowercase letters, digits, underscores, or hyphens.',
    '请填写提供方名称。': 'Enter a provider name.',
    '请输入完整 HTTPS 地址，或仅指向本机回环地址的 HTTP 地址；不要包含账号密码或片段。': 'Enter a full HTTPS URL, or an HTTP URL pointing only to a local loopback address, without a username, password, or fragment.',
    '请填写提供方实际支持的模型 ID。': 'Enter a model ID that your provider actually supports.',
    '表单选项无效，请重新选择。': 'Invalid form options. Select them again.',
    '本机 Keychain 不可用，请选择环境变量或无认证模式。': 'Keychain is unavailable on this machine. Use an environment variable or no authentication.',
    '此提供方没有可复用的受管理凭据，请填写 API Key。': 'This provider has no reusable managed credentials. Enter an API key.',
    '请填写有效的环境变量名称，例如 OPENAI_API_KEY，而不是密钥内容。': 'Enter a valid environment variable name, such as OPENAI_API_KEY, not the secret value.',
    '请检查标出的字段；尚未发送预览请求。': 'Check the highlighted fields. No preview request has been sent.',
    '显示 API Key': 'Show API key',
    '显示': 'Show',
    '尚无已保存的提供方': 'No saved providers yet',
    '当前默认提供方': 'Current default provider',
    '这个提供方使用已有的外部认证。本工具不会覆盖它，请点击新建提供方并使用独立 ID。': 'This provider uses existing external authentication. This tool will not overwrite it. Click New provider and use a separate ID.',
    '已切换提供方。请重新预览。': 'Provider changed. Preview again.',
    '编辑已保存的提供方': 'Edit saved provider',
    '已填入提供方信息，未读取或回填任何密钥。尚未修改配置。': 'Provider details loaded. No keys were read or filled in. No configuration has changed.',
    '新建提供方': 'New provider',
    '填写提供方信息后，先预览将要写入的配置。': 'Enter provider details, then preview the configuration before saving.',
    '尚未设置默认模型': 'No default model set',
    '未设置提供方': 'No provider set',
    '未指定': 'Not specified',
    '保留原始目录': 'Original catalog preserved',
    '目录保留状态异常': 'Catalog preservation not confirmed',
    '未设置自定义目录（使用内置目录）': 'No custom catalog set (using the built-in catalog)',
    '服务未返回配置路径': 'No configuration path returned',
    '服务未返回': 'Not returned by the service',
    '服务未返回应用路径': 'No application path returned',
    '演示服务 · 非实际配置': 'Demo service · Not real configuration',
    '本地配置已读取': 'Local configuration loaded',
    '服务尚未返回备份记录。应用配置后，可在这里查看。': 'No backup records returned yet. After applying, view backups here.',
    '状态已刷新，请基于最新配置重新预览。': 'Status refreshed. Preview again using the latest configuration.',
    '配置已写入，正在重新读取本地状态…': 'Configuration saved. Reloading local status…',
    '正在从本地服务读取配置…': 'Reading configuration from the local service…',
    '正在刷新 · 仍显示上次读取的数据': 'Refreshing · Showing previously loaded data',
    '正在读取本地配置': 'Reading local configuration',
    '本地状态数据不完整。为避免误操作，暂不能预览或应用。': 'Local status data is incomplete. Preview and apply are disabled to prevent unintended changes.',
    '演示数据 · 不代表实际 Codex 配置': 'Demo data · Not actual Codex configuration',
    '来自本地配置 · 不代表连接状态': 'From local configuration · Not connection status',
    '配置已写入，状态已刷新。模型生成未测试；请按下方说明手动重启 Codex。': 'Configuration saved and status refreshed. Model generation is untested. Follow the instructions below to restart Codex manually.',
    '当前连接的是演示服务，以下内容不是实际 Codex 配置。': 'Connected to a demo service. The following is not actual Codex configuration.',
    '本地状态已同步。预览不会写入配置，也不会发送 API Key。': 'Local status synced. Preview does not write configuration or send API keys.',
    '读取失败 · 显示上次读取的数据，仅供参考': 'Read failed · Previously loaded data shown for reference only',
    '未能读取本地配置': 'Could not read local configuration',
    '配置状态不可用': 'Configuration status unavailable',
    '尚未读取': 'Not yet read',
    '读取成功后显示已保存提供方': 'Saved providers appear after a successful read',
    '读取成功后显示备份记录': 'Backups appear after a successful read',
    '本地状态读取失败': 'Could not read local status',
    '写入已成功，但状态刷新失败。': 'Configuration was saved, but refreshing the status failed. ',
    '写入已确认；最新状态暂不可用，不必再次应用。': 'Save confirmed. Latest status is temporarily unavailable; do not apply again.',
    '本次状态读取未修改配置。请检查本地服务并重试。': 'This status request did not change configuration. Check the local service and retry.',
    '正在生成更改计划。此请求不含 API Key，不会写入配置。': 'Preparing the change plan. This request contains no API key and does not write configuration.',
    '预览结果不完整，未启用应用操作。请重新预览。': 'The preview is incomplete. Apply remains disabled. Preview again.',
    '设为默认模型': 'Set as default model',
    '保留当前默认': 'Keep current default',
    '待你确认': 'Awaiting confirmation',
    '预览已生成，尚未写入。请核对文件、配置差异与提示，再勾选确认。': 'Preview ready. Nothing has been saved. Review the files, changes, and notices, then check the confirmation box.',
    '预览未完成，应用操作保持禁用。没有写入配置。': 'Preview did not complete. Apply remains disabled. No configuration was written.',
    '演示服务已返回应用结果': 'The demo service returned an apply result',
    '配置已写入，下一步重启 Codex': 'Configuration saved. Restart Codex next.',
    '本地服务已确认完成配置写入。': 'The local service confirmed that configuration was saved.',
    '已设为默认': 'Set as default',
    '原默认设置未变': 'Previous default unchanged',
    '本地 Keychain 已保存': 'Saved to local Keychain',
    '未报告新的 Keychain 写入': 'No new Keychain write reported',
    '使用环境变量，不写入密钥值': 'Using an environment variable; no secret value written',
    '不使用认证凭据': 'No authentication credentials used',
    '请先保存工作，然后使用 ⌘Q 完全退出 Codex，再手动重新打开。页面不会自动重启应用。': 'Save your work, fully quit Codex with ⌘Q, then reopen it manually. This page does not restart the app automatically.',
    '如需让新配置生效，请先保存工作，使用 ⌘Q 完全退出 Codex，再手动重新打开。页面不会自动重启应用。': 'To activate the new configuration if needed, save your work, fully quit Codex with ⌘Q, then reopen it manually. This page does not restart the app automatically.',
    '表单与预览不一致。请重新生成预览。': 'The form no longer matches the preview. Generate a new preview.',
    '正在请求本地服务先备份、再写入。请不要重复操作或关闭页面。': 'The local service is backing up before saving. Do not repeat this action or close the page.',
    '服务响应未完整确认写入结果。': 'The service response did not fully confirm the save result.',
    '本地服务已确认写入。模型生成未测试，尚未重启应用。': 'The local service confirmed the save. Model generation is untested, and the app has not been restarted.',
    ' 未收到明确的完成结果，配置可能已写入。请先刷新状态并检查备份，不要直接重复应用。': ' No definitive completion result was received; configuration may already have been saved. Refresh the status and check backups before doing anything else. Do not simply apply again.',
    ' 本次计划已作废。请检查状态与提示，重新预览后再操作。': ' This plan is no longer valid. Check the status and notices, then preview again before proceeding.',
    '写入结果待确认。请先刷新状态并查看备份。': 'Save result unconfirmed. Refresh the status and inspect backups first.',
    '应用未获成功确认，请检查错误提示。': 'Apply was not confirmed successful. Check the error message.',
    '此计划已完成。': 'This plan is complete.',
    '此计划已失效。请先刷新状态，再重新预览。': 'This plan is no longer valid. Refresh the status, then preview again.',
    '应用后状态待确认 · 请刷新': 'Post-apply status unconfirmed · Refresh required',
    '表单尚未应用。请预览最新更改后再确认。': 'The form has not been applied. Preview the latest changes, then confirm.',
    '表单已重置，请重新预览。': 'The form was reset. Preview again.',
    '隐藏 API Key': 'Hide API key',
    '隐藏': 'Hide',
    '已取消本次预览。没有写入配置。': 'Preview cancelled. No configuration was written.',
    '继续编辑提供方': 'Continue editing provider',
    '会话已结束，请从配置工具重新打开。': 'The session has ended. Reopen it from the configuration tool.',
    '页面已从历史记录恢复，但会话凭据未保留。请从配置工具重新打开。': 'The page was restored from history, but session credentials were not retained. Reopen it from the configuration tool.',
    '未检测到有效的本地会话令牌。为保护配置与密钥，页面不会请求任何 API。请从本地配置工具重新打开，不要手动粘贴或分享令牌。': 'No valid local session token was detected. To protect configuration and secrets, this page will not call any API. Reopen it from the local configuration tool. Do not manually paste or share the token.',
    '请仅在本机回环地址直接打开此工具。此页面不能发布到公网或嵌入其他页面，当前未发送任何 API 请求。': 'Open this tool directly on a local loopback address only. Do not publish it publicly or embed it in another page. No API requests have been sent.',
    '等待安全的本地会话': 'Awaiting a secure local session',
    '未连接 · 尚未读取任何配置': 'Disconnected · No configuration read',
    '等待本地会话': 'Awaiting a local session',
    '从配置工具打开后读取': 'Read after opening from the configuration tool',
    '连接本地会话后显示': 'Shown after connecting to a local session',
    '连接本地会话后读取备份记录': 'Backups loaded after connecting to a local session',
    '尚未访问配置，也未发送任何请求。请重新启动本地配置工具。': 'No configuration has been accessed and no requests sent. Restart the local configuration tool.',
    '跳转到模型配置': 'Skip to model configuration',
    '本地配置导航': 'Local configuration navigation',
    '页面导航': 'Page navigation',
    '模型配置': 'Model configuration',
    '配置备份': 'Configuration backups',
    '已保存的提供方': 'Saved providers',
    '提供方数量': 'Provider count',
    '等待从本地服务读取…': 'Waiting to read from the local service…',
    '当前配置文件': 'Current configuration file',
    '等待本地状态…': 'Awaiting local status…',
    '等待读取': 'Awaiting read',
    '工作区': 'Workspace',
    '模型与提供方': 'Models & providers',
    '为 Codex 接入你的模型': 'Bring your models to Codex',
    '配置提供方，保留熟悉的工作方式。每一次更改，都先预览，再由你确认。': 'Configure providers without changing how you work. Preview every change, then confirm it yourself.',
    '此页面需要启用 JavaScript 才能读取本地状态。当前没有请求 API，也不会修改配置。': 'Enable JavaScript to read local status. No API requests have been made, and no configuration will be changed.',
    '演示服务': 'Demo service',
    '· 服务端标记了 demo 模式。以下状态与操作结果不代表实际 Codex 配置。': '· The server reports demo mode. The status and results below do not represent actual Codex configuration.',
    '服务未确认模型目录处于保留状态。此界面不会编辑目录；请先核对服务端返回的预览与警告。': 'The service has not confirmed that the model catalog is preserved. This interface does not edit the catalog. Review the server preview and warnings first.',
    '本地服务提示': 'Local service notices',
    '当前本地配置': 'Current local configuration',
    '当前工作区': 'Current workspace',
    '等待本地服务响应': 'Awaiting local service response',
    '当前默认模型': 'Current default model',
    '正在读取…': 'Reading…',
    '配置值 · 未测试模型生成': 'Configured value · Generation untested',
    '模型提供方': 'Model provider',
    '推理强度': 'Reasoning effort',
    '原始模型目录': 'Original model catalog',
    '条记录': 'entries',
    '配置流程': 'Configuration workflow',
    '填写配置': 'Enter details',
    '预览与确认': 'Preview & confirm',
    '应用后重启': 'Apply & restart',
    '提供方配置': 'Provider configuration',
    '先预览 · 后写入': 'Preview before saving',
    '模型提供方与认证设置': 'Model provider and authentication settings',
    '提供方名称': 'Provider name',
    '例如：我的模型服务': 'e.g. My model service',
    '提供方 ID': 'Provider ID',
    '小写字母、数字、_ 或 -；留空使用 custom。': 'Lowercase letters, digits, _ or -. Leave blank to use custom.',
    'API 地址': 'API URL',
    '使用完整 HTTPS 地址；HTTP 仅允许 localhost、127.0.0.1 或 ::1 回环地址。': 'Use a full HTTPS URL. HTTP is allowed only for localhost, 127.0.0.1, or ::1 loopback addresses.',
    '模型 ID': 'Model ID',
    '可直接手动填写，或仅用 API 地址与认证信息获取列表；无需先填写名称、模型或推理强度。列表不证明 Responses API 兼容性或生成能力。': 'Enter a model manually, or fetch a list using only the API URL and authentication. A name, model, or reasoning effort is not required to fetch. The list does not verify Responses API compatibility or generation capability.',
    '获取前会弹窗确认：将使用你的凭据向下方服务商读取模型列表（无认证模式除外），不保存密钥、不修改配置、不调用模型生成。输入和打开页面都不会自动获取；预览不发送密钥。': 'A confirmation dialog appears before fetching. Your credentials are used to read the model list from the provider below (unless authentication is disabled). No keys are saved, no configuration changed, and no generation called. Typing or opening this page never fetches automatically. Preview never sends keys.',
    '填写 API 地址后显示 GET …/models。': 'Enter an API URL to see GET …/models.',
    '尚未请求列表。仅点击获取并确认后发送请求；也可直接手动填写模型 ID。': 'No list requested. A request is sent only after you click Fetch and confirm. You can also enter a model ID manually.',
    '模型列表服务提示': 'Model-list service notices',
    '搜索已获取的模型': 'Search fetched models',
    '仅本地筛选': 'Local filtering only',
    '按模型 ID 筛选': 'Filter by model ID',
    '从列表选择模型': 'Choose a model from the list',
    '认证与运行选项': 'Authentication & runtime options',
    '认证方式': 'Authentication method',
    '本地 Keychain（推荐）': 'Local Keychain (recommended)',
    '环境变量': 'Environment variable',
    '无认证': 'No authentication',
    '密钥值不写入 TOML 配置。': 'Secret values are not written to TOML configuration.',
    '不指定（不发送此字段）': 'Unspecified (omit this field)',
    '仅选择目标模型支持的级别。': 'Choose only a level supported by the target model.',
    '无（none）': 'None (none)',
    '最小（minimal）': 'Minimal (minimal)',
    '低（low）': 'Low (low)',
    '中（medium）': 'Medium (medium)',
    '高（high）': 'High (high)',
    '极高（xhigh）': 'Extra high (xhigh)',
    '确认获取列表时，密钥经本地服务发送给你填写的服务商，但不保存。仅应用时保存密钥；预览请求不包含密钥。': 'After fetch confirmation, the local service sends the key to your provider without saving it. Keys are saved only when applying; preview requests contain no key.',
    '本机 Keychain 不可用，当前模式不能预览或应用；仍可填写临时 API Key 获取列表，不会保存。预览与应用请改用其他认证方式。': 'Keychain is unavailable, so this mode cannot preview or apply. You can still enter a temporary API key to fetch models without saving it. Use another authentication method to preview and apply.',
    '环境变量名': 'Environment variable name',
    '仅填写变量名称，不设置变量。获取列表只允许已保存且提供方 ID、API 地址、变量名均匹配的配置，并从本地服务进程环境读取凭据；启动 Codex 的环境也须能读取该变量。': 'Enter only the variable name; this does not set the variable. Fetching requires a saved configuration matching the provider ID, API URL, and variable name, and reads credentials from the local service process environment. The environment launching Codex must also have this variable.',
    '此模式不保存或发送认证凭据。仅适用于无需认证的服务；确认获取列表会请求 /models，但不会测试模型生成。': 'This mode neither saves nor sends authentication credentials. Use only with services that require no authentication. Confirmed fetching calls /models but does not test model generation.',
    '设为 Codex 默认模型': 'Set as the Codex default model',
    '更新默认提供方与模型；不修改原始模型目录。': 'Update the default provider and model without changing the original model catalog.',
    '预览不写入文件，也不发送密钥。': 'Preview neither writes files nor sends keys.',
    '配置说明': 'Configuration guidance',
    '更改之前，心中有数': 'Know what will change',
    '只改需要改的配置': 'Change only what is needed',
    '预览准确的文件路径与 TOML 变更片段，不替换或扩写原始模型目录。': 'Preview exact file paths and TOML changes without replacing or extending the original model catalog.',
    '由你确认，先备份再写入': 'You confirm. Backup comes first.',
    '核对更改并明确勾选确认后，才能应用。完成后展示实际备份目录。': 'Apply is enabled only after you review the changes and check the confirmation box. The actual backup directory is shown afterward.',
    '保存工作，手动重启': 'Save your work. Restart manually.',
    '使用 ⌘Q 完全退出 Codex 后重新打开。已有会话不保证沿用新配置。': 'Fully quit Codex with ⌘Q, then reopen it. Existing sessions are not guaranteed to use the new configuration.',
    '原始目录，保持原样': 'Keep the original catalog intact',
    '只读': 'Read-only',
    '目录记录数以本地服务为准。配置成功不保证模型出现在 Codex 模型选择器中。': 'Catalog counts come from the local service. Successful configuration does not guarantee visibility in the Codex model picker.',
    '等待目录路径…': 'Awaiting catalog path…',
    '配置成功 ≠ 连接成功': 'Configuration success ≠ connection success',
    '仅在点击获取并确认后请求服务商的 /models 列表；不调用模型生成。列表成功不证明 Responses API 兼容性，配置成功也不代表模型可用。': 'The provider’s /models list is requested only after you click Fetch and confirm. No model generation is called. A successful list does not prove Responses API compatibility, and a saved configuration does not prove the model works.',
    '正在初始化本地会话…': 'Initializing local session…',
    '核对这一次更改': 'Review this change',
    '以下计划来自本地服务；尚未写入任何配置': 'This plan comes from the local service. No configuration has been written.',
    '将涉及的文件 · 完整路径': 'Affected files · Full paths',
    '服务未返回文件变更。': 'The service returned no file changes.',
    '配置变更预览 · 不含密钥': 'Configuration preview · No secrets',
    '服务返回的非敏感 TOML 变更片段': 'Non-sensitive TOML changes returned by the service',
    '仅显示变更片段，不是完整配置文件': 'Changes only, not the complete configuration file',
    '变更清单': 'Changes',
    '服务未报告配置差异。': 'The service reported no configuration differences.',
    '服务端提示': 'Server notices',
    '服务没有附加警告；这不代表模型生成或 Responses API 兼容性已经验证。': 'The service provided no additional warnings. This does not verify model generation or Responses API compatibility.',
    '此操作会修改 Codex 配置；先备份再写入，已有会话不受保证，请先保存工作。': 'This changes Codex configuration after creating a backup. Existing sessions are not guaranteed to be unaffected. Save your work first.',
    '我已核对上述文件路径、配置差异与提示，已保存工作，确认应用本次更改。': 'I have reviewed the file paths, configuration changes, and notices, saved my work, and confirm applying this change.',
    '修改任意表单项后，必须重新预览与确认。': 'Changing any form field requires a new preview and confirmation.',
    '取消预览': 'Cancel preview',
    '提供方': 'Provider',
    '模型': 'Model',
    '默认配置': 'Default configuration',
    '认证处理': 'Credential handling',
    '实际配置文件': 'Actual configuration file',
    '本次备份目录': 'Backup directory for this change',
    '请手动重启 Codex': 'Restart Codex manually',
    '模型生成未测试。': 'Model generation has not been tested. ',
    '列表与配置写入都不证明 Responses API 兼容性，模型选择器可见性也不保证。': 'Neither model listing nor saving configuration proves Responses API compatibility. Model-picker visibility is not guaranteed either.',
    '继续编辑': 'Continue editing',
    '备份数量': 'Backup count',
    '仅查看记录': 'View records only',
    '只展示本地服务返回的备份，不执行恢复或删除操作。时间按服务原始记录显示。': 'Only backups returned by the local service are shown. No restore or delete operations are performed. Times are displayed exactly as recorded by the service.',
    'Codex 应用路径': 'Codex application path',
    '仅供本机使用，请勿发布或暴露到公网。获取列表时会把凭据发送到你填写的服务商（无认证模式除外）；仅点击应用才保存钥匙串和配置。预览不发送密钥，列表请求不调用模型生成。本地 HTTP 不等于加密传输；会话令牌与密钥不写入浏览器本地存储。': 'For local use only. Do not publish or expose this tool to the public internet. Fetching sends credentials to your provider (unless authentication is disabled); only Apply saves Keychain entries and configuration. Preview never sends keys, and list requests do not call model generation. Local HTTP is not encrypted transport. Session tokens and secrets are never stored in browser storage.',
    '本地配置工具': 'LOCAL CONFIGURATOR',
    '你的模型，你的工作区。': 'YOUR MODELS. YOUR WORKSPACE.',
    '[ 本地工作区 ]': '[ LOCAL WORKSPACE ]',
    '预览 → 确认 → 应用': 'PREVIEW → CONFIRM → APPLY',
    '仅限本机': 'LOCAL ONLY',
    '仅限本机 · {host}': 'LOCAL ONLY · {host}',
    '修订版本': 'Revision',
    '语言': 'Language',
    '切换为中文': 'Switch to Chinese',
    '切换为英文': 'Switch to English',
    '此服务端消息暂无本地译文；保留原文。可手动刷新状态获取当前语言，请勿为翻译重复应用配置。': 'No local translation is available for this server message; the original is preserved. You can refresh status manually for the current language. Do not apply configuration again just to translate it.'
  });
  const storageKey = 'weswitch.language';
  let language = 'zh-CN';
  try {
    const saved = window.localStorage.getItem(storageKey);
    if (saved === 'en' || saved === 'zh-CN') language = saved;
  } catch (_) { /* Storage may be unavailable; Chinese remains the default. */ }

  const bindings = new Map();
  const missing = new Set();
  const reverse = new Map(Object.entries(english).map(([zh, en]) => [en, zh]));
  const backend = window.WeSwitchBackendMessages || { exact: {}, templates: [] };
  const backendReverse = new Map(Object.entries(backend.exact).map(([zh, en]) => [en, zh]));
  function matchTemplate(source, template) {
    const names = [];
    const parts = template.split(/(\{[a-zA-Z][a-zA-Z0-9_]*\})/g);
    const pattern = parts.map(part => {
      if (/^\{[a-zA-Z][a-zA-Z0-9_]*\}$/.test(part)) { names.push(part.slice(1, -1)); return '(.*?)'; }
      return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }).join('');
    const found = new RegExp('^' + pattern + '$', 'u').exec(source);
    return found ? Object.fromEntries(names.map((name, index) => [name, found[index + 1]])) : null;
  }
  const interpolate = (template, params) => template.replace(/\{([a-zA-Z][a-zA-Z0-9_]*)\}/g,
    (match, name) => Object.prototype.hasOwnProperty.call(params, name) ? params[name] : match);
  const message = (source, params = {}) => Object.freeze({ kind: 'message', source, params: Object.freeze({ ...params }) });
  const raw = (value) => Object.freeze({ kind: 'raw', value: String(value) });
  const concat = (...parts) => Object.freeze({ kind: 'concat', parts });
  const hasKey = (key) => Object.prototype.hasOwnProperty.call(english, key);
  const note = '此服务端消息暂无本地译文；保留原文。可手动刷新状态获取当前语言，请勿为翻译重复应用配置。';

  // Only explicitly classified human response fields enter this path. IDs, paths,
  // provider names, secrets and TOML never enter the translation dictionary.
  function server(source, responseLanguage) {
    if (Object.prototype.hasOwnProperty.call(backend.exact, source))
      return Object.freeze({kind: 'bilingual', zh: source, en: backend.exact[source]});
    if (backendReverse.has(source))
      return Object.freeze({kind: 'bilingual', zh: backendReverse.get(source), en: source});
    for (const templates of backend.templates) {
      for (const locale of ['zh', 'en']) {
        const params = matchTemplate(source, templates[locale]);
        if (params) return Object.freeze({kind: 'bilingual', zh: interpolate(templates.zh, params), en: interpolate(templates.en, params)});
      }
    }
    if (hasKey(source)) return message(source);
    if (reverse.has(source)) return message(reverse.get(source));
    return Object.freeze({ kind: 'server', source, language: responseLanguage });
  }

  function t(source, params = {}) {
    if (source && typeof source === 'object') return render(source);
    const key = String(source ?? '');
    if (language === 'en' && /\p{Script=Han}/u.test(key) && !hasKey(key)) missing.add(key);
    const template = language === 'en' && hasKey(key) ? english[key] : key;
    // A callback keeps $, braces, Unicode and markup in parameter values literal.
    // Values are inserted once, never recursively translated or interpreted as HTML.
    return template.replace(/\{([a-zA-Z][a-zA-Z0-9_]*)\}/g, (match, name) =>
      Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match);
  }

  function render(value) {
    if (!value || typeof value !== 'object') return t(value);
    if (value.kind === 'raw') return value.value;
    if (value.kind === 'bilingual') return language === 'en' ? value.en : value.zh;
    if (value.kind === 'message') return t(value.source, value.params);
    if (value.kind === 'concat') return value.parts.map(render).join('');
    if (value.kind === 'server') {
      const sameLanguage = language === value.language;
      return value.source + (sameLanguage ? '' : '\n' + t(note));
    }
    return '';
  }

  function bind(target, property, value) {
    let properties = bindings.get(target);
    if (!properties) bindings.set(target, properties = new Map());
    properties.set(property, value);
    write(target, property, value);
  }

  function write(target, property, value) {
    const result = render(value);
    if (property === 'textContent') target.textContent = result;
    else if (property === 'nodeValue') target.nodeValue = result;
    else target.setAttribute(property, result);
  }

  function setText(element, value) {
    // Discard static text-node bindings superseded by this dynamic message.
    for (const node of element.childNodes) bindings.delete(node);
    bind(element, 'textContent', value);
  }

  function setAttribute(element, attribute, value) {
    if (!['placeholder', 'aria-label', 'title'].includes(attribute)) throw new TypeError('Unsupported localized attribute');
    bind(element, attribute, value);
  }

  function mount(root = document) {
    root.querySelectorAll('[data-i18n]').forEach((element) => {
      // Marked mixed-content elements translate only their own text nodes, keeping
      // child controls, icons, code, listeners and their identities intact.
      for (const node of element.childNodes) {
        if (node.nodeType !== Node.TEXT_NODE || !node.nodeValue.trim()) continue;
        const original = node.nodeValue;
        const source = element.dataset.i18n || original.trim();
        const leading = original.match(/^\s*/)[0];
        const trailing = original.match(/\s*$/)[0];
        bind(node, 'nodeValue', concat(raw(leading), message(source), raw(trailing)));
      }
    });
    for (const attribute of ['placeholder', 'aria-label', 'title']) {
      root.querySelectorAll('[data-i18n-' + attribute + ']').forEach((element) => {
        const source = element.getAttribute('data-i18n-' + attribute) || element.getAttribute(attribute);
        setAttribute(element, attribute, source);
      });
    }
    document.querySelectorAll('[data-locale]').forEach((button) => {
      button.addEventListener('click', () => setLanguage(button.dataset.locale));
    });
    updateDocument();
  }

  function updateDocument() {
    document.documentElement.lang = language;
    document.title = 'WeSwitch';
    document.querySelectorAll('[data-locale]').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.locale === language));
    });
  }

  function setLanguage(next) {
    if (!['zh-CN', 'en'].includes(next) || next === language) return;
    language = next;
    try { window.localStorage.setItem(storageKey, language); } catch (_) { /* Keep the in-memory preference. */ }
    for (const [target, properties] of bindings) {
      if (!target.isConnected) { bindings.delete(target); continue; }
      for (const [property, value] of properties) write(target, property, value);
    }
    updateDocument();
    // Notification only: the application intentionally has no locale-change
    // listener that fetches data, clears credentials or invalidates a preview.
    window.dispatchEvent(new CustomEvent('weswitch:languagechange', { detail: { language } }));
  }

  window.WeSwitchI18n = Object.freeze({
    t, message, concat, raw, server, setText, setAttribute, mount, setLanguage,
    get language() { return language; },
    get missingKeys() { return [...missing]; },
    hasTranslation: hasKey
  });
  document.documentElement.lang = language;
})();
