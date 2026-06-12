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
