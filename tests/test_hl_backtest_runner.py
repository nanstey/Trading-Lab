from trading_lab.runner.hl_backtest import _instantiate_strategy


def test_instantiate_strategy_injects_coin_when_config_supports_it() -> None:
    strategy = _instantiate_strategy(
        strategy_module="trading_lab.strategies.hl_funding_carry",
        strategy_class="FundingCarryStrategy",
        strategy_config_class="FundingCarryConfig",
        params={},
        bar_type=None,
        instrument_id=None,
        coin="BTC",
    )
    assert strategy._cfg.coin == "BTC"
