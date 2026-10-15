"""SYSCOHADA revised chart bundled from the supplied reference PDF."""

import json
from functools import lru_cache
from pathlib import Path

from django.db import transaction


@lru_cache(maxsize=1)
def load_syscohada_accounts():
    data_path = Path(__file__).resolve().parent / "data" / "syscohada_revise.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    accounts = payload.get("accounts", [])
    codes = [str(account["code"]) for account in accounts]
    if len(accounts) != 1330 or len(set(codes)) != 1330:
        raise ValueError("Le référentiel SYSCOHADA doit contenir 1 330 comptes uniques.")
    return tuple((str(account["code"]), account["name"]) for account in accounts)


@transaction.atomic
def seed_company_chart(company):
    """Insert or refresh SYSCOHADA names without deleting custom accounts."""
    from ledger.models import Account

    standard_accounts = load_syscohada_accounts()
    existing = {
        account.code: account
        for account in Account.objects.filter(company=company, code__in=[code for code, _ in standard_accounts])
    }
    to_create = []
    to_update = []
    for code, name in standard_accounts:
        account = existing.get(code)
        if account is None:
            to_create.append(Account(company=company, code=code, name=name))
        elif account.name != name:
            account.name = name
            to_update.append(account)

    if to_create:
        Account.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        Account.objects.bulk_update(to_update, ["name"], batch_size=500)
    return len(to_create), len(to_update)
