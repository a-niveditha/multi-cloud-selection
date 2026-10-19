from rmc.config import config_hash, provider_names, validate_config


def test_shipped_config_is_valid(cfg):
    assert validate_config(cfg) == []


def test_three_providers(cfg):
    assert provider_names(cfg) == ["aws_s3", "gcs", "b2"]


def test_bad_weights_are_reported(cfg):
    cfg["weights"]["profiles"]["normal"]["cost"] = 0.9
    assert any("weights.profiles.normal" in p for p in validate_config(cfg))


def test_config_hash_changes_with_content(cfg):
    h = config_hash(cfg)
    cfg["transfer"]["timeout_s"] += 1
    assert config_hash(cfg) != h
