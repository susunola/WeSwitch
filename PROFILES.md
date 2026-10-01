# 多模型方案依据（v0.5.0）

核对来源：openai/codex 标签 `rust-v0.159.2`，与本机打包 CLI 版本一致。

1. `codex-rs/model-provider-info/src/lib.rs`
   - `ModelProviderInfo` 字段：name / base_url / model_catalog_url / env_key /
     env_key_instructions / experimental_bearer_token / auth / gateway_oauth /
     aws / wire_api / query_params / http_headers / env_http_headers /
     request_max_retries / stream_max_retries / stream_idle_timeout_ms /
     websocket_connect_timeout_ms / requires_openai_auth / supports_websockets /
     supports_standalone_web_search。
   - **不存在** per-provider 的模型列表字段。
   - `#[schemars(deny_unknown_fields)]`：自定义字段会被拒绝，不能塞私有字段。
   - `wire_api` 只接受 `"responses"`，`"chat"` 已移除。

2. `codex-rs/config/src/config_toml.rs`
   - 根级 `model` / `model_provider` / `profiles: HashMap<String, ConfigProfile>` /
     `profile`（选用哪个）/ `model_catalog_json`。
   - `profiles` 注释：便于切换不同配置。

3. `codex-rs/config/src/profile_toml.rs`
   - `ConfigProfile` 含 `model` / `model_provider` / `model_reasoning_effort` /
     `model_reasoning_summary` / `model_verbosity` / `model_catalog_json` 等。
   - 同样 `deny_unknown_fields`。

结论：同一网关、同一凭据只需一个 provider 条目；多个模型保存为多个命名
profile，每个 profile 只写 model / model_provider / model_reasoning_effort 三个键，
其余设置不动。切换方式以 Codex 实际支持为准（配置 `profile`、命令行等），
本工具不声称桌面下拉一定会显示这些模型。
