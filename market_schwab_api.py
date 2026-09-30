# market_schwab_api.py
# Revision history
# Created on 09/28/26 - Schwab Trader API client (OAuth 2) for quotes, account data and orders.
#                       Alternative to the IB Client Portal Gateway (market_ib_trade_test.py), which
#                       needs an interactive browser login on a locally running gateway for every session.
# Requirements, 09/28/26:
# 1. Individual-developer access to the Schwab Trader API (developer.schwab.com) using OAuth 2
#    authorization-code flow: app key/secret/callback URL come from .env
#    (SCHWAB_APP_KEY, SCHWAB_APP_SECRET, SCHWAB_CALLBACK_URL). No gateway process.
# 2. One-time interactive "login": print the authorize URL, the user signs in at Schwab and pastes the
#    redirected URL back; the code in it is exchanged for access + refresh tokens.
# 3. Tokens persist in a JSON file outside the repo (SCHWAB_TOKEN_FILE, default ~/.schwab/token.json,
#    mode 600). The access token (30 min) is refreshed automatically; the refresh token lasts 7 days
#    from login and Schwab does not extend it, so a human re-login is needed once a week.
#    The "token" command reports the remaining time and exits non-zero near expiry so cron can alert.
# 4. Several scripts/processes may share the token file: refresh under a file lock.
# 5. Look-ups: accounts/balances/positions, quotes, price history, market hours, instrument search,
#    orders list and single-order status.
# 6. Trading: equity MARKET / LIMIT / STOP / STOP_LIMIT orders, BUY / SELL / SELL_SHORT / BUY_TO_COVER,
#    cancel, and an optional watch-then-cancel mode for testing (--cancel-after).
# 7. Schwab has NO paper trading: every order is real money. Print the order and require a typed "yes"
#    unless --yes is given (for unattended automation). --dry-run prints the order JSON without sending.
# 8. Accounts are addressed by an encrypted hash in the API; map the plain account number
#    (or its last digits, SCHWAB_ACCOUNT / --account) to the hash automatically.
# 9. Retry once on 401 after a forced token refresh, and back off on 429 (rate limit 120 req/min).
# 10. SchwabClient is importable so other scripts (e.g. market_event_agents.py) can trade without the CLI.
#
# Usage:
#   uv run market_schwab_api.py login
#   uv run market_schwab_api.py token
#   uv run market_schwab_api.py accounts
#   uv run market_schwab_api.py quote AAPL MSFT F
#   uv run market_schwab_api.py history AAPL --period-type month --period 3
#   uv run market_schwab_api.py orders --days 7
#   uv run market_schwab_api.py order BUY F 1 --type LIMIT --offset-pct 5 --cancel-after 15
#   uv run market_schwab_api.py cancel <order_id>
#
import os
import sys
import json
import time
import fcntl
import base64
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

ET = ZoneInfo("America/New_York")

AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
TRADER_URL = "https://api.schwabapi.com/trader/v1"
MARKET_URL = "https://api.schwabapi.com/marketdata/v1"

DEFAULT_TOKEN_FILE = Path.home() / ".schwab" / "token.json"
REFRESH_TOKEN_LIFETIME = 7 * 24 * 3600   # fixed by Schwab, counted from the interactive login
ACCESS_TOKEN_MARGIN = 60                 # refresh this many seconds before the access token expires
REFRESH_WARN_SECONDS = 24 * 3600         # "token" exits 2 when less than this remains

ORDER_TYPES = ["MARKET", "LIMIT", "STOP", "STOP_LIMIT"]
INSTRUCTIONS = ["BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER"]
TERMINAL_STATUSES = {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "REPLACED"}


class SchwabError(RuntimeError):
    pass


class LoginRequired(SchwabError):
    pass


def now_et():
    return datetime.now(ET).strftime("%m/%d/%Y %H:%M:%S ET")


def fmt_epoch(ts):
    return datetime.fromtimestamp(ts, ET).strftime("%m/%d/%Y %H:%M ET")


def fmt_duration(seconds):
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    return f"{days}d {hours}h {rem // 60}m"


# ---------------------------------------------------------------------------------------------
# OAuth 2 token handling
# ---------------------------------------------------------------------------------------------

class SchwabAuth:
    """Authorization-code login, token persistence and automatic access-token refresh."""

    def __init__(self, app_key, app_secret, callback_url, token_file):
        if not (app_key and app_secret and callback_url):
            raise SchwabError(
                "SCHWAB_APP_KEY, SCHWAB_APP_SECRET and SCHWAB_CALLBACK_URL must be set in .env.\n"
                "They are on your app's page at https://developer.schwab.com (Dashboard -> Apps)."
            )
        self.app_key = app_key
        self.app_secret = app_secret
        self.callback_url = callback_url
        self.token_file = Path(token_file).expanduser()
        self.lock_file = self.token_file.with_suffix(".lock")

    def authorize_url(self):
        return f"{AUTH_URL}?{urlencode({'client_id': self.app_key, 'redirect_uri': self.callback_url})}"

    def _post_token(self, data):
        basic = base64.b64encode(f"{self.app_key}:{self.app_secret}".encode()).decode()
        try:
            response = requests.post(
                TOKEN_URL,
                headers={"Authorization": f"Basic {basic}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data=data,
                timeout=20,
            )
        except requests.exceptions.RequestException as exc:
            raise SchwabError(f"Could not reach {TOKEN_URL}: {exc}") from exc
        if response.status_code != 200:
            text = response.text[:400]
            if data.get("grant_type") == "refresh_token" and response.status_code in (400, 401):
                raise LoginRequired(
                    f"Schwab rejected the refresh token (HTTP {response.status_code}: {text}).\n"
                    "Run:  uv run market_schwab_api.py login"
                )
            raise SchwabError(f"Token request failed: HTTP {response.status_code}: {text}")
        return response.json()

    def _load(self):
        if not self.token_file.exists():
            raise LoginRequired(
                f"No token file at {self.token_file}.\nRun:  uv run market_schwab_api.py login"
            )
        with open(self.token_file) as f:
            return json.load(f)

    def _save(self, token, refresh_expires_at):
        data = {
            "access_token": token["access_token"],
            "refresh_token": token["refresh_token"],
            "id_token": token.get("id_token"),
            "scope": token.get("scope"),
            "expires_at": time.time() + int(token.get("expires_in", 1800)),
            "refresh_expires_at": refresh_expires_at,
        }
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_file.with_suffix(".tmp")
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.token_file)
        return data

    def _locked(self):
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_file, "w")
        fcntl.flock(handle, fcntl.LOCK_EX)
        return handle  # released when closed

    def login_with_redirect(self, redirected_url):
        query = parse_qs(urlparse(redirected_url.strip()).query)  # also decodes "%40" -> "@"
        if "error" in query:
            raise SchwabError(f"Schwab returned an error: {query['error'][0]}")
        if "code" not in query:
            raise SchwabError("No ?code= in that URL. Paste the full address from the browser bar.")
        token = self._post_token({
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": self.callback_url,
        })
        with self._locked():
            return self._save(token, time.time() + REFRESH_TOKEN_LIFETIME)

    def status(self):
        data = self._load()
        return {
            "access_expires_at": data["expires_at"],
            "refresh_expires_at": data["refresh_expires_at"],
            "refresh_remaining": data["refresh_expires_at"] - time.time(),
        }

    def access_token(self, force_refresh=False):
        with self._locked():
            data = self._load()
            now = time.time()
            if not force_refresh and data["expires_at"] - ACCESS_TOKEN_MARGIN > now:
                return data["access_token"]
            if data["refresh_expires_at"] <= now:
                raise LoginRequired(
                    f"The refresh token expired on {fmt_epoch(data['refresh_expires_at'])} "
                    "(Schwab limits it to 7 days).\nRun:  uv run market_schwab_api.py login"
                )
            token = self._post_token({"grant_type": "refresh_token",
                                      "refresh_token": data["refresh_token"]})
            # Refreshing does not restart Schwab's 7-day clock, so keep the original expiry.
            return self._save(token, data["refresh_expires_at"])["access_token"]


# ---------------------------------------------------------------------------------------------
# REST client
# ---------------------------------------------------------------------------------------------

class SchwabClient:
    """Thin wrapper over the Trader API (accounts/orders) and Market Data API endpoints."""

    def __init__(self, auth):
        self.auth = auth
        self.session = requests.Session()
        self._hash_by_number = None

    @classmethod
    def from_env(cls):
        return cls(SchwabAuth(
            os.getenv("SCHWAB_APP_KEY"),
            os.getenv("SCHWAB_APP_SECRET"),
            os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1"),
            os.getenv("SCHWAB_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)),
        ))

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 20)
        force_refresh = False
        for attempt in range(4):
            headers = {"Authorization": f"Bearer {self.auth.access_token(force_refresh)}",
                       "Accept": "application/json"}
            try:
                response = self.session.request(method, url, headers=headers, **kwargs)
            except requests.exceptions.RequestException as exc:
                raise SchwabError(f"{method} {url} failed: {exc}") from exc
            if response.status_code == 401 and not force_refresh:
                force_refresh = True  # token revoked or clock skew: refresh once and retry
                continue
            if response.status_code == 429:
                time.sleep(float(response.headers.get("Retry-After", 2 ** attempt)))
                continue
            if response.status_code >= 400:
                raise SchwabError(f"{method} {url} -> HTTP {response.status_code}: {response.text[:600]}")
            return response
        raise SchwabError(f"{method} {url} -> HTTP {response.status_code} after retries: {response.text[:300]}")

    def get_json(self, url, params=None):
        response = self.request("GET", url, params=params)
        return response.json() if response.content else None

    # ---- market data ----

    def quotes(self, symbols, fields="quote,reference"):
        return self.get_json(f"{MARKET_URL}/quotes",
                             {"symbols": ",".join(s.upper() for s in symbols), "fields": fields})

    def last_price(self, symbol):
        data = self.quotes([symbol], fields="quote").get(symbol.upper())
        if not data:
            raise SchwabError(f"No quote for {symbol}")
        q = data["quote"]
        price = q.get("lastPrice") or q.get("mark") or q.get("closePrice")
        if not price:
            raise SchwabError(f"Quote for {symbol} has no usable price: {q}")
        return float(price)

    def price_history(self, symbol, period_type="month", period=1, frequency_type="daily", frequency=1,
                      extended_hours=False):
        return self.get_json(f"{MARKET_URL}/pricehistory", {
            "symbol": symbol.upper(), "periodType": period_type, "period": period,
            "frequencyType": frequency_type, "frequency": frequency,
            "needExtendedHoursData": str(extended_hours).lower(),
            # Without an explicit end time Schwab stops at the previous session's close.
            "endDate": int(time.time() * 1000),
        })

    def market_hours(self, markets="equity", date=None):
        params = {"markets": markets}
        if date:
            params["date"] = date
        return self.get_json(f"{MARKET_URL}/markets", params)

    def search(self, symbol, projection="symbol-search"):
        return self.get_json(f"{MARKET_URL}/instruments", {"symbol": symbol, "projection": projection})

    # ---- accounts ----

    def account_numbers(self):
        """[{'accountNumber': '12345678', 'hashValue': '...'}]"""
        return self.get_json(f"{TRADER_URL}/accounts/accountNumbers")

    def account_hash(self, account=None):
        """Map a plain account number (or its trailing digits) to the encrypted hash the API needs."""
        if self._hash_by_number is None:
            self._hash_by_number = {a["accountNumber"]: a["hashValue"] for a in self.account_numbers()}
        numbers = list(self._hash_by_number)
        if account:
            matches = [n for n in numbers if n.endswith(str(account))]
        else:
            matches = numbers
        if len(matches) == 1:
            return self._hash_by_number[matches[0]]
        masked = ", ".join("..." + n[-4:] for n in numbers)
        if not matches:
            raise SchwabError(f"Account '{account}' not found. Linked accounts: {masked}")
        raise SchwabError(f"Several accounts linked ({masked}); pass --account or set SCHWAB_ACCOUNT.")

    def accounts(self, positions=True):
        return self.get_json(f"{TRADER_URL}/accounts", {"fields": "positions"} if positions else None)

    def account(self, account_hash, positions=True):
        return self.get_json(f"{TRADER_URL}/accounts/{account_hash}",
                             {"fields": "positions"} if positions else None)

    # ---- orders ----

    def orders(self, account_hash, days=1, status=None):
        to_time = datetime.now(timezone.utc)
        params = {
            "fromEnteredTime": (to_time - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "toEnteredTime": to_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }
        if status:
            params["status"] = status
        return self.get_json(f"{TRADER_URL}/accounts/{account_hash}/orders", params)

    def order(self, account_hash, order_id):
        return self.get_json(f"{TRADER_URL}/accounts/{account_hash}/orders/{order_id}")

    def place_order(self, account_hash, order):
        """Returns the new order id, taken from the Location header (the body is empty)."""
        response = self.request("POST", f"{TRADER_URL}/accounts/{account_hash}/orders", json=order)
        location = response.headers.get("Location", "")
        return location.rstrip("/").rsplit("/", 1)[-1] or None

    def replace_order(self, account_hash, order_id, order):
        response = self.request("PUT", f"{TRADER_URL}/accounts/{account_hash}/orders/{order_id}", json=order)
        location = response.headers.get("Location", "")
        return location.rstrip("/").rsplit("/", 1)[-1] or None

    def cancel_order(self, account_hash, order_id):
        self.request("DELETE", f"{TRADER_URL}/accounts/{account_hash}/orders/{order_id}")


def format_price(price):
    """Schwab rejects sub-penny prices at or above $1; below $1 four decimals are allowed."""
    return f"{price:.2f}" if price >= 1 else f"{price:.4f}"


def build_equity_order(symbol, instruction, quantity, order_type="MARKET", price=None, stop_price=None,
                       duration="DAY", session="NORMAL"):
    instruction, order_type = instruction.upper(), order_type.upper()
    if instruction not in INSTRUCTIONS:
        raise SchwabError(f"instruction must be one of {INSTRUCTIONS}")
    if order_type not in ORDER_TYPES:
        raise SchwabError(f"order type must be one of {ORDER_TYPES}")
    if order_type in ("LIMIT", "STOP_LIMIT") and price is None:
        raise SchwabError(f"{order_type} needs a limit price")
    if order_type in ("STOP", "STOP_LIMIT") and stop_price is None:
        raise SchwabError(f"{order_type} needs a stop price")
    if order_type == "MARKET" and session != "NORMAL":
        raise SchwabError("MARKET orders are only accepted in the NORMAL session")

    order = {
        "orderType": order_type,
        "session": session,
        "duration": duration,
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [{
            "instruction": instruction,
            "quantity": quantity,
            "instrument": {"symbol": symbol.upper(), "assetType": "EQUITY"},
        }],
    }
    if price is not None:
        order["price"] = format_price(price)
    if stop_price is not None:
        order["stopPrice"] = format_price(stop_price)
    return order


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

def cmd_login(client, args):
    auth = client.auth
    print("1. Open this URL in a browser and sign in with your Schwab brokerage login:\n")
    print(f"   {auth.authorize_url()}\n")
    print("2. Approve the accounts you want the app to reach.")
    print(f"3. The browser is sent to {auth.callback_url}/?code=... and shows an error page -- that is expected.")
    print("   Copy the FULL address from the browser bar and paste it below.")
    print("   The code expires about 30 seconds after the redirect, so paste promptly.\n")
    redirected = args.redirect_url or input("Redirected URL: ")
    data = auth.login_with_redirect(redirected)
    print(f"\nLogged in. Tokens saved to {auth.token_file}")
    print(f"Refresh token valid until {fmt_epoch(data['refresh_expires_at'])} -- log in again before then.")


def cmd_token(client, args):
    try:
        st = client.auth.status()
    except LoginRequired as exc:
        print(exc)
        return 1
    if args.refresh:
        client.auth.access_token(force_refresh=True)
        st = client.auth.status()
        print("Access token refreshed.")
    print(f"Access token expires:  {fmt_epoch(st['access_expires_at'])}")
    print(f"Refresh token expires: {fmt_epoch(st['refresh_expires_at'])} "
          f"({fmt_duration(st['refresh_remaining'])} left)")
    if st["refresh_remaining"] <= 0:
        print("EXPIRED -- run: uv run market_schwab_api.py login")
        return 1
    if st["refresh_remaining"] < REFRESH_WARN_SECONDS:
        print("Less than a day left -- run: uv run market_schwab_api.py login")
        return 2
    return 0


def cmd_accounts(client, args):
    for item in client.accounts(positions=True):
        acct = item.get("securitiesAccount", {})
        bal = acct.get("currentBalances", {})
        print(f"\nAccount ...{acct.get('accountNumber', '????')[-4:]}  ({acct.get('type')})")
        for label, key in [("Liquidation value", "liquidationValue"), ("Cash", "cashBalance"),
                           ("Buying power", "buyingPower"),
                           ("Cash for trading", "cashAvailableForTrading")]:
            if key in bal:
                print(f"  {label:<18} {bal[key]:>14,.2f}")
        positions = acct.get("positions", [])
        if positions:
            print(f"  {'Symbol':<10}{'Qty':>10}{'Avg cost':>12}{'Mkt value':>14}{'Day P/L':>12}")
            for p in sorted(positions, key=lambda p: -abs(p.get("marketValue", 0))):
                qty = p.get("longQuantity", 0) - p.get("shortQuantity", 0)
                inst = p["instrument"]
                name = inst.get("symbol") or inst.get("cusip") or inst.get("description", "?")[:9]
                print(f"  {name:<10}{qty:>10g}{p.get('averagePrice', 0):>12,.2f}"
                      f"{p.get('marketValue', 0):>14,.2f}{p.get('currentDayProfitLoss', 0):>12,.2f}")


def cmd_quote(client, args):
    data = client.quotes(args.symbols)
    print(f"Quotes at {now_et()}")
    print(f"{'Symbol':<8}{'Last':>10}{'Bid':>10}{'Ask':>10}{'Chg':>9}{'Chg%':>8}{'Volume':>14}  Description")
    for symbol, item in data.items():
        if symbol == "errors":
            continue
        q, ref = item.get("quote", {}), item.get("reference", {})
        print(f"{symbol:<8}{q.get('lastPrice', 0):>10.2f}{q.get('bidPrice', 0):>10.2f}{q.get('askPrice', 0):>10.2f}"
              f"{q.get('netChange', 0):>9.2f}{q.get('netPercentChange', 0):>7.2f}%{q.get('totalVolume', 0):>14,}"
              f"  {ref.get('description', '')}")
    if "errors" in data:
        print(f"Errors: {data['errors']}")


def cmd_history(client, args):
    data = client.price_history(args.symbol, args.period_type, args.period, args.frequency_type,
                                args.frequency, args.extended_hours)
    candles = data.get("candles", [])
    print(f"{data.get('symbol', args.symbol)}: {len(candles)} candles")
    print(f"{'Time (ET)':<18}{'Open':>10}{'High':>10}{'Low':>10}{'Close':>10}{'Volume':>14}")
    # Daily and longer bars are stamped at midnight Central time, so only the date is meaningful.
    time_format = "%m/%d/%Y %H:%M" if args.frequency_type == "minute" else "%m/%d/%Y"
    for c in candles[-args.last:]:
        t = datetime.fromtimestamp(c["datetime"] / 1000, ET).strftime(time_format)
        print(f"{t:<18}{c['open']:>10.2f}{c['high']:>10.2f}{c['low']:>10.2f}{c['close']:>10.2f}{c['volume']:>14,}")


def cmd_hours(client, args):
    print(json.dumps(client.market_hours(args.markets, args.date), indent=2))


def cmd_search(client, args):
    print(json.dumps(client.search(args.symbol, args.projection), indent=2))


def print_order(o):
    legs = ", ".join(f"{leg['instruction']} {leg['quantity']:g} {leg['instrument'].get('symbol')}"
                     for leg in o.get("orderLegCollection", []))
    price = o.get("price", "")
    print(f"  {o.get('orderId')}  {o.get('enteredTime', '')[:19]}  {o.get('status'):<16}{o.get('orderType'):<11}"
          f"{legs}  {('@ ' + str(price)) if price else ''}  filled {o.get('filledQuantity', 0):g}")


def cmd_orders(client, args):
    orders = client.orders(client.account_hash(args.account), args.days, args.status)
    print(f"{len(orders)} order(s) in the last {args.days} day(s)")
    for o in orders:
        print_order(o)


def cmd_status(client, args):
    o = client.order(client.account_hash(args.account), args.order_id)
    if args.json:
        print(json.dumps(o, indent=2))
    else:
        print_order(o)


def cmd_cancel(client, args):
    client.cancel_order(client.account_hash(args.account), args.order_id)
    print(f"Cancel requested for order {args.order_id}")


def cmd_order(client, args):
    price, stop = args.price, args.stop_price
    if args.type in ("LIMIT", "STOP_LIMIT") and price is None:
        if args.offset_pct is None:
            raise SchwabError("Give --price, or --offset-pct to price the limit off the last quote.")
        last = client.last_price(args.symbol)
        # Buy below / sell above the market so a test order rests instead of filling.
        sign = -1 if args.instruction in ("BUY", "BUY_TO_COVER") else 1
        price = last * (1 + sign * args.offset_pct / 100)
        print(f"{args.symbol.upper()} last {last:.2f} -> limit {format_price(price)} ({args.offset_pct:+g}% away)")

    order = build_equity_order(args.symbol, args.instruction, args.quantity, args.type, price, stop,
                               args.duration, args.session)
    print(json.dumps(order, indent=2))
    if args.dry_run:
        print("Dry run: order not sent.")
        return 0

    account_hash = client.account_hash(args.account)
    if not args.yes:
        print("\nSchwab has no paper trading -- this is a REAL order in a live account.")
        if input("Type 'yes' to send it: ").strip().lower() != "yes":
            print("Not sent.")
            return 1

    order_id = client.place_order(account_hash, order)
    print(f"{now_et()}  Order placed, id {order_id}")
    if not order_id:
        return 0

    watch = args.cancel_after if args.cancel_after is not None else 3
    deadline = time.time() + watch
    status = None
    while True:
        o = client.order(account_hash, order_id)
        if o.get("status") != status:
            status = o.get("status")
            print(f"{now_et()}  status {status}, filled {o.get('filledQuantity', 0):g}"
                  + (f", reason: {o['statusDescription']}" if o.get("statusDescription") else ""))
        if status in TERMINAL_STATUSES or time.time() >= deadline:
            break
        time.sleep(2)

    if args.cancel_after is not None and status not in TERMINAL_STATUSES:
        client.cancel_order(account_hash, order_id)
        time.sleep(1)
        print(f"{now_et()}  cancelled; final status {client.order(account_hash, order_id).get('status')}")
    return 0


def parse_args():
    p = argparse.ArgumentParser(description="Schwab Trader API: OAuth login, look-ups and orders.")
    p.add_argument("--account", default=os.getenv("SCHWAB_ACCOUNT"),
                   help="Account number or its last digits (needed when several accounts are linked)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("login", help="Interactive OAuth login (needed once every 7 days)")
    s.add_argument("--redirect-url", help="The redirected URL, instead of pasting it at the prompt")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("token", help="Show token expiry; exit 1 expired, 2 under a day left")
    s.add_argument("--refresh", action="store_true", help="Force an access-token refresh")
    s.set_defaults(func=cmd_token)

    sub.add_parser("accounts", help="Balances and positions").set_defaults(func=cmd_accounts)

    s = sub.add_parser("quote", help="Quotes for one or more symbols")
    s.add_argument("symbols", nargs="+")
    s.set_defaults(func=cmd_quote)

    s = sub.add_parser("history", help="Price history candles")
    s.add_argument("symbol")
    s.add_argument("--period-type", default="month", choices=["day", "month", "year", "ytd"])
    s.add_argument("--period", type=int, default=1)
    s.add_argument("--frequency-type", default="daily", choices=["minute", "daily", "weekly", "monthly"])
    s.add_argument("--frequency", type=int, default=1, help="Minutes per bar for minute data (1,5,10,15,30)")
    s.add_argument("--extended-hours", action="store_true")
    s.add_argument("--last", type=int, default=20, help="Print only the last N candles (default 20)")
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("hours", help="Market hours")
    s.add_argument("--markets", default="equity", help="equity,option,bond,future,forex")
    s.add_argument("--date", help="YYYY-MM-DD")
    s.set_defaults(func=cmd_hours)

    s = sub.add_parser("search", help="Instrument look-up")
    s.add_argument("symbol")
    s.add_argument("--projection", default="symbol-search",
                   choices=["symbol-search", "symbol-regex", "desc-search", "desc-regex", "search", "fundamental"])
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("orders", help="Recent orders")
    s.add_argument("--days", type=int, default=1)
    s.add_argument("--status", help="e.g. WORKING, FILLED, CANCELED")
    s.set_defaults(func=cmd_orders)

    s = sub.add_parser("status", help="One order's status")
    s.add_argument("order_id")
    s.add_argument("--json", action="store_true", help="Print the full order JSON")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("cancel", help="Cancel an order")
    s.add_argument("order_id")
    s.set_defaults(func=cmd_cancel)

    s = sub.add_parser("order", help="Place an equity order (REAL money)")
    s.add_argument("instruction", type=str.upper, choices=INSTRUCTIONS)
    s.add_argument("symbol")
    s.add_argument("quantity", type=int)
    s.add_argument("--type", type=str.upper, default="MARKET", choices=ORDER_TYPES)
    s.add_argument("--price", type=float, help="Limit price")
    s.add_argument("--offset-pct", type=float,
                   help="Price a LIMIT this %% away from the last quote (below for buys, above for sells)")
    s.add_argument("--stop-price", type=float)
    s.add_argument("--duration", type=str.upper, default="DAY", choices=["DAY", "GOOD_TILL_CANCEL", "FILL_OR_KILL"])
    s.add_argument("--session", type=str.upper, default="NORMAL", choices=["NORMAL", "AM", "PM", "SEAMLESS"])
    s.add_argument("--dry-run", action="store_true", help="Print the order JSON only")
    s.add_argument("--yes", action="store_true", help="Skip the typed confirmation (unattended use)")
    s.add_argument("--cancel-after", type=int, metavar="SECONDS",
                   help="Watch the order this long, then cancel it if still working (for testing)")
    s.set_defaults(func=cmd_order)
    return p.parse_args()


def main():
    args = parse_args()
    try:
        client = SchwabClient.from_env()
        return args.func(client, args) or 0
    except LoginRequired as exc:
        print(f"Login required: {exc}", file=sys.stderr)
        return 1
    except SchwabError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
