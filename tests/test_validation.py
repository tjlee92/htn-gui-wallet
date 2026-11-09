import pytest

from guiwallet.core.commands import WalletContext, send_arguments
from guiwallet.core.validation import (
    InvalidInput,
    validate_address,
    validate_amount,
    validate_daemon_address,
    validate_node_address,
    validate_password,
)

ADDRESS = "hoosat:qqqqpzry9x8gf2"


@pytest.mark.parametrize("amount", ["1", "0.1", "12.5", "1234.12345678", "999999999999", " 5 "])
def test_accepts_amounts_htnwallet_accepts(amount):
    assert validate_amount(amount) == amount.strip()


@pytest.mark.parametrize(
    "amount",
    ["", "0", "0.0", "0.", "1.123456789", "01", "-1", "1,5", "1e5", "1 000", "1000000000000", "٣"],
)
def test_refuses_bad_amounts(amount):
    with pytest.raises(InvalidInput):
        validate_amount(amount)


def test_address_must_match_the_network():
    assert validate_address(ADDRESS, "mainnet") == ADDRESS
    with pytest.raises(InvalidInput):
        validate_address(ADDRESS, "testnet")


@pytest.mark.parametrize(
    "address",
    ["", "hoosat:", "qqqq", "--password=x", "hoosat:QQQQ", "hoosat:qq b", "hoosat:qq1"],
)
def test_refuses_bad_addresses(address):
    with pytest.raises(InvalidInput):
        validate_address(address, "mainnet")


@pytest.mark.parametrize("password", ["", '"quoted"', '"', "a\x00b"])
def test_refuses_passwords_htnwallet_would_not_receive_intact(password):
    with pytest.raises(InvalidInput):
        validate_password(password)


@pytest.mark.parametrize("password", ["-starts-with-dash", "/slash", "a b", 'mid"quote', "trail\\", "=", "é"])
def test_allows_unusual_but_safe_passwords(password):
    assert validate_password(password) == password


@pytest.mark.parametrize(
    "address",
    ["localhost", "192.168.0.221", "192.168.0.221:42420", "node.example.com:42420", "[2001:db8::1]:42420"],
)
def test_accepts_node_addresses(address):
    assert validate_node_address(address) == address


@pytest.mark.parametrize("address", ["", "-x", "--rpcserver=y", "a b", "http://x", "a;b", "host:port", "x:123456"])
def test_refuses_bad_node_addresses(address):
    with pytest.raises(InvalidInput):
        validate_node_address(address)


@pytest.mark.parametrize("address", ["127.0.0.1:4282", "localhost:4282", " 127.0.0.1:1 "])
def test_accepts_daemon_addresses_on_this_computer(address):
    assert validate_daemon_address(address) == address.strip()


@pytest.mark.parametrize(
    "address",
    ["", "4282", "127.0.0.1", "192.168.0.5:4282", "evil.example.com:4282", "127.0.0.1:0", "127.0.0.1:70000", "0.0.0.0:4282"],
)
def test_refuses_daemon_addresses_elsewhere(address):
    with pytest.raises(InvalidInput):
        validate_daemon_address(address)


def test_send_arguments_keep_each_value_joined_to_its_flag():
    context = WalletContext("mainnet", "localhost:14282")
    assert send_arguments(context, ADDRESS, "-secret", amount="1.5", from_address=ADDRESS) == [
        f"--to-address={ADDRESS}",
        "--password=-secret",
        "--daemonaddress=localhost:14282",
        "--send-amount=1.5",
        f"--from-address={ADDRESS}",
        "-u",
    ]


def test_send_always_ends_with_the_existing_change_address_flag():
    context = WalletContext("mainnet", "localhost:14282")
    assert send_arguments(context, ADDRESS, "pw", amount="1.5")[-1] == "-u"
    assert send_arguments(context, ADDRESS, "pw", send_all=True)[-1] == "-u"


def test_send_needs_exactly_one_of_amount_and_send_all():
    context = WalletContext("mainnet", "localhost:14282")
    with pytest.raises(ValueError):
        send_arguments(context, ADDRESS, "pw")
    with pytest.raises(ValueError):
        send_arguments(context, ADDRESS, "pw", amount="1", send_all=True)


def test_testnet_context_adds_the_network_flag():
    context = WalletContext("testnet", "localhost:14282")
    arguments = send_arguments(context, "hoosattest:qqqq", "pw", send_all=True)
    assert "--testnet" in arguments and "--send-all" in arguments
