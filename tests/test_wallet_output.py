"""The sample output below follows the format strings in cmd/htnwallet/balance.go and
utxos.go, including htnwallet's habit of leaving zero amounts blank."""

from decimal import Decimal

import pytest

from guiwallet.core.wallet import WalletOutputError, format_htn, format_htn_exact, parse_addresses, parse_balance, parse_coins

ONE = "hoosat:qq111"
TWO = "hoosat:qq222"


def column(amount=""):
    return f"{amount:>19}"


def address_line(address, available="", pending=""):
    return f"{address} {column(available)} {column(pending)}"


def total_line(available="", pending=""):
    return f"Total balance, HTN {column(available)}  {column(pending)}"


def test_reads_balances():
    output = "\n".join(
        [
            address_line(ONE, "1000.00000000", "2.10000000"),
            address_line(TWO, "234.56000000"),
            total_line("1234.56000000", "2.10000000"),
        ]
    )
    balance = parse_balance(output)
    assert balance.available == Decimal("1234.56")
    assert balance.pending == Decimal("2.1")
    assert [(a.address, a.available, a.pending) for a in balance.addresses] == [
        (ONE, Decimal("1000"), Decimal("2.1")),
        (TWO, Decimal("234.56"), Decimal(0)),
    ]


def test_a_blank_available_column_is_not_mistaken_for_the_pending_amount():
    balance = parse_balance("\n".join([address_line(ONE, "", "5.00000000"), total_line("", "5.00000000")]))
    assert balance.available == 0
    assert balance.pending == 5
    assert balance.addresses[0].available == 0
    assert balance.addresses[0].pending == 5


def test_an_empty_wallet_reads_as_zero():
    balance = parse_balance(total_line())
    assert (balance.available, balance.pending, balance.addresses) == (0, 0, ())


@pytest.mark.parametrize("output", ["", "something else", f"{ONE} {column('abc')} {column()}\n{total_line()}"])
def test_unrecognised_output_is_an_error_rather_than_a_guess(output):
    with pytest.raises(WalletOutputError):
        parse_balance(output)


def test_reads_coins():
    output = "\n".join(
        [
            f"abc:0 {column('1.00000000')} {ONE} m/44'/0/0 DAA 100",
            f"def:1 {column('2.00000000')} {ONE} m/44'/0/0 DAA 200 coinbase",
            f"ghi:2 {column('3.00000000')} {TWO} imported key DAA 300",
            f"3 UTXOs, total HTN {column('6.00000000')}",
        ]
    )
    coins = parse_coins(output)
    assert coins.total == 3
    assert coins.by_address == {ONE: 2, TWO: 1}


def test_missing_coin_count_is_an_error():
    with pytest.raises(WalletOutputError):
        parse_coins("nothing useful")


def test_reads_addresses_and_skips_the_header_and_note():
    output = f"Addresses (2):\n{ONE}\n{TWO}\n\nNote: the above are only addresses that were manually created\n"
    assert parse_addresses(output) == [ONE, TWO]


def test_addresses_keep_the_same_order_whatever_order_the_daemon_returns():
    first = parse_balance("\n".join([address_line(TWO, "1.00000000"), address_line(ONE, "2.00000000"), total_line("3.00000000")]))
    second = parse_balance("\n".join([address_line(ONE, "2.00000000"), address_line(TWO, "1.00000000"), total_line("3.00000000")]))
    assert first == second
    assert parse_addresses(f"{TWO}\n{ONE}") == parse_addresses(f"{ONE}\n{TWO}") == [ONE, TWO]


@pytest.mark.parametrize(
    "amount, shown",
    [("1234.56", "1,234"), ("0", "0"), ("1000", "1,000"), ("0.99999999", "0"), ("1234567.5", "1,234,567")],
)
def test_format_htn_shows_whole_numbers_rounded_down(amount, shown):
    assert format_htn(Decimal(amount)) == shown


@pytest.mark.parametrize(
    "amount, shown",
    [("1234.56", "1,234.56"), ("0", "0"), ("1000", "1,000"), ("0.00000001", "0.00000001")],
)
def test_format_htn_exact_keeps_every_decimal(amount, shown):
    assert format_htn_exact(Decimal(amount)) == shown
