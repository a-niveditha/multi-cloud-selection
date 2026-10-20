from rmc.netem.netem import MockNetem
from rmc.providers.mock import MockProvider


def test_mock_netem_degrades_and_clears(cfg):
    mocks = MockProvider.from_config(cfg)
    n = MockNetem(mocks)
    assert not n.has_rules()
    n.apply("gcs", 1.0, 5.0)
    assert n.has_rules() and mocks["gcs"].rtt_ms == 2 * mocks["gcs"].base.rtt_ms
    assert mocks["gcs"].loss_pct == mocks["gcs"].base.loss_pct + 5.0
    n.clear()
    assert not n.has_rules()
    assert n.log == [("apply", "gcs", 1.0, 5.0), ("clear",)]
