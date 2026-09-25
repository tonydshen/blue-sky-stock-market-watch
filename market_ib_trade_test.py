# market_ib_trade_test.py
# Revision history
# Created on 09/25/26 - First connectivity + order test against the Interactive Brokers TWS API.
# revised on 09/25/26 - Replaced the TWS API (ib_async socket client) with the Client Portal Web API
#                       (REST over HTTPS against the locally running Client Portal Gateway).
#                       The previous ib_async version is in git at commit c977754.
# Requirements, 09/25/26:
# 1. Talk to the Client Portal Gateway over REST at https://127.0.0.1:5000/v1/api (IB_WEBAPI_URL in .env).
#    The gateway uses a self-signed certificate, so TLS verification is disabled for that host only.
# 2. Check the session first and explain the browser login when the gateway is not authenticated;
#    every endpoint returns 401 until the user logs in at https://localhost:5000 and the SSO completes.
# 3. Refuse to touch a live account unless --live is passed AND the user types a confirmation.
#    IB paper accounts start with "DU"; anything else is treated as real money.
# 4. Report which account is connected and its net liquidation / buying power.
# 5. Resolve the symbol to a conid and read a current price from the market data snapshot,
#    tolerating the prefixed values IB returns ("C12.34" = previous close) and the empty first snapshot.
# 6. Place one small order. Default is a limit order priced away from the market so the full
#    lifecycle (submit -> open -> cancel) can be exercised without an unintended fill.
# 7. Answer the gateway's order confirmation prompts (/iserver/reply) instead of stalling on them.
# 8. Offer --what-if as a no-order dry run: IB returns margin and commission impact only.
# 9. Print the order status, then cancel the order unless --keep is passed.
#
import os
import sys
import time
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import urllib3
from dotenv import load_dotenv

load_dotenv()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ET = ZoneInfo("America/New_York")

# Use 127.0.0.1 rather than "localhost": under some proxy/WSL setups "localhost" is resolved
# or intercepted elsewhere and the request never reaches the gateway.
DEFAULT_BASE_URL = "https://127.0.0.1:5000/v1/api"

DEFAULT_SYMBOL = "F"        # cheap, very liquid; one share is a few dollars
DEFAULT_QUANTITY = 1
DEFAULT_OFFSET_PCT = 5.0    # limit this far below the market on a BUY, so it rests unfilled

# Snapshot field ids: 31 last, 84 bid, 86 ask, 7295 open, 7296 close
SNAPSHOT_FIELDS = "31,84,86,7295,7296"

LOGIN_HELP = (
    "The gateway is running but this session is not authenticated.\n"
    "\n"
    "If you have not logged in yet:\n"
    "  1. Open https://localhost:5000 in a browser on this machine.\n"
    "  2. Accept the self-signed certificate warning.\n"
    "  3. Log in with your IB username and password.\n"
    "  4. Wait for 'Client login succeeds', then re-run this script.\n"
    "The session also expires after inactivity, so this can appear on a gateway that worked earlier.\n"
    "\n"
    "If you DID log in and still get this, the browser login can succeed while IB separately\n"
    "denies the gateway's own entitlement check. Check the gateway log:\n"
    "  grep -E 'Client login succeeds|Access Denied|CP_LOGIN_FAILED' <gateway>/logs/gw.*.log\n"
    "'Client login succeeds' followed by 'sso/validate ... Access Denied' means the credentials\n"
    "were accepted but the account is not entitled to the Web API -- not a problem in this script.\n"
    "Usual causes: a paper username (paper logins have limited Client Portal access), or an\n"
    "account that is not fully approved/funded yet. Try the live username, or use the TWS API."
)


class GatewayError(RuntimeError):
    pass


class IBWebAPI:
    """Thin wrapper over the Client Portal Web API endpoints this script needs."""

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = False  # gateway ships a self-signed cert (requirement 1)

    def request(self, method, endpoint, **kwargs):
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        kwargs.setdefault("timeout", 20)
        try:
            response = self.session.request(method, url, **kwargs)
        except requests.exceptions.SSLError as exc:
            raise GatewayError(f"TLS error talking to {url}: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise GatewayError(
                f"Could not reach the Client Portal Gateway at {url} ({exc}).\n"
                "  - Is the gateway running?  cd clientportal.gw && bin/run.sh root/conf.yaml\n"
                "  - It listens on port 5000 by default; set IB_WEBAPI_URL in .env if you changed it."
            ) from exc

        if response.status_code == 401:
            raise GatewayError(LOGIN_HELP)
        if response.status_code >= 400:
            raise GatewayError(f"{method} {endpoint} -> HTTP {response.status_code}: {response.text[:400]}")

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise GatewayError(f"{method} {endpoint} returned non-JSON: {response.text[:400]}") from None

    def get(self, endpoint, **kwargs):
        return self.request("GET", endpoint, **kwargs)

    def post(self, endpoint, **kwargs):
        return self.request("POST", endpoint, **kwargs)

    def delete(self, endpoint, **kwargs):
        return self.request("DELETE", endpoint, **kwargs)


def parse_args():
    p = argparse.ArgumentParser(
        description="Place one small test trade through the Interactive Brokers Client Portal Web API."
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
    p.add_argument("--url", default=os.getenv("IB_WEBAPI_URL", DEFAULT_BASE_URL),
                   help=f"Gateway base URL (default {DEFAULT_BASE_URL})")
    p.add_argument("--account", default=os.getenv("IB_ACCOUNT"), help="Account id, if the login has more than one")
    p.add_argument("--live", action="store_true", help="Required to trade a non-paper (real money) account")
    return p.parse_args()


def check_auth(api):
    """Requirement 2: confirm the SSO session before anything else."""
    status = api.post("iserver/auth/status")
    if not isinstance(status, dict):
        raise GatewayError(f"Unexpected auth status payload: {status!r}")

    authenticated = status.get("authenticated", False)
    connected = status.get("connected", False)
    competing = status.get("competing", False)

    print(f"Gateway session: authenticated={authenticated} connected={connected} competing={competing}")

    if competing:
        raise GatewayError(
            "Another session is competing for this login (TWS, the mobile app or IB's website is "
            "logged in with the same user). Log that one out and re-run."
        )

    if authenticated:
        return

    # A browser SSO login leaves the gateway authenticated but without a brokerage session;
    # ssodh/init opens that session. A session that merely lapsed needs reauthenticate instead.
    for endpoint, payload in (("iserver/auth/ssodh/init", {"publish": True, "compete": True}),
                              ("iserver/reauthenticate", None)):
        print(f"Not authenticated; attempting /{endpoint} ...")
        try:
            api.post(endpoint, json=payload) if payload else api.post(endpoint)
        except GatewayError:
            continue
        for _ in range(8):
            time.sleep(1)
            retry = api.post("iserver/auth/status")
            if isinstance(retry, dict) and retry.get("authenticated"):
                print("Session established.")
                return

    raise GatewayError(LOGIN_HELP)


def choose_account(api, requested):
    """Returns the account id to trade. /iserver/accounts must be called before any order endpoint."""
    payload = api.get("iserver/accounts")
    accounts = (payload or {}).get("accounts") or []
    if not accounts:
        raise GatewayError("The gateway reported no accounts for this login.")

    if requested:
        if requested not in accounts:
            raise GatewayError(f"Account {requested} is not in this login's accounts: {', '.join(accounts)}")
        return requested
    if len(accounts) > 1:
        raise GatewayError(f"This login has several accounts. Pass --account with one of: {', '.join(accounts)}")
    return accounts[0]


def guard_live_account(account, live_flag, dry_run=False):
    """Requirement 3: a non-DU account is real money and needs --live plus a typed confirmation.

    dry_run is the --what-if case: /orders/whatif is a preview endpoint that sends no order,
    so it is allowed to run against a live account without --live.
    """
    is_paper = account.upper().startswith("DU")
    print(f"Account {account} is a {'PAPER' if is_paper else 'LIVE (real money)'} account.")

    if is_paper:
        if live_flag:
            print("Note: --live was passed but this is a paper account; continuing.")
        return

    if dry_run:
        print("--what-if on a live account: this is a preview only, no order will be sent.")
        return

    if not live_flag:
        sys.exit(
            f"Refusing to place an order on live account {account} without --live.\n"
            "Log the gateway into your paper account first, or re-run with --live if you\n"
            "really mean to trade real money."
        )

    if not sys.stdin.isatty():
        sys.exit("Refusing to trade a live account without an interactive confirmation.")

    if input(f"Type the account id {account} to confirm a REAL order: ").strip() != account:
        sys.exit("Confirmation did not match. Nothing was sent.")


def show_account(api, account):
    """Requirement 4: print enough of the account to see it is the one intended."""
    try:
        summary = api.get(f"portfolio/{account}/summary")
    except GatewayError as exc:
        print(f"(account summary unavailable: {exc})")
        return

    print("Account summary:")
    for key, label in (("netliquidation", "Net liquidation"), ("totalcashvalue", "Total cash"),
                       ("buyingpower", "Buying power"), ("availablefunds", "Available funds")):
        entry = (summary or {}).get(key)
        if isinstance(entry, dict) and entry.get("amount") is not None:
            print(f"  {label:<16} {entry['amount']:>15,.2f} {entry.get('currency', '')}")


def find_conid(api, symbol):
    """Requirement 5: resolve the symbol to an IB contract id."""
    results = api.post("iserver/secdef/search", json={"symbol": symbol, "name": False, "secType": "STK"})
    if not results:
        raise GatewayError(f"No contract found for symbol '{symbol}'.")

    for row in results:
        if row.get("conid") and (row.get("symbol") or "").upper() == symbol.upper():
            return int(row["conid"]), row.get("companyName") or row.get("companyHeader") or ""

    first = results[0]
    if not first.get("conid"):
        raise GatewayError(f"No usable contract id for symbol '{symbol}'.")
    return int(first["conid"]), first.get("companyName") or ""


def parse_price(raw):
    """IB prefixes snapshot values, e.g. 'C12.34' (previous close) or 'H12.34' (halted)."""
    if raw is None:
        return None
    text = str(raw).strip().lstrip("CHBAtc").replace(",", "")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


def get_price(api, conid):
    """Requirement 5: the first snapshot call often returns an empty shell, so ask twice."""
    snapshot = None
    for _ in range(4):
        snapshot = api.get("iserver/marketdata/snapshot",
                           params={"conids": str(conid), "fields": SNAPSHOT_FIELDS})
        if snapshot and any(field in snapshot[0] for field in ("31", "84", "86", "7296")):
            break
        time.sleep(1.5)

    if not snapshot:
        return None

    row = snapshot[0]
    for field in ("31", "7296", "84", "86"):  # last, close, bid, ask
        price = parse_price(row.get(field))
        if price:
            return price
    return None


def build_order(args, conid, price):
    """Requirement 6: default to a limit that rests away from the market."""
    order = {
        "conid": conid,
        "orderType": args.order_type,
        "side": args.action,
        "quantity": args.quantity,
        "tif": "DAY",
        "outsideRTH": args.outside_rth,
        "cOID": f"test-{int(time.time())}",
    }

    if args.order_type == "MKT":
        print(f"Order: MARKET {args.action} {args.quantity} {args.symbol} -- this is expected to FILL.")
        return order

    if args.limit_price is not None:
        limit = args.limit_price
    elif price is None:
        sys.exit("Error: no price available for the default limit. Pass --limit-price explicitly.")
    else:
        factor = (1 - args.offset_pct / 100) if args.action == "BUY" else (1 + args.offset_pct / 100)
        limit = round(price * factor, 2)

    order["price"] = limit
    detail = "" if args.limit_price is not None else f" ({args.offset_pct:g}% away from {price:.2f})"
    print(f"Order: LIMIT {args.action} {args.quantity} {args.symbol} @ {limit:.2f}{detail}")
    return order


def answer_confirmations(api, response, max_replies=5):
    """Requirement 7: the gateway returns confirmation prompts that must be replied to."""
    for _ in range(max_replies):
        if not isinstance(response, list) or not response:
            return response
        first = response[0]
        if not isinstance(first, dict) or "id" not in first or "message" not in first:
            return response
        for line in first.get("message", []):
            print(f"  gateway asks: {line}")
        response = api.post(f"iserver/reply/{first['id']}", json={"confirmed": True})
        print("  replied: confirmed")
    return response


def run_what_if(api, account, order):
    """Requirement 8: margin and commission impact, without sending the order."""
    print("\n--what-if: asking IB for the impact of this order (nothing will be placed).")
    result = api.post(f"iserver/account/{account}/orders/whatif", json={"orders": [order]})
    if not isinstance(result, dict):
        print(f"  Unexpected what-if payload: {result!r}")
        return

    if result.get("error"):
        print(f"  Rejected: {result['error']}")
        return

    amount = result.get("amount") or {}
    for label, key in (("Order value", "amount"), ("Commission", "commission"), ("Total", "total")):
        if amount.get(key):
            print(f"  {label:<26} {amount[key]}")

    for label, key in (("Initial margin", "initial"), ("Maintenance margin", "maintenance")):
        section = result.get(key) or {}
        if section.get("change"):
            print(f"  {label + ' change':<26} {section['change']}")

    for warning in (result.get("warn"), result.get("warning")):
        if warning:
            print(f"  Warning: {warning}")


def place_and_watch(api, account, order, args):
    """Requirement 9: place, report status, then cancel unless --keep."""
    response = api.post(f"iserver/account/{account}/orders", json={"orders": [order]})
    response = answer_confirmations(api, response)

    if isinstance(response, dict) and response.get("error"):
        sys.exit(f"Order rejected: {response['error']}")
    if not isinstance(response, list) or not response:
        sys.exit(f"Unexpected order response: {response!r}")

    placed = response[0]
    order_id = placed.get("order_id") or placed.get("orderId")
    if not order_id:
        sys.exit(f"Order response carried no order id: {placed!r}")

    print(f"\nOrder submitted at {datetime.now(ET):%Y-%m-%d %H:%M:%S %Z}. order_id={order_id}")

    seen = set()
    status = placed.get("order_status", "")
    deadline = time.time() + args.wait
    while time.time() < deadline:
        try:
            detail = api.get(f"iserver/account/order/status/{order_id}")
        except GatewayError as exc:
            print(f"  (status poll failed: {exc})")
            break

        status = (detail or {}).get("order_status", status)
        if status not in seen:
            seen.add(status)
            print(f"  status: {status:<16} filled={detail.get('cum_fill')} "
                  f"size={detail.get('total_size')} avgFill={detail.get('average_price')}")
        if status in ("Filled", "Cancelled", "ApiCancelled", "Rejected", "Inactive"):
            break
        time.sleep(1)

    if status == "Filled":
        print(f"FILLED. Check the position below.")
        return
    if status in ("Cancelled", "ApiCancelled", "Rejected", "Inactive"):
        print(f"Order finished as {status}.")
        return

    if args.keep:
        print("Order left working (--keep). Cancel it in Client Portal when you are done.")
        return

    print("Cancelling the test order ...")
    try:
        cancelled = api.delete(f"iserver/account/{account}/order/{order_id}")
        print(f"  cancel response: {cancelled}")
    except GatewayError as exc:
        print(f"  cancel failed: {exc}")


def main():
    args = parse_args()
    api = IBWebAPI(args.url)
    print(f"Gateway: {api.base_url}")

    try:
        check_auth(api)
        account = choose_account(api, args.account)
        guard_live_account(account, args.live, dry_run=args.what_if)
        show_account(api, account)

        conid, name = find_conid(api, args.symbol)
        print(f"\nContract: {args.symbol} conid={conid} {name}")

        price = get_price(api, conid)
        print(f"Price: {price:.2f}" if price else "Price: unavailable (market closed or no data permission)")

        order = build_order(args, conid, price)

        if args.what_if:
            run_what_if(api, account, order)
        else:
            place_and_watch(api, account, order, args)

        live_orders = api.get("iserver/account/orders") or {}
        working = [o for o in live_orders.get("orders", [])
                   if o.get("status") not in ("Filled", "Cancelled", "Inactive")]
        print("\nWorking orders now:", working or "none")

        positions = api.get(f"portfolio/{account}/positions/0") or []
        mine = [p for p in positions if p.get("conid") == conid]
        print(f"Position in {args.symbol}:", mine or "none")
    except GatewayError as exc:
        sys.exit(f"\nError: {exc}")


if __name__ == "__main__":
    main()
