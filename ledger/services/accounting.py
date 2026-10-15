import hashlib
import calendar
from decimal import Decimal, InvalidOperation
from datetime import date
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ledger.models import Account, AuditEvent, Company, FiscalPeriod, JournalEntry, JournalLine, SupportingDocument

from ledger.syscohada import seed_company_chart

def validate_upload(uploaded):
    suffix = Path(uploaded.name).suffix.lower()
    allowed = {".pdf": "PDF", ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
    if suffix not in allowed:
        raise ValidationError(f"Type de pièce non autorisé : {uploaded.name}")
    if uploaded.size <= 0 or uploaded.size > 15 * 1024 * 1024:
        raise ValidationError(f"La pièce {uploaded.name} est vide ou dépasse 15 Mo.")
    uploaded.seek(0)
    if suffix == ".pdf":
        if not uploaded.read(5).startswith(b"%PDF-"):
            raise ValidationError(f"Le fichier {uploaded.name} n’est pas un PDF valide.")
    else:
        try:
            image = Image.open(uploaded)
            if image.format != allowed[suffix]:
                raise ValidationError(f"Le contenu de {uploaded.name} ne correspond pas à son extension.")
            image.verify()
        except (UnidentifiedImageError, OSError):
            raise ValidationError(f"L’image {uploaded.name} est illisible ou invalide.")
    uploaded.seek(0)

@transaction.atomic
def create_company_for_owner(*, user, name, tax_id="", country="Togo"):
    company = Company.objects.create(name=name.strip(), tax_id=tax_id.strip(), country=country.strip() or "Togo")
    company.memberships.create(user=user, role="OWNER")
    seed_company_chart(company)
    today = timezone.localdate()
    year = today.year if today.month >= company.fiscal_start_month else today.year - 1
    start = date(year, company.fiscal_start_month, 1)
    end_year = year + 1 if company.fiscal_start_month != 1 else year
    end_month = company.fiscal_start_month - 1 or 12
    end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
    FiscalPeriod.objects.create(company=company, label=str(start.year) if company.fiscal_start_month == 1 else f"{start.year}-{end.year}", start_date=start, end_date=end)
    AuditEvent.objects.create(company=company, actor=user, action="company.created", object_type="Company", object_id=str(company.pk))
    return company

def parse_amount(value):
    try:
        amount = Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValidationError("Montant invalide.")
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise ValidationError("Le montant doit être positif et limité à deux décimales.")
    if amount == 0:
        return Decimal("0.00")
    return amount.quantize(Decimal("0.01"))

@transaction.atomic
def post_entry(*, company, user, date, journal, description, lines, documents=(), reverses=None, state=JournalEntry.State.POSTED):
    if not description.strip():
        raise ValidationError("Le libellé est obligatoire.")
    period = FiscalPeriod.objects.filter(company=company, start_date__lte=date, end_date__gte=date).first()
    if not period:
        raise ValidationError("Aucun exercice ne couvre cette date. Crée d’abord l’exercice correspondant.")
    if period.closed:
        raise ValidationError(f"L’exercice {period.label} est clôturé. Aucune nouvelle écriture n’y est acceptée.")
    if len(lines) < 2:
        raise ValidationError("Une écriture doit contenir au moins deux lignes.")
    for uploaded in documents:
        validate_upload(uploaded)
    normalized = []
    debit_total = Decimal("0.00")
    credit_total = Decimal("0.00")
    for raw in lines:
        account = Account.objects.filter(company=company, active=True, pk=raw["account_id"]).first()
        if not account:
            raise ValidationError("Un compte sélectionné n’existe pas dans cette entreprise.")
        debit = parse_amount(raw.get("debit") or "0") if str(raw.get("debit") or "0").strip() not in ("0", "0.0", "0.00", "") else Decimal("0.00")
        credit = parse_amount(raw.get("credit") or "0") if str(raw.get("credit") or "0").strip() not in ("0", "0.0", "0.00", "") else Decimal("0.00")
        if (debit > 0) == (credit > 0):
            raise ValidationError("Chaque ligne doit contenir un débit ou un crédit, mais pas les deux.")
        normalized.append((account, debit, credit, str(raw.get("description", ""))[:240]))
        debit_total += debit
        credit_total += credit
    if debit_total != credit_total:
        raise ValidationError(f"Écriture déséquilibrée : débit {debit_total} ≠ crédit {credit_total}.")
    Company.objects.select_for_update().get(pk=company.pk)
    company.refresh_from_db(fields=["next_entry_number"])
    entry = JournalEntry.objects.create(company=company, fiscal_period=period, number=company.next_entry_number, date=date, journal=journal,
        description=description.strip(), created_by=user, posted_at=timezone.now() if state == JournalEntry.State.POSTED else None,
        state=state, reverses=reverses)
    company.next_entry_number += 1
    company.save(update_fields=["next_entry_number"])
    JournalLine.objects.bulk_create([JournalLine(entry=entry, account=a, debit=d, credit=c, description=desc) for a, d, c, desc in normalized])
    for uploaded in documents:
        digest = hashlib.sha256(uploaded.read()).hexdigest()
        uploaded.seek(0)
        SupportingDocument.objects.create(entry=entry, file=uploaded, original_name=uploaded.name[:255], sha256=digest,
            size=uploaded.size, uploaded_by=user)
    AuditEvent.objects.create(company=company, actor=user, action="entry.posted" if state == JournalEntry.State.POSTED else "entry.submitted", object_type="JournalEntry", object_id=str(entry.pk),
        details={"number": entry.number, "debit": str(debit_total), "credit": str(credit_total)})
    return entry

@transaction.atomic
def approve_entry(*, entry, user):
    entry = JournalEntry.objects.select_for_update().select_related("company", "fiscal_period").get(pk=entry.pk)
    if entry.state != JournalEntry.State.PENDING:
        raise ValidationError("Seule une écriture en attente peut être validée.")
    if entry.fiscal_period.closed:
        raise ValidationError("L’exercice a été clôturé avant validation de cette écriture.")
    if entry.total_debit <= 0 or entry.total_debit != entry.total_credit:
        raise ValidationError("Écriture déséquilibrée : validation refusée.")
    entry.state = JournalEntry.State.POSTED
    entry.posted_at = timezone.now()
    entry.save(update_fields=["state", "posted_at"])
    AuditEvent.objects.create(company=entry.company, actor=user, action="entry.approved", object_type="JournalEntry", object_id=str(entry.pk))
    return entry

@transaction.atomic
def reject_entry(*, entry, user, reason=""):
    entry = JournalEntry.objects.select_for_update().select_related("company").get(pk=entry.pk)
    if entry.state != JournalEntry.State.PENDING:
        raise ValidationError("Seule une écriture en attente peut être rejetée.")
    entry.state = JournalEntry.State.REJECTED
    entry.save(update_fields=["state"])
    AuditEvent.objects.create(company=entry.company, actor=user, action="entry.rejected", object_type="JournalEntry", object_id=str(entry.pk), details={"reason": reason[:240]})
    return entry

@transaction.atomic
def reverse_entry(*, entry, user):
    entry = JournalEntry.objects.select_for_update().select_related("company").get(pk=entry.pk)
    if entry.state != JournalEntry.State.POSTED:
        raise ValidationError("Seule une écriture comptabilisée peut être contrepassée.")
    if entry.reversal_entries.exists():
        raise ValidationError("Cette écriture possède déjà une contrepassation.")
    lines = [{"account_id": line.account_id, "debit": line.credit, "credit": line.debit, "description": line.description}
             for line in entry.lines.all()]
    reversal = post_entry(company=entry.company, user=user, date=timezone.localdate(), journal=entry.journal,
        description=f"Contrepassation de E-{entry.number:06d}", lines=lines, reverses=entry)
    entry.state = JournalEntry.State.VOID
    entry.save(update_fields=["state"])
    AuditEvent.objects.create(company=entry.company, actor=user, action="entry.reversed", object_type="JournalEntry", object_id=str(entry.pk),
        details={"reversal_id": reversal.pk})
    return reversal
