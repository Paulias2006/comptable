from getpass import getpass

from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ledger.models import InstallationState, Membership
from ledger.services.accounting import create_company_for_owner


class Command(BaseCommand):
    help = "Crée le premier compte administrateur et son entreprise depuis un terminal privé."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--company", required=True)
        parser.add_argument("--username", default="")
        parser.add_argument("--first-name", default="Administrateur")
        parser.add_argument("--last-name", default="")
        parser.add_argument("--tax-id", default="")
        parser.add_argument("--country", default="Togo")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        username = options["username"].strip() or email.split("@", 1)[0]
        if not username or not email or not options["company"].strip():
            raise CommandError("Identifiant, e-mail et nom d’entreprise sont obligatoires.")
        if User.objects.filter(username__iexact=username).exists() or User.objects.filter(email__iexact=email).exists():
            raise CommandError("Cet identifiant ou cet e-mail est déjà utilisé. Aucun compte n’a été modifié.")
        password = getpass("Mot de passe administrateur (saisi masqué): ")
        confirmation = getpass("Confirme le mot de passe: ")
        if password != confirmation:
            raise CommandError("Les mots de passe ne correspondent pas.")
        user = User(username=username, email=email, first_name=options["first_name"].strip(), last_name=options["last_name"].strip())
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError("Mot de passe refusé par la politique de sécurité: " + "; ".join(exc.messages))
        with transaction.atomic():
            state = InstallationState.objects.select_for_update().get(pk="primary")
            if state.claimed or Membership.objects.exists():
                raise CommandError("Un accès entreprise existe déjà. Utilise la gestion des utilisateurs après connexion.")
            user.set_password(password)
            user.save()
            company = create_company_for_owner(user=user, name=options["company"], tax_id=options["tax_id"], country=options["country"])
            state.claimed = True
            state.claimed_at = timezone.now()
            state.save(update_fields=["claimed", "claimed_at"])
        self.stdout.write(self.style.SUCCESS(f"Administrateur créé pour {company.name} ({email}). Aucun mot de passe n’a été affiché."))
