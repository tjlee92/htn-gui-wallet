"""Reads the text htnwallet prints and turns it into values the screens can show.

If the output isn't in the expected layout, these raise an error rather than guess, so a
future htnwallet version can't make the app display a wrong balance.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

AMOUNT_WIDTH = 19  # htnwallet prints amounts in 19 columns, and leaves them blank for zero.
TOTAL_PREFIX = "Total balance, HTN "
ADDRESS_PREFIXES = ("hoosat:", "hoosattest:")


class WalletOutputError(Exception):
    """htnwallet printed something this version of the app doesn't understand."""


@dataclass(frozen=True)
class AddressBalance:
    address: str
    available: Decimal
    pending: Decimal


@dataclass(frozen=True)
class WalletBalance:
    available: Decimal
    pending: Decimal
    addresses: tuple[AddressBalance, ...]


@dataclass(frozen=True)
class WalletCoins:
    total: int
    by_address: dict[str, int]  # How many coins each address holds.


def _amount(column: str) -> Decimal:
    text = column.strip()
    if not text:
        return Decimal(0)
    try:
        return Decimal(text)
    except InvalidOperation:
        raise WalletOutputError(f"Unexpected amount in htnwallet output: {text[:30]}") from None


def parse_balance(stdout: str) -> WalletBalance:
    """Read the output of `htnwallet balance --verbose`."""
    addresses = []
    total = None
    for line in stdout.splitlines():
        if line.startswith(TOTAL_PREFIX):
            rest = line[len(TOTAL_PREFIX):]
            available_end = AMOUNT_WIDTH
            pending_start = available_end + 2  # Two spaces between the columns.
            total = (_amount(rest[:available_end]), _amount(rest[pending_start:]))
        elif line.startswith(ADDRESS_PREFIXES):
            address, _, rest = line.partition(" ")
            addresses.append(
                AddressBalance(
                    address,
                    _amount(rest[:AMOUNT_WIDTH]),
                    _amount(rest[AMOUNT_WIDTH + 1:]),
                )
            )
    if total is None:
        raise WalletOutputError("htnwallet did not report a total balance")
    # The daemon returns addresses in a random order, so sort them to keep them in place.
    addresses.sort(key=lambda entry: entry.address)
    return WalletBalance(total[0], total[1], tuple(addresses))


def parse_coins(stdout: str) -> WalletCoins:
    """Read the output of `htnwallet utxos`: a line per coin, then a line with the count."""
    counts: dict[str, int] = {}
    total = None
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "UTXOs,":
            total = int(parts[0]) if parts[0].isdecimal() else None
        elif "DAA" in parts and len(parts) >= 3:
            # <txid>:<index> <amount> <address> <derivation path> DAA <score> [coinbase]
            address = parts[2]
            if not address.startswith(ADDRESS_PREFIXES):
                raise WalletOutputError("Unexpected coin line in htnwallet output")
            counts[address] = counts.get(address, 0) + 1
    if total is None:
        raise WalletOutputError("htnwallet did not report a coin count")
    return WalletCoins(total, counts)


def parse_addresses(stdout: str) -> list[str]:
    """Read the output of `htnwallet show-addresses`, ignoring its header and closing note."""
    return sorted(line.strip() for line in stdout.splitlines() if line.startswith(ADDRESS_PREFIXES))


def format_htn(amount: Decimal) -> str:
    """Show a whole number of HTN: 1,234. Rounded down, so it never overstates a balance."""
    return f"{amount.quantize(Decimal(1), rounding=ROUND_DOWN):,}"


def format_htn_cents(amount: Decimal) -> str:
    """Show two decimal places: 1,234.56. Rounded down, so it never overstates a balance."""
    return f"{amount.quantize(Decimal('0.01'), rounding=ROUND_DOWN):,.2f}"


def format_htn_exact(amount: Decimal) -> str:
    """Show every decimal place, with no trailing zeros: 1,234.56"""
    return f"{amount:,.8f}".rstrip("0").rstrip(".")
