from decimal import Decimal

from django.db.models import Sum

from ledger.models import Account, JournalLine

def account_balances(company, through=None):
    lines = JournalLine.objects.filter(entry__company=company, entry__state="POSTED")
    if through:
        lines = lines.filter(entry__date__lte=through)
    totals = lines.values("account_id").annotate(debit=Sum("debit"), credit=Sum("credit"))
    by_id = {row["account_id"]: row for row in totals}
    result = []
    for account in Account.objects.filter(company=company, active=True):
        row = by_id.get(account.pk, {})
        debit, credit = row.get("debit") or Decimal("0"), row.get("credit") or Decimal("0")
        result.append({"account": account, "debit": debit, "credit": credit, "balance": debit - credit})
    return result
