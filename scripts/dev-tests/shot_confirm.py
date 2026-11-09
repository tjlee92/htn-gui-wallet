"""Pictures of the Confirm and Processing views. No command runs: the dialog is only shown."""
import sys
from decimal import Decimal
from pathlib import Path
from PySide6.QtWidgets import QApplication, QWidget
from guiwallet.core.send import SendRequest
from guiwallet.ui.send_dialog import SendDialog
from guiwallet.ui.theme import apply_theme

out = Path(__file__).parent / "shots"; out.mkdir(exist_ok=True)
app = QApplication(sys.argv)
apply_theme(app)
parent = QWidget()
request = SendRequest("hoosat:qqaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", Decimal(1000),
                      "hoosat:qqppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp")
dialog = SendDialog(parent, request, "htnwallet.exe send\n  --to-address=...", show_debug=True)
dialog.show()
for _ in range(20):
    app.processEvents()
dialog.grab().save(str(out / "confirm.png"))
dialog.show_waiting(60)
for _ in range(20):
    app.processEvents()
dialog.grab().save(str(out / "processing.png"))
print("saved")
