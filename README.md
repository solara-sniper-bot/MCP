<!-- mcp-name: co.solsniperbot/solana-snipe-bot-mcp -->
# Solana Sniper Bot MCP

![Solana Sniper Bot MCP](https://solsniperbot.co/mcp/mcp-software-box.png)

**Autonomous Solana trading bot with 281 MCP tools for AI-assisted control.**

Four trading modes. One Windows desktop app. Full MCP integration for Claude, Cursor, Devin, and any MCP-compatible AI assistant.

## IMPORTANT: Requires the Solana Sniper Bot Windows Application

This MCP server is **useless on its own**. It requires the **Solana Sniper Bot V4** Windows GUI application to be installed and running.

**Download the Windows executable from [solsniperbot.co](https://solsniperbot.co/download.html).**

## What It Does

The Solana Sniper Bot watches the Solana blockchain for trading opportunities across four modes:

1. **Meme / New-Token Sniping** — Pump.fun launch detection with safety/liquidity/market-cap/volume/holder/age/creator-holding filters, take-profit ladders, trailing stops, rug detection, volume-death detection, and momentum-reversal detection.

2. **Spot Trading** — Jupiter DEX aggregator integration with market/limit orders, DCA, portfolio rebalancing, risk-level presets, and slippage control.

3. **Perpetual Futures** — Long/short with leverage, margin trading, funding-rate controls, liquidation avoidance, market discovery, and signal generation.

4. **Mirror Mode** — Whale-wallet copy trading with proportional sizing, configurable limits, token filtering, and dry-run support.

Using the 281 MCP tools, your AI agent can start/stop bots, execute trades, configure safety filters, query positions and P&L, manage wallets, analyze market data, and fine-tune strategy across all four trading modes.

## Pricing

**This MCP server is 100% free.**

The Solana Sniper Bot V4 Windows application (required to use this MCP server) has the following pricing:

- **7-day free trial** (168 hours of actual bot running time)
- **First month: $49.99** (50% off with promo code `SOLV4FIRST50`)
- **$99.99/month** thereafter
- **Cancel anytime**

Subscribe at: [https://buy.stripe.com/dRm14ngiB5MBfIK6QM7bW07](https://buy.stripe.com/dRm14ngiB5MBfIK6QM7bW07?prefilled_promo_code=SOLV4FIRST50)

## Tools (281)

### Bot Control
`start_bot`, `stop_bot`, `sell_all`, `sell_position`, `buy_mint`, `get_bot_stats`

### RPC Pool
`get_rpc_pool_status`, `set_rpc_pool_key`, `set_rpc_pool_skip_until` — No API key values exposed.

### Configuration
`get_config`, `update_config`, `update_config_batch`, `get_config_catalog`, `validate_current_config`, `preview_strategy_profile`, `apply_strategy_profile`, `save_settings`

### Positions
`get_open_positions`, `get_positions_status`, `get_graduation_status`, `audit_open_positions`, `get_position_exit_quote`, `close_position`, `get_bot_status`, `get_pending_commands`

### Trade History & P&L
`get_trade_history`, `analyze_trade_performance`, `clear_trade_history`, `get_pnl_tally`, `reset_pnl_tally`, `refresh_pnl`, `get_fee_summary`, `clear_commands`

### Wallet
`get_wallet_balances`, `transfer_sol`, `move_all_sol`

### Pulse Trends
`get_pulse_results`, `set_pulse_match_mode`, `clear_pulse_results`, `get_pulse_price_history`, `get_pulse_purchase_preview`, `buy_pulse_asset`

### Trading Mode
`set_trading_mode`

### Log & Commands
`get_log`, `clear_log`

### ATA Rent
`close_empty_token_accounts`

### Meme Bot — Safety & Filters
`set_meme_max_buy_amount`, `set_meme_min_buy_amount`, `set_meme_min_liquidity`, `set_meme_min_market_cap`, `set_meme_min_volume_24h`, `set_meme_min_holders`, `set_meme_max_token_age`, `set_meme_max_creator_holdings_pct`, `set_meme_max_positions`, `set_meme_position_size_pct`, `set_meme_slippage_bps`, `add_to_blacklist`

### Meme Bot — Exit Strategy
`set_meme_stop_loss_pct`, `set_meme_take_profit_pct`, `set_meme_tp_ladder_steps`, `set_meme_trailing_stop`, `set_meme_rug_detection`, `set_meme_volume_death_detection`, `set_meme_momentum_reversal`, `set_meme_position_timeout`, `enable_meme_stop_loss`, `enable_meme_take_profit_ladder`, `enable_meme_trailing_stop`

### Meme Bot — Signals & Auto-Buy
`enable_meme_auto_buy`, `enable_meme_signals`, `clear_meme_signals`

### Meme Bot — Savings & Alerts
`set_meme_auto_savings`, `set_meme_savings_threshold`, `set_meme_alert_threshold`, `set_meme_exposure`

### Meme Bot — Analytics
`get_meme_statistics`, `get_meme_win_rate`, `get_meme_total_pnl`, `get_meme_best_worst_trades`, `get_meme_portfolio_concentration`, `get_meme_market_stats`, `get_meme_pulse_summary`, `get_meme_risk_status`, `get_meme_signal_detail`, `get_meme_position_detail`, `get_meme_position_pnl`, `get_meme_position_timeout`, `get_meme_trade_detail`, `get_meme_pending_graduations`, `get_meme_graduated_tokens`

### Meme Bot — Tokens
`get_trending_meme_tokens`, `search_meme_tokens`, `get_meme_alerts`, `add_to_whitelist`, `remove_from_whitelist`, `add_to_blacklist`, `remove_from_blacklist`, `get_blacklist`, `get_whitelist`

### Meme Bot — Risk Levels
`apply_risk_level`, `preview_risk_level`

### Spot Trading — Orders
`spot_market_buy`, `spot_market_sell`, `spot_limit_buy`, `cancel_spot_order`, `close_spot_position`

### Spot Trading — Configuration
`get_spot_config`, `update_spot_config`, `batch_update_spot_config`, `get_spot_config_field`, `validate_spot_config_values`, `get_spot_config_catalog`

### Spot Trading — Risk & DCA
`set_spot_risk_level`, `set_spot_max_exposure`, `set_spot_max_positions`, `set_spot_slippage`, `set_spot_dca_interval`, `set_spot_dca_steps`, `enable_spot_dca`, `enable_spot_rebalancing`, `set_spot_rebalance_threshold`, `set_spot_alert_threshold`

### Spot Trading — Positions & P&L
`get_spot_positions`, `get_spot_position_detail`, `get_spot_position_pnl`, `get_spot_trade_history`, `get_spot_trade_detail`, `get_spot_statistics`, `get_spot_win_rate`, `get_spot_total_pnl`, `get_spot_best_worst_trades`, `get_spot_portfolio_concentration`, `get_spot_trade_pnl_tally`, `reset_spot_pnl_tally`, `clear_spot_trade_history`

### Spot Trading — Markets & Quotes
`get_spot_markets`, `get_spot_market_detail`, `get_spot_market_stats`, `search_spot_tokens`, `get_trending_spot_tokens`, `get_spot_quote`, `get_spot_swap_transaction`, `refresh_spot_markets`

### Spot Trading — Signals
`get_spot_signals`, `get_spot_signal_detail`, `clear_spot_signals`, `enable_spot_signals`, `get_spot_risk_status`, `get_spot_risk_levels`, `get_spot_alerts`

### Spot Trading — Wallet & Tokens
`get_spot_wallet_balances`, `get_spot_token_balance`, `transfer_spot_to_savings`, `add_allowed_spot_token`, `remove_allowed_spot_token`

### Spot Trading — Bot Control
`start_spot_bot`, `stop_spot_bot`, `sell_all_spot`, `get_spot_bot_status`, `get_spot_logs`, `clear_spot_logs`

### Perpetual Futures — Positions
`open_perp_long`, `open_perp_short`, `close_perp_position`, `sell_all_perps`, `get_perp_positions`, `get_perp_position_detail`, `get_perp_position_pnl`

### Perpetual Futures — Configuration
`get_perp_config`, `update_perp_config`, `update_perp_config_batch`, `get_perp_config_catalog`, `save_perp_settings`, `validate_perp_config_tool`, `set_perp_auto_execute`, `set_perp_dry_run`

### Perpetual Futures — Leverage & Margin
`set_perp_leverage`, `set_perp_max_leverage`, `set_perp_position_size`, `set_perp_max_exposure`, `set_perp_margin_mode`, `get_perp_margin_info`, `get_perp_margin_ratio`, `get_perp_liquidation_price`

### Perpetual Futures — Risk & Exits
`set_perp_stop_loss`, `set_perp_take_profit_ladder`, `set_perp_trailing_stop`, `set_perp_funding_exit`, `set_perp_liquidation_floor`, `set_perp_position_timeout`, `set_perp_direction`, `set_perp_auto_savings`, `get_perp_exposure`, `get_perp_risk_status`

### Perpetual Futures — Market Filters
`set_perp_market_filters`, `set_perp_allowed_tokens`, `set_perp_blocked_tokens`, `set_perp_signal_mode`, `enable_perp_signal`, `disable_perp_signal`

### Perpetual Futures — Orders
`set_perp_order_type`, `set_perp_slippage`, `set_perp_poll_intervals`, `set_perp_testnet`, `cancel_all_perp_orders`, `cancel_perp_order`, `modify_perp_order`, `get_perp_open_orders`, `get_perp_order_history`

### Perpetual Futures — Markets & Data
`get_perp_markets`, `get_top_perp_markets`, `search_perp_markets`, `get_perp_market_detail`, `get_perp_market_stats`, `get_perp_candles`, `get_perp_orderbook`, `get_perp_price_changes`

### Perpetual Futures — P&L & Analytics
`get_perp_pnl_tally`, `reset_perp_pnl_tally`, `get_perp_trade_history`, `get_perp_trade_detail`, `clear_perp_trade_history`, `get_perp_fee_summary`, `refresh_perp_pnl`, `analyze_perp_performance`, `get_perp_funding_cost`, `get_funding_rates`, `get_funding_history`, `get_liquidations`, `get_perp_correlation_matrix`, `get_perp_var_estimate`

### Perpetual Futures — Signals
`get_perp_signals`, `get_perp_signal_detail`, `get_perp_signal_summary`, `get_perp_signal_history`, `clear_perp_signal_history`

### Perpetual Futures — Bot Control
`start_perp_bot`, `stop_perp_bot`, `get_perp_bot_status`, `get_perp_log`, `clear_perp_log`, `get_perp_wallet_balances`, `transfer_perp_sol`

### Perpetual Futures — Risk Levels
`apply_perp_risk_level`, `preview_perp_risk_level`

### Mirror Mode — Whale Management
`add_mirror_whale`, `remove_mirror_whale`, `toggle_mirror_whale`, `get_mirror_whales`, `get_mirror_whale_stats`, `get_all_mirror_whale_stats`

### Mirror Mode — Configuration
`get_mirror_config`, `update_mirror_config_field`, `set_mirror_mode`, `set_mirror_allocation_pct`, `set_mirror_copy_sells`, `set_mirror_dry_run`, `set_mirror_max_trade_usd`, `set_mirror_stop_loss_pct`, `set_mirror_take_profit_pct`

### Mirror Mode — Positions & Trades
`get_mirror_trades`, `clear_mirror_trade_history`, `sell_all_mirror_positions`, `get_mirror_bot_status`, `start_mirror_bot`, `stop_mirror_bot`

### Technical Indicators
`get_all_indicators`, `get_rsi`, `get_macd`, `get_moving_averages`, `get_bollinger_bands`, `get_atr`, `get_volume_analysis`, `get_token_candles`

### Market Data
`get_token_market_data`, `get_token_price`, `get_token_prices`, `get_sol_price`, `get_signal_summary`

## Installation

```bash
pip install solana-snipe-bot-mcp
```

## Client Configuration

```json
{
  "mcpServers": {
    "solana-snipe-bot": {
      "command": "python",
      "args": ["-m", "solana_snipe_bot_mcp"],
      "env": {
        "SOLANA_SNIPER_BOT_DIR": "/path/to/snipe-bot"
      }
    }
  }
}
```

## Links

- **GitHub:** [https://github.com/solara-sniper-bot/MCP](https://github.com/solara-sniper-bot/MCP)
- **Website:** [https://solsniperbot.co/](https://solsniperbot.co/)
- **Download:** [https://solsniperbot.co/download.html](https://solsniperbot.co/download.html)
- **MCP Landing Page:** [https://solsniperbot.co/mcp/](https://solsniperbot.co/mcp/)
- **PyPI:** [https://pypi.org/project/solana-snipe-bot-mcp/](https://pypi.org/project/solana-snipe-bot-mcp/)
- **MCP Registry:** `co.solsniperbot/solana-snipe-bot-mcp`
- **Smithery:** [https://smithery.ai/servers/mcleer-michael/solana-snipe-bot-mcp](https://smithery.ai/servers/mcleer-michael/solana-snipe-bot-mcp)
- **Subscribe (app):** [https://buy.stripe.com/dRm14ngiB5MBfIK6QM7bW07](https://buy.stripe.com/dRm14ngiB5MBfIK6QM7bW07?prefilled_promo_code=SOLV4FIRST50)

## License

MCP server: MIT (free). The Solana Sniper Bot V4 Windows application is proprietary — membership required for live trading after the 7-day trial. Use at your own risk. On-chain trading is risky.
