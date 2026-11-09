"""Turns form values into htnwallet command-line arguments.

Each value is joined to its flag as --flag=value. That way a value starting with "-"
can't be mistaken for another flag, and it works the same on every operating system.
"""

from dataclasses import dataclass
from pathlib import Path

from guiwallet.core.settings import Settings
from guiwallet.core.validation import validate_address, validate_amount, validate_password


@dataclass(frozen=True)
class WalletContext:
    """What every wallet command needs, so it can never pick up the wrong wallet or daemon."""

    network: str  # "mainnet" or "testnet"
    daemon_address: str
    keys_file: Path | None = None

    def network_flags(self) -> list[str]:
        return ["--testnet"] if self.network == "testnet" else []

    def daemon_flags(self) -> list[str]:
        return [f"--daemonaddress={self.daemon_address}", *self.network_flags()]

    def keys_flags(self) -> list[str]:
        return [f"--keys-file={self.keys_file}"] if self.keys_file else []


def wallet_context(settings: Settings) -> WalletContext:
    return WalletContext(
        network=settings.network,
        daemon_address=settings.daemon_address,
        keys_file=Path(settings.keys_file) if settings.keys_file else None,
    )


def daemon_arguments(context: WalletContext) -> list[str]:
    """For commands that only talk to the daemon, such as `utxos` and `show-addresses`."""
    return context.daemon_flags()


def balance_arguments(context: WalletContext) -> list[str]:
    return ["--verbose", *context.daemon_flags()]


def start_daemon_arguments(context: WalletContext, node_address: str) -> list[str]:
    return [
        f"--listen={context.daemon_address}",
        f"--rpcserver={node_address}",
        *context.keys_flags(),
        *context.network_flags(),
    ]


def send_arguments(
    context: WalletContext,
    to_address: str,
    password: str,
    amount: str | None = None,
    send_all: bool = False,
    from_address: str | None = None,
) -> list[str]:
    """Check the send form's values and build the arguments for `htnwallet send`."""
    if send_all == (amount is not None):
        raise ValueError("Give either an amount or send_all, not both or neither")

    arguments = [
        f"--to-address={validate_address(to_address, context.network)}",
        f"--password={validate_password(password)}",
        *context.keys_flags(),
        *context.daemon_flags(),
    ]
    if send_all:
        arguments.append("--send-all")
    else:
        arguments.append(f"--send-amount={validate_amount(amount)}")
    if from_address:
        arguments.append(f"--from-address={validate_address(from_address, context.network)}")
    # Without this, htnwallet moves the change to a brand new address on every send.
    arguments.append("-u")
    return arguments
