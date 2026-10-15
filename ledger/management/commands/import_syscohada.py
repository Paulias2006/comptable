from django.core.management.base import BaseCommand
from django.db import transaction

from ledger.models import AuditEvent, Company
from ledger.syscohada import load_syscohada_accounts, seed_company_chart


class Command(BaseCommand):
    help = "Importe ou actualise les 1 330 comptes du SYSCOHADA révisé dans les entreprises."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, help="Limiter l’import à une entreprise.")

    def handle(self, *args, **options):
        if options["company_id"]:
            companies = Company.objects.filter(pk=options["company_id"])
        else:
            companies = Company.objects.all()

        if not companies.exists():
            self.stdout.write(self.style.WARNING("Aucune entreprise à mettre à jour."))
            return

        reference_count = len(load_syscohada_accounts())
        for company in companies:
            with transaction.atomic():
                created, updated = seed_company_chart(company)
                if created or updated:
                    AuditEvent.objects.create(
                        company=company,
                        action="chart.syscohada.imported",
                        object_type="Account",
                        details={
                            "reference": "SYSCOHADA_REVISE",
                            "reference_count": reference_count,
                            "created": created,
                            "updated": updated,
                        },
                    )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{company.name} : {reference_count} comptes SYSCOHADA prêts "
                    f"({created} ajoutés, {updated} intitulés actualisés)."
                )
            )
