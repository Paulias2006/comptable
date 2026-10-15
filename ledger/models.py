import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class InstallationState(models.Model):
    key = models.CharField(max_length=16, primary_key=True, default="primary")
    claimed = models.BooleanField(default=False)
    claimed_at = models.DateTimeField(null=True, blank=True)


class Company(models.Model):
    name = models.CharField("Nom de l’entreprise", max_length=180)
    tax_id = models.CharField("Identifiant fiscal", max_length=80, blank=True)
    country = models.CharField("Pays", max_length=80, default="Togo")
    currency = models.CharField("Devise", max_length=3, default="XOF")
    fiscal_start_month = models.PositiveSmallIntegerField(default=1)
    next_entry_number = models.PositiveIntegerField(default=1)
    last_backup_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Administrateur"
        ACCOUNTANT = "ACCOUNTANT", "Comptable"
        MANAGER = "MANAGER", "Responsable"
        AUDITOR = "AUDITOR", "Auditeur"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="company_memberships")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.ACCOUNTANT)
    active = models.BooleanField(default=True)
    must_change_password = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "user"], name="one_company_membership")]


class Account(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="accounts")
    code = models.CharField(max_length=12)
    name = models.CharField(max_length=160)
    active = models.BooleanField(default=True)
    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_company_account_code")]
    def __str__(self):
        return f"{self.code} · {self.name}"


class ThirdParty(models.Model):
    class Kind(models.TextChoices):
        SUPPLIER = "SUPPLIER", "Fournisseur"
        CUSTOMER = "CUSTOMER", "Client"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="third_parties")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    code = models.CharField(max_length=24)
    name = models.CharField(max_length=180)
    account = models.OneToOneField(Account, on_delete=models.PROTECT, related_name="third_party")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "code"]
        constraints = [models.UniqueConstraint(fields=["company", "kind", "code"], name="unique_company_third_party_code")]

    def __str__(self):
        return f"{self.code} · {self.name}"


class FiscalPeriod(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="fiscal_periods")
    label = models.CharField(max_length=80)
    start_date = models.DateField()
    end_date = models.DateField()
    closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    class Meta:
        ordering = ["-start_date"]
        constraints = [models.UniqueConstraint(fields=["company", "label"], name="unique_company_period_label")]
    def __str__(self):
        return self.label


class JournalEntry(models.Model):
    class State(models.TextChoices):
        PENDING = "PENDING", "En attente de validation"
        POSTED = "POSTED", "Comptabilisée"
        REJECTED = "REJECTED", "Rejetée"
        VOID = "VOID", "Contrepassée"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="entries")
    fiscal_period = models.ForeignKey(FiscalPeriod, null=True, blank=True, on_delete=models.PROTECT, related_name="entries")
    number = models.PositiveIntegerField()
    date = models.DateField(default=timezone.localdate)
    journal = models.CharField(max_length=24, choices=[("ACH", "Achats"), ("VEN", "Ventes"), ("BNQ", "Banque"), ("CAI", "Caisse"), ("OD", "Opérations diverses")], default="ACH")
    description = models.CharField(max_length=240)
    document_reference = models.CharField("Référence de la pièce", max_length=100, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_entries")
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    reverses = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal_entries")
    class Meta:
        ordering = ["-date", "-number"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_company_entry_number")]

    @property
    def total_debit(self):
        return sum((line.debit for line in self.lines.all()), Decimal("0.00"))
    @property
    def total_credit(self):
        return sum((line.credit for line in self.lines.all()), Decimal("0.00"))
    @property
    def balanced(self):
        return self.total_debit == self.total_credit
    def __str__(self):
        return f"E-{self.number:06d} {self.description}"


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="journal_lines")
    description = models.CharField(max_length=240, blank=True)
    debit = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0)])
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0)])
    class Meta:
        ordering = ["id"]


def document_path(instance, filename):
    suffix = Path(filename).suffix.lower()[:12]
    stamp = timezone.localdate()
    return f"company_{instance.entry.company_id}/{stamp:%Y/%m}/{uuid.uuid4().hex}{suffix}"


class SupportingDocument(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="documents")
    file = models.FileField(upload_to=document_path, validators=[FileExtensionValidator(["pdf", "png", "jpg", "jpeg", "webp"])])
    original_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class BankReconciliation(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="bank_reconciliations")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="reconciliations")
    start_date = models.DateField()
    end_date = models.DateField()
    statement_balance = models.DecimalField(max_digits=16, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-end_date", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "account", "end_date"], name="unique_account_reconciliation_end")]


class ReconciliationMatch(models.Model):
    reconciliation = models.ForeignKey(BankReconciliation, on_delete=models.CASCADE, related_name="matches")
    journal_line = models.OneToOneField(JournalLine, on_delete=models.PROTECT, related_name="reconciliation_match")


class AuditEvent(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]
