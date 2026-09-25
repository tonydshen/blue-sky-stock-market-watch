# market_ib_trade_test.py
# Revision history
# Created on 09/25/26 - First connectivity + order test against the Interactive Brokers TWS API.
# Requirements, 09/25/26:
# 1. Connect to a running TWS or IB Gateway over the TWS API (ib_async), with host/port/client id
#    read from .env (IB_HOST, IB_PORT, IB_CLIENT_ID, IB_ACCOUNT) and overridable on the command line.
# 2. Refuse to touch a live account unless --live is passed AND the user types a confirmation.
#    IB paper accounts start with "DU"; anything else is treated as real money.
# 3. Report which account is connected and its buying power, so the target account is never a guess.
# 4. Qualify the contract and read a current price, falling back to delayed data when the account
#    has no live market data subscription (the usual case on a new account).
# 5. Place one small order. Default is a limit order priced away from the market so the full
#    lifecycle (submit -> open -> cancel) can be exercised without an unintended fill.
# 6. Offer --what-if as a no-order dry run: IB returns margin and commission impact only.
# 7. Print the order status transitions, then cancel the order unless --keep is passed.
#
import os
import sys
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

try:
    from ib_async import IB, Stock, LimitOrder, MarketOrder
except ImportError:
    sys.exit("Error: ib_async is not installed. Run: uv sync   (or: uv add ib_async)")

load_dotenv()

ET = ZoneInfo("America/New_York")

# TWS API listening ports, as configured in Global Configuration -> API -> Settings
PORTS = {
    "tws-paper": 7497,
    "tws-live": 7496,
    "gateway-paper": 4002,
    "gateway-live": 4001,
}

DEFAULT_SYMBOL = "F"        # cheap, very liquid; one share is a few dollars
DEFAULT_QUANTITY = 1
DEFAULT_OFFSET_PCT = 5.0    # limit this far below the market on a BUY, so it rests unfilled


def parse_args():
    p = argparse.ArgumentParser(
        description="Place one small test trade through the Interactive Brokers TWS API."
    )
    p.add_argument("--symbol", default=DEFAULT_SYMBOL, help=f"US stock symbol (default {DEFAULT_SYMBOL})")
    p.add_argument("--quantity", type=int, default=DEFAULT_QUANTITY, help="Share count (default 1)")
    p.add_argument("--action", choices=["BUY", "SELL"], default="BUY", help="Order side (default BUY)")
    p.add_argument("--order-type", choices=["LMT", "MKT"], default="LMT",
                   help="LMT rests away from the market (default); MKT will actually fill")
    p.add_argument("--limit-price", type=float,
                   help="Explicit limit price; default is the market price offset by --offset-pct")
    p.add_argument("--offset-pct", type=float, default=DEFAULT_OFFSET_PCT,
                   help=f"How far from the market to place the default limit (default {DEFAULT_OFFSET_PCT}%%)")
    p.add_argument("--what-if", action="store_true",
                   help="Dry run: ask IB for margin/commission impact, place nothing")
    p.add_argument("--keep", action="store_true", help="Leave the order working instead of cancelling it")
    p.add_argument("--wait", type=int, default=10, help="Seconds to watch the order before cancelling (default 10)")
    p.add_argument("--outside-rth", action="store_true", help="Allow the order to work outside regular trading hours")
    p.add_argument("--host", default=os.getenv("IB_HOST", "127.0.0.1"), help="TWS/Gateway host (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=None, help="TWS/Gateway port; overrides --endpoint and IB_PORT")
    p.add_argument("--endpoint", choices=sorted(PORTS), default=None,
                   help="Named port preset; overrides IB_PORT (default tws-paper = 7497)")
    p.add_argument("--client-id", type=int, default=int(os.getenv("IB_CLIENT_ID", "17")),
                   help="API client id; must be unique per connection (default 17)")
    p.add_argument("--account", default=os.getenv("IB_ACCOUNT"), help="Account code, if the login has more than one")
    p.add_argument("--live", action="store_true", help="Required to trade a non-paper (real money) account")
    return p.parse_args()


def resolve_port(args):
    """--port wins, then an explicit --endpoint preset, then IB_PORT in .env, then tws-paper."""
    if args.port:
        return args.port
    if args.endpoint:
        return PORTS[args.endpoint]
    if os.getenv("IB_PORT"):
        return int(os.getenv("IB_PORT"))
    return PORTS["tws-paper"]


def connect(ib, host, port, client_id):
    print(f"Connecting to {host}:{port} as client {client_id} ...")
    try:
        ib.connect(host, port, clientId=client_id, timeout=15)
    except (ConnectionRefusedError, OSError) as exc:
        sys.exit(
            f"Error: could not reach TWS/IB Gateway at {host}:{port} ({exc}).\n"
            "  - Is TWS or IB Gateway running and logged in?\n"
            "  - Global Configuration -> API -> Settings: 'Enable ActiveX and Socket Clients' checked,\n"
            "    'Socket port' matching the port above, and this machine in 'Trusted IPs'.\n"
            f"  - Ports: {', '.join(f'{k}={v}' for k, v in sorted(PORTS.items()))}"
        )
    print(f"Connected. Server version {ib.client.serverVersion()}.")


def choose_account(ib, requested):
    """Returns the account code to trade, refusing to guess when the login has several."""
    accounts = ib.managedAccounts()
    if not accounts:
        sys.exit("Error: the connection reported no managed accounts.")
    if requested:
        if requested not in accounts:
            sys.exit(f"Error: account {requested} is not in this login's accounts: {', '.join(accounts)}")
        return requested
    if len(accounts) > 1:
        sys.exit(f"Error: this login has several accounts. Pass --account with one of: {', '.join(accounts)}")
    return accounts[0]


def guard_live_account(ib, account, live_flag):
    """Requirement 2: a non-DU account is real money and needs --live plus a typed confirmation."""
    is_paper = account.upper().startswith("DU")
    kind = "PAPER" if is_paper else "LIVE (real money)"
    print(f"Account {account} is a {kind} account.")

    if is_paper:
        if live_flag:
            print("Note: --live was passed but this is a paper account; continuing.")
        return

    if not live_flag:
        ib.disconnect()
        sys.exit(
            f"Refusing to place an order on live account {account} without --live.\n"
            "Point the script at your paper account first (--endpoint tws-paper, port 7497),\n"
            "or re-run with --live if you really mean to trade real money."
        )

    if not sys.stdin.isatty():
        ib.disconnect()
        sys.exit("Refusing to trade a live account without an interactive confirmation.")

    answer = input(f"Type the account code {account} to confirm a REAL order: ").strip()
    if answer != account:
        ib.disconnect()
        sys.exit("Confirmation did not match. Nothing was sent.")


def show_account(ib, account):
    """Requirement 3: print enough of the account to see it is the one intended."""
    wanted = {"NetLiquidation", "TotalCashValue", "BuyingPower", "AvailableFunds"}
    values = {v.tag: (v.value, v.currency) for v in ib.accountSummary(account) if v.tag in wanted}
    print("Account summary:")
    for tag in ("NetLiquidation", "TotalCashValue", "BuyingPower", "AvailableFunds"):
        if tag in values:
            value, currency = values[tag]
            print(f"  {tag:<16} {float(value):>15,.2f} {currency}")


def get_price(ib, contract):
    """Requirement 4: last/close price, using delayed data when there is no live subscription."""
    ib.reqMarketDataType(1)  # 1 = live
    ticker = ib.reqMktData(contract, "", False, False)
    ib.sleep(3)

    price = ticker.marketPrice()
    if price != price or price <= 0:  # NaN or unset -> no live subscription
        print("No live market data; falling back to delayed (market data type 3).")
        ib.reqMarketDataType(3)
        ib.sleep(3)
        price = ticker.marketPrice()

    if price != price or price <= 0:
        price = ticker.close

    ib.cancelMktData(contract)

    if price is None or price != price or price <= 0:
        return None
    return float(price)


def build_order(args, price):
    """Requirement 5: default to a limit that rests away from the market."""
    if args.order_type == "MKT":
        order = MarketOrder(args.action, args.quantity)
        print(f"Order: MARKET {args.action} {args.quantity} {args.symbol} -- this is expected to FILL.")
    else:
        if args.limit_price is not None:
            limit = args.limit_price
        elif price is None:
            sys.exit("Error: no price available for the default limit. Pass --limit-price explicitly.")
        else:
            factor = (1 - args.offset_pct / 100) if args.action == "BUY" else (1 + args.offset_pct / 100)
            limit = round(price * factor, 2)
        order = LimitOrder(args.action, args.quantity, limit)
        print(f"Order: LIMIT {args.action} {args.quantity} {args.symbol} @ {limit:.2f}"
              f"{'' if args.limit_price is not None else f' ({args.offset_pct:g}% away from {price:.2f})'}")

    order.account = args.account
    order.outsideRth = args.outside_rth
    order.tif = "DAY"
    return order


def is_set(value):
    """IB leaves unset doubles at DBL_MAX (1.79e308); treat those and NaN as missing."""
    return value is not None and value == value and abs(value) < 1e300


def run_what_if(ib, contract, order):
    """Requirement 6: margin and commission impact, without sending the order."""
    print("\n--what-if: asking IB for the impact of this order (nothing will be placed).")
    state = ib.whatIfOrder(contract, order)
    if not state:
        print("  IB returned no what-if state (the order may have been rejected as invalid).")
        return
    for label, value in (
        ("Status", state.status),
        ("Initial margin change", state.initMarginChange),
        ("Maintenance margin change", state.maintMarginChange),
        ("Equity with loan change", state.equityWithLoanChange),
        ("Commission", f"{state.commission} {state.commissionCurrency}" if is_set(state.commission) else ""),
        ("Warning", state.warningText),
    ):
        if value:
            print(f"  {label:<26} {value}")


def place_and_watch(ib, contract, order, args):
    """Requirement 7: place, report each status transition, then cancel unless --keep."""
    trade = ib.placeOrder(contract, order)
    print(f"\nOrder submitted at {datetime.now(ET):%Y-%m-%d %H:%M:%S %Z}.")

    seen = set()
    for _ in range(args.wait * 2):
        ib.sleep(0.5)
        status = trade.orderStatus.status
        if status not in seen:
            seen.add(status)
            print(f"  status: {status:<16} filled={trade.orderStatus.filled} "
                  f"remaining={trade.orderStatus.remaining} avgFill={trade.orderStatus.avgFillPrice}")
        if trade.isDone():
            break

    print(f"  IB order id: {trade.order.orderId}, perm id: {trade.order.permId}")
    for entry in trade.log:
        print(f"  log: {entry.time:%H:%M:%S} {entry.status} {entry.message or ''}".rstrip())

    if trade.orderStatus.status == "Filled":
        print(f"FILLED {trade.orderStatus.filled} @ {trade.orderStatus.avgFillPrice}")
        for fill in trade.fills:
            comm = fill.commissionReport
            detail = (f" commission {comm.commission} {comm.currency}"
                      if is_set(comm.commission) else "")
            print(f"  fill {fill.execution.shares} @ {fill.execution.price}{detail}")
        return

    if trade.isDone():
        print(f"Order finished as {trade.orderStatus.status}.")
        return

    if args.keep:
        print("Order left working (--keep). Cancel it in TWS when you are done.")
        return

    print("Cancelling the test order ...")
    ib.cancelOrder(trade.order)
    for _ in range(20):
        ib.sleep(0.5)
        if trade.orderStatus.status in ("Cancelled", "ApiCancelled", "Filled"):
            break
    print(f"Final status: {trade.orderStatus.status}")


def main():
    args = parse_args()
    port = resolve_port(args)

    ib = IB()
    connect(ib, args.host, port, args.client_id)
    try:
        args.account = choose_account(ib, args.account)
        guard_live_account(ib, args.account, args.live)
        show_account(ib, args.account)

        contract = Stock(args.symbol, "SMART", "USD")
        if not ib.qualifyContracts(contract):
            sys.exit(f"Error: IB could not resolve symbol '{args.symbol}' as a US stock.")
        print(f"\nContract: {contract.symbol} conId={contract.conId} "
              f"exchange={contract.exchange} primary={contract.primaryExchange}")

        price = get_price(ib, contract)
        print(f"Price: {price:.2f}" if price else "Price: unavailable (market closed or no data permission)")

        order = build_order(args, price)

        if args.what_if:
            run_what_if(ib, contract, order)
        else:
            place_and_watch(ib, contract, order, args)

        print("\nOpen orders now:", ib.reqOpenOrders() or "none")
        positions = [p for p in ib.positions(args.account) if p.contract.symbol == args.symbol]
        print(f"Position in {args.symbol}:", positions or "none")
    finally:
        ib.disconnect()
        print("Disconnected.")


if __name__ == "__main__":
    main()
