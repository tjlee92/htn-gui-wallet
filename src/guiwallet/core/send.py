"""Running `htnwallet send`, and working out from its output what actually happened.

The one question that matters afterwards is whether money may have left the wallet. htnwallet prints
"Broadcasting" immediately before it hands the transaction over, so:

* No "Broadcasting" line means nothing was sent, however the command ended.
* After that line, only a printed transaction ID proves it was sent. Anything else means it may have
  been, because a lost reply looks just like a failure. The user must check before trying again.

While it can't send, htnwallet retries by itself: for about 22 seconds after most failures, and for up
to 10 minutes while it's short of spendable coins, after which it would send with nobody watching. It
pauses for 2 seconds after each of those messages, so ending it on seeing one can't interrupt a send.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path

from guiwallet.core.commands import WalletContext, send_arguments
from guiwallet.core.tools import Tool, WatchedRun, run_watched

SEND_DELAY_SECONDS = 60  # A last chance to spot a mistake. htnwallet doesn't run until this is over.
SEND_TIMEOUT_SECONDS = 5  # To reach broadcast. Ending it before then is harmless: nothing has been sent.
# To print the node's answer once broadcast has started. Ending it then can't recall the payment and only
# loses the answer, so this is far longer. A normal send never gets near either limit.
BROADCAST_REPLY_SECONDS = 60
PASSWORD_MASK = "••••••••"
_PREVIEW_PASSWORD = "preview"  # Stands in for the password when showing the command.

_BROADCAST_STARTED = re.compile(r"Broadcasting (\d+) transaction")
_TRANSACTION_ID = re.compile(r"\s+([0-9a-f]{64})")
_CANNOT_SEND_LINES = (
    "Waiting for spendable UTXO",
    "Failed to create unsigned transactions",
    "Failed to sign unsigned transactions",
)

# What htnwallet says, and how to explain it. The first match wins.
_EXPLANATIONS = (
    (
        ("Waiting for spendable UTXO", "Insufficient funds"),
        "There isn't enough spendable HTN for this amount and its fee. Coins you've only just received "
        "can't be spent for a few minutes.",
    ),
    (
        ("message authentication failed", "Password decryption failed"),
        "Wrong password, or the wallet file in Settings isn't the wallet htnwallet is running.",
    ),
    (
        ("doesn't match any of the transaction", "None of the imported keys"),
        "The wallet file in Settings isn't the wallet htnwallet is running.",
    ),
    (
        ("would have a mass of",),
        "This needs more coins than fit in one transaction. Send a smaller amount, or compound the "
        "wallet's coins first.",
    ),
    (("not synced yet",), "htnwallet is still syncing with the node. Try again shortly."),
    (("daemon is not running",), "htnwallet isn't running."),
    (("from address", "does not exists"), "The From address isn't in this wallet."),
    (("decoded address",), "That address isn't valid. Check it was copied in full."),
    (("multisig wallet",), "This wallet needs other signers, so it can't send from this app."),
)


class SendResult(Enum):
    SENT = auto()
    NOT_SENT = auto()
    MAY_HAVE_BEEN_SENT = auto()


@dataclass(frozen=True)
class SendRequest:
    """What the send form asked for. The password is deliberately not part of it."""

    to_address: str
    amount: str | None = None  # None means send everything.
    from_address: str | None = None

    @property
    def send_all(self) -> bool:
        return self.amount is None

    def arguments(self, context: WalletContext, password: str) -> list[str]:
        return send_arguments(
            context,
            self.to_address,
            password,
            amount=self.amount,
            send_all=self.send_all,
            from_address=self.from_address,
        )


@dataclass(frozen=True)
class SendOutcome:
    result: SendResult
    message: str
    detail: str = ""  # htnwallet's own words, when they say something more.
    transaction_ids: tuple[str, ...] = ()
    seconds: float | None = None


def command_preview(tool_name: str, request: SendRequest, context: WalletContext) -> str:
    """The command that will run, one flag per line, with the password masked."""
    shown = [
        f"--password={PASSWORD_MASK}" if argument.startswith("--password=") else argument
        for argument in request.arguments(context, _PREVIEW_PASSWORD)
    ]
    return "\n".join([f"{tool_name} send", *(f"  {argument}" for argument in shown)])


def _stop_when_it_cannot_send(line: str) -> bool:
    return line.startswith(_CANNOT_SEND_LINES)


def _limit_after_line(line: str) -> float | None:
    return BROADCAST_REPLY_SECONDS if _BROADCAST_STARTED.match(line) else None


def _explain(lines: tuple[str, ...]) -> tuple[str | None, str]:
    """A plain explanation of the output if there is one, and the last thing htnwallet said."""
    said = [line.strip() for line in lines if line.strip()]
    last = said[-1] if said else ""
    for line in reversed(said):
        for needles, explanation in _EXPLANATIONS:
            if any(needle in line for needle in needles):
                return explanation, last
    return None, last


def interpret(run: WatchedRun, timeout: float = SEND_TIMEOUT_SECONDS) -> SendOutcome:
    """Work out what happened from what the command printed and how it ended."""
    expected = 0
    for line in run.lines:
        if started := _BROADCAST_STARTED.match(line):
            expected = int(started.group(1))
            break
    explanation, last_line = _explain(run.lines)

    if not expected and not run.still_running:
        if run.timed_out:
            message = f"htnwallet didn't finish within {timeout:g} seconds, so the app stopped it."
        else:
            message = explanation or "htnwallet couldn't send this."
        return SendOutcome(SendResult.NOT_SENT, message, last_line)

    ids = tuple(match.group(1) for line in run.lines if (match := _TRANSACTION_ID.fullmatch(line)))
    if expected and len(ids) >= expected:
        return SendOutcome(SendResult.SENT, "Your transaction was handed to the network.", "", ids)
    return SendOutcome(
        SendResult.MAY_HAVE_BEEN_SENT,
        "The app can't tell whether this went through. Check your balance and the recipient's before "
        "trying again, or you could pay twice.",
        last_line,
        ids,
    )


def run_send(tool_path: Path, arguments: list[str], timeout: float = SEND_TIMEOUT_SECONDS) -> SendOutcome:
    """Run the send. A ToolError means it never started, so nothing was sent."""
    began = time.monotonic()
    run = run_watched(
        tool_path, Tool.HTNWALLET, "send", arguments, _stop_when_it_cannot_send, timeout, _limit_after_line
    )
    outcome = interpret(run, timeout)
    return SendOutcome(outcome.result, outcome.message, outcome.detail, outcome.transaction_ids, time.monotonic() - began)


# ----- Remembering a send in progress, in case the app dies during it -----


def send_journal_path(settings_file: Path) -> Path:
    return settings_file.parent / "send-in-progress.json"


def record_send_started(path: Path, request: SendRequest) -> None:
    """Note a send before it starts. If this fails the send must not go ahead."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "to_address": request.to_address,
                "amount": request.amount,
                "started": datetime.now().strftime("%d %b %Y, %H:%M:%S"),
            }
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def forget_send(path: Path) -> None:
    path.unlink(missing_ok=True)


def interrupted_send_message(path: Path) -> str | None:
    """If the app died during a send, what to tell the user about it. Otherwise None."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        amount = f"{saved['amount']} HTN" if saved["amount"] is not None else "all spendable HTN"
        return (
            f"The app closed unexpectedly while sending {amount} to:\n{saved['to_address']}\n\n"
            f"That was at {saved['started']}. It may have gone through. Check your balance and the "
            "recipient's before trying again, or you could pay twice."
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return (
            "The app closed unexpectedly during a send, and it may have gone through. Check your "
            "balance and the recipient's before trying again, or you could pay twice."
        )
