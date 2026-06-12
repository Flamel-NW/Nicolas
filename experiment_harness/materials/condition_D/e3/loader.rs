// @nico-module: config.loader
// @nico-intent: 通过窄模块边界提供配置键值读取能力，集中承载 reads_config effect，避免其他模块隐式读取配置来源。config.loader 是整个场景中唯一的 reads_config 边界模块。
// @nico-imports:
// @nico-module-effects: reads_config
// @nico-type: ConfigKey | pub | opaque
// @nico-type: ConfigValue | pub | opaque
// @nico-fn: new_config_key | pub fn new_config_key(raw: String) -> ConfigKey | effects= | calls=
// @nico-fn: load | pub fn load(key: ConfigKey) -> Option | effects=reads_config | calls=

/// Opaque configuration key (internal representation: String).
#[allow(dead_code)]
pub struct ConfigKey(String);

/// Opaque configuration value (internal representation: String).
#[allow(dead_code)]
pub struct ConfigValue(String);

/// Constructs a `ConfigKey` from a raw string. Pure constructor, no effects.
pub fn new_config_key(raw: String) -> ConfigKey {
    ConfigKey(raw)
}

/// Reads a configuration value for the given key.
///
/// Returns `Some(ConfigValue)` if the key exists, or `None` if not found.
pub fn load(_key: ConfigKey) -> Option<ConfigValue> {
    todo!("config.loader::load: real implementation pending")
}
