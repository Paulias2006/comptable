from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User

from ledger.models import Account, Company, Membership

class LoginForm(AuthenticationForm):
    username = forms.CharField(label="E-mail ou identifiant", widget=forms.TextInput(attrs={"autocomplete": "username", "placeholder": "Votre e-mail ou identifiant"}))
    password = forms.CharField(label="Mot de passe", widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "placeholder": "Votre mot de passe"}))

    def clean(self):
        submitted = self.cleaned_data.get("username", "").strip()
        if "@" in submitted:
            user = User.objects.filter(email__iexact=submitted).only("username").first()
            if user:
                self.cleaned_data["username"] = user.username
        return super().clean()

class OwnerRegistrationForm(UserCreationForm):
    first_name = forms.CharField(label="Prénom", max_length=150)
    last_name = forms.CharField(label="Nom", max_length=150)
    email = forms.EmailField(label="Adresse e-mail")
    company_name = forms.CharField(label="Nom de l’entreprise", max_length=180)
    tax_id = forms.CharField(label="Identifiant fiscal", max_length=80, required=False)
    country = forms.CharField(label="Pays", max_length=80, initial="Togo")
    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "password1", "password2")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Identifiant de connexion"
        self.fields["password1"].label = "Mot de passe (10 caractères minimum)"
        self.fields["password2"].label = "Confirmer le mot de passe"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Cette adresse e-mail est déjà utilisée. Connecte-toi ou demande à un administrateur de t’ajouter à une entreprise.")
        return email

class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ("code", "name")
        labels = {"code": "Numéro du compte", "name": "Intitulé"}

class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ("name", "tax_id", "country", "currency", "fiscal_start_month")
        labels = {"name": "Nom de l’entreprise", "tax_id": "Identifiant fiscal", "country": "Pays", "currency": "Devise (code ISO)", "fiscal_start_month": "Mois de début de l’exercice"}
        widgets = {"fiscal_start_month": forms.Select(choices=[(i, name) for i, name in enumerate(["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]) if i])}

class MemberForm(forms.Form):
    username = forms.CharField(label="Identifiant", max_length=150)
    first_name = forms.CharField(label="Prénom", max_length=150)
    last_name = forms.CharField(label="Nom", max_length=150)
    email = forms.EmailField(label="E-mail")
    role = forms.ChoiceField(label="Rôle", choices=[(Membership.Role.OWNER, "Administrateur"), (Membership.Role.ACCOUNTANT, "Comptable"), (Membership.Role.MANAGER, "Responsable"), (Membership.Role.AUDITOR, "Auditeur")])
