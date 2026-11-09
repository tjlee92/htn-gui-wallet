"""Checks on what the user types, before it gets anywhere near a command line."""

import re

BECH32_CHARACTERS = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
ADDRESS_PREFIXES = {"mainnet": "hoosat", "testnet": "hoosattest"}

# The same rule htnwallet applies to amounts (see cmd/htnwallet/utils/format_kas.go).
AMOUNT_PATTERN = re.compile(r"([1-9][0-9]{0,11}|0)(\.[0-9]{0,8})?")

# A host name or IPv4 address, or an IPv6 address in brackets, with an optional port.
NODE_ADDRESS_PATTERN = re.compile(r"([A-Za-z0-9][A-Za-z0-9.-]{0,252}|\[[0-9A-Fa-f:]{2,45}\])(:[0-9]{1,5})?")

# The daemon has no login or encryption, so the app only uses one on this computer.
DAEMON_ADDRESS_PATTERN = re.compile(r"(127\.0\.0\.1|localhost):([0-9]{1,5})")


class InvalidInput(ValueError):
    """Something the user typed cannot be used. The message is safe to show them."""


def validate_address(address: str, network: str) -> str:
    """Check an address looks right for this network. htnwallet verifies its checksum."""
    address = address.strip()
    prefix = ADDRESS_PREFIXES[network]
    start = prefix + ":"
    if not address.startswith(start):
        raise InvalidInput(f"The address must start with {start}")
    body = address[len(start):]
    if not body or any(character not in BECH32_CHARACTERS for character in body):
        raise InvalidInput("The address contains characters an address cannot have")
    return address


def validate_amount(amount: str) -> str:
    """Check an amount of HTN, such as 1234.12345678, and that it is above zero."""
    amount = amount.strip()
    if not AMOUNT_PATTERN.fullmatch(amount):
        raise InvalidInput("Enter an amount like 12.5, with at most 8 decimal places")
    if not amount.strip("0."):
        raise InvalidInput("The amount must be more than zero")
    return amount


def validate_node_address(address: str) -> str:
    """Check a typed node address: a name or IP, optionally followed by :port."""
    address = address.strip()
    if not NODE_ADDRESS_PATTERN.fullmatch(address):
        raise InvalidInput("Enter the node as address:port, like 192.168.0.10:42420")
    return address


def validate_daemon_address(address: str) -> str:
    """Check htnwallet's address:port, which must be on this computer."""
    address = address.strip()
    match = DAEMON_ADDRESS_PATTERN.fullmatch(address)
    if not match or not 1 <= int(match.group(2)) <= 65535:
        raise InvalidInput("htnwallet must be on this computer, like 127.0.0.1:4282")
    return address


def validate_password(password: str) -> str:
    """Refuse the passwords that htnwallet would not receive exactly as typed."""
    if not password:
        # htnwallet treats an empty --password as "ask in the terminal", and there is none.
        raise InvalidInput("Enter the wallet password")
    if password.startswith('"'):
        # The flag parser treats a leading quote as a quoted string and changes or rejects it.
        raise InvalidInput(
            "Passwords starting with a double quote can't be passed to htnwallet from this app. "
            "Use the command line for this wallet."
        )
    if "\x00" in password:
        raise InvalidInput("The password contains a character that can't be used")
    return password
