"""Solana Sniper Bot MCP Server.

Exposes every control, setting, and data view from the Solana Sniper Bot as MCP
tools. The project directory is read from the SOLANA_SNIPER_BOT_DIR environment
variable (default is the current working directory).
"""
import json
import os
import time
from datetime import datetime, timezone
from typing import Any
from mcp.server.fastmcp import FastMCP

from .config_schema import (
    PROFILE_NAME,
    analyze_trade_history,
    apply_researched_profile,
    build_risk_level_profile,
    config_catalog,
    parse_config_value,
    risk_level_name,
    validate_config,
)
from .position_audit import (
    audit_open_positions as run_position_audit,
    audit_token_account_rent,
    get_exit_quote,
    get_wallet_summary,
)

PROJECT_DIR = os.environ.get("SOLANA_SNIPER_BOT_DIR", os.getcwd())
COMMAND_FILE = os.path.join(PROJECT_DIR, "command_queue.json")
GUI_COMMAND_FILE = os.path.join(PROJECT_DIR, "gui_command_queue.json")
GUI_STATUS_FILE = os.path.join(PROJECT_DIR, "gui_control_status.json")

WALLET_IDS = ("funding", "meme", "savings", "perpetuals", "spot")
WALLET_TRANSFER_DIRECTIONS = tuple(
    f"{source}_to_{destination}"
    for source in WALLET_IDS
    for destination in WALLET_IDS
    if source != destination
)
LEGACY_TRANSFER_DIRECTIONS = (
    "to_savings", "to_trading", "to_perpetuals", "to_spot",
    "trading_to_savings", "savings_to_trading",
    "trading_to_perpetuals", "trading_to_spot",
)

mcp = FastMCP("solana-snipe-bot")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_json(path, data):
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, path)


def _read_env(path):
    entries = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key, value = stripped.split("=", 1)
                    entries[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return entries


def _set_env_value(path, key, value):
    lines = []
    replaced = False
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = ["# Local secrets. Never commit this file."]
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, path)


def _rpc_pool_status():
    entries = _read_env(os.path.join(PROJECT_DIR, "secrets.env"))
    slots = []
    configured_count = 0
    for slot in range(1, 6):
        prefix = f"HELIUS_{slot:02d}"
        configured = bool(entries.get(f"{prefix}_KEY", "").strip())
        configured_count += int(configured)
        slots.append({
            "slot": slot,
            "label": f"Helius {slot:02d}",
            "configured": configured,
            "skip_until": entries.get(f"{prefix}_SKIP_UNTIL") or None,
        })
    return {"configured_count": configured_count, "minimum_required": 1, "slots": slots}


def _enqueue_command(cmd: str, **params):
    """Write a command to the command queue file. The running bot polls this."""
    queue = _read_json(COMMAND_FILE) or []
    queue.append({
        "cmd": cmd,
        "params": params,
        "timestamp": time.time(),
    })
    queue = queue[-50:]
    _write_json(COMMAND_FILE, queue)


def _enqueue_gui_command(cmd: str, **params):
    """Queue a command for the GUI controller, even when the bot is stopped."""
    queue = _read_json(GUI_COMMAND_FILE) or []
    queue.append({
        "cmd": cmd,
        "params": params,
        "timestamp": time.time(),
    })
    _write_json(GUI_COMMAND_FILE, queue[-50:])


def _pulse_purchase_context(mint: str) -> dict:
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    asset = next((row for row in (_read_json(os.path.join(PROJECT_DIR, "pulse_data.json")) or [])
                  if row.get("mint") == mint), {})
    status = _read_json(os.path.join(PROJECT_DIR, "positions_status.json")) or {}
    sol_price = float(status.get("sol_price", 0) or status.get("sol_price_usd", 0) or 0)
    if cfg.get("dry_run", True):
        practice_state = _read_json(
            os.path.join(PROJECT_DIR, "practice_state.json")
        ) or {}
        balance_sol = float(
            practice_state.get(
                "cash_balance_sol", cfg["simulated_balance_sol"]
            ) or 0
        )
        reserve_sol = 0.0
    else:
        wallet_summary = get_wallet_summary(PROJECT_DIR)
        wallet = wallet_summary.get(
            "meme_wallet", wallet_summary.get("trading_wallet", {}))
        balance_sol = float(wallet.get("sol", 0) or 0)
        reserve_sol = float(cfg.get("min_wallet_reserve_sol", 0) or 0)
        if sol_price <= 0 and balance_sol > 0:
            sol_price = float(wallet.get("usd", 0) or 0) / balance_sol
    available_sol = max(0.0, balance_sol - reserve_sol)
    return {
        "asset": asset,
        "mint": mint,
        "dry_run": bool(cfg.get("dry_run", True)),
        "balance_sol": balance_sol,
        "reserved_sol": reserve_sol,
        "available_sol": available_sol,
        "sol_price_usd": sol_price,
        "available_usd": available_sol * sol_price,
    }


@mcp.tool()
def start_bot() -> str:
    """Start the snipe bot (equivalent to clicking 'Start Bot' in the GUI)."""
    _enqueue_gui_command("start_bot")
    return "GUI command queued: start_bot. The controller will start the bot even while its engine is stopped."


@mcp.tool()
def stop_bot() -> str:
    """Stop the snipe bot (equivalent to clicking 'Stop Bot' in the GUI)."""
    # The running engine consumes COMMAND_FILE; the always-on GUI controller
    # consumes GUI_COMMAND_FILE. Queue both so either launch style can stop.
    _enqueue_command("stop_bot")
    _enqueue_gui_command("stop_bot")
    return "Stop queued for both the bot engine and GUI controller."


@mcp.tool()
def set_trading_mode(mode: str) -> str:
    """Switch between practice and live trading, matching the GUI mode button."""
    normalized = mode.strip().lower()
    if normalized not in {"practice", "live"}:
        return "Mode not changed: mode must be practice or live."
    cfg_path = os.path.join(PROJECT_DIR, "config.json")
    cfg = _read_json(cfg_path) or {}
    cfg["dry_run"] = normalized == "practice"
    _write_json(cfg_path, cfg)
    _enqueue_command("set_trading_mode", dry_run=cfg["dry_run"])
    _enqueue_gui_command("reload_config")
    return json.dumps({"updated": True, "mode": normalized, "dry_run": cfg["dry_run"]}, indent=2)


@mcp.tool()
def sell_all() -> str:
    """Panic sell — liquidate ALL open positions immediately."""
    _enqueue_command("sell_all")
    return "Command queued: sell_all. All open positions will be liquidated."


@mcp.tool()
def sell_position(mint: str) -> str:
    """Sell a single position by mint address immediately."""
    _enqueue_command("sell_position", mint=mint)
    return f"Command queued: sell_position for {mint[:12]}..."


@mcp.tool()
def buy_mint(mint: str) -> str:
    """Buy a specific token by mint address using configured automatic sizing."""
    _enqueue_command("buy_mint", mint=mint)
    return f"Command queued: buy_mint for {mint[:12]}..."


@mcp.tool()
def get_pulse_purchase_preview(mint: str) -> str:
    """Return the Pulse asset data and SOL/USD funds available for a purchase."""
    return json.dumps(_pulse_purchase_context(mint), indent=2)


@mcp.tool()
def buy_pulse_asset(mint: str, amount: float, amount_type: str = "sol") -> str:
    """Purchase a Pulse asset by SOL quantity, USD value, or available-funds percentage.

    amount_type must be sol, usd, or percentage. This queues the purchase immediately
    without another confirmation, matching the Pulse-tab purchase dialog.
    """
    context = _pulse_purchase_context(mint)
    kind = amount_type.strip().lower()
    value = float(amount)
    if value <= 0:
        return "Purchase not queued: amount must be greater than zero."
    if kind == "sol":
        buy_sol = value
    elif kind == "usd":
        if context["sol_price_usd"] <= 0:
            return "Purchase not queued: SOL/USD price is unavailable."
        buy_sol = value / context["sol_price_usd"]
    elif kind in {"percentage", "percent", "pct"}:
        if value > 100:
            return "Purchase not queued: percentage must be between 0 and 100."
        buy_sol = context["available_sol"] * value / 100
    else:
        return "Purchase not queued: amount_type must be sol, usd, or percentage."
    if buy_sol > context["available_sol"]:
        return "Purchase not queued: requested amount exceeds available funds."
    buy_lamports = int(buy_sol * 1e9)
    if buy_lamports <= 0:
        return "Purchase not queued: calculated purchase amount is below one lamport."
    _enqueue_command("buy_mint", mint=mint, buy_lamports=buy_lamports)
    return json.dumps({
        "queued": True,
        "mint": mint,
        "buy_sol": buy_lamports / 1e9,
        "buy_usd": (buy_lamports / 1e9) * context["sol_price_usd"],
        "amount_type": kind,
        "dry_run": context["dry_run"],
    }, indent=2)


@mcp.tool()
def get_config() -> str:
    """Get the current bot configuration as JSON."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json"))
    if cfg is None:
        return "No config file found."
    return json.dumps(cfg, indent=2)


@mcp.tool()
def get_rpc_pool_status() -> str:
    """List configured RPC pool slots without exposing API key values."""
    return json.dumps(_rpc_pool_status(), indent=2)


@mcp.tool()
def set_rpc_pool_key(slot: int, api_key: str) -> str:
    """Set one Helius RPC pool key in the private local secrets.env file."""
    if slot < 1 or slot > 5:
        return "RPC pool key not changed: slot must be between 1 and 5."
    if not api_key.strip():
        return "RPC pool key not changed: api_key cannot be empty."
    key = f"HELIUS_{slot:02d}_KEY"
    _set_env_value(os.path.join(PROJECT_DIR, "secrets.env"), key, api_key.strip())
    return json.dumps({
        "updated": True,
        "slot": slot,
        "label": f"Helius {slot:02d}",
        "configured": True,
        "restart_required": True,
    }, indent=2)


@mcp.tool()
def set_rpc_pool_skip_until(slot: int, skip_date: str) -> str:
    """Set or clear the skip-until date for an RPC pool slot.

    Pass an empty string to clear the skip date and re-activate the slot.
    The slot will be omitted from the round-robin pool until the specified
    date (YYYY-MM-DD), then automatically rejoin.
    """
    if slot < 1 or slot > 5:
        return "Skip date not changed: slot must be between 1 and 5."
    key = f"HELIUS_{slot:02d}_SKIP_UNTIL"
    if skip_date.strip():
        try:
            datetime.strptime(skip_date.strip(), "%Y-%m-%d")
        except ValueError:
            return "Skip date not changed: must be YYYY-MM-DD format."
        _set_env_value(os.path.join(PROJECT_DIR, "secrets.env"), key, skip_date.strip())
    else:
        # Clear by setting empty value (load_rpc_pool treats missing/empty as no skip)
        _set_env_value(os.path.join(PROJECT_DIR, "secrets.env"), key, "")
    return json.dumps({
        "updated": True,
        "slot": slot,
        "label": f"Helius {slot:02d}",
        "skip_until": skip_date.strip() or None,
        "restart_required": True,
    }, indent=2)


@mcp.tool()
def update_config(key: str, value: str) -> str:
    """Update a single config key. The bot must be restarted for some settings."""
    cfg_path = os.path.join(PROJECT_DIR, "config.json")
    cfg = _read_json(cfg_path) or {}
    try:
        parsed = parse_config_value(key, value)
    except (TypeError, ValueError) as exc:
        return f"Config not changed: {exc}"
    cfg[key] = parsed
    validation = validate_config(cfg)
    if validation["errors"]:
        return "Config not changed: " + "; ".join(validation["errors"])
    _write_json(cfg_path, cfg)
    _enqueue_gui_command("reload_config")
    return f"Config updated: {key} = {cfg.get(key)}"


@mcp.tool()
def get_config_catalog() -> str:
    """List every supported variable, type, range, description, and value."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    return json.dumps(config_catalog(cfg), indent=2)


@mcp.tool()
def validate_current_config() -> str:
    """Validate all current settings and return errors and risk warnings."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    return json.dumps(validate_config(cfg), indent=2)


@mcp.tool()
def preview_strategy_profile(live_trading: bool = False) -> str:
    """Preview the guarded small-account profile without changing files."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    proposed = apply_researched_profile(cfg, live_trading=live_trading)
    changes = {
        key: {"current": cfg.get(key), "proposed": value}
        for key, value in proposed.items() if cfg.get(key) != value
    }
    return json.dumps({
        "profile": PROFILE_NAME,
        "live_trading": live_trading,
        "change_count": len(changes),
        "changes": changes,
        "validation": validate_config(proposed),
    }, indent=2)


@mcp.tool()
def apply_strategy_profile(confirm: bool = False,
                           live_trading: bool = False) -> str:
    """Apply the guarded profile atomically; requires confirm=true."""
    if not confirm:
        return "No change made. Call again with confirm=true after previewing the profile."
    cfg_path = os.path.join(PROJECT_DIR, "config.json")
    cfg = _read_json(cfg_path) or {}
    proposed = apply_researched_profile(cfg, live_trading=live_trading)
    _write_json(cfg_path, proposed)
    _enqueue_gui_command("reload_config")
    return json.dumps({
        "applied": True,
        "profile": PROFILE_NAME,
        "live_trading": live_trading,
        "dry_run": proposed["dry_run"],
        "validation": validate_config(proposed),
    }, indent=2)


@mcp.tool()
def preview_risk_level(level: int) -> str:
    """Preview one of the 20 unified risk levels without changing files."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    try:
        proposed = build_risk_level_profile(cfg, level)
    except (TypeError, ValueError) as exc:
        return f"Risk profile not previewed: {exc}"
    changes = {
        key: {"current": cfg.get(key), "proposed": value}
        for key, value in proposed.items() if cfg.get(key) != value
    }
    return json.dumps({
        "risk_level": int(level),
        "name": risk_level_name(int(level)),
        "change_count": len(changes),
        "changes": changes,
        "validation": validate_config(proposed),
    }, indent=2)


@mcp.tool()
def apply_risk_level(level: int, confirm: bool = False) -> str:
    """Atomically apply a 1-20 unified risk level; requires confirm=true."""
    if not confirm:
        return "No change made. Preview the level, then call again with confirm=true."
    cfg_path = os.path.join(PROJECT_DIR, "config.json")
    cfg = _read_json(cfg_path) or {}
    try:
        proposed = build_risk_level_profile(cfg, level)
    except (TypeError, ValueError) as exc:
        return f"Risk profile not applied: {exc}"
    _write_json(cfg_path, proposed)
    _enqueue_gui_command("reload_config")
    return json.dumps({
        "applied": True,
        "risk_level": int(level),
        "name": risk_level_name(int(level)),
        "validation": validate_config(proposed),
    }, indent=2)


@mcp.tool()
def update_config_batch(settings_json: str, confirm: bool = False) -> str:
    """Validate and atomically update multiple settings from a JSON object."""
    if not confirm:
        return "No change made. Call again with confirm=true."
    try:
        updates = json.loads(settings_json)
        if not isinstance(updates, dict):
            raise ValueError("settings_json must contain a JSON object")
        parsed = {key: parse_config_value(key, value) for key, value in updates.items()}
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return f"Config not changed: {exc}"
    cfg_path = os.path.join(PROJECT_DIR, "config.json")
    cfg = _read_json(cfg_path) or {}
    proposed = dict(cfg)
    proposed.update(parsed)
    validation = validate_config(proposed)
    if validation["errors"]:
        return "Config not changed: " + "; ".join(validation["errors"])
    _write_json(cfg_path, proposed)
    _enqueue_gui_command("reload_config")
    return json.dumps({"updated": parsed, "validation": validation}, indent=2)


@mcp.tool()
def analyze_trade_performance() -> str:
    """Analyze closed-trade performance and exit-reason distribution."""
    trades = _read_json(os.path.join(PROJECT_DIR, "trade_history.json")) or []
    return json.dumps(analyze_trade_history(trades), indent=2)


@mcp.tool()
def audit_open_positions() -> str:
    """Compare saved positions to wallet balances and live exit routes (read-only)."""
    try:
        return json.dumps(run_position_audit(PROJECT_DIR), indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc), "read_only": True}, indent=2)


@mcp.tool()
def get_position_exit_quote(mint: str = "") -> str:
    """Get a read-only Jupiter exit quote for a persisted open position."""
    positions = _read_json(os.path.join(PROJECT_DIR, "open_positions.json")) or []
    active = [position for position in positions if int(position.get("token_amount", 0)) > 0]
    if not mint and len(active) == 1:
        mint = active[0].get("mint", "")
    position = next((item for item in active if item.get("mint") == mint), None)
    if not position:
        return json.dumps({"error": "Open position not found", "mint": mint}, indent=2)
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    try:
        result = get_exit_quote(
            mint, int(position["token_amount"]), int(cfg.get("slippage_bps", 250)))
        result["mint"] = mint
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc), "mint": mint, "read_only": True}, indent=2)


@mcp.tool()
def save_settings() -> str:
    """Save current settings to config.json."""
    _enqueue_command("save_settings")
    return "Command queued: save_settings."


@mcp.tool()
def get_open_positions() -> str:
    """Get all currently open (unsold) positions as JSON."""
    positions = _read_json(os.path.join(PROJECT_DIR, "open_positions.json"))
    if positions is None:
        return "No open positions file found."
    return json.dumps(positions, indent=2)


@mcp.tool()
def get_positions_status() -> str:
    """Get real-time position status (P&L, portfolio value, SOL price)."""
    status = _read_json(os.path.join(PROJECT_DIR, "positions_status.json"))
    if status is None:
        return "No positions status file found. Bot may not be running."
    return json.dumps(status, indent=2)


@mcp.tool()
def get_graduation_status(mint: str = "") -> str:
    """Check graduation status for all open positions or a specific mint."""
    positions = _read_json(os.path.join(PROJECT_DIR, "open_positions.json")) or []
    if not positions:
        return "No open positions."
    results = []
    for pos in positions:
        if mint and pos.get("mint", "") != mint:
            continue
        results.append({
            "mint": pos.get("mint", ""),
            "name": pos.get("name", ""),
            "symbol": pos.get("symbol", ""),
            "bonding_curve": pos.get("bonding_curve", False),
            "graduated": pos.get("graduated", False),
            "graduation_time": pos.get("graduation_time", 0),
            "token_amount": pos.get("token_amount", 0),
            "buy_sol_amount": pos.get("buy_sol_amount", 0),
        })
    return json.dumps({"positions": results}, indent=2)


@mcp.tool()
def get_bot_status() -> str:
    """Get overall bot status."""
    status = _read_json(os.path.join(PROJECT_DIR, "positions_status.json")) or {}
    positions = _read_json(os.path.join(PROJECT_DIR, "open_positions.json")) or []
    summary = {
        "open_positions": len(positions),
        "sol_price_usd": status.get("sol_price_usd", 0),
        "total_invested_sol": status.get("total_invested_sol", 0),
        "total_current_sol": status.get("total_current_sol", 0),
        "total_pnl_sol": status.get("total_pnl_sol", 0),
        "total_pnl_usd": status.get("total_pnl_usd", 0),
        "active_count": status.get("active_count", 0),
        "wallet_balance_sol": status.get("wallet_balance_sol", 0),
        "gui_controller": _read_json(GUI_STATUS_FILE) or {},
    }
    return json.dumps(summary, indent=2)


@mcp.tool()
def get_trade_history(limit: int = 20) -> str:
    """Get recent trade history records."""
    history = _read_json(os.path.join(PROJECT_DIR, "trade_history.json")) or []
    return json.dumps(history[-limit:], indent=2)


@mcp.tool()
def clear_trade_history() -> str:
    """Clear all trade history."""
    _write_json(os.path.join(PROJECT_DIR, "trade_history.json"), [])
    return "Trade history cleared."


@mcp.tool()
def get_pnl_tally() -> str:
    """Get the persistent all-time P&L tally."""
    tally = _read_json(os.path.join(PROJECT_DIR, "pnl_tally.json"))
    if tally is None:
        return "No P&L tally file found."
    return json.dumps(tally, indent=2)


@mcp.tool()
def reset_pnl_tally() -> str:
    """Reset the all-time P&L tally to zero."""
    history = _read_json(os.path.join(PROJECT_DIR, "trade_history.json")) or []
    if not isinstance(history, list):
        history = []
    _write_json(os.path.join(PROJECT_DIR, "pnl_tally.json"), {
        "pnl_usd": 0.0,
        "pnl_sol": 0.0,
        "realized_trade_pnl_usd": 0.0,
        "unaccounted_pnl_usd": 0.0,
        "reset_after": datetime.now(timezone.utc).isoformat(),
        "reset_trade_count": len(history),
        "accounting_method": "realized_trade_results_only",
    })
    return "All-time P&L reset to zero; deposits and starting balance are excluded."


@mcp.tool()
def get_wallet_balances() -> str:
    """Get Funding, Meme-Coin, Savings, Perpetuals, and Spot wallet balances."""
    try:
        return json.dumps(get_wallet_summary(PROJECT_DIR), indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc), "read_only": True}, indent=2)


@mcp.tool()
def transfer_sol(direction: str, amount_sol: float) -> str:
    """Transfer SOL over any directed route among the five application wallets."""
    if direction not in WALLET_TRANSFER_DIRECTIONS + LEGACY_TRANSFER_DIRECTIONS:
        return "Invalid direction. Use source_to_destination with Funding, Meme, Savings, Perpetuals, or Spot."
    _enqueue_command("transfer", direction=direction, amount_sol=amount_sol)
    return f"Command queued: transfer {amount_sol} SOL {direction}."


@mcp.tool()
def move_all_sol(direction: str) -> str:
    """Move all transferable SOL over any route among the five application wallets."""
    if direction not in WALLET_TRANSFER_DIRECTIONS + LEGACY_TRANSFER_DIRECTIONS:
        return "Invalid direction. Use source_to_destination with Funding, Meme, Savings, Perpetuals, or Spot."
    _enqueue_command("transfer_all", direction=direction)
    return f"Command queued: move ALL SOL {direction}."


@mcp.tool()
def get_pulse_results() -> str:
    """Get all Pulse tab results."""
    results = _read_json(os.path.join(PROJECT_DIR, "pulse_data.json"))
    if results is None:
        return "No pulse data found."
    return json.dumps(results, indent=2)


@mcp.tool()
def set_pulse_match_mode(mode: str) -> str:
    """Set the Pulse match mode."""
    if mode not in ("exact", "word", "fuzzy"):
        return "Invalid mode."
    _enqueue_command("set_pulse_mode", mode=mode)
    return f"Command queued: set pulse match mode to {mode}."


@mcp.tool()
def clear_pulse_results() -> str:
    """Clear all Pulse results."""
    _write_json(os.path.join(PROJECT_DIR, "pulse_data.json"), [])
    return "Pulse results cleared."


@mcp.tool()
def get_log(tail_lines: int = 50) -> str:
    """Get the last N lines of the bot log."""
    log_path = os.path.join(PROJECT_DIR, "snipe_bot.log")
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-tail_lines:])
    except FileNotFoundError:
        return "No log file found."


@mcp.tool()
def clear_log() -> str:
    """Clear the bot log file."""
    log_path = os.path.join(PROJECT_DIR, "snipe_bot.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("")
    _enqueue_command("clear_log")
    return "Log cleared."


@mcp.tool()
def get_pending_commands() -> str:
    """Get pending commands from the command queue."""
    queue = _read_json(COMMAND_FILE)
    if queue is None:
        return "No command queue file."
    return json.dumps(queue, indent=2)


@mcp.tool()
def clear_commands() -> str:
    """Clear the command queue."""
    _write_json(COMMAND_FILE, [])
    return "Command queue cleared."


@mcp.tool()
def get_fee_summary() -> str:
    """Get fee + ATA rent breakdown."""
    tally = _read_json(os.path.join(PROJECT_DIR, "pnl_tally.json")) or {}
    summary = {
        "total_fees_paid_sol": tally.get("total_fees_paid_sol", 0),
        "total_fees_paid_usd": tally.get("total_fees_paid_usd", 0),
    }
    try:
        summary.update(audit_token_account_rent(PROJECT_DIR))
    except Exception as exc:
        summary["rent_audit_error"] = str(exc)
    return json.dumps(summary, indent=2)


@mcp.tool()
def close_empty_token_accounts() -> str:
    """Close all empty token accounts to reclaim ATA rent."""
    _enqueue_command("close_empty_token_accounts")
    return "Command queued: close_empty_token_accounts."


@mcp.tool()
def refresh_pnl() -> str:
    """Force a P&L recompute."""
    _enqueue_command("refresh_pnl")
    return "Command queued: refresh_pnl."


@mcp.tool()
def close_position(mint: str) -> str:
    """Manually close a position without executing an on-chain sell."""
    if not mint:
        return "Error: mint required."
    positions_path = os.path.join(PROJECT_DIR, "open_positions.json")
    positions = _read_json(positions_path) or []
    for pos in positions:
        if pos.get("mint", "") == mint:
            pos["sold"] = True
            pos["manual_review"] = True
            _write_json(positions_path, positions)
            return f"Position {mint[:12]}... closed manually."
    return f"Error: Position {mint} not found."


@mcp.tool()
def get_pulse_price_history(mint: str) -> str:
    """Get price tracking for a specific mint."""
    if not mint:
        return "Error: mint required."
    results = _read_json(os.path.join(PROJECT_DIR, "pulse_data.json")) or []
    for r in results:
        if r.get("mint", "") == mint:
            return json.dumps({
                "mint": mint,
                "name": r.get("name", ""),
                "symbol": r.get("symbol", ""),
                "price_at_match_sol": r.get("price_at_match_sol", 0),
                "price_at_match_usd": r.get("price_at_match_usd", 0),
                "current_price_sol": r.get("current_price_sol", 0),
                "current_price_usd": r.get("current_price_usd", 0),
                "price_change_pct": r.get("price_change_pct", 0),
                "price_history": r.get("price_history", []),
                "last_price_update": r.get("last_price_update", 0),
            }, indent=2)
    return f"Mint {mint} not found in pulse data."


@mcp.tool()
def get_bot_stats() -> str:
    """Get bot operational statistics and per-category rejection totals."""
    log_path = os.path.join(PROJECT_DIR, "snipe_bot.log")
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return "No log file found."
    stats_line = ""
    stats_index = -1
    for index, line in enumerate(lines):
        if "Stats" in line and "=" in line:
            stats_line = line.strip()
            stats_index = index
    if not stats_line:
        return "No stats found."
    stats = {}
    for part in stats_line.split():
        if "=" in part:
            key, val = part.split("=", 1)
            try:
                stats[key] = int(val)
            except ValueError:
                stats[key] = val
    rejection_reasons = {}
    for line in lines[stats_index + 1:]:
        if "Stats" in line and "=" in line:
            break
        marker = "Rejections by reason:"
        if marker not in line:
            continue
        for part in line.split(marker, 1)[1].split():
            if "=" not in part:
                continue
            key, val = part.split("=", 1)
            try:
                rejection_reasons[key] = int(val)
            except ValueError:
                continue
    stats["rejection_reasons"] = rejection_reasons
    return json.dumps(stats, indent=2)


# ===========================================================================
# ADDITIONAL MEME TOOLS — market discovery, signals, tokens, alerts, stats
# ===========================================================================

@mcp.tool()
def get_trending_meme_tokens(limit: int = 20) -> str:
    """Get trending/new tokens from Pump.fun and other launch sources."""
    pulse_path = os.path.join(PROJECT_DIR, "pulse_results.json")
    results = _read_json(pulse_path) or []
    # Sort by discovery time (most recent first)
    results.sort(key=lambda x: x.get("discovered_at", 0), reverse=True)
    trending = results[:limit]
    return json.dumps({"tokens": trending, "count": len(trending)}, indent=2)


@mcp.tool()
def search_meme_tokens(query: str, limit: int = 20) -> str:
    """Search for meme tokens by name, symbol, or mint address in Pulse results."""
    pulse_path = os.path.join(PROJECT_DIR, "pulse_results.json")
    results = _read_json(pulse_path) or []
    query_lower = query.lower()
    matched = []
    for r in results:
        symbol = str(r.get("symbol", "")).lower()
        name = str(r.get("name", "")).lower()
        mint = str(r.get("mint", "")).lower()
        if query_lower in symbol or query_lower in name or query_lower in mint:
            matched.append(r)
    return json.dumps({"results": matched[:limit], "count": len(matched)}, indent=2)


@mcp.tool()
def get_meme_market_stats() -> str:
    """Return aggregate statistics across all discovered meme tokens."""
    pulse_path = os.path.join(PROJECT_DIR, "pulse_results.json")
    results = _read_json(pulse_path) or []
    total = len(results)
    safe_count = sum(1 for r in results if r.get("safety_state") == "safe")
    unsafe_count = sum(1 for r in results if r.get("safety_state") == "unsafe")
    unknown_count = total - safe_count - unsafe_count
    avg_buy_signal = 0
    if total:
        scores = [float(r.get("buy_signal_score", 0)) for r in results]
        avg_buy_signal = sum(scores) / len(scores)
    return json.dumps({
        "total_discovered": total,
        "safe": safe_count,
        "unsafe": unsafe_count,
        "unknown": unknown_count,
        "avg_buy_signal_score": round(avg_buy_signal, 1),
    }, indent=2)


@mcp.tool()
def get_meme_signal_detail(index: int) -> str:
    """Return details for a specific Pulse signal by index (most recent first)."""
    pulse_path = os.path.join(PROJECT_DIR, "pulse_results.json")
    results = _read_json(pulse_path) or []
    if index < 0 or index >= len(results):
        return json.dumps({"error": "Invalid index"})
    return json.dumps(results[-(index + 1)], indent=2)


@mcp.tool()
def enable_meme_signals(enabled: bool) -> str:
    """Enable or disable automatic signal-based buying for the meme bot."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["auto_buy_enabled"] = bool(enabled)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", auto_buy_enabled=bool(enabled))
    return json.dumps({"auto_buy_enabled": cfg["auto_buy_enabled"]})


@mcp.tool()
def clear_meme_signals() -> str:
    """Clear all stored Pulse signal results."""
    _write_json(os.path.join(PROJECT_DIR, "pulse_results.json"), [])
    return "Pulse signals cleared."


@mcp.tool()
def get_meme_alerts() -> str:
    """Return recent high-quality meme trading alerts from Pulse results."""
    pulse_path = os.path.join(PROJECT_DIR, "pulse_results.json")
    results = _read_json(pulse_path) or []
    alerts = [r for r in results if float(r.get("buy_signal_score", 0)) >= 70]
    return json.dumps({"alerts": alerts[-50:], "count": len(alerts)}, indent=2)


@mcp.tool()
def set_meme_alert_threshold(score: float) -> str:
    """Set the minimum Pulse buy-signal score to trigger an alert."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["pulse_alert_threshold"] = float(score)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    return json.dumps({"pulse_alert_threshold": cfg["pulse_alert_threshold"]})


@mcp.tool()
def get_meme_win_rate() -> str:
    """Return the win rate for meme bot trading."""
    tally = _read_json(os.path.join(PROJECT_DIR, "pnl_tally.json")) or {}
    total = tally.get("total_trades", 0)
    wins = tally.get("wins", 0)
    rate = (wins / total * 100) if total > 0 else 0
    return json.dumps({
        "total_trades": total,
        "wins": wins,
        "losses": tally.get("losses", 0),
        "win_rate_pct": round(rate, 1),
    }, indent=2)


@mcp.tool()
def get_meme_best_worst_trades() -> str:
    """Return the best and worst meme trades."""
    tally = _read_json(os.path.join(PROJECT_DIR, "pnl_tally.json")) or {}
    return json.dumps({
        "best_trade_usd": tally.get("best_trade_usd", 0),
        "worst_trade_usd": tally.get("worst_trade_usd", 0),
    }, indent=2)


@mcp.tool()
def get_meme_risk_status() -> str:
    """Return current risk metrics for meme bot trading."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    positions = _read_json(os.path.join(PROJECT_DIR, "positions.json")) or []
    open_count = len([p for p in positions if not p.get("sold")])
    max_positions = int(cfg.get("max_positions", 10))
    return json.dumps({
        "open_positions": open_count,
        "max_positions": max_positions,
        "risk_level": cfg.get("risk_level", 5),
        "dry_run": cfg.get("dry_run", True),
        "auto_buy_enabled": cfg.get("auto_buy_enabled", False),
        "stop_loss_enabled": cfg.get("stop_loss_enabled", True),
        "trailing_stop_enabled": cfg.get("trailing_stop_enabled", True),
    }, indent=2)


@mcp.tool()
def get_meme_portfolio_concentration() -> str:
    """Return portfolio concentration analysis for meme positions."""
    positions = _read_json(os.path.join(PROJECT_DIR, "positions.json")) or []
    open_positions = [p for p in positions if not p.get("sold")]
    if not open_positions:
        return json.dumps({"error": "No open positions"})
    total_cost = sum(float(p.get("cost_usd", 0) or p.get("buy_cost", 0)) for p in open_positions)
    concentrations = []
    for p in open_positions:
        cost = float(p.get("cost_usd", 0) or p.get("buy_cost", 0))
        pct = (cost / total_cost * 100) if total_cost > 0 else 0
        concentrations.append({
            "mint": p.get("mint", ""),
            "symbol": p.get("symbol", ""),
            "cost_usd": round(cost, 2),
            "concentration_pct": round(pct, 1),
        })
    concentrations.sort(key=lambda x: x["concentration_pct"], reverse=True)
    return json.dumps({"total_cost_usd": round(total_cost, 2), "concentrations": concentrations}, indent=2)


@mcp.tool()
def get_meme_statistics() -> str:
    """Return comprehensive meme trading statistics."""
    tally = _read_json(os.path.join(PROJECT_DIR, "pnl_tally.json")) or {}
    positions = _read_json(os.path.join(PROJECT_DIR, "positions.json")) or []
    pulse = _read_json(os.path.join(PROJECT_DIR, "pulse_results.json")) or []
    history = _read_json(os.path.join(PROJECT_DIR, "trade_history.json")) or []
    open_positions = [p for p in positions if not p.get("sold")]
    return json.dumps({
        "total_realized_pnl_usd": tally.get("total_realized_pnl_usd", 0),
        "total_trades": tally.get("total_trades", 0),
        "wins": tally.get("wins", 0),
        "losses": tally.get("losses", 0),
        "best_trade_usd": tally.get("best_trade_usd", 0),
        "worst_trade_usd": tally.get("worst_trade_usd", 0),
        "open_positions": len(open_positions),
        "total_pulse_discovered": len(pulse),
        "total_trade_history": len(history),
    }, indent=2)


@mcp.tool()
def add_to_blacklist(mint: str) -> str:
    """Add a token mint to the blacklist (never buy this token)."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    blacklist = cfg.get("blacklist_mints", [])
    if isinstance(blacklist, str):
        blacklist = [m.strip() for m in blacklist.split(",") if m.strip()]
    if mint not in blacklist:
        blacklist.append(mint)
    cfg["blacklist_mints"] = blacklist
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    return json.dumps({"blacklist": blacklist, "added": mint})


@mcp.tool()
def remove_from_blacklist(mint: str) -> str:
    """Remove a token mint from the blacklist."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    blacklist = cfg.get("blacklist_mints", [])
    if isinstance(blacklist, str):
        blacklist = [m.strip() for m in blacklist.split(",") if m.strip()]
    blacklist = [m for m in blacklist if m != mint]
    cfg["blacklist_mints"] = blacklist
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    return json.dumps({"blacklist": blacklist, "removed": mint})


@mcp.tool()
def get_blacklist() -> str:
    """Return the current blacklist of token mints."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    blacklist = cfg.get("blacklist_mints", [])
    if isinstance(blacklist, str):
        blacklist = [m.strip() for m in blacklist.split(",") if m.strip()]
    return json.dumps({"blacklist": blacklist, "count": len(blacklist)}, indent=2)


@mcp.tool()
def add_to_whitelist(mint: str) -> str:
    """Add a token mint to the whitelist (only buy these tokens)."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    whitelist = cfg.get("whitelist_mints", [])
    if isinstance(whitelist, str):
        whitelist = [m.strip() for m in whitelist.split(",") if m.strip()]
    if mint not in whitelist:
        whitelist.append(mint)
    cfg["whitelist_mints"] = whitelist
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    return json.dumps({"whitelist": whitelist, "added": mint})


@mcp.tool()
def remove_from_whitelist(mint: str) -> str:
    """Remove a token mint from the whitelist."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    whitelist = cfg.get("whitelist_mints", [])
    if isinstance(whitelist, str):
        whitelist = [m.strip() for m in whitelist.split(",") if m.strip()]
    whitelist = [m for m in whitelist if m != mint]
    cfg["whitelist_mints"] = whitelist
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    return json.dumps({"whitelist": whitelist, "removed": mint})


@mcp.tool()
def get_whitelist() -> str:
    """Return the current whitelist of token mints."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    whitelist = cfg.get("whitelist_mints", [])
    if isinstance(whitelist, str):
        whitelist = [m.strip() for m in whitelist.split(",") if m.strip()]
    return json.dumps({"whitelist": whitelist, "count": len(whitelist)}, indent=2)


@mcp.tool()
def set_meme_max_positions(max_positions: int) -> str:
    """Set the maximum number of simultaneous meme positions."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["max_positions"] = int(max_positions)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", max_positions=int(max_positions))
    return json.dumps({"max_positions": cfg["max_positions"]})


@mcp.tool()
def set_meme_stop_loss_pct(pct: float) -> str:
    """Set the hard stop loss percentage for meme positions."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["hard_stop_loss_pct"] = float(pct)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", hard_stop_loss_pct=float(pct))
    return json.dumps({"hard_stop_loss_pct": cfg["hard_stop_loss_pct"]})


@mcp.tool()
def set_meme_take_profit_pct(pct: float) -> str:
    """Set the take profit percentage for meme positions."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["take_profit_pct"] = float(pct)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", take_profit_pct=float(pct))
    return json.dumps({"take_profit_pct": cfg["take_profit_pct"]})


@mcp.tool()
def set_meme_trailing_stop(pct: float) -> str:
    """Set the trailing stop percentage for meme positions."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["trailing_stop_pct"] = float(pct)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", trailing_stop_pct=float(pct))
    return json.dumps({"trailing_stop_pct": cfg["trailing_stop_pct"]})


@mcp.tool()
def set_meme_position_size_pct(pct: float) -> str:
    """Set the percentage of wallet to use per meme trade."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["position_size_pct"] = float(pct)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", position_size_pct=float(pct))
    return json.dumps({"position_size_pct": cfg["position_size_pct"]})


@mcp.tool()
def set_meme_slippage_bps(bps: int) -> str:
    """Set the maximum slippage in basis points for meme swaps."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["slippage_bps"] = int(bps)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", slippage_bps=int(bps))
    return json.dumps({"slippage_bps": cfg["slippage_bps"]})


@mcp.tool()
def set_meme_min_liquidity(usd: float) -> str:
    """Set the minimum liquidity (USD) required for a meme token to be considered."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["min_liquidity_usd"] = float(usd)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", min_liquidity_usd=float(usd))
    return json.dumps({"min_liquidity_usd": cfg["min_liquidity_usd"]})


@mcp.tool()
def set_meme_min_market_cap(usd: float) -> str:
    """Set the minimum market cap (USD) for a meme token to be considered."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["min_market_cap_usd"] = float(usd)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", min_market_cap_usd=float(usd))
    return json.dumps({"min_market_cap_usd": cfg["min_market_cap_usd"]})


@mcp.tool()
def set_meme_min_volume_24h(usd: float) -> str:
    """Set the minimum 24h volume (USD) for a meme token to be considered."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["min_volume_24h_usd"] = float(usd)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", min_volume_24h_usd=float(usd))
    return json.dumps({"min_volume_24h_usd": cfg["min_volume_24h_usd"]})


@mcp.tool()
def set_meme_max_token_age(minutes: int) -> str:
    """Set the maximum token age (minutes) for meme sniping (only buy new tokens)."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["max_token_age_minutes"] = int(minutes)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", max_token_age_minutes=int(minutes))
    return json.dumps({"max_token_age_minutes": cfg["max_token_age_minutes"]})


@mcp.tool()
def set_meme_min_holders(count: int) -> str:
    """Set the minimum number of token holders required for a meme token."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["min_holder_count"] = int(count)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", min_holder_count=int(count))
    return json.dumps({"min_holder_count": cfg["min_holder_count"]})


@mcp.tool()
def set_meme_max_creator_holdings_pct(pct: float) -> str:
    """Set the max percentage of supply the creator/minter can hold (rug pull protection)."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["max_creator_holdings_pct"] = float(pct)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", max_creator_holdings_pct=float(pct))
    return json.dumps({"max_creator_holdings_pct": cfg["max_creator_holdings_pct"]})


@mcp.tool()
def get_meme_pulse_summary() -> str:
    """Return a summary of recent Pulse discovery results including top opportunities."""
    pulse_path = os.path.join(PROJECT_DIR, "pulse_results.json")
    results = _read_json(pulse_path) or []
    total = len(results)
    if not total:
        return json.dumps({"total": 0, "message": "No Pulse results yet"})
    top_signals = sorted(results, key=lambda x: float(x.get("buy_signal_score", 0)), reverse=True)[:10]
    safe_count = sum(1 for r in results if r.get("safety_state") == "safe")
    return json.dumps({
        "total_discovered": total,
        "safe_tokens": safe_count,
        "unsafe_tokens": total - safe_count,
        "top_opportunities": [{"mint": r.get("mint", ""), "symbol": r.get("symbol", ""),
                               "buy_signal_score": r.get("buy_signal_score", 0),
                               "safety_state": r.get("safety_state", "unknown")}
                              for r in top_signals],
    }, indent=2)


@mcp.tool()
def get_meme_position_detail(mint: str) -> str:
    """Return details for a specific meme position by token mint."""
    positions = _read_json(os.path.join(PROJECT_DIR, "positions.json")) or []
    for p in positions:
        if p.get("mint") == mint:
            return json.dumps(p, indent=2)
    return json.dumps({"error": f"Position not found for mint: {mint}"})


@mcp.tool()
def get_meme_position_pnl(mint: str) -> str:
    """Return P&L for a specific meme position."""
    positions = _read_json(os.path.join(PROJECT_DIR, "positions.json")) or []
    for p in positions:
        if p.get("mint") == mint:
            return json.dumps({
                "mint": mint,
                "realized_pnl": p.get("realized_pnl", 0),
                "unrealized_pnl": p.get("unrealized_pnl", 0),
                "buy_price": p.get("buy_price", 0),
                "current_price": p.get("current_price", 0),
            }, indent=2)
    return json.dumps({"error": f"Position not found for mint: {mint}"})


@mcp.tool()
def get_meme_exposure() -> str:
    """Return total exposure across all open meme positions."""
    positions = _read_json(os.path.join(PROJECT_DIR, "positions.json")) or []
    open_positions = [p for p in positions if not p.get("sold")]
    total_cost = sum(float(p.get("cost_usd", 0) or p.get("buy_cost", 0)) for p in open_positions)
    total_unrealized = sum(float(p.get("unrealized_pnl", 0)) for p in open_positions)
    return json.dumps({
        "open_positions": len(open_positions),
        "total_cost_usd": round(total_cost, 2),
        "total_unrealized_pnl_usd": round(total_unrealized, 2),
    }, indent=2)


@mcp.tool()
def get_meme_total_pnl() -> str:
    """Return total realized and unrealized P&L for meme trading."""
    tally = _read_json(os.path.join(PROJECT_DIR, "pnl_tally.json")) or {}
    positions = _read_json(os.path.join(PROJECT_DIR, "positions.json")) or []
    unrealized = sum(float(p.get("unrealized_pnl", 0)) for p in positions if not p.get("sold"))
    return json.dumps({
        "total_realized_pnl_usd": tally.get("total_realized_pnl_usd", 0),
        "total_unrealized_pnl_usd": round(unrealized, 2),
        "total_trades": tally.get("total_trades", 0),
        "wins": tally.get("wins", 0),
        "losses": tally.get("losses", 0),
    }, indent=2)


@mcp.tool()
def enable_meme_auto_buy(enabled: bool) -> str:
    """Enable or disable automatic buying when signals are detected."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["auto_buy_enabled"] = bool(enabled)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", auto_buy_enabled=bool(enabled))
    return json.dumps({"auto_buy_enabled": cfg["auto_buy_enabled"]})


@mcp.tool()
def enable_meme_stop_loss(enabled: bool) -> str:
    """Enable or disable stop loss protection for meme positions."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["stop_loss_enabled"] = bool(enabled)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", stop_loss_enabled=bool(enabled))
    return json.dumps({"stop_loss_enabled": cfg["stop_loss_enabled"]})


@mcp.tool()
def enable_meme_trailing_stop(enabled: bool) -> str:
    """Enable or disable trailing stop for meme positions."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["trailing_stop_enabled"] = bool(enabled)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", trailing_stop_enabled=bool(enabled))
    return json.dumps({"trailing_stop_enabled": cfg["trailing_stop_enabled"]})


@mcp.tool()
def enable_meme_take_profit_ladder(enabled: bool) -> str:
    """Enable or disable take-profit ladder for meme positions."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["tp_ladder_enabled"] = bool(enabled)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", tp_ladder_enabled=bool(enabled))
    return json.dumps({"tp_ladder_enabled": cfg["tp_ladder_enabled"]})


@mcp.tool()
def set_meme_tp_ladder_steps(steps: str) -> str:
    """Set the take-profit ladder steps (format: gain%:close_pct,gain%:close_pct)."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["tp_ladder_steps"] = steps
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", tp_ladder_steps=steps)
    return json.dumps({"tp_ladder_steps": cfg["tp_ladder_steps"]})


@mcp.tool()
def set_meme_min_buy_amount(usd: float) -> str:
    """Set the minimum buy amount in USD for meme trades."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["min_buy_usd"] = float(usd)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", min_buy_usd=float(usd))
    return json.dumps({"min_buy_usd": cfg["min_buy_usd"]})


@mcp.tool()
def set_meme_max_buy_amount(usd: float) -> str:
    """Set the maximum buy amount in USD for meme trades."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["max_buy_usd"] = float(usd)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", max_buy_usd=float(usd))
    return json.dumps({"max_buy_usd": cfg["max_buy_usd"]})


@mcp.tool()
def get_meme_graduated_tokens() -> str:
    """Return tokens that have graduated from Pump.fun to Raydium."""
    positions = _read_json(os.path.join(PROJECT_DIR, "positions.json")) or []
    graduated = [p for p in positions if p.get("graduated", False) and not p.get("sold")]
    return json.dumps({"graduated_tokens": graduated, "count": len(graduated)}, indent=2)


@mcp.tool()
def get_meme_pending_graduations() -> str:
    """Return tokens that are pending graduation from Pump.fun."""
    positions = _read_json(os.path.join(PROJECT_DIR, "positions.json")) or []
    pending = [p for p in positions if not p.get("graduated", False) and not p.get("sold")]
    return json.dumps({"pending_graduations": pending, "count": len(pending)}, indent=2)


@mcp.tool()
def set_meme_rug_detection(enabled: bool) -> str:
    """Enable or disable rug detection for meme positions."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["rug_detection_enabled"] = bool(enabled)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", rug_detection_enabled=bool(enabled))
    return json.dumps({"rug_detection_enabled": cfg["rug_detection_enabled"]})


@mcp.tool()
def set_meme_volume_death_detection(enabled: bool) -> str:
    """Enable or disable volume death detection (exit when volume dies)."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["volume_death_detection_enabled"] = bool(enabled)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", volume_death_detection_enabled=bool(enabled))
    return json.dumps({"volume_death_detection_enabled": cfg["volume_death_detection_enabled"]})


@mcp.tool()
def set_meme_momentum_reversal(enabled: bool) -> str:
    """Enable or disable momentum reversal exit for meme positions."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["momentum_reversal_enabled"] = bool(enabled)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", momentum_reversal_enabled=bool(enabled))
    return json.dumps({"momentum_reversal_enabled": cfg["momentum_reversal_enabled"]})


@mcp.tool()
def set_meme_auto_savings(enabled: bool) -> str:
    """Enable or disable auto savings sweep for meme bot profits."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["auto_savings_enabled"] = bool(enabled)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", auto_savings_enabled=bool(enabled))
    return json.dumps({"auto_savings_enabled": cfg["auto_savings_enabled"]})


@mcp.tool()
def set_meme_savings_threshold(usd: float) -> str:
    """Set the savings sweep threshold (USD) for the meme bot."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["savings_threshold_usd"] = float(usd)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", savings_threshold_usd=float(usd))
    return json.dumps({"savings_threshold_usd": cfg["savings_threshold_usd"]})


@mcp.tool()
def get_meme_trade_detail(index: int) -> str:
    """Return details for a specific meme trade by index (most recent first)."""
    history = _read_json(os.path.join(PROJECT_DIR, "trade_history.json")) or []
    if index < 0 or index >= len(history):
        return json.dumps({"error": "Invalid trade index"})
    return json.dumps(history[-(index + 1)], indent=2)


@mcp.tool()
def get_meme_position_timeout(timeout_sec: int) -> str:
    """Set the auto-close timeout for meme positions (0 = disabled)."""
    cfg = _read_json(os.path.join(PROJECT_DIR, "config.json")) or {}
    cfg["position_timeout_sec"] = int(timeout_sec)
    _write_json(os.path.join(PROJECT_DIR, "config.json"), cfg)
    _enqueue_command("update_config", position_timeout_sec=int(timeout_sec))
    return json.dumps({"position_timeout_sec": cfg["position_timeout_sec"]})


# ===========================================================================
# UNIFIED MCP SERVER - Perpetuals + Spot tools merged into Meme server
# ===========================================================================

# --- Perpetuals + Spot imports ---
import sys as _sys
_V4_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _V4_ROOT not in _sys.path:
    _sys.path.insert(0, _V4_ROOT)

from perp_config_schema import (
    PERP_CONFIG_RULES as _PERP_CONFIG_RULES,
    RISK_LEVEL_MAX as _PERP_RISK_LEVEL_MAX,
    RISK_LEVEL_MIN as _PERP_RISK_LEVEL_MIN,
    build_perp_risk_level_profile as _build_perp_risk_level_profile,
    parse_perp_config_value as _parse_perp_config_value,
    risk_level_name as _perp_risk_level_name,
    validate_perp_config as _validate_perp_config,
)

from spot_config_schema import (
    SPOT_CONFIG_RULES as _SPOT_CONFIG_RULES,
    RISK_LEVEL_MAX as _SPOT_RISK_LEVEL_MAX,
    RISK_LEVEL_MIN as _SPOT_RISK_LEVEL_MIN,
    build_spot_risk_level_profile as _build_spot_risk_level_profile,
    parse_spot_config_value as _parse_spot_config_value,
    risk_level_name as _spot_risk_level_name,
    validate_spot_config as _validate_spot_config,
)

PERP_CONFIG_PATH = os.path.join(PROJECT_DIR, "perp_config.json")
PERP_COMMAND_FILE = os.path.join(PROJECT_DIR, "perp_command_queue.json")
SPOT_CONFIG_PATH = os.path.join(PROJECT_DIR, "spot_config.json")
SPOT_COMMAND_FILE = os.path.join(PROJECT_DIR, "spot_command_queue.json")

# ---------------------------------------------------------------------------
# Perpetuals helpers
# ---------------------------------------------------------------------------
def _load_perp_cfg():
    defaults = {k: v.get("default") for k, v in _PERP_CONFIG_RULES.items()}
    cfg = _read_json(PERP_CONFIG_PATH) or {}
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = v
    return cfg

def _save_perp_cfg(cfg):
    _write_json(PERP_CONFIG_PATH, cfg)

def _enqueue_perp_command(cmd, **params):
    queue = _read_json(PERP_COMMAND_FILE) or []
    queue.append({"cmd": cmd, "params": params, "timestamp": time.time()})
    queue = queue[-100:]
    _write_json(PERP_COMMAND_FILE, queue)

def _set_perp_cfg_value(key, value):
    cfg = _load_perp_cfg()
    cfg[key] = _parse_perp_config_value(key, value)
    _save_perp_cfg(cfg)
    return cfg

def _update_perp_batch(updates):
    cfg = _load_perp_cfg()
    for k, v in updates.items():
        if k in _PERP_CONFIG_RULES:
            cfg[k] = _parse_perp_config_value(k, v)
    _save_perp_cfg(cfg)
    return cfg

# ---------------------------------------------------------------------------
# Spot helpers
# ---------------------------------------------------------------------------
def _load_spot_cfg():
    defaults = {k: v.get("default") for k, v in _SPOT_CONFIG_RULES.items()}
    cfg = _read_json(SPOT_CONFIG_PATH) or {}
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = v
    return cfg

def _save_spot_cfg(cfg):
    _write_json(SPOT_CONFIG_PATH, cfg)

def _enqueue_spot_command(cmd, **params):
    queue = _read_json(SPOT_COMMAND_FILE) or []
    queue.append({"action": cmd, **params, "timestamp": time.time()})
    queue = queue[-100:]
    _write_json(SPOT_COMMAND_FILE, queue)

def _set_spot_cfg_value(key, value):
    cfg = _load_spot_cfg()
    cfg[key] = _parse_spot_config_value(key, value)
    _save_spot_cfg(cfg)
    return cfg

def _update_spot_batch(updates):
    cfg = _load_spot_cfg()
    for k, v in updates.items():
        if k in _SPOT_CONFIG_RULES:
            cfg[k] = _parse_spot_config_value(k, v)
    _save_spot_cfg(cfg)
    return cfg

def _pretty(d):
    return json.dumps(d, ensure_ascii=False, indent=2)

# ===========================================================================
# PERPETUALS TOOLS
# ===========================================================================
@mcp.tool()
def start_perp_bot() -> str:
    """Start the perpetuals trading bot."""
    _enqueue_perp_command("start_perp_bot")
    return "Command queued: start_perp_bot."


@mcp.tool()
def stop_perp_bot() -> str:
    """Stop the perpetuals trading bot."""
    _enqueue_perp_command("stop_perp_bot")
    return "Command queued: stop_perp_bot."


@mcp.tool()
def sell_all_perps() -> str:
    """Panic close all open perpetual positions immediately."""
    _enqueue_perp_command("sell_all_perps")
    return "Command queued: sell_all_perps."


@mcp.tool()
def get_perp_bot_status() -> str:
    """Return the current perpetuals bot status (running, stopped, etc.)."""
    status = _read_json(os.path.join(PROJECT_DIR, "perp_bot_status.json")) or {}
    return _pretty(status)


# ---------------------------------------------------------------------------
# 2. Market data (8)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_markets() -> str:
    """Return the list of available perpetual markets."""
    markets = _read_json(os.path.join(PROJECT_DIR, "perp_markets.json")) or []
    return _pretty({"markets": markets, "count": len(markets)})


@mcp.tool()
def get_perp_market_detail(symbol: str) -> str:
    """Return detailed data for a specific perp market."""
    markets = _read_json(os.path.join(PROJECT_DIR, "perp_markets.json")) or []
    for m in markets:
        if (m.get("symbol") or "").upper() == symbol.upper():
            return _pretty(m)
    return _pretty({"error": f"Market {symbol} not found"})


@mcp.tool()
def get_perp_orderbook(symbol: str) -> str:
    """Return the latest orderbook snapshot for a perp market."""
    data = _read_json(os.path.join(PROJECT_DIR,
                                   f"perp_orderbook_{symbol.upper()}.json")) or {}
    return _pretty(data)


@mcp.tool()
def get_perp_candles(symbol: str, interval: str = "1m", limit: int = 100) -> str:
    """Return recent candle (kline) data for a perp market."""
    data = _read_json(os.path.join(PROJECT_DIR,
                                   f"perp_candles_{symbol.upper()}_{interval}.json")) or {}
    return _pretty({"symbol": symbol, "interval": interval,
                    "limit": limit, "data": data})


@mcp.tool()
def get_funding_rates() -> str:
    """Return current funding rates for all perp markets."""
    data = _read_json(os.path.join(PROJECT_DIR, "perp_funding_rates.json")) or []
    return _pretty({"count": len(data), "rates": data})


@mcp.tool()
def get_funding_history(symbol: str, limit: int = 30) -> str:
    """Return recent funding payment history for a perp market."""
    data = _read_json(os.path.join(PROJECT_DIR,
                                   f"perp_funding_history_{symbol.upper()}.json")) or []
    return _pretty({"symbol": symbol, "limit": limit, "history": data})


@mcp.tool()
def get_liquidations(limit: int = 50) -> str:
    """Return recent large liquidation events."""
    data = _read_json(os.path.join(PROJECT_DIR, "perp_liquidations.json")) or []
    return _pretty({"count": len(data), "liquidations": data[:limit]})


@mcp.tool()
def get_perp_price_changes() -> str:
    """Return multi-timeframe price changes for all perp markets."""
    data = _read_json(os.path.join(PROJECT_DIR, "perp_price_changes.json")) or []
    return _pretty({"count": len(data), "changes": data})


# ---------------------------------------------------------------------------
# 3. Configuration (6)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_config() -> str:
    """Return the current perp configuration."""
    return _pretty(_load_perp_cfg())


@mcp.tool()
def update_perp_config(key: str, value: Any) -> str:
    """Update a single perp configuration value."""
    if key not in _PERP_CONFIG_RULES:
        return _pretty({"error": f"Unknown perp config key: {key}"})
    cfg = _set_perp_cfg_value(key, value)
    warnings = _validate_perp_config(cfg)
    return _pretty({"updated": key, "value": cfg[key],
                    "warnings": warnings})


@mcp.tool()
def get_perp_config_catalog() -> str:
    """Return the catalog of all available perp config keys."""
    return _pretty({k: {"type": v["type"], "default": v.get("default"),
                        "min": v.get("min"), "max": v.get("max"),
                        "choices": v.get("choices"),
                        "description": v.get("description", "")}
                    for k, v in _PERP_CONFIG_RULES.items()})


@mcp.tool()
def validate_perp_config_tool() -> str:
    """Validate the current perp configuration and return any warnings."""
    cfg = _load_perp_cfg()
    warnings = _validate_perp_config(cfg)
    return _pretty({"valid": len(warnings) == 0, "warnings": warnings})


@mcp.tool()
def preview_perp_risk_level(level: int) -> str:
    """Preview the perp configuration for a specific risk level (1-20)."""
    try:
        level = int(level)
    except (ValueError, TypeError):
        return _pretty({"error": "level must be an integer"})
    if level < _PERP_RISK_LEVEL_MIN or level > _PERP_RISK_LEVEL_MAX:
        return _pretty({"error": f"level must be between {_PERP_RISK_LEVEL_MIN} and {_PERP_RISK_LEVEL_MAX}"})
    profile = _build_perp_risk_level_profile(level, base_cfg=_load_perp_cfg())
    return _pretty({
        "level": level,
        "name": _perp_risk_level_name(level),
        "leverage": profile.get("perp_default_leverage"),
        "max_leverage": profile.get("perp_max_leverage"),
        "margin_mode": profile.get("perp_margin_mode"),
        "pct_of_wallet": profile.get("perp_pct_of_wallet"),
        "stop_loss_pct": profile.get("perp_hard_stop_loss_pct"),
        "tp_ladder_steps": profile.get("perp_tp_ladder_steps"),
    })


@mcp.tool()
def apply_perp_risk_level(level: int) -> str:
    """Apply a risk level (1-20) to the perp configuration."""
    try:
        level = int(level)
    except (ValueError, TypeError):
        return _pretty({"error": "level must be an integer"})
    if level < _PERP_RISK_LEVEL_MIN or level > _PERP_RISK_LEVEL_MAX:
        return _pretty({"error": f"level must be between {_PERP_RISK_LEVEL_MIN} and {_PERP_RISK_LEVEL_MAX}"})
    base = _load_perp_cfg()
    profile = _build_perp_risk_level_profile(level, base_cfg=base)
    profile["perp_risk_level"] = level
    _save_perp_cfg(profile)
    _enqueue_perp_command("apply_perp_risk_level", level=level)
    return _pretty({"updated": True, "level": level,
                    "name": _perp_risk_level_name(level)})


# ---------------------------------------------------------------------------
# 4. Positions (9)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_positions() -> str:
    """Return all open perpetual positions."""
    positions = _read_json(os.path.join(PROJECT_DIR, "perp_positions.json")) or []
    return _pretty({"count": len(positions), "positions": positions})


@mcp.tool()
def open_perp_long(symbol: str, notional_usd: float) -> str:
    """Open a long perpetual position by symbol and notional size."""
    _enqueue_perp_command("open_perp_long", symbol=symbol,
                     notional_usd=float(notional_usd))
    return f"Command queued: open long {symbol} for {notional_usd} USDC."


@mcp.tool()
def open_perp_short(symbol: str, notional_usd: float) -> str:
    """Open a short perpetual position by symbol and notional size."""
    _enqueue_perp_command("open_perp_short", symbol=symbol,
                     notional_usd=float(notional_usd))
    return f"Command queued: open short {symbol} for {notional_usd} USDC."


@mcp.tool()
def close_perp_position(symbol: str, pct: float = 100.0) -> str:
    """Close (reduce) a perp position by symbol and percentage."""
    _enqueue_perp_command("close_perp_position", symbol=symbol, pct=float(pct))
    return f"Command queued: close {pct}% of {symbol}."


@mcp.tool()
def get_perp_position_detail(symbol: str) -> str:
    """Return detailed data for a specific perp position."""
    positions = _read_json(os.path.join(PROJECT_DIR, "perp_positions.json")) or []
    for p in positions:
        if (p.get("symbol") or "").upper() == symbol.upper():
            return _pretty(p)
    return _pretty({"error": f"Position {symbol} not found"})


@mcp.tool()
def get_perp_position_pnl(symbol: str) -> str:
    """Return the P&L for a specific perp position."""
    positions = _read_json(os.path.join(PROJECT_DIR, "perp_positions.json")) or []
    for p in positions:
        if (p.get("symbol") or "").upper() == symbol.upper():
            return _pretty({"symbol": symbol,
                            "unrealized_pnl": p.get("unrealized_pnl", 0),
                            "realized_pnl": p.get("realized_pnl", 0),
                            "pnl_pct": p.get("pnl_pct", 0)})
    return _pretty({"error": f"Position {symbol} not found"})


@mcp.tool()
def set_perp_leverage(symbol: str, leverage: float) -> str:
    """Set leverage for an existing perp position or as default."""
    _enqueue_perp_command("set_perp_leverage", symbol=symbol, leverage=float(leverage))
    return f"Command queued: set leverage for {symbol} to {leverage}x."


@mcp.tool()
def set_perp_margin_mode(mode: str) -> str:
    """Set perp margin mode to isolated or cross."""
    if mode not in ("isolated", "cross"):
        return _pretty({"error": "mode must be 'isolated' or 'cross'"})
    _update_perp_batch({"perp_margin_mode": mode})
    _enqueue_perp_command("set_perp_margin_mode", mode=mode)
    return _pretty({"updated": "perp_margin_mode", "mode": mode})


@mcp.tool()
def get_perp_exposure() -> str:
    """Return total perp exposure and margin usage summary."""
    status = _read_json(os.path.join(PROJECT_DIR, "perp_positions.json")) or []
    total = sum(float(p.get("notional", 0)) for p in status)
    margin = sum(float(p.get("margin", 0) or 0) for p in status)
    return _pretty({"total_notional": total, "total_margin": margin,
                    "position_count": len(status)})


# ---------------------------------------------------------------------------
# 5. Orders (4)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_open_orders() -> str:
    """Return all open perp orders."""
    orders = _read_json(os.path.join(PROJECT_DIR, "perp_open_orders.json")) or []
    return _pretty({"count": len(orders), "orders": orders})


@mcp.tool()
def cancel_perp_order(order_id: str) -> str:
    """Cancel a specific perp order by order ID."""
    _enqueue_perp_command("cancel_perp_order", order_id=order_id)
    return f"Command queued: cancel order {order_id}."


@mcp.tool()
def cancel_all_perp_orders(symbol: str = "") -> str:
    """Cancel all open perp orders, optionally filtered by symbol."""
    _enqueue_perp_command("cancel_all_perp_orders", symbol=symbol)
    return "Command queued: cancel all perp orders."


@mcp.tool()
def get_perp_order_history(limit: int = 50) -> str:
    """Return recent perp order history."""
    history = _read_json(os.path.join(PROJECT_DIR, "perp_order_history.json")) or []
    return _pretty({"count": len(history), "history": history[:limit]})


# ---------------------------------------------------------------------------
# 6. Trade history & P&L (5)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_trade_history(limit: int = 100) -> str:
    """Return perp trade history."""
    trades = _read_json(os.path.join(PROJECT_DIR, "perp_trade_history.json")) or []
    return _pretty({"count": len(trades), "trades": trades[:limit]})


@mcp.tool()
def clear_perp_trade_history() -> str:
    """Clear the perp trade history file."""
    _write_json(os.path.join(PROJECT_DIR, "perp_trade_history.json"), [])
    return "Perp trade history cleared."


@mcp.tool()
def get_perp_pnl_tally() -> str:
    """Return the all-time perp P&L tally."""
    tally = _read_json(os.path.join(PROJECT_DIR, "perp_pnl_tally.json")) or {}
    return _pretty(tally)


@mcp.tool()
def reset_perp_pnl_tally() -> str:
    """Reset the all-time perp P&L tally to zero."""
    _write_json(os.path.join(PROJECT_DIR, "perp_pnl_tally.json"),
                {"realized_pnl": 0, "unrealized_pnl": 0, "fees_paid": 0,
                 "win_count": 0, "loss_count": 0})
    return "Perp P&L tally reset."


@mcp.tool()
def analyze_perp_performance() -> str:
    """Analyze perp trade performance and return summary stats."""
    trades = _read_json(os.path.join(PROJECT_DIR, "perp_trade_history.json")) or []
    if not trades:
        return _pretty({"error": "No perp trades found"})
    wins = [t for t in trades if float(t.get("pnl", 0)) > 0]
    losses = [t for t in trades if float(t.get("pnl", 0)) <= 0]
    total_pnl = sum(float(t.get("pnl", 0)) for t in trades)
    return _pretty({
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(trades), 2) if trades else 0,
        "total_pnl": round(total_pnl, 4),
        "avg_pnl": round(total_pnl / len(trades), 4) if trades else 0,
    })


# ---------------------------------------------------------------------------
# 7. Wallet & risk (4)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_wallet_balances() -> str:
    """Return the perp trading and savings wallet balances."""
    balances = _read_json(os.path.join(PROJECT_DIR, "perp_wallet_balances.json")) or {}
    return _pretty(balances)


@mcp.tool()
def transfer_perp_sol(direction: str, amount_sol: float) -> str:
    """Transfer SOL between trading and savings wallets."""
    if direction not in ("to_savings", "to_trading"):
        return _pretty({"error": "direction must be 'to_savings' or 'to_trading'"})
    _enqueue_perp_command("transfer_perp_sol", direction=direction,
                     amount_sol=float(amount_sol))
    return f"Command queued: transfer {amount_sol} SOL {direction}."


@mcp.tool()
def get_perp_margin_ratio() -> str:
    """Return the current perp margin ratio / health."""
    data = _read_json(os.path.join(PROJECT_DIR, "perp_margin_health.json")) or {}
    return _pretty(data)


@mcp.tool()
def get_perp_liquidation_price(symbol: str) -> str:
    """Return the estimated liquidation price for a perp position."""
    positions = _read_json(os.path.join(PROJECT_DIR, "perp_positions.json")) or []
    for p in positions:
        if (p.get("symbol") or "").upper() == symbol.upper():
            return _pretty({"symbol": symbol,
                            "liquidation_price": p.get("liquidation_price", 0),
                            "entry_price": p.get("entry_price", 0),
                            "leverage": p.get("leverage", 0)})
    return _pretty({"error": f"Position {symbol} not found"})


# ---------------------------------------------------------------------------
# 8. Signals (4)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_signals() -> str:
    """Return the latest perp trading signals."""
    signals = _read_json(os.path.join(PROJECT_DIR, "perp_signals.json")) or []
    return _pretty({"count": len(signals), "signals": signals})


@mcp.tool()
def get_perp_signal_detail(signal_id: str) -> str:
    """Return details for a specific perp signal."""
    signals = _read_json(os.path.join(PROJECT_DIR, "perp_signals.json")) or []
    for s in signals:
        if str(s.get("id", "")) == str(signal_id):
            return _pretty(s)
    return _pretty({"error": f"Signal {signal_id} not found"})


@mcp.tool()
def enable_perp_signal(symbol: str) -> str:
    """Re-enable signal generation for a blocked perp market."""
    _enqueue_perp_command("enable_perp_signal", symbol=symbol)
    return f"Command queued: enable signals for {symbol}."


@mcp.tool()
def disable_perp_signal(symbol: str) -> str:
    """Disable signal generation for a perp market."""
    _enqueue_perp_command("disable_perp_signal", symbol=symbol)
    return f"Command queued: disable signals for {symbol}."


# ---------------------------------------------------------------------------
# 9. Risk (4)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_risk_status() -> str:
    """Return the current perp risk status (leverage, exposure, etc.)."""
    cfg = _load_perp_cfg()
    positions = _read_json(os.path.join(PROJECT_DIR, "perp_positions.json")) or []
    exposure = sum(float(p.get("notional", 0)) for p in positions)
    return _pretty({
        "risk_level": cfg.get("perp_risk_level"),
        "default_leverage": cfg.get("perp_default_leverage"),
        "max_leverage": cfg.get("perp_max_leverage"),
        "margin_mode": cfg.get("perp_margin_mode"),
        "max_total_exposure": cfg.get("perp_max_total_exposure_usd"),
        "current_exposure": exposure,
        "position_count": len(positions),
    })


@mcp.tool()
def set_perp_max_leverage(leverage: float) -> str:
    """Set the perp max leverage cap."""
    cfg = _set_perp_cfg_value("perp_max_leverage", leverage)
    _enqueue_perp_command("set_perp_max_leverage", leverage=float(leverage))
    return _pretty({"updated": "perp_max_leverage",
                    "value": cfg["perp_max_leverage"]})


@mcp.tool()
def set_perp_max_exposure(usd: float) -> str:
    """Set the perp max total exposure in USD."""
    cfg = _set_perp_cfg_value("perp_max_total_exposure_usd", usd)
    _enqueue_perp_command("set_perp_max_exposure", usd=float(usd))
    return _pretty({"updated": "perp_max_total_exposure_usd",
                    "value": cfg["perp_max_total_exposure_usd"]})


@mcp.tool()
def get_perp_var_estimate(confidence: float = 0.95) -> str:
    """Return a simple parametric VaR estimate for open positions."""
    positions = _read_json(os.path.join(PROJECT_DIR, "perp_positions.json")) or []
    notional = sum(float(p.get("notional", 0)) for p in positions)
    vol = 0.05  # assumed 5% per-period volatility placeholder
    import math
    z = 1.65 if confidence <= 0.95 else 2.33
    var = notional * vol * z
    return _pretty({"notional": notional, "confidence": confidence,
                    "var": round(var, 2)})


# ---------------------------------------------------------------------------
# 10. Logs (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_log(limit: int = 100) -> str:
    """Return the tail of the perp bot log."""
    log_path = os.path.join(PROJECT_DIR, "perp_bot.log")
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tail = "".join(lines[-int(limit):])
        return json.dumps({"tail": tail}, ensure_ascii=False, indent=2)
    except FileNotFoundError:
        return _pretty({"tail": ""})


@mcp.tool()
def clear_perp_log() -> str:
    """Clear the perp bot log file."""
    log_path = os.path.join(PROJECT_DIR, "perp_bot.log")
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("")
    except FileNotFoundError:
        pass
    return "Perp bot log cleared."


# ---------------------------------------------------------------------------
# 11. Batch & convenience config (7)
# ---------------------------------------------------------------------------
@mcp.tool()
def update_perp_config_batch(updates: str) -> str:
    """Update multiple perp config values at once.

    Pass a JSON object string of key-value pairs, e.g.:
    '{"perp_default_leverage": 5, "perp_dry_run": true}'
    """
    try:
        parsed = json.loads(updates)
    except json.JSONDecodeError as e:
        return _pretty({"error": f"Invalid JSON: {e}"})
    if not isinstance(parsed, dict):
        return _pretty({"error": "Expected a JSON object"})
    cfg = _update_perp_batch(parsed)
    warnings = _validate_perp_config(cfg)
    _enqueue_perp_command("update_config", **parsed)
    return _pretty({"updated_keys": list(parsed.keys()),
                    "warnings": warnings})


@mcp.tool()
def save_perp_settings() -> str:
    """Persist the current perp configuration to disk."""
    cfg = _load_perp_cfg()
    _save_perp_cfg(cfg)
    _enqueue_perp_command("save_settings")
    return _pretty({"saved": True, "keys": len(cfg)})


@mcp.tool()
def set_perp_stop_loss(pct: float) -> str:
    """Set the hard stop loss percentage for perp positions."""
    cfg = _set_perp_cfg_value("perp_hard_stop_loss_pct", pct)
    _enqueue_perp_command("update_config", perp_hard_stop_loss_pct=float(pct))
    return _pretty({"updated": "perp_hard_stop_loss_pct",
                    "value": cfg["perp_hard_stop_loss_pct"]})


@mcp.tool()
def set_perp_take_profit_ladder(steps: str) -> str:
    """Set the take-profit ladder steps string (e.g. '2%:30,5%:30,10%:20')."""
    cfg = _set_perp_cfg_value("perp_tp_ladder_steps", steps)
    _enqueue_perp_command("update_config", perp_tp_ladder_steps=steps)
    return _pretty({"updated": "perp_tp_ladder_steps",
                    "value": cfg["perp_tp_ladder_steps"]})


@mcp.tool()
def set_perp_trailing_stop(activation_pct: float, drawdown_pct: float) -> str:
    """Configure the trailing stop activation and drawdown percentages."""
    cfg = _update_perp_batch({
        "perp_trailing_stop_activation_pct": activation_pct,
        "perp_trailing_stop_drawdown_pct": drawdown_pct,
    })
    _enqueue_perp_command("update_config",
                     perp_trailing_stop_activation_pct=float(activation_pct),
                     perp_trailing_stop_drawdown_pct=float(drawdown_pct))
    return _pretty({"updated": ["perp_trailing_stop_activation_pct",
                                "perp_trailing_stop_drawdown_pct"],
                    "activation_pct": cfg["perp_trailing_stop_activation_pct"],
                    "drawdown_pct": cfg["perp_trailing_stop_drawdown_pct"]})


@mcp.tool()
def set_perp_position_size(pct_of_wallet: float, max_notional: float) -> str:
    """Set position sizing: % of wallet as margin and max notional cap."""
    cfg = _update_perp_batch({
        "perp_pct_of_wallet": pct_of_wallet,
        "perp_max_notional": max_notional,
    })
    _enqueue_perp_command("update_config",
                     perp_pct_of_wallet=float(pct_of_wallet),
                     perp_max_notional=float(max_notional))
    return _pretty({"updated": ["perp_pct_of_wallet", "perp_max_notional"],
                    "pct_of_wallet": cfg["perp_pct_of_wallet"],
                    "max_notional": cfg["perp_max_notional"]})


@mcp.tool()
def set_perp_direction(mode: str) -> str:
    """Set allowed trade directions: 'long', 'short', or 'both'."""
    if mode not in ("long", "short", "both"):
        return _pretty({"error": "mode must be 'long', 'short', or 'both'"})
    cfg = _set_perp_cfg_value("perp_allowed_directions", mode)
    _enqueue_perp_command("update_config", perp_allowed_directions=mode)
    return _pretty({"updated": "perp_allowed_directions", "mode": mode})


# ---------------------------------------------------------------------------
# 12. Order management extensions (3)
# ---------------------------------------------------------------------------
@mcp.tool()
def modify_perp_order(order_id: str, new_price: float = 0,
                      new_size: float = 0) -> str:
    """Modify an existing perp order's price and/or size."""
    _enqueue_perp_command("modify_perp_order", order_id=order_id,
                     new_price=float(new_price), new_size=float(new_size))
    return f"Command queued: modify order {order_id}."


@mcp.tool()
def set_perp_order_type(order_type: str) -> str:
    """Set the default perp order type: MARKET, LIMIT, POST_ONLY, IOC, or FOK."""
    valid = {"MARKET", "LIMIT", "POST_ONLY", "IOC", "FOK"}
    if order_type.upper() not in valid:
        return _pretty({"error": f"order_type must be one of {sorted(valid)}"})
    cfg = _set_perp_cfg_value("perp_default_order_type", order_type.upper())
    _enqueue_perp_command("update_config",
                     perp_default_order_type=order_type.upper())
    return _pretty({"updated": "perp_default_order_type",
                    "value": cfg["perp_default_order_type"]})


@mcp.tool()
def set_perp_slippage(bps: int) -> str:
    """Set the max acceptable slippage in basis points for perp orders."""
    cfg = _set_perp_cfg_value("perp_slippage_bps", bps)
    _enqueue_perp_command("update_config", perp_slippage_bps=int(bps))
    return _pretty({"updated": "perp_slippage_bps",
                    "value": cfg["perp_slippage_bps"]})


# ---------------------------------------------------------------------------
# 13. Market search & filtering (3)
# ---------------------------------------------------------------------------
@mcp.tool()
def search_perp_markets(query: str, min_volume: float = 0,
                        min_leverage: float = 0) -> str:
    """Search perp markets by symbol substring with optional volume/leverage filters."""
    markets = _read_json(os.path.join(PROJECT_DIR, "perp_markets.json")) or []
    q = query.upper()
    results = []
    for m in markets:
        sym = (m.get("symbol") or "").upper()
        if q and q not in sym:
            continue
        vol = float(m.get("24h_volume", 0) or 0)
        if min_volume > 0 and vol < min_volume:
            continue
        maxlev = float(m.get("max_leverage", 0) or 0)
        if min_leverage > 0 and maxlev < min_leverage:
            continue
        results.append(m)
    return _pretty({"query": query, "count": len(results), "markets": results})


@mcp.tool()
def get_top_perp_markets(sort_by: str = "24h_volume", limit: int = 20) -> str:
    """Return top perp markets sorted by volume, open interest, or 24h change."""
    markets = _read_json(os.path.join(PROJECT_DIR, "perp_markets.json")) or []
    valid_sorts = {"24h_volume", "open_interest", "change_24h_pct",
                   "mark_price", "max_leverage"}
    if sort_by not in valid_sorts:
        return _pretty({"error": f"sort_by must be one of {sorted(valid_sorts)}"})
    sorted_markets = sorted(
        markets,
        key=lambda m: float(m.get(sort_by, 0) or 0),
        reverse=True
    )
    return _pretty({"sort_by": sort_by, "count": len(sorted_markets[:limit]),
                    "markets": sorted_markets[:limit]})


@mcp.tool()
def get_perp_market_stats() -> str:
    """Return aggregate statistics across all perp markets."""
    markets = _read_json(os.path.join(PROJECT_DIR, "perp_markets.json")) or []
    if not markets:
        return _pretty({"error": "No market data loaded. Use Refresh Markets first."})
    total_vol = sum(float(m.get("24h_volume", 0) or 0) for m in markets)
    total_oi = sum(float(m.get("open_interest", 0) or 0) for m in markets)
    avg_funding = (sum(float(m.get("est_funding_rate", 0) or 0) for m in markets)
                   / len(markets)) if markets else 0
    gains = [m for m in markets if float(m.get("change_24h_pct", 0) or 0) > 0]
    losses = [m for m in markets if float(m.get("change_24h_pct", 0) or 0) < 0]
    return _pretty({
        "total_markets": len(markets),
        "total_24h_volume": round(total_vol, 2),
        "total_open_interest": round(total_oi, 2),
        "avg_funding_rate": round(avg_funding, 8),
        "gainers": len(gains),
        "losers": len(losses),
        "max_leverage_available": max(
            (float(m.get("max_leverage", 0) or 0) for m in markets), default=0),
    })


# ---------------------------------------------------------------------------
# 14. Signal history & management (3)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_signal_history(limit: int = 50) -> str:
    """Return the history of past perp signals (executed and rejected)."""
    history = _read_json(os.path.join(PROJECT_DIR,
                                      "perp_signal_history.json")) or []
    return _pretty({"count": len(history), "history": history[:limit]})


@mcp.tool()
def clear_perp_signal_history() -> str:
    """Clear the perp signal history file."""
    _write_json(os.path.join(PROJECT_DIR, "perp_signal_history.json"), [])
    return "Perp signal history cleared."


@mcp.tool()
def get_perp_signal_summary() -> str:
    """Return a summary of signal generation stats (counts by type, direction)."""
    history = _read_json(os.path.join(PROJECT_DIR,
                                      "perp_signal_history.json")) or []
    if not history:
        return _pretty({"error": "No signal history found"})
    by_type: dict = {}
    by_direction: dict = {}
    executed = 0
    rejected = 0
    for s in history:
        stype = s.get("signal_type", "unknown")
        by_type[stype] = by_type.get(stype, 0) + 1
        direction = s.get("direction", "unknown")
        by_direction[direction] = by_direction.get(direction, 0) + 1
        if s.get("executed"):
            executed += 1
        else:
            rejected += 1
    return _pretty({
        "total_signals": len(history),
        "executed": executed,
        "rejected": rejected,
        "by_type": by_type,
        "by_direction": by_direction,
    })


# ---------------------------------------------------------------------------
# 15. Fee & cost analysis (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_fee_summary() -> str:
    """Return a summary of fees paid across all perp trades."""
    trades = _read_json(os.path.join(PROJECT_DIR,
                                     "perp_trade_history.json")) or []
    total_fees = sum(float(t.get("fees", 0) or 0) for t in trades)
    total_notional = sum(float(t.get("notional", 0) or 0) for t in trades)
    fee_pct = (total_fees / total_notional * 100) if total_notional else 0
    return _pretty({
        "total_trades": len(trades),
        "total_fees_usd": round(total_fees, 4),
        "total_notional_usd": round(total_notional, 2),
        "effective_fee_pct": round(fee_pct, 4),
    })


@mcp.tool()
def get_perp_funding_cost(symbol: str = "") -> str:
    """Return total funding payments paid/received for a position or all."""
    positions = _read_json(os.path.join(PROJECT_DIR,
                                        "perp_positions.json")) or []
    results = []
    for p in positions:
        if symbol and (p.get("symbol") or "").upper() != symbol.upper():
            continue
        funding_paid = float(p.get("funding_paid", 0) or 0)
        funding_received = float(p.get("funding_received", 0) or 0)
        net = funding_received - funding_paid
        results.append({
            "symbol": p.get("symbol"),
            "funding_paid": round(funding_paid, 4),
            "funding_received": round(funding_received, 4),
            "net_funding": round(net, 4),
        })
    return _pretty({"positions": results, "count": len(results)})


# ---------------------------------------------------------------------------
# 16. Risk extensions (3)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_correlation_matrix() -> str:
    """Return a simple correlation matrix between open perp positions."""
    positions = _read_json(os.path.join(PROJECT_DIR,
                                        "perp_positions.json")) or []
    symbols = [p.get("symbol", "") for p in positions]
    if len(symbols) < 2:
        return _pretty({"error": "Need at least 2 positions for correlation"})
    # Placeholder: identity matrix (real correlation requires price history)
    n = len(symbols)
    matrix = [[1.0 if i == j else 0.0 for j in range(n)]
              for i in range(n)]
    return _pretty({"symbols": symbols, "matrix": matrix,
                    "note": "Placeholder - requires price history for real correlation"})


@mcp.tool()
def set_perp_liquidation_floor(pct: float) -> str:
    """Set the minimum liquidation distance floor percentage."""
    cfg = _set_perp_cfg_value("perp_liquidation_distance_floor_pct", pct)
    _enqueue_perp_command("update_config",
                     perp_liquidation_distance_floor_pct=float(pct))
    return _pretty({"updated": "perp_liquidation_distance_floor_pct",
                    "value": cfg["perp_liquidation_distance_floor_pct"]})


@mcp.tool()
def set_perp_funding_exit(max_rate: float) -> str:
    """Set the max adverse funding rate before the bot exits a position."""
    cfg = _set_perp_cfg_value("perp_max_adverse_funding_rate", max_rate)
    _enqueue_perp_command("update_config",
                     perp_max_adverse_funding_rate=float(max_rate))
    return _pretty({"updated": "perp_max_adverse_funding_rate",
                    "value": cfg["perp_max_adverse_funding_rate"]})


# ---------------------------------------------------------------------------
# 17. P&L extensions (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def refresh_perp_pnl() -> str:
    """Force a refresh of the perp P&L tally from trade history."""
    _enqueue_perp_command("refresh_pnl")
    trades = _read_json(os.path.join(PROJECT_DIR,
                                     "perp_trade_history.json")) or []
    realized = sum(float(t.get("pnl", 0) or 0) for t in trades)
    fees = sum(float(t.get("fees", 0) or 0) for t in trades)
    tally = {
        "realized_pnl": round(realized, 4),
        "fees_paid": round(fees, 4),
        "net_pnl": round(realized - fees, 4),
        "trade_count": len(trades),
    }
    _write_json(os.path.join(PROJECT_DIR, "perp_pnl_tally.json"), tally)
    return _pretty(tally)


@mcp.tool()
def get_perp_trade_detail(trade_index: int) -> str:
    """Return details for a specific perp trade by its index in history."""
    trades = _read_json(os.path.join(PROJECT_DIR,
                                     "perp_trade_history.json")) or []
    try:
        idx = int(trade_index)
    except (ValueError, TypeError):
        return _pretty({"error": "trade_index must be an integer"})
    if idx < 0 or idx >= len(trades):
        return _pretty({"error": f"Index {idx} out of range (0-{len(trades)-1})"})
    return _pretty(trades[idx])


# ---------------------------------------------------------------------------
# 18. Wallet extensions (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_perp_margin_info() -> str:
    """Return detailed margin information for all open perp positions."""
    positions = _read_json(os.path.join(PROJECT_DIR,
                                        "perp_positions.json")) or []
    info = []
    for p in positions:
        info.append({
            "symbol": p.get("symbol"),
            "margin_mode": p.get("margin_mode", "isolated"),
            "initial_margin": float(p.get("initial_margin", 0) or 0),
            "maintenance_margin": float(p.get("maintenance_margin", 0) or 0),
            "available_margin": float(p.get("available_margin", 0) or 0),
            "margin_ratio": float(p.get("margin_ratio", 0) or 0),
        })
    return _pretty({"positions": info, "count": len(info)})


@mcp.tool()
def set_perp_auto_savings(enabled: bool, threshold_usd: float = 0,
                           transfer_usd: float = 0) -> str:
    """Configure auto savings sweep for perp profits."""
    updates = {"perp_enable_auto_savings": enabled}
    if threshold_usd > 0:
        updates["perp_savings_threshold_usd"] = threshold_usd
    if transfer_usd > 0:
        updates["perp_savings_transfer_usd"] = transfer_usd
    cfg = _update_perp_batch(updates)
    _enqueue_perp_command("update_config", **updates)
    return _pretty({"updated": list(updates.keys()), "values": updates})


# ---------------------------------------------------------------------------
# 19. Monitoring & intervals (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def set_perp_poll_intervals(market_sec: float = 0, position_sec: float = 0,
                             signal_sec: float = 0) -> str:
    """Set polling intervals for market data, position monitoring, and signals."""
    updates = {}
    if market_sec > 0:
        updates["perp_market_poll_sec"] = market_sec
    if position_sec > 0:
        updates["perp_position_poll_sec"] = position_sec
    if signal_sec > 0:
        updates["perp_signal_poll_sec"] = signal_sec
    if not updates:
        return _pretty({"error": "Provide at least one non-zero interval"})
    cfg = _update_perp_batch(updates)
    _enqueue_perp_command("update_config", **updates)
    return _pretty({"updated": list(updates.keys()), "values": updates})


@mcp.tool()
def set_perp_market_filters(min_volume: float = -1, min_oi: float = -1,
                             max_spread: float = -1) -> str:
    """Set market entry filters: min 24h volume, min open interest, max spread."""
    updates = {}
    if min_volume >= 0:
        updates["perp_min_24h_volume_usd"] = min_volume
    if min_oi >= 0:
        updates["perp_min_open_interest_usd"] = min_oi
    if max_spread >= 0:
        updates["perp_max_spread_pct"] = max_spread
    if not updates:
        return _pretty({"error": "Provide at least one filter value (>=0)"})
    cfg = _update_perp_batch(updates)
    _enqueue_perp_command("update_config", **updates)
    return _pretty({"updated": list(updates.keys()), "values": updates})


# ---------------------------------------------------------------------------
# 20. Token allow/block lists (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def set_perp_allowed_tokens(tokens: str) -> str:
    """Set the comma-separated list of allowed base tokens (empty = all allowed)."""
    cfg = _set_perp_cfg_value("perp_allowed_base_tokens", tokens)
    _enqueue_perp_command("update_config", perp_allowed_base_tokens=tokens)
    return _pretty({"updated": "perp_allowed_base_tokens",
                    "value": cfg["perp_allowed_base_tokens"]})


@mcp.tool()
def set_perp_blocked_tokens(tokens: str) -> str:
    """Set the comma-separated list of blocked base tokens (never trade these)."""
    cfg = _set_perp_cfg_value("perp_blocked_base_tokens", tokens)
    _enqueue_perp_command("update_config", perp_blocked_base_tokens=tokens)
    return _pretty({"updated": "perp_blocked_base_tokens",
                    "value": cfg["perp_blocked_base_tokens"]})


# ---------------------------------------------------------------------------
# 21. Dry-run / live mode (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def set_perp_dry_run(enabled: bool) -> str:
    """Enable or disable perp dry-run (practice) mode."""
    cfg = _set_perp_cfg_value("perp_dry_run", enabled)
    _enqueue_perp_command("update_config", perp_dry_run=bool(enabled))
    return _pretty({"updated": "perp_dry_run", "value": cfg["perp_dry_run"]})


@mcp.tool()
def set_perp_auto_execute(enabled: bool) -> str:
    """Enable or disable automatic perp order execution."""
    cfg = _set_perp_cfg_value("perp_auto_execute", enabled)
    _enqueue_perp_command("update_config", perp_auto_execute=bool(enabled))
    return _pretty({"updated": "perp_auto_execute",
                    "value": cfg["perp_auto_execute"]})


# ---------------------------------------------------------------------------
# 22. Signal mode (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def set_perp_signal_mode(mode: str) -> str:
    """Set the perp signal generation mode."""
    valid = {"momentum", "mean_reversion", "breakout", "funding_arb", "multi"}
    if mode not in valid:
        return _pretty({"error": f"mode must be one of {sorted(valid)}"})
    cfg = _set_perp_cfg_value("perp_signal_mode", mode)
    _enqueue_perp_command("update_config", perp_signal_mode=mode)
    return _pretty({"updated": "perp_signal_mode", "value": mode})


@mcp.tool()
def set_perp_position_timeout(seconds: int) -> str:
    """Set the auto-close position timeout in seconds (0 = disable)."""
    cfg = _set_perp_cfg_value("perp_position_timeout_sec", seconds)
    _enqueue_perp_command("update_config", perp_position_timeout_sec=int(seconds))
    return _pretty({"updated": "perp_position_timeout_sec",
                    "value": cfg["perp_position_timeout_sec"]})


# ---------------------------------------------------------------------------
# 23. Testnet toggle (1)
# ---------------------------------------------------------------------------
@mcp.tool()
def set_perp_testnet(enabled: bool) -> str:
    """Switch perp API between testnet and mainnet."""
    cfg = _set_perp_cfg_value("perp_use_testnet", enabled)
    _enqueue_perp_command("update_config", perp_use_testnet=bool(enabled))
    return _pretty({"updated": "perp_use_testnet",
                    "value": cfg["perp_use_testnet"]})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# ===========================================================================
# SPOT TOOLS
# ===========================================================================
@mcp.tool()
def start_spot_bot() -> str:
    """Start the spot trading bot."""
    _enqueue_spot_command("start")
    return "Command queued: start spot bot."


@mcp.tool()
def stop_spot_bot() -> str:
    """Stop the spot trading bot."""
    _enqueue_spot_command("stop")
    return "Command queued: stop spot bot."


@mcp.tool()
def sell_all_spot() -> str:
    """Panic close all open spot positions immediately."""
    _enqueue_spot_command("sell_all")
    return "Command queued: sell all spot positions."


@mcp.tool()
def get_spot_bot_status() -> str:
    """Return the current spot bot status (running, stopped, etc.)."""
    status = _read_json(os.path.join(PROJECT_DIR, "spot_bot_status.json")) or {}
    return _pretty(status)


# ---------------------------------------------------------------------------
# 2. Market discovery (6)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_markets() -> str:
    """Return the list of monitored spot markets with current data."""
    markets = _read_json(os.path.join(PROJECT_DIR, "spot_markets.json")) or {}
    if isinstance(markets, dict):
        count = len(markets)
    else:
        count = len(markets)
    return _pretty({"markets": markets, "count": count})


@mcp.tool()
def get_spot_market_detail(mint: str) -> str:
    """Return detailed market data for a specific token mint."""
    markets = _read_json(os.path.join(PROJECT_DIR, "spot_markets.json")) or {}
    if isinstance(markets, dict):
        data = markets.get(mint)
        if data:
            return _pretty(data)
    return _pretty({"error": f"Market data not found for mint: {mint}"})


@mcp.tool()
def search_spot_tokens(query: str, limit: int = 20) -> str:
    """Search for tokens by name, symbol, or mint address via Jupiter."""
    try:
        from spot_jupiter import JupiterClient
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        results = asyncio.run(client.search_tokens(query, limit))
        return _pretty({"results": results, "count": len(results)})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_trending_spot_tokens(limit: int = 20) -> str:
    """Get trending/popular tokens from Jupiter."""
    try:
        from spot_jupiter import JupiterClient
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        results = asyncio.run(client.get_trending_tokens(limit))
        return _pretty({"tokens": results, "count": len(results)})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_spot_market_stats() -> str:
    """Return aggregate statistics across all monitored spot markets."""
    markets = _read_json(os.path.join(PROJECT_DIR, "spot_markets.json")) or {}
    if isinstance(markets, dict):
        market_list = list(markets.values())
    elif isinstance(markets, list):
        market_list = markets
    else:
        market_list = []
    total_volume = sum(float(m.get("volume_24h", 0)) for m in market_list if isinstance(m, dict))
    avg_change = 0
    if market_list:
        changes = [float(m.get("change_24h_pct", 0)) for m in market_list if isinstance(m, dict)]
        avg_change = sum(changes) / len(changes) if changes else 0
    return _pretty({
        "total_markets": len(market_list),
        "total_volume_24h_usd": round(total_volume, 2),
        "avg_change_24h_pct": round(avg_change, 2),
    })


@mcp.tool()
def refresh_spot_markets() -> str:
    """Trigger a market data refresh from Jupiter."""
    _enqueue_spot_command("refresh_markets")
    return "Command queued: refresh spot markets."


# ---------------------------------------------------------------------------
# 3. Price data (5)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_token_price(mint: str) -> str:
    """Get the current USD price of a token."""
    try:
        from spot_jupiter import JupiterClient
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        price = asyncio.run(client.get_price(mint))
        return _pretty({"mint": mint, "price_usd": price})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_token_prices(mints: str) -> str:
    """Get USD prices for multiple tokens (comma-separated mints)."""
    try:
        from spot_jupiter import JupiterClient
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        mint_list = [m.strip() for m in mints.split(",") if m.strip()]
        prices = asyncio.run(client.get_prices(mint_list))
        return _pretty({"prices": prices})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_sol_price() -> str:
    """Get the current SOL price in USD."""
    try:
        from spot_jupiter import JupiterClient
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        price = asyncio.run(client.get_sol_price())
        return _pretty({"sol_price_usd": price})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_token_candles(mint: str, interval: str = "5m", limit: int = 200) -> str:
    """Get OHLCV candlestick data for a token."""
    try:
        from spot_jupiter import JupiterClient
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        candles = asyncio.run(client.get_ohlcv(mint, interval, limit))
        return _pretty({"mint": mint, "interval": interval,
                        "candles": candles[-50:], "count": len(candles)})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_token_market_data(mint: str) -> str:
    """Get aggregated market data for a token (price, volume, change)."""
    try:
        from spot_jupiter import JupiterClient
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        data = asyncio.run(client.get_token_market_data(mint))
        return _pretty(data if data else {"error": "No data"})
    except Exception as e:
        return _pretty({"error": str(e)})


# ---------------------------------------------------------------------------
# 4. Technical analysis (7)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_rsi(mint: str, period: int = 14) -> str:
    """Calculate RSI for a token based on recent candle data."""
    try:
        from spot_jupiter import JupiterClient
        from spot_snipe_bot import Indicators
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        candles = asyncio.run(client.get_ohlcv(mint, "5m", max(period + 2, 200)))
        if not candles or len(candles) < period + 1:
            return _pretty({"error": "Insufficient candle data"})
        closes = [c["close"] for c in candles]
        rsi = Indicators.rsi(closes, period)
        return _pretty({"mint": mint, "rsi": round(rsi, 2), "period": period})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_macd(mint: str, fast: int = 12, slow: int = 26, signal: int = 9) -> str:
    """Calculate MACD for a token."""
    try:
        from spot_jupiter import JupiterClient
        from spot_snipe_bot import Indicators
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        candles = asyncio.run(client.get_ohlcv(mint, "5m", max(slow + signal + 10, 200)))
        if not candles:
            return _pretty({"error": "Insufficient candle data"})
        closes = [c["close"] for c in candles]
        macd_line, signal_line, histogram = Indicators.macd(closes, fast, slow, signal)
        return _pretty({"mint": mint, "macd_line": round(macd_line, 6),
                        "signal_line": round(signal_line, 6),
                        "histogram": round(histogram, 6)})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_moving_averages(mint: str, short_period: int = 10, long_period: int = 50) -> str:
    """Calculate SMA and EMA for a token."""
    try:
        from spot_jupiter import JupiterClient
        from spot_snipe_bot import Indicators
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        candles = asyncio.run(client.get_ohlcv(mint, "5m", max(long_period + 10, 200)))
        if not candles:
            return _pretty({"error": "Insufficient candle data"})
        closes = [c["close"] for c in candles]
        return _pretty({
            "mint": mint,
            "sma_short": round(Indicators.sma(closes, short_period), 6),
            "sma_long": round(Indicators.sma(closes, long_period), 6),
            "ema_short": round(Indicators.ema(closes, short_period), 6),
            "ema_long": round(Indicators.ema(closes, long_period), 6),
        })
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_bollinger_bands(mint: str, period: int = 20, std_dev: float = 2.0) -> str:
    """Calculate Bollinger Bands for a token."""
    try:
        from spot_jupiter import JupiterClient
        from spot_snipe_bot import Indicators
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        candles = asyncio.run(client.get_ohlcv(mint, "5m", max(period + 10, 200)))
        if not candles:
            return _pretty({"error": "Insufficient candle data"})
        closes = [c["close"] for c in candles]
        mid, upper, lower = Indicators.bollinger_bands(closes, period, std_dev)
        return _pretty({"mint": mint, "middle": round(mid, 6),
                        "upper": round(upper, 6), "lower": round(lower, 6)})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_volume_analysis(mint: str, period: int = 20) -> str:
    """Analyze volume patterns for a token."""
    try:
        from spot_jupiter import JupiterClient
        from spot_snipe_bot import Indicators
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        candles = asyncio.run(client.get_ohlcv(mint, "5m", max(period + 10, 200)))
        if not candles:
            return _pretty({"error": "Insufficient candle data"})
        volumes = [c["volume"] for c in candles]
        vol_sma = Indicators.volume_sma(volumes, period)
        current_vol = volumes[-1] if volumes else 0
        spike_mult = current_vol / vol_sma if vol_sma > 0 else 0
        return _pretty({
            "mint": mint,
            "current_volume": round(current_vol, 2),
            "volume_sma": round(vol_sma, 2),
            "spike_multiplier": round(spike_mult, 2),
            "is_spike": spike_mult > 3.0,
        })
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_all_indicators(mint: str) -> str:
    """Get all technical indicators for a token in one call."""
    try:
        from spot_jupiter import JupiterClient
        from spot_snipe_bot import Indicators
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        candles = asyncio.run(client.get_ohlcv(mint, "5m", 200))
        if not candles:
            return _pretty({"error": "Insufficient candle data"})
        closes = [c["close"] for c in candles]
        volumes = [c["volume"] for c in candles]
        macd_line, signal_line, histogram = Indicators.macd(closes)
        mid, upper, lower = Indicators.bollinger_bands(closes)
        return _pretty({
            "mint": mint,
            "price": closes[-1] if closes else 0,
            "rsi": round(Indicators.rsi(closes), 2),
            "macd_line": round(macd_line, 6),
            "macd_signal": round(signal_line, 6),
            "macd_histogram": round(histogram, 6),
            "sma_10": round(Indicators.sma(closes, 10), 6),
            "sma_50": round(Indicators.sma(closes, 50), 6),
            "ema_12": round(Indicators.ema(closes, 12), 6),
            "ema_26": round(Indicators.ema(closes, 26), 6),
            "bb_mid": round(mid, 6),
            "bb_upper": round(upper, 6),
            "bb_lower": round(lower, 6),
            "volume_sma": round(Indicators.volume_sma(volumes, 20), 2),
            "current_volume": round(volumes[-1] if volumes else 0, 2),
            "atr": round(Indicators.atr(candles), 6),
        })
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_atr(mint: str, period: int = 14) -> str:
    """Calculate ATR (Average True Range) for a token."""
    try:
        from spot_jupiter import JupiterClient
        from spot_snipe_bot import Indicators
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        candles = asyncio.run(client.get_ohlcv(mint, "5m", max(period + 10, 200)))
        if not candles:
            return _pretty({"error": "Insufficient candle data"})
        atr = Indicators.atr(candles, period)
        return _pretty({"mint": mint, "atr": round(atr, 6), "period": period})
    except Exception as e:
        return _pretty({"error": str(e)})


# ---------------------------------------------------------------------------
# 5. Signals (5)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_signals() -> str:
    """Return recent spot trading signals."""
    signals = _read_json(os.path.join(PROJECT_DIR, "spot_signals.json")) or []
    return _pretty({"signals": signals[-50:], "count": len(signals)})


@mcp.tool()
def get_spot_signal_detail(index: int) -> str:
    """Return details for a specific signal by index (from most recent)."""
    signals = _read_json(os.path.join(PROJECT_DIR, "spot_signals.json")) or []
    if index < 0 or index >= len(signals):
        return _pretty({"error": "Invalid signal index"})
    return _pretty(signals[-(index + 1)])


@mcp.tool()
def enable_spot_signals(enabled: bool) -> str:
    """Enable or disable automatic signal generation."""
    cfg = _set_spot_cfg_value("spot_auto_execute", enabled)
    return _pretty({"spot_auto_execute": cfg["spot_auto_execute"]})


@mcp.tool()
def get_signal_summary() -> str:
    """Return a summary of recent signals by type and strength."""
    signals = _read_json(os.path.join(PROJECT_DIR, "spot_signals.json")) or []
    by_type = {}
    total = len(signals)
    avg_strength = 0
    if total:
        strengths = [float(s.get("strength", 0)) for s in signals if isinstance(s, dict)]
        avg_strength = sum(strengths) / len(strengths) if strengths else 0
        for s in signals:
            if isinstance(s, dict):
                stype = s.get("signal_type", "unknown")
                by_type[stype] = by_type.get(stype, 0) + 1
    return _pretty({
        "total_signals": total,
        "avg_strength": round(avg_strength, 1),
        "by_type": by_type,
    })


@mcp.tool()
def clear_spot_signals() -> str:
    """Clear all stored spot signals."""
    _write_json(os.path.join(PROJECT_DIR, "spot_signals.json"), [])
    return "Spot signals cleared."


# ---------------------------------------------------------------------------
# 6. Configuration (6)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_config() -> str:
    """Return the full spot trading configuration."""
    return _pretty(_load_spot_cfg())


@mcp.tool()
def update_spot_config(key: str, value: Any) -> str:
    """Update a single spot configuration field."""
    cfg = _set_spot_cfg_value(key, value)
    _enqueue_spot_command("update_config", key=key, value=cfg[key])
    return _pretty({"updated": key, "value": cfg[key]})


@mcp.tool()
def batch_update_spot_config(updates: dict) -> str:
    """Update multiple spot configuration fields at once."""
    cfg = _update_spot_batch(updates)
    return _pretty({"updated": len(updates), "config": cfg})


@mcp.tool()
def get_spot_config_catalog() -> str:
    """Return the catalog of all available spot config fields with metadata."""
    catalog = {}
    for key, rule in _SPOT_CONFIG_RULES.items():
        catalog[key] = {
            "type": rule.get("type", "str"),
            "default": rule.get("default"),
            "description": rule.get("description", ""),
            "min": rule.get("min"),
            "max": rule.get("max"),
            "choices": rule.get("choices"),
            "requires_restart": rule.get("requires_restart", False),
        }
    return _pretty(catalog)


@mcp.tool()
def validate_spot_config_values() -> str:
    """Validate the current spot configuration and return warnings."""
    cfg = _load_spot_cfg()
    warnings = _validate_spot_config(cfg)
    return _pretty({"valid": len(warnings) == 0, "warnings": warnings})


@mcp.tool()
def get_spot_config_field(key: str) -> str:
    """Get the current value and metadata for a single config field."""
    cfg = _load_spot_cfg()
    rule = _SPOT_CONFIG_RULES.get(key, {})
    return _pretty({
        "key": key,
        "value": cfg.get(key),
        "default": rule.get("default"),
        "description": rule.get("description", ""),
        "type": rule.get("type", "str"),
    })


# ---------------------------------------------------------------------------
# 7. Positions (5)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_positions() -> str:
    """Return all open spot positions."""
    positions = _read_json(os.path.join(PROJECT_DIR, "spot_positions.json")) or []
    return _pretty({"positions": positions, "count": len(positions)})


@mcp.tool()
def get_spot_position_detail(mint: str) -> str:
    """Return details for a specific spot position by token mint."""
    positions = _read_json(os.path.join(PROJECT_DIR, "spot_positions.json")) or []
    for p in positions:
        if p.get("mint") == mint:
            return _pretty(p)
    return _pretty({"error": f"Position not found for mint: {mint}"})


@mcp.tool()
def get_spot_position_pnl(mint: str) -> str:
    """Return P&L for a specific spot position."""
    positions = _read_json(os.path.join(PROJECT_DIR, "spot_positions.json")) or []
    for p in positions:
        if p.get("mint") == mint:
            return _pretty({
                "mint": mint,
                "realized_pnl": p.get("realized_pnl", 0),
                "unrealized_pnl": p.get("unrealized_pnl", 0),
                "entry_price": p.get("entry_price", 0),
                "current_price": p.get("current_price", 0),
            })
    return _pretty({"error": f"Position not found for mint: {mint}"})


@mcp.tool()
def get_spot_exposure() -> str:
    """Return total exposure across all open spot positions."""
    positions = _read_json(os.path.join(PROJECT_DIR, "spot_positions.json")) or []
    total_cost = sum(float(p.get("cost_usd", 0)) for p in positions if not p.get("sold"))
    total_unrealized = sum(float(p.get("unrealized_pnl", 0)) for p in positions if not p.get("sold"))
    return _pretty({
        "open_positions": len([p for p in positions if not p.get("sold")]),
        "total_cost_usd": round(total_cost, 2),
        "total_unrealized_pnl_usd": round(total_unrealized, 2),
    })


@mcp.tool()
def close_spot_position(mint: str) -> str:
    """Close a specific spot position by token mint."""
    _enqueue_spot_command("close_position", mint=mint)
    return f"Command queued: close position for {mint}."


# ---------------------------------------------------------------------------
# 8. Orders (5)
# ---------------------------------------------------------------------------
@mcp.tool()
def spot_market_buy(mint: str, usd_amount: float) -> str:
    """Execute a manual market buy order for a token."""
    _enqueue_spot_command("buy", mint=mint, price=0, amount=usd_amount)
    return f"Command queued: market buy {mint} for ${usd_amount}."


@mcp.tool()
def spot_market_sell(mint: str) -> str:
    """Execute a manual market sell order for a token (sell all holdings)."""
    _enqueue_spot_command("close_position", mint=mint)
    return f"Command queued: market sell {mint}."


@mcp.tool()
def spot_limit_buy(mint: str, usd_amount: float, limit_price: float) -> str:
    """Place a limit buy order for a token (queued for engine execution)."""
    _enqueue_spot_command("buy", mint=mint, price=limit_price, amount=usd_amount)
    return f"Command queued: limit buy {mint} for ${usd_amount} at ${limit_price}."


@mcp.tool()
def cancel_spot_order(mint: str) -> str:
    """Cancel a pending spot order for a token."""
    _enqueue_spot_command("cancel_order", mint=mint)
    return f"Command queued: cancel order for {mint}."


@mcp.tool()
def get_spot_open_orders() -> str:
    """Return any pending spot orders."""
    return _pretty({"orders": [], "count": 0, "note": "Spot orders are executed immediately via Jupiter"})


# ---------------------------------------------------------------------------
# 9. Trade history (4)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_trade_history(limit: int = 50) -> str:
    """Return recent spot trade history."""
    history = _read_json(os.path.join(PROJECT_DIR, "spot_trade_history.json")) or []
    return _pretty({"trades": history[-limit:], "count": len(history)})


@mcp.tool()
def get_spot_trade_detail(index: int) -> str:
    """Return details for a specific trade by index."""
    history = _read_json(os.path.join(PROJECT_DIR, "spot_trade_history.json")) or []
    if index < 0 or index >= len(history):
        return _pretty({"error": "Invalid trade index"})
    return _pretty(history[-(index + 1)])


@mcp.tool()
def clear_spot_trade_history() -> str:
    """Clear all spot trade history."""
    _write_json(os.path.join(PROJECT_DIR, "spot_trade_history.json"), [])
    return "Spot trade history cleared."


@mcp.tool()
def get_spot_trade_pnl_tally() -> str:
    """Return the aggregate P&L tally for spot trading."""
    tally = _read_json(os.path.join(PROJECT_DIR, "spot_pnl_tally.json")) or {}
    return _pretty(tally)


# ---------------------------------------------------------------------------
# 10. P&L (3)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_total_pnl() -> str:
    """Return total realized and unrealized P&L for spot trading."""
    tally = _read_json(os.path.join(PROJECT_DIR, "spot_pnl_tally.json")) or {}
    positions = _read_json(os.path.join(PROJECT_DIR, "spot_positions.json")) or []
    unrealized = sum(float(p.get("unrealized_pnl", 0)) for p in positions if not p.get("sold"))
    return _pretty({
        "total_realized_pnl_usd": tally.get("total_realized_pnl_usd", 0),
        "total_unrealized_pnl_usd": round(unrealized, 2),
        "total_trades": tally.get("total_trades", 0),
        "wins": tally.get("wins", 0),
        "losses": tally.get("losses", 0),
    })


@mcp.tool()
def get_spot_win_rate() -> str:
    """Return the win rate for spot trading."""
    tally = _read_json(os.path.join(PROJECT_DIR, "spot_pnl_tally.json")) or {}
    total = tally.get("total_trades", 0)
    wins = tally.get("wins", 0)
    rate = (wins / total * 100) if total > 0 else 0
    return _pretty({
        "total_trades": total,
        "wins": wins,
        "losses": tally.get("losses", 0),
        "win_rate_pct": round(rate, 1),
    })


@mcp.tool()
def get_spot_best_worst_trades() -> str:
    """Return the best and worst spot trades."""
    tally = _read_json(os.path.join(PROJECT_DIR, "spot_pnl_tally.json")) or {}
    return _pretty({
        "best_trade_usd": tally.get("best_trade_usd", 0),
        "worst_trade_usd": tally.get("worst_trade_usd", 0),
    })


# ---------------------------------------------------------------------------
# 11. Risk (4)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_risk_status() -> str:
    """Return current risk metrics for spot trading."""
    cfg = _load_spot_cfg()
    positions = _read_json(os.path.join(PROJECT_DIR, "spot_positions.json")) or []
    open_count = len([p for p in positions if not p.get("sold")])
    total_cost = sum(float(p.get("cost_usd", 0)) for p in positions if not p.get("sold"))
    max_exposure = float(cfg.get("spot_max_total_exposure_usd", 0))
    max_positions = int(cfg.get("spot_max_positions", 5))
    return _pretty({
        "open_positions": open_count,
        "max_positions": max_positions,
        "total_exposure_usd": round(total_cost, 2),
        "max_exposure_usd": max_exposure,
        "exposure_utilization_pct": round((total_cost / max_exposure * 100) if max_exposure > 0 else 0, 1),
        "stop_loss_enabled": cfg.get("spot_stop_loss_enabled", True),
        "hard_stop_loss_pct": cfg.get("spot_hard_stop_loss_pct", 5),
        "trailing_stop_enabled": cfg.get("spot_trailing_stop_enabled", True),
    })


@mcp.tool()
def set_spot_max_positions(max_positions: int) -> str:
    """Set the maximum number of simultaneous spot positions."""
    cfg = _set_spot_cfg_value("spot_max_positions", max_positions)
    return _pretty({"updated": "spot_max_positions", "value": cfg["spot_max_positions"]})


@mcp.tool()
def set_spot_max_exposure(max_exposure_usd: float) -> str:
    """Set the maximum total exposure for spot trading."""
    cfg = _set_spot_cfg_value("spot_max_total_exposure_usd", max_exposure_usd)
    return _pretty({"updated": "spot_max_total_exposure_usd", "value": cfg["spot_max_total_exposure_usd"]})


@mcp.tool()
def get_spot_portfolio_concentration() -> str:
    """Return portfolio concentration analysis for spot positions."""
    positions = _read_json(os.path.join(PROJECT_DIR, "spot_positions.json")) or []
    open_positions = [p for p in positions if not p.get("sold")]
    if not open_positions:
        return _pretty({"error": "No open positions"})
    total_cost = sum(float(p.get("cost_usd", 0)) for p in open_positions)
    concentrations = []
    for p in open_positions:
        cost = float(p.get("cost_usd", 0))
        pct = (cost / total_cost * 100) if total_cost > 0 else 0
        concentrations.append({
            "mint": p.get("mint", ""),
            "symbol": p.get("symbol", ""),
            "cost_usd": round(cost, 2),
            "concentration_pct": round(pct, 1),
        })
    concentrations.sort(key=lambda x: x["concentration_pct"], reverse=True)
    return _pretty({"total_cost_usd": round(total_cost, 2), "concentrations": concentrations})


# ---------------------------------------------------------------------------
# 12. Wallet (3)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_wallet_balances(wallet_pubkey: str = "") -> str:
    """Return spot wallet balances (USDC, SOL, and token holdings)."""
    if not wallet_pubkey:
        return _pretty({"error": "Wallet public key required"})
    try:
        from spot_jupiter import JupiterClient
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        sol_balance = asyncio.run(client.get_wallet_sol_balance(wallet_pubkey))
        tokens = asyncio.run(client.get_wallet_all_tokens(wallet_pubkey))
        return _pretty({
            "wallet": wallet_pubkey,
            "sol_balance": round(sol_balance, 4),
            "token_accounts": tokens,
        })
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_spot_token_balance(wallet_pubkey: str, token_mint: str) -> str:
    """Get a specific token balance for a wallet."""
    try:
        from spot_jupiter import JupiterClient
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        balance = asyncio.run(client.get_wallet_token_balance(wallet_pubkey, token_mint))
        return _pretty({"wallet": wallet_pubkey, "mint": token_mint, "balance": balance})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def transfer_spot_to_savings(amount_usd: float) -> str:
    """Transfer funds from spot trading wallet to savings wallet."""
    _enqueue_spot_command("transfer_to_savings", amount=amount_usd)
    return f"Command queued: transfer ${amount_usd} to savings."


# ---------------------------------------------------------------------------
# 13. Logs (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_logs(lines: int = 50) -> str:
    """Return recent spot bot log lines."""
    log_path = os.path.join(PROJECT_DIR, "spot_bot.log")
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return _pretty({"logs": all_lines[-lines:], "count": len(all_lines)})
    except FileNotFoundError:
        return _pretty({"logs": [], "note": "No log file yet"})


@mcp.tool()
def clear_spot_logs() -> str:
    """Clear the spot bot log file."""
    log_path = os.path.join(PROJECT_DIR, "spot_bot.log")
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("")
        return "Spot logs cleared."
    except Exception as e:
        return _pretty({"error": str(e)})


# ---------------------------------------------------------------------------
# 14. Alerts (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_alerts() -> str:
    """Return recent spot trading alerts."""
    signals = _read_json(os.path.join(PROJECT_DIR, "spot_signals.json")) or []
    cfg = _load_spot_cfg()
    threshold = float(cfg.get("spot_alert_threshold_score", 50))
    alerts = [s for s in signals if float(s.get("strength", 0)) >= threshold]
    return _pretty({"alerts": alerts[-50:], "count": len(alerts), "threshold": threshold})


@mcp.tool()
def set_spot_alert_threshold(score: float) -> str:
    """Set the minimum signal score to trigger an alert."""
    cfg = _set_spot_cfg_value("spot_alert_threshold_score", score)
    return _pretty({"updated": "spot_alert_threshold_score", "value": cfg["spot_alert_threshold_score"]})


# ---------------------------------------------------------------------------
# 15. DCA (3)
# ---------------------------------------------------------------------------
@mcp.tool()
def enable_spot_dca(enabled: bool) -> str:
    """Enable or disable DCA (Dollar Cost Averaging) for spot positions."""
    cfg = _set_spot_cfg_value("spot_dca_enabled", enabled)
    return _pretty({"updated": "spot_dca_enabled", "value": cfg["spot_dca_enabled"]})


@mcp.tool()
def set_spot_dca_steps(steps: int) -> str:
    """Set the maximum number of DCA steps per position."""
    cfg = _set_spot_cfg_value("spot_dca_steps", steps)
    return _pretty({"updated": "spot_dca_steps", "value": cfg["spot_dca_steps"]})


@mcp.tool()
def set_spot_dca_interval(seconds: int) -> str:
    """Set the DCA interval in seconds."""
    cfg = _set_spot_cfg_value("spot_dca_interval_sec", seconds)
    return _pretty({"updated": "spot_dca_interval_sec", "value": cfg["spot_dca_interval_sec"]})


# ---------------------------------------------------------------------------
# 16. Risk level (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def set_spot_risk_level(level: int) -> str:
    """Set the spot risk level (1-20) and apply the coordinated profile."""
    if level < _SPOT_RISK_LEVEL_MIN or level > _SPOT_RISK_LEVEL_MAX:
        return _pretty({"error": f"Risk level must be between {_SPOT_RISK_LEVEL_MIN} and {_SPOT_RISK_LEVEL_MAX}"})
    cfg = _load_spot_cfg()
    profile = _build_spot_risk_level_profile(level, base_cfg=cfg)
    cfg.update(profile)
    _save_spot_cfg(cfg)
    _enqueue_spot_command("apply_risk_level", level=level)
    return _pretty({"risk_level": level, "name": _spot_risk_level_name(level), "applied": True})


@mcp.tool()
def get_spot_risk_levels() -> str:
    """Return all 20 spot risk levels with their names."""
    levels = []
    for lvl in range(_SPOT_RISK_LEVEL_MIN, _SPOT_RISK_LEVEL_MAX + 1):
        levels.append({"level": lvl, "name": _spot_risk_level_name(lvl)})
    return _pretty({"levels": levels, "min": _SPOT_RISK_LEVEL_MIN, "max": _SPOT_RISK_LEVEL_MAX})


# ---------------------------------------------------------------------------
# 17. Rebalancing (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def enable_spot_rebalancing(enabled: bool) -> str:
    """Enable or disable portfolio rebalancing."""
    cfg = _set_spot_cfg_value("spot_rebalance_enabled", enabled)
    return _pretty({"updated": "spot_rebalance_enabled", "value": cfg["spot_rebalance_enabled"]})


@mcp.tool()
def set_spot_rebalance_threshold(threshold_pct: float) -> str:
    """Set the rebalancing threshold percentage."""
    cfg = _set_spot_cfg_value("spot_rebalance_threshold_pct", threshold_pct)
    return _pretty({"updated": "spot_rebalance_threshold_pct", "value": cfg["spot_rebalance_threshold_pct"]})


# ---------------------------------------------------------------------------
# 18. Token management (3)
# ---------------------------------------------------------------------------
@mcp.tool()
def add_allowed_spot_token(mint: str) -> str:
    """Add a token to the allowed list for spot trading."""
    cfg = _load_spot_cfg()
    allowed = cfg.get("spot_allowed_tokens", "")
    mints = [m.strip() for m in allowed.split(",") if m.strip()]
    if mint not in mints:
        mints.append(mint)
    cfg["spot_allowed_tokens"] = ",".join(mints)
    _save_spot_cfg(cfg)
    return _pretty({"allowed_tokens": cfg["spot_allowed_tokens"]})


@mcp.tool()
def add_blocked_spot_token(mint: str) -> str:
    """Add a token to the blocked list for spot trading."""
    cfg = _load_spot_cfg()
    blocked = cfg.get("spot_blocked_tokens", "")
    mints = [m.strip() for m in blocked.split(",") if m.strip()]
    if mint not in mints:
        mints.append(mint)
    cfg["spot_blocked_tokens"] = ",".join(mints)
    _save_spot_cfg(cfg)
    return _pretty({"blocked_tokens": cfg["spot_blocked_tokens"]})


@mcp.tool()
def remove_allowed_spot_token(mint: str) -> str:
    """Remove a token from the allowed list."""
    cfg = _load_spot_cfg()
    allowed = cfg.get("spot_allowed_tokens", "")
    mints = [m.strip() for m in allowed.split(",") if m.strip() and m.strip() != mint]
    cfg["spot_allowed_tokens"] = ",".join(mints)
    _save_spot_cfg(cfg)
    return _pretty({"allowed_tokens": cfg["spot_allowed_tokens"]})


# ---------------------------------------------------------------------------
# 19. Quote/swap (3)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_quote(input_mint: str, output_mint: str, amount: int,
                   slippage_bps: int = 100) -> str:
    """Get a swap quote from Jupiter DEX."""
    try:
        from spot_jupiter import JupiterClient
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        quote = asyncio.run(client.get_quote(input_mint, output_mint, amount, slippage_bps))
        return _pretty(quote if quote else {"error": "No quote returned"})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def get_spot_swap_transaction(input_mint: str, output_mint: str, amount: int,
                              user_wallet: str, slippage_bps: int = 100) -> str:
    """Get a serialized swap transaction from Jupiter."""
    try:
        from spot_jupiter import JupiterClient
        import asyncio
        client = JupiterClient(_load_spot_cfg())
        quote = asyncio.run(client.get_quote(input_mint, output_mint, amount, slippage_bps))
        if not quote:
            return _pretty({"error": "No quote available"})
        swap_tx = asyncio.run(client.get_swap_transaction(quote, user_wallet))
        return _pretty(swap_tx if swap_tx else {"error": "No swap transaction returned"})
    except Exception as e:
        return _pretty({"error": str(e)})


@mcp.tool()
def set_spot_slippage(bps: int) -> str:
    """Set the maximum slippage in basis points for spot swaps."""
    cfg = _set_spot_cfg_value("spot_slippage_bps", bps)
    return _pretty({"updated": "spot_slippage_bps", "value": cfg["spot_slippage_bps"]})


# ---------------------------------------------------------------------------
# 20. Statistics (2)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_spot_statistics() -> str:
    """Return comprehensive spot trading statistics."""
    tally = _read_json(os.path.join(PROJECT_DIR, "spot_pnl_tally.json")) or {}
    positions = _read_json(os.path.join(PROJECT_DIR, "spot_positions.json")) or []
    signals = _read_json(os.path.join(PROJECT_DIR, "spot_signals.json")) or []
    history = _read_json(os.path.join(PROJECT_DIR, "spot_trade_history.json")) or []
    open_positions = [p for p in positions if not p.get("sold")]
    total_unrealized = sum(float(p.get("unrealized_pnl", 0)) for p in open_positions)
    return _pretty({
        "total_realized_pnl_usd": tally.get("total_realized_pnl_usd", 0),
        "total_unrealized_pnl_usd": round(total_unrealized, 2),
        "total_trades": tally.get("total_trades", 0),
        "wins": tally.get("wins", 0),
        "losses": tally.get("losses", 0),
        "best_trade_usd": tally.get("best_trade_usd", 0),
        "worst_trade_usd": tally.get("worst_trade_usd", 0),
        "open_positions": len(open_positions),
        "total_signals": len(signals),
        "total_history": len(history),
    })


@mcp.tool()
def reset_spot_pnl_tally() -> str:
    """Reset the spot P&L tally to zero."""
    empty_tally = {
        "total_realized_pnl_usd": 0.0,
        "total_fees_paid_usd": 0.0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "best_trade_usd": 0.0,
        "worst_trade_usd": 0.0,
    }
    _write_json(os.path.join(PROJECT_DIR, "spot_pnl_tally.json"), empty_tally)
    return "Spot P&L tally reset to zero."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# ===========================================================================
# MIRROR MODE TOOLS (20 tools)
# ===========================================================================

# --- Mirror imports ---
from mirror_mode import (
    add_whale as _mirror_add_whale,
    remove_whale as _mirror_remove_whale,
    toggle_whale as _mirror_toggle_whale,
    get_whale_stats as _mirror_get_whale_stats,
    get_all_whale_stats as _mirror_get_all_whale_stats,
    get_mirror_statistics as _mirror_get_stats,
    get_mirror_trade_history as _mirror_get_history,
    clear_mirror_trades as _mirror_clear_trades,
    get_mirror_config as _mirror_get_config,
    update_mirror_config as _mirror_update_config,
    load_mirror_config as _mirror_load_config,
    save_mirror_config as _mirror_save_config,
    load_whales as _mirror_load_whales,
    calculate_mirror_size as _mirror_calc_size,
    should_copy_trade as _mirror_should_copy,
    record_mirror_trade as _mirror_record_trade,
    update_whale_performance as _mirror_update_perf,
    MirrorTrade,
    _enqueue_command as _mirror_enqueue_cmd,
    _read_json as _mirror_read_json,
    _write_json as _mirror_write_json,
    MIRROR_CONFIG_PATH,
    MIRROR_WHALES_PATH,
    MIRROR_TRADES_PATH,
    MIRROR_COMMAND_QUEUE_PATH,
)


@mcp.tool()
def start_mirror_bot() -> str:
    """Start the mirror trading bot (copy whale trades)."""
    cfg = _mirror_load_config()
    cfg["mirror_enabled"] = True
    _mirror_save_config(cfg)
    _mirror_enqueue_cmd("start")
    return "Mirror bot started."


@mcp.tool()
def stop_mirror_bot() -> str:
    """Stop the mirror trading bot."""
    cfg = _mirror_load_config()
    cfg["mirror_enabled"] = False
    _mirror_save_config(cfg)
    _mirror_enqueue_cmd("stop")
    return "Mirror bot stopped."


@mcp.tool()
def sell_all_mirror_positions() -> str:
    """Close all mirror positions immediately."""
    _mirror_enqueue_cmd("sell_all")
    return "Command queued: sell all mirror positions."


@mcp.tool()
def get_mirror_bot_status() -> str:
    """Return the current mirror bot status and statistics."""
    return _pretty(_mirror_get_stats())


@mcp.tool()
def get_mirror_config() -> str:
    """Return the full mirror trading configuration."""
    return _pretty(_mirror_get_config())


@mcp.tool()
def update_mirror_config_field(key: str, value: Any) -> str:
    """Update a single mirror configuration field."""
    return _pretty(_mirror_update_config(key, value))


@mcp.tool()
def add_mirror_whale(address: str, label: str = "", mode: str = "spot") -> str:
    """Add a whale wallet to monitor. Mode: spot, perp, or both."""
    return _pretty(_mirror_add_whale(address, label, mode))


@mcp.tool()
def remove_mirror_whale(address: str) -> str:
    """Remove a whale wallet from monitoring."""
    return _pretty(_mirror_remove_whale(address))


@mcp.tool()
def toggle_mirror_whale(address: str, enabled: bool) -> str:
    """Enable or disable a whale wallet for mirror trading."""
    return _pretty(_mirror_toggle_whale(address, enabled))


@mcp.tool()
def get_mirror_whale_stats(address: str) -> str:
    """Return performance stats for a specific whale."""
    return _pretty(_mirror_get_whale_stats(address))


@mcp.tool()
def get_all_mirror_whale_stats() -> str:
    """Return performance stats for all monitored whales."""
    return _pretty(_mirror_get_all_whale_stats())


@mcp.tool()
def get_mirror_whales() -> str:
    """Return the list of all monitored whale wallets."""
    return _pretty({"whales": _mirror_load_whales()})


@mcp.tool()
def get_mirror_trades(limit: int = 50) -> str:
    """Return recent mirror trade history."""
    return _pretty({"trades": _mirror_get_history(limit), "count": len(_mirror_get_history(limit))})


@mcp.tool()
def clear_mirror_trade_history() -> str:
    """Clear all mirror trade history."""
    return _mirror_clear_trades()


@mcp.tool()
def set_mirror_allocation_pct(pct: float) -> str:
    """Set the percentage of wallet allocated to mirror trading."""
    return _pretty(_mirror_update_config("mirror_allocation_pct", pct))


@mcp.tool()
def set_mirror_max_trade_usd(usd: float) -> str:
    """Set the maximum USD per mirrored trade."""
    return _pretty(_mirror_update_config("mirror_max_trade_usd", usd))


@mcp.tool()
def set_mirror_mode(mode: str) -> str:
    """Set mirror mode: 'spot', 'perp', or 'both'."""
    if mode not in ("spot", "perp", "both"):
        return _pretty({"error": "Mode must be 'spot', 'perp', or 'both'"})
    return _pretty(_mirror_update_config("mirror_mode", mode))


@mcp.tool()
def set_mirror_dry_run(enabled: bool) -> str:
    """Enable or disable dry-run mode for mirror trading."""
    return _pretty(_mirror_update_config("mirror_dry_run", enabled))


@mcp.tool()
def set_mirror_copy_sells(enabled: bool) -> str:
    """Enable or disable copying whale sell trades (not just buys)."""
    return _pretty(_mirror_update_config("mirror_copy_sells", enabled))


@mcp.tool()
def set_mirror_stop_loss_pct(pct: float) -> str:
    """Set the stop loss percentage for mirror positions."""
    return _pretty(_mirror_update_config("mirror_stop_loss_pct", pct))


@mcp.tool()
def set_mirror_take_profit_pct(pct: float) -> str:
    """Set the take profit percentage for mirror positions."""
    return _pretty(_mirror_update_config("mirror_take_profit_pct", pct))


# ===========================================================================
# Entry point
# ===========================================================================
def main():
    mcp.run()


if __name__ == "__main__":
    main()
