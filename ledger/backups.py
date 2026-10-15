import json
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from ledger.exports import rows_for
from ledger.models import Account, JournalEntry

def create_backup(company):
    folder = Path(settings.BASE_DIR) / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"comptapieces-{company.pk}-{timezone.now():%Y%m%d-%H%M%S}.zip"
    payload = {"format": "ComptaPieces-1", "created_at": timezone.now().isoformat(),
        "company": {"name": company.name, "tax_id": company.tax_id, "country": company.country, "currency": company.currency},
        "accounts": list(Account.objects.filter(company=company).values("code", "name", "active")), "entries": []}
    for entry in JournalEntry.objects.filter(company=company).prefetch_related("lines__account", "documents").order_by("number"):
        payload["entries"].append({"number": entry.number, "date": entry.date.isoformat(), "journal": entry.journal,
            "description": entry.description, "state": entry.state, "created_by": entry.created_by.username,
            "lines": [{"account": line.account.code, "description": line.description, "debit": str(line.debit), "credit": str(line.credit)} for line in entry.lines.all()],
            "documents": []})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backup.json", json.dumps(payload, ensure_ascii=False, indent=2))
        for entry in JournalEntry.objects.filter(company=company).prefetch_related("documents"):
            for doc in entry.documents.all():
                try:
                    with doc.file.open("rb") as source: archive.writestr(f"pieces/E-{entry.number:06d}/{Path(doc.original_name).name}", source.read())
                except OSError as exc:
                    raise RuntimeError(f"Pièce manquante pour E-{entry.number:06d} : {doc.original_name}") from exc
    return path
