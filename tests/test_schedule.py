from collections import Counter

import pytest

from rmc.runner.schedule import generate_schedule, get_block, load_schedule, save_schedule


def test_same_seed_same_schedule(cfg):
    a, b = generate_schedule(cfg, 42), generate_schedule(cfg, 42)
    assert a["blocks"] == b["blocks"]
    assert generate_schedule(cfg, 43)["blocks"] != a["blocks"]


def test_block_structure_balances_conditions_and_providers(cfg):
    s = generate_schedule(cfg, 42)
    blocks = s["blocks"]
    assert len(blocks) == 9
    assert Counter(b["condition"] for b in blocks) == {"natural": 3, "mild": 3, "severe": 3}
    for cond in ("mild", "severe"):
        assert sorted(b["degraded_provider"] for b in blocks if b["condition"] == cond) == sorted(cfg["providers"])
    assert all(b["degraded_provider"] is None for b in blocks if b["condition"] == "natural")
    assert [b["block_id"] for b in blocks] == [f"B{i:02d}" for i in range(1, 10)]


def test_rounds_per_condition_and_warmups(cfg):
    s = generate_schedule(cfg, 1)
    n_warm = cfg["experiment"]["warmup_rounds_per_block"]
    for cond in ("natural", "mild", "severe"):
        measured = sum(sum(not r["is_warmup"] for r in b["rounds"]) for b in s["blocks"] if b["condition"] == cond)
        assert measured == cfg["experiment"]["rounds_per_condition"]
    for b in s["blocks"]:
        assert [r["is_warmup"] for r in b["rounds"][:n_warm]] == [True] * n_warm
        assert not any(r["is_warmup"] for r in b["rounds"][n_warm:])
        assert [r["round_id"] for r in b["rounds"]] == list(range(1, len(b["rounds"]) + 1))


def test_every_round_runs_every_strategy_in_random_order(cfg):
    s = generate_schedule(cfg, 7)
    orders = [tuple(r["strategy_order"]) for b in s["blocks"] for r in b["rounds"]]
    assert all(sorted(o) == sorted(cfg["experiment"]["strategies"]) for o in orders)
    assert len(set(orders)) > 10
    first = Counter(o[0] for o in orders)
    assert set(first) == set(cfg["experiment"]["strategies"])


def test_block_order_is_randomised(cfg):
    orders = {tuple((b["condition"], b["degraded_provider"]) for b in generate_schedule(cfg, s)["blocks"])
              for s in range(5)}
    assert len(orders) == 5


def test_rejects_unbalanced_round_count(cfg):
    cfg["experiment"]["rounds_per_condition"] = 8
    with pytest.raises(ValueError):
        generate_schedule(cfg, 1)


def test_records_seed_and_config_hash_and_is_write_once(cfg, tmp_path):
    s = generate_schedule(cfg, 99, code_version="abc")
    assert s["seed"] == 99 and s["code_version"] == "abc" and len(s["config_hash"]) == 12
    path = tmp_path / "schedule.json"
    save_schedule(s, path)
    assert load_schedule(path) == s
    with pytest.raises(FileExistsError):
        save_schedule(s, path)
    assert get_block(s, "B03")["block_id"] == "B03"
    with pytest.raises(KeyError):
        get_block(s, "B99")
