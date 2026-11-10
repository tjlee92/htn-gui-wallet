import os
from decimal import Decimal

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from guiwallet.core.commands import WalletContext  # noqa: E402
from guiwallet.core.wallet import AddressBalance, WalletBalance  # noqa: E402
from guiwallet.ui.pages import SendPage  # noqa: E402
from guiwallet.ui.widgets import HIDDEN_AMOUNT  # noqa: E402

FUNDED = "hoosat:qq" + "a" * 61
ALSO_FUNDED = "hoosat:qq" + "b" * 61
EMPTY = "hoosat:qq" + "c" * 61
BALANCE = WalletBalance(
    Decimal("812.5"),
    Decimal(0),
    (AddressBalance(ALSO_FUNDED, Decimal("12.5"), Decimal(0)), AddressBalance(FUNDED, Decimal(800), Decimal(0))),
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app):
    send_page = SendPage("mainnet")
    send_page.show_balance(BALANCE)
    send_page.set_receive_addresses([EMPTY, FUNDED])
    send_page.show()
    return send_page


def reviewed(page):
    requests = []
    page.review_requested.connect(requests.append)
    page.review.click()
    return requests


def addresses_in(combo):
    return [combo.itemData(index) for index in range(combo.count())]


def choose(combo, address):
    combo.setCurrentIndex(combo.findData(address))


def test_nothing_shows_until_a_tab_is_chosen(page):
    assert page.tab_group.checkedButton() is None
    assert not page.form.isVisible()
    assert reviewed(page) == []
    page.own_tab.click()
    assert page.form.isVisible()


def test_the_tabs_match_in_size(page):
    assert page.own_tab.width() == page.other_tab.width()


def test_own_wallets_are_listed_from_balance_and_show_addresses(page):
    page.own_tab.click()
    assert addresses_in(page.own_source) == sorted([FUNDED, ALSO_FUNDED, EMPTY])
    assert "0 HTN" in page.own_source.itemText(page.own_source.findData(EMPTY))


def test_each_tab_shows_only_its_own_fields(page):
    page.own_tab.click()
    assert page.own_source.isVisible() and page.own_target.isVisible()
    assert not page.source.isVisible() and not page.recipient.isVisible()
    page.other_tab.click()
    assert page.source.isVisible() and page.recipient.isVisible()
    assert not page.own_source.isVisible() and not page.own_target.isVisible()


def test_own_send_starts_with_nothing_chosen_and_asks_for_both(page):
    page.own_tab.click()
    page.amount.setText("1")
    assert reviewed(page) == [] and "send from" in page.error.text()
    choose(page.own_source, FUNDED)
    assert reviewed(page) == [] and "send to" in page.error.text()


def test_to_never_offers_the_from_address(page):
    page.own_tab.click()
    choose(page.own_source, FUNDED)
    assert FUNDED not in addresses_in(page.own_target)


def test_to_empties_rather_than_moving_when_from_takes_its_address(page):
    page.own_tab.click()
    choose(page.own_target, FUNDED)
    choose(page.own_source, FUNDED)
    assert page.own_target.currentData() is None


def test_a_refresh_keeps_both_choices_and_any_error(page):
    page.own_tab.click()
    choose(page.own_source, FUNDED)
    choose(page.own_target, EMPTY)
    page.send_all.setChecked(True)
    page.show_error("Something to read")
    page.show_balance(BALANCE)
    page.set_receive_addresses([EMPTY, FUNDED])
    assert page.own_source.currentData() == FUNDED and page.own_target.currentData() == EMPTY
    assert page.error.isVisible()


def test_own_send_all_runs_from_the_chosen_address_and_keeps_change_there(page):
    page.own_tab.click()
    choose(page.own_source, FUNDED)
    choose(page.own_target, EMPTY)
    page.send_all.setChecked(True)
    assert page.amount.text() == "All spendable: 800 HTN"
    requests = reviewed(page)
    assert len(requests) == 1
    arguments = requests[0].arguments(WalletContext("mainnet", "localhost:14282"), "pw")
    assert f"--to-address={EMPTY}" in arguments
    assert f"--from-address={FUNDED}" in arguments
    assert "--send-all" in arguments and arguments[-1] == "-u"


def test_send_all_shows_what_the_from_choice_can_spend_on_the_other_tab(page):
    page.other_tab.click()
    page.send_all.setChecked(True)
    assert page.amount.text() == "All spendable: 812.5 HTN"
    choose(page.source, ALSO_FUNDED)
    assert page.amount.text() == "All spendable: 12.5 HTN"


def test_send_all_turned_off_leaves_an_empty_amount_to_type_in(page):
    page.other_tab.click()
    page.send_all.setChecked(True)
    page.send_all.setChecked(False)
    assert page.amount.text() == "" and page.amount.isEnabled()


def test_hidden_balances_stay_hidden(page):
    page.set_balances_hidden(True)
    page.own_tab.click()
    choose(page.own_source, FUNDED)
    page.send_all.setChecked(True)
    assert HIDDEN_AMOUNT in page.amount.text() and "800" not in page.amount.text()
    assert all("800" not in page.own_source.itemText(index) for index in range(page.own_source.count()))


def test_changing_tab_drops_the_amount_and_send_all(page):
    page.other_tab.click()
    page.send_all.setChecked(True)
    page.own_tab.click()
    assert not page.send_all.isChecked() and page.amount.text() == ""
    page.amount.setText("5")
    page.other_tab.click()
    assert page.amount.text() == ""


def test_clear_goes_back_to_no_tab_and_no_choices(page):
    page.own_tab.click()
    choose(page.own_source, FUNDED)
    choose(page.own_target, EMPTY)
    page.clear()
    assert page.tab_group.checkedButton() is None and not page.form.isVisible()
    assert page.own_source.currentData() is None and page.own_target.currentData() is None
    page.other_tab.click()  # The group still works after being emptied.
    page.own_tab.click()
    assert page.own_tab.isChecked() and not page.other_tab.isChecked()


def test_forgetting_the_wallet_empties_the_lists(page):
    page.own_tab.click()
    choose(page.own_source, FUNDED)
    page.forget_wallet()
    assert page.own_source.count() == 0 and page.own_source.currentData() is None
    assert page.own_target.count() == 0
    assert reviewed(page) == []


# ----- The From balance, and amounts it can't cover -----


def test_the_from_balance_shows_under_the_list_with_two_decimals(page):
    page.other_tab.click()
    assert page.available.text() == "Available: 812.50 HTN" and not page.available.isHidden()
    choose(page.source, ALSO_FUNDED)
    assert page.available.text() == "Available: 12.50 HTN"
    page.own_tab.click()
    assert page.available.isHidden()  # No From chosen yet.
    choose(page.own_source, FUNDED)
    assert page.available.text() == "Available: 800.00 HTN"
    choose(page.own_source, EMPTY)
    assert page.available.text() == "Available: 0.00 HTN"


def test_the_balance_rounds_down_and_hides_with_the_eye(page):
    page.show_balance(WalletBalance(Decimal("1234.569"), Decimal(0), (AddressBalance(FUNDED, Decimal("1234.569"), Decimal(0)),)))
    page.other_tab.click()
    assert page.available.text() == "Available: 1,234.56 HTN"
    page.set_balances_hidden(True)
    assert page.available.text() == f"Available: {HIDDEN_AMOUNT} HTN"


def test_more_than_the_from_balance_is_refused_as_it_is_typed(page):
    page.other_tab.click()
    choose(page.source, ALSO_FUNDED)
    page.recipient.setText(FUNDED)
    page.amount.setText("12.50000001")
    assert not page.review.isEnabled() and "more than the available" in page.error.text()
    assert reviewed(page) == []
    page.amount.setText("12.4")
    assert page.review.isEnabled() and page.error.isHidden()


def test_exactly_the_balance_points_to_send_all(page):
    page.own_tab.click()
    choose(page.own_source, ALSO_FUNDED)
    choose(page.own_target, FUNDED)
    page.amount.setText("12.5")
    assert not page.review.isEnabled() and "Send all" in page.error.text()
    page.send_all.click()
    assert page.review.isEnabled() and page.error.isHidden()
    assert len(reviewed(page)) == 1


def test_a_lower_balance_after_a_refresh_refuses_the_typed_amount(page):
    page.other_tab.click()
    page.recipient.setText(FUNDED)
    page.amount.setText("500")
    assert page.review.isEnabled()
    page.show_balance(WalletBalance(Decimal(100), Decimal(0), (AddressBalance(FUNDED, Decimal(100), Decimal(0)),)))
    assert not page.review.isEnabled() and "more than the available" in page.error.text()


def test_a_chosen_from_that_leaves_the_balance_list_stays_chosen(page):
    # Such as when an auto-compound has just moved its coins away. Quietly falling back to "Any address"
    # would spend the wallet's other addresses instead.
    page.other_tab.click()
    choose(page.source, ALSO_FUNDED)
    page.recipient.setText(EMPTY)
    page.amount.setText("5")
    assert page.review.isEnabled()
    page.show_balance(WalletBalance(Decimal(800), Decimal(0), (AddressBalance(FUNDED, Decimal(800), Decimal(0)),)))
    assert page.source.currentData() == ALSO_FUNDED
    assert page.available.text() == "Available: 0.00 HTN"
    assert not page.review.isEnabled() and "more than the available" in page.error.text()
    assert reviewed(page) == []


def test_send_all_from_a_chosen_address_that_emptied_still_names_it(page):
    page.other_tab.click()
    choose(page.source, ALSO_FUNDED)
    page.recipient.setText(EMPTY)
    page.send_all.click()
    page.show_balance(WalletBalance(Decimal(800), Decimal(0), (AddressBalance(FUNDED, Decimal(800), Decimal(0)),)))
    [request] = reviewed(page)
    assert request.from_address == ALSO_FUNDED and request.send_all


def test_forgetting_the_wallet_drops_the_chosen_from(page):
    page.other_tab.click()
    choose(page.source, ALSO_FUNDED)
    page.forget_wallet()
    assert page.source.currentData() is None and page.source.count() == 1
