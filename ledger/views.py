import json
import os
import secrets
from datetime import date
from mimetypes import guess_type
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Sum, Count, Q, IntegerField
from django.db.models.functions import Cast
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_POST
from cloudinary.exceptions import Error as CloudinaryError
import requests

from ledger.forms import AccountForm, CompanyForm, LoginForm, MemberForm, OwnerRegistrationForm
from ledger.models import Account, AuditEvent, Company, FiscalPeriod, InstallationState, JournalEntry, JournalLine, Membership, SupportingDocument
from ledger.permissions import can_manage, can_post, require_company, roles
from ledger.reports import account_balances
from ledger.services.accounting import approve_entry, create_company_for_owner, post_entry, reject_entry, reverse_entry, validate_upload

def cloudinary_upload_error_message(exc):
    if "cloud_name mismatch" in str(exc).lower():
        return "Cloudinary refuse cette configuration : le nom du cloud, la clé API et le secret doivent provenir du même environnement produit (Console > Settings > API Keys)."
    return "Cloudinary a refusé la pièce. Vérifie les identifiants du même environnement produit, puis réessaie."

def setup(request):
    if not settings.DEBUG:
        raise Http404
    if Membership.objects.exists():
        InstallationState.objects.filter(pk="primary", claimed=False).update(claimed=True, claimed_at=timezone.now())
        return redirect("login")
    if request.method == "POST":
        form = OwnerRegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                state = InstallationState.objects.select_for_update().get(pk="primary")
                if state.claimed or Membership.objects.exists():
                    state.claimed = True
                    state.claimed_at = state.claimed_at or timezone.now()
                    state.save(update_fields=["claimed", "claimed_at"])
                    return redirect("login")
                user = form.save(commit=False)
                user.first_name = form.cleaned_data["first_name"]
                user.last_name = form.cleaned_data["last_name"]
                user.email = form.cleaned_data["email"]
                user.save()
                create_company_for_owner(user=user, name=form.cleaned_data["company_name"], tax_id=form.cleaned_data["tax_id"], country=form.cleaned_data["country"])
                state.claimed = True
                state.claimed_at = timezone.now()
                state.save(update_fields=["claimed", "claimed_at"])
                login(request, user)
                messages.success(request, "Votre espace comptable est prêt. Les écritures de démonstration ne sont pas importées.")
                return redirect("dashboard")
    else:
        form = OwnerRegistrationForm()
    return render(request, "ledger/setup.html", {"form": form})

def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if not Membership.objects.exists():
        raise Http404
    from django.contrib.auth import authenticate
    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if not user.company_memberships.filter(active=True).exists():
                form.add_error(None, "Aucun accès actif à une entreprise. Contacte son administrateur.")
            else:
                login(request, user)
                target = request.GET.get("next", "")
                if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                    return redirect(target)
                return redirect("dashboard")
    else:
        form = LoginForm(request)
    return render(request, "ledger/login.html", {"form": form})

@login_required
@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")

@login_required
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        request.user.company_memberships.filter(active=True).update(must_change_password=False)
        messages.success(request, "Mot de passe modifié.")
        return redirect("dashboard")
    return render(request, "ledger/password_change.html", {"form": form})

@login_required
@require_POST
def switch_company(request):
    membership = get_object_or_404(Membership, user=request.user, active=True, company_id=request.POST.get("company_id"))
    request.session["company_id"] = membership.company_id
    return redirect("dashboard")

@login_required
@require_company
def dashboard(request):
    company = request.company
    entries = JournalEntry.objects.filter(company=company).prefetch_related("lines__account", "documents")
    posted = entries.filter(state=JournalEntry.State.POSTED)
    totals = JournalLine.objects.filter(entry__company=company, entry__state=JournalEntry.State.POSTED).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    missing = posted.annotate(document_count=Count("documents")).filter(document_count=0)
    recent = entries[:8]
    return render(request, "ledger/dashboard.html", {"entry_count": entries.count(), "debit": totals["debit"] or 0,
        "credit": totals["credit"] or 0, "missing_count": missing.count(), "recent": recent,
        "pending_count": entries.filter(state=JournalEntry.State.PENDING).count(),
        "recent_backup": timezone.localtime(company.last_backup_at).strftime("%d/%m/%Y %H:%M") if company.last_backup_at else "Aucune sauvegarde effectuée"})

@login_required
@roles("OWNER")
def admin_workspace(request):
    company = request.company
    members_qs = Membership.objects.filter(company=company)
    entries_qs = JournalEntry.objects.filter(company=company)
    return render(request, "ledger/admin_workspace.html", {
        "member_count": members_qs.count(),
        "active_member_count": members_qs.filter(active=True).count(),
        "admin_count": members_qs.filter(role=Membership.Role.OWNER, active=True).count(),
        "pending_count": entries_qs.filter(state=JournalEntry.State.PENDING).count(),
        "posted_count": entries_qs.filter(state=JournalEntry.State.POSTED).count(),
        "document_count": SupportingDocument.objects.filter(entry__company=company).count(),
        "recent_events": AuditEvent.objects.filter(company=company).select_related("actor")[:8],
    })

@login_required
@require_company
def journal(request):
    qs = JournalEntry.objects.filter(company=request.company).prefetch_related("lines__account", "documents")
    query = request.GET.get("q", "").strip()
    journal_type = request.GET.get("type", "")
    start = request.GET.get("start", "")
    end = request.GET.get("end", "")
    if query:
        qs = qs.filter(description__icontains=query)
    if journal_type:
        qs = qs.filter(journal=journal_type)
    try:
        if start:
            qs = qs.filter(date__gte=date.fromisoformat(start))
        if end:
            qs = qs.filter(date__lte=date.fromisoformat(end))
    except ValueError:
        messages.error(request, "La période de recherche contient une date invalide.")
        qs = JournalEntry.objects.filter(company=request.company).prefetch_related("lines__account", "documents")
    return render(request, "ledger/journal.html", {"entries": qs[:500], "q": query, "type": journal_type, "start": start, "end": end})

@login_required
@roles("OWNER", "MANAGER")
def approvals(request):
    entries = JournalEntry.objects.filter(company=request.company, state=JournalEntry.State.PENDING).select_related("created_by").prefetch_related("lines__account", "documents").order_by("date", "number")
    return render(request, "ledger/approvals.html", {"entries": entries})

@login_required
@require_company
def entry_new(request):
    accounts = Account.objects.filter(company=request.company, active=True).order_by(Cast("code", IntegerField()))
    if not can_post(request):
        messages.error(request, "Votre rôle ne permet pas de saisir des écritures.")
        return redirect("journal")
    if request.method == "POST":
        try:
            rows = json.loads(request.POST.get("lines_json", "[]"))
            target_state = JournalEntry.State.PENDING if request.membership.role == "ACCOUNTANT" else JournalEntry.State.POSTED
            entry = post_entry(company=request.company, user=request.user, date=date.fromisoformat(request.POST.get("date", "")),
                journal=request.POST.get("journal", "ACH"), description=request.POST.get("description", ""),
                lines=rows, documents=request.FILES.getlist("documents"), state=target_state)
            messages.success(request, f"Écriture E-{entry.number:06d} envoyée au responsable pour validation." if target_state == JournalEntry.State.PENDING else f"Écriture E-{entry.number:06d} comptabilisée et équilibrée.")
            return redirect("entry_detail", pk=entry.pk)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Vérifie la date et les lignes de l’écriture.")
        except CloudinaryError as exc:
            messages.error(request, cloudinary_upload_error_message(exc) + " L’écriture n’a pas été enregistrée.")
        except requests.RequestException:
            messages.error(request, "Cloudinary est momentanément inaccessible. L’écriture n’a pas été enregistrée ; réessaie sans pièce ou plus tard.")
    return render(request, "ledger/entry_form.html", {"accounts": accounts, "today": timezone.localdate().isoformat()})

@login_required
@require_company
def entry_detail(request, pk):
    entry = get_object_or_404(JournalEntry.objects.prefetch_related("lines__account", "documents"), pk=pk, company=request.company)
    return render(request, "ledger/entry_detail.html", {"entry": entry})

@login_required
@require_POST
@require_company
def entry_reverse(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk, company=request.company)
    if not can_post(request):
        messages.error(request, "Votre rôle ne permet pas de contrepasser une écriture.")
        return redirect("entry_detail", pk=pk)
    try:
        reversal = reverse_entry(entry=entry, user=request.user)
        messages.success(request, f"Contrepassation enregistrée sous E-{reversal.number:06d}.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("entry_detail", pk=pk)

@login_required
@require_POST
@roles("OWNER", "MANAGER")
def entry_approve(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk, company=request.company)
    try:
        approve_entry(entry=entry, user=request.user)
        messages.success(request, f"E-{entry.number:06d} validée et intégrée au journal comptable.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("entry_detail", pk=entry.pk)

@login_required
@require_POST
@roles("OWNER", "MANAGER")
def entry_reject(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk, company=request.company)
    try:
        reject_entry(entry=entry, user=request.user, reason=request.POST.get("reason", ""))
        messages.warning(request, f"E-{entry.number:06d} rejetée. L’écriture reste conservée et n’entre pas dans les rapports.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("entry_detail", pk=entry.pk)

@login_required
@require_company
def documents(request):
    docs = SupportingDocument.objects.filter(entry__company=request.company).select_related("entry", "uploaded_by")
    return render(request, "ledger/documents.html", {"documents": docs[:500]})

@login_required
def _document_response(request, pk, *, inline):
    doc = get_object_or_404(SupportingDocument.objects.select_related("entry"), pk=pk, entry__company__memberships__user=request.user, entry__company__memberships__active=True)
    try:
        handle = doc.file.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404("Le fichier archivé est introuvable.")
    filename = Path(doc.original_name).name
    action = "document.viewed" if inline else "document.downloaded"
    AuditEvent.objects.create(company=doc.entry.company, actor=request.user, action=action, object_type="SupportingDocument", object_id=str(doc.pk), details={"entry": doc.entry.number})
    response = FileResponse(
        handle,
        as_attachment=not inline,
        filename=filename,
        content_type=guess_type(filename)[0] or "application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response

@login_required
def document_preview(request, pk):
    return _document_response(request, pk, inline=True)

@login_required
def document_download(request, pk):
    return _document_response(request, pk, inline=False)

@login_required
@require_company
def missing_documents(request):
    entries = JournalEntry.objects.filter(company=request.company, state=JournalEntry.State.POSTED).annotate(document_count=Count("documents")).filter(document_count=0)
    return render(request, "ledger/missing.html", {"entries": entries})

@login_required
@roles("OWNER", "ACCOUNTANT")
def document_add(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk, company=request.company)
    if request.method == "POST":
        files = request.FILES.getlist("documents")
        if not files:
            messages.error(request, "Choisis au moins un fichier.")
        else:
            import hashlib
            saved = 0
            for uploaded in files:
                try:
                    validate_upload(uploaded)
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    continue
                digest = hashlib.sha256(uploaded.read()).hexdigest(); uploaded.seek(0)
                try:
                    SupportingDocument.objects.create(entry=entry, file=uploaded, original_name=Path(uploaded.name).name[:255], sha256=digest, size=uploaded.size, uploaded_by=request.user)
                except CloudinaryError as exc:
                    messages.error(request, cloudinary_upload_error_message(exc))
                    continue
                except (requests.RequestException, OSError):
                    messages.error(request, "Cloudinary est momentanément inaccessible. Vérifie la connexion Internet, puis réessaie.")
                    continue
                AuditEvent.objects.create(company=request.company, actor=request.user, action="document.added", object_type="SupportingDocument", details={"entry": entry.number, "sha256": digest})
                saved += 1
            if saved:
                messages.success(request, f"{saved} pièce(s) enregistrée(s) dans l’archive privée.")
        return redirect("missing_documents")
    return render(request, "ledger/document_add.html", {"entry": entry})

@login_required
@require_company
def accounts(request):
    qs = Account.objects.filter(company=request.company)
    query = request.GET.get("q", "").strip()
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(code__icontains=query))
    qs = qs.order_by(Cast("code", IntegerField()))
    page_obj = Paginator(qs, 50).get_page(request.GET.get("page"))
    form = AccountForm(request.POST or None)
    if request.method == "POST" and can_manage(request):
        if form.is_valid():
            if Account.objects.filter(company=request.company, code=form.cleaned_data["code"]).exists():
                form.add_error("code", "Ce numéro de compte existe déjà dans cette entreprise.")
                return render(request, "ledger/accounts.html", {"accounts": page_obj, "form": form, "q": query})
            account = form.save(commit=False); account.company = request.company; account.save()
            AuditEvent.objects.create(company=request.company, actor=request.user, action="account.created", object_type="Account", object_id=str(account.pk))
            messages.success(request, "Compte ajouté au plan comptable.")
            return redirect("accounts")
    elif request.method == "POST":
        messages.error(request, "Seul l’administrateur peut modifier le plan comptable.")
    return render(request, "ledger/accounts.html", {"accounts": page_obj, "form": form, "q": query})

@login_required
@roles("OWNER")
def company_settings(request):
    form = CompanyForm(request.POST or None, instance=request.company)
    if request.method == "POST" and form.is_valid():
        company = form.save()
        AuditEvent.objects.create(company=company, actor=request.user, action="company.updated", object_type="Company", object_id=str(company.pk))
        messages.success(request, "Fiche entreprise enregistrée.")
        return redirect("company_settings")
    return render(request, "ledger/company.html", {"form": form, "companies": request.user.company_memberships.filter(active=True).select_related("company"),
        "periods": FiscalPeriod.objects.filter(company=request.company)})

@login_required
@roles("OWNER")
@require_POST
def fiscal_period_action(request):
    action = request.POST.get("action")
    if action == "create":
        try:
            label = request.POST.get("label", "").strip()
            start = date.fromisoformat(request.POST.get("start_date", ""))
            end = date.fromisoformat(request.POST.get("end_date", ""))
            if not label or end < start:
                raise ValueError
            overlaps = FiscalPeriod.objects.filter(company=request.company, start_date__lte=end, end_date__gte=start).exists()
            if overlaps:
                messages.error(request, "Les dates chevauchent un exercice existant.")
            else:
                period = FiscalPeriod.objects.create(company=request.company, label=label, start_date=start, end_date=end)
                AuditEvent.objects.create(company=request.company, actor=request.user, action="fiscal_period.created", object_type="FiscalPeriod", object_id=str(period.pk))
                messages.success(request, "Exercice comptable créé.")
        except ValueError:
            messages.error(request, "Vérifie le libellé et les dates de l’exercice.")
    elif action == "close":
        period = get_object_or_404(FiscalPeriod, pk=request.POST.get("period_id"), company=request.company)
        if period.closed:
            messages.error(request, "Cet exercice est déjà clôturé.")
        else:
            pending = JournalEntry.objects.filter(company=request.company, fiscal_period=period, state=JournalEntry.State.PENDING).exists()
            totals = JournalLine.objects.filter(entry__company=request.company, entry__fiscal_period=period, entry__state=JournalEntry.State.POSTED).aggregate(debit=Sum("debit"), credit=Sum("credit"))
            if pending:
                messages.error(request, "Clôture refusée : valide ou rejette d’abord les écritures en attente de cet exercice.")
            elif (totals["debit"] or 0) != (totals["credit"] or 0):
                messages.error(request, "Clôture refusée : la balance de l’exercice est déséquilibrée.")
            else:
                period.closed = True; period.closed_at = timezone.now(); period.closed_by = request.user
                period.save(update_fields=["closed", "closed_at", "closed_by"])
                AuditEvent.objects.create(company=request.company, actor=request.user, action="fiscal_period.closed", object_type="FiscalPeriod", object_id=str(period.pk))
                messages.success(request, "Exercice clôturé. Toute nouvelle saisie à ces dates est désormais bloquée.")
    return redirect("company_settings")

@login_required
@roles("OWNER")
@transaction.atomic
def members(request):
    form = MemberForm(request.POST or None)
    generated_password = request.session.pop("new_member_password", None)
    if request.method == "POST" and request.POST.get("membership_id"):
        action = request.POST.get("action")
        # Serialize owner changes on the company row so concurrent requests
        # cannot both demote/disable the last two administrators.
        Company.objects.select_for_update().get(pk=request.company.pk)
        current_membership = Membership.objects.filter(company=request.company, user=request.user, active=True).first()
        if not current_membership or current_membership.role != Membership.Role.OWNER:
            messages.error(request, "Ton accès administrateur a été modifié. Reconnecte-toi pour continuer.")
            return redirect("dashboard")
        membership = get_object_or_404(Membership.objects.select_for_update(), pk=request.POST["membership_id"], company=request.company)
        active_owners = Membership.objects.filter(company=request.company, role=Membership.Role.OWNER, active=True).count()
        if action == "role" and membership.role == Membership.Role.OWNER and request.POST.get("role") != "OWNER" and active_owners <= 1:
            messages.error(request, "Conserve au moins un administrateur actif pour cette entreprise.")
        elif action == "role" and membership.user_id == request.user.id and request.POST.get("role") != "OWNER":
            messages.error(request, "Tu ne peux pas retirer toi-même ton rôle administrateur.")
        elif action == "role":
            role = request.POST.get("role")
            if role in {"OWNER", "ACCOUNTANT", "MANAGER", "AUDITOR"}:
                membership.role = role; membership.save(update_fields=["role"])
                AuditEvent.objects.create(company=request.company, actor=request.user, action="member.role_changed", object_type="Membership", object_id=str(membership.pk), details={"role": role})
                messages.success(request, "Rôle utilisateur mis à jour.")
        elif action == "disable" and membership.role == Membership.Role.OWNER and active_owners <= 1:
            messages.error(request, "Impossible de suspendre le dernier administrateur actif.")
        elif action == "disable" and membership.user_id == request.user.id:
            messages.error(request, "Tu ne peux pas suspendre ton propre accès administrateur.")
        elif action == "disable":
            membership.active = False; membership.save(update_fields=["active"])
            AuditEvent.objects.create(company=request.company, actor=request.user, action="member.disabled", object_type="Membership", object_id=str(membership.pk))
            messages.success(request, "Accès à cette entreprise suspendu.")
        elif action == "enable":
            membership.active = True; membership.save(update_fields=["active"])
            AuditEvent.objects.create(company=request.company, actor=request.user, action="member.enabled", object_type="Membership", object_id=str(membership.pk))
            messages.success(request, "Accès à cette entreprise réactivé.")
        elif action == "reset_password":
            other_companies = membership.user.company_memberships.exclude(company=request.company)
            if membership.user_id == request.user.id:
                messages.error(request, "Utilise la page de changement de ton propre mot de passe.")
            elif other_companies.exists():
                messages.error(request, "Ce compte est partagé avec une autre entreprise. Pour protéger les autres accès, demande à son utilisateur de changer lui-même son mot de passe.")
            else:
                generated_password = secrets.token_urlsafe(24)
                membership.user.set_password(generated_password)
                membership.user.save(update_fields=["password"])
                membership.user.company_memberships.filter(company=request.company, active=True).update(must_change_password=True)
                request.session["new_member_password"] = generated_password
                AuditEvent.objects.create(company=request.company, actor=request.user, action="member.password_reset", object_type="User", object_id=str(membership.user_id))
                messages.success(request, "Mot de passe provisoire généré. Le changement sera exigé à la prochaine connexion.")
        else:
            messages.error(request, "Opération utilisateur non autorisée.")
        return redirect("members")
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        user = User.objects.filter(username=data["username"]).first()
        email_user = User.objects.filter(email=data["email"]).first()
        if user and (user.email.lower() != data["email"].lower() or user != email_user):
            messages.error(request, "Cet identifiant est déjà associé à une autre adresse e-mail.")
        elif email_user and user != email_user:
            messages.error(request, "Cette adresse e-mail est associée à un autre identifiant.")
        elif user and Membership.objects.filter(company=request.company, user=user).exists():
            messages.error(request, "Cet utilisateur a déjà un accès à cette entreprise.")
        else:
            if user is None:
                generated_password = secrets.token_urlsafe(15)
                user = User.objects.create_user(username=data["username"], password=generated_password, email=data["email"], first_name=data["first_name"], last_name=data["last_name"])
                request.session["new_member_password"] = generated_password
            else:
                user.first_name = data["first_name"]; user.last_name = data["last_name"]; user.save(update_fields=["first_name", "last_name"])
                generated_password = None
            Membership.objects.create(company=request.company, user=user, role=data["role"], must_change_password=bool(generated_password))
            AuditEvent.objects.create(company=request.company, actor=request.user, action="member.added", object_type="User", object_id=str(user.pk), details={"role": data["role"]})
            messages.success(request, "Accès rattaché. Un mot de passe temporaire a été généré." if generated_password else "Accès à l’entreprise ajouté au compte existant.")
            return redirect("members")
    return render(request, "ledger/members.html", {"form": form, "members": Membership.objects.filter(company=request.company).select_related("user"), "generated_password": generated_password,
        "companies": request.user.company_memberships.filter(active=True).select_related("company")})

@login_required
@roles("OWNER")
def add_company(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Le nom de l’entreprise est obligatoire.")
        else:
            company = create_company_for_owner(user=request.user, name=name, tax_id=request.POST.get("tax_id", ""), country=request.POST.get("country", "Togo"))
            request.session["company_id"] = company.pk
            messages.success(request, "Entreprise créée avec son espace comptable vide.")
    return redirect("company_settings")

@login_required
@roles("OWNER", "ACCOUNTANT", "MANAGER", "AUDITOR")
def reports(request):
    through = request.GET.get("through", "")
    try:
        through = date.fromisoformat(through) if through else None
    except ValueError:
        through = None
    balances = account_balances(request.company, through)
    return render(request, "ledger/reports.html", {"balances": balances, "through": through})

@login_required
@roles("OWNER", "ACCOUNTANT", "MANAGER", "AUDITOR")
def audit_log(request):
    return render(request, "ledger/audit.html", {"events": AuditEvent.objects.filter(company=request.company).select_related("actor")[:300]})

@login_required
@roles("OWNER", "ACCOUNTANT", "MANAGER", "AUDITOR")
def export(request):
    from ledger.exports import export_zip, export_xlsx, export_pdf
    kind = request.GET.get("type", "xlsx")
    if not request.GET.get("type"):
        return render(request, "ledger/exports.html", {"entries_count": JournalEntry.objects.filter(company=request.company).count(),
            "documents_count": SupportingDocument.objects.filter(entry__company=request.company).count()})
    try:
        if kind == "pdf":
            return export_pdf(request.company)
        if kind == "zip":
            return export_zip(request.company)
        return export_xlsx(request.company)
    except (OSError, RuntimeError) as exc:
        messages.error(request, f"Export impossible : {exc}")
        return redirect("export")

@login_required
@roles("OWNER")
@require_POST
def backup(request):
    from ledger.backups import create_backup
    try:
        path = create_backup(request.company)
        request.company.last_backup_at = timezone.now()
        request.company.save(update_fields=["last_backup_at"])
        AuditEvent.objects.create(company=request.company, actor=request.user, action="backup.created", object_type="Company", object_id=str(request.company.pk), details={"file": path.name})
        return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)
    except (OSError, RuntimeError, ValueError) as exc:
        messages.error(request, f"Sauvegarde impossible : {exc}")
        return redirect("dashboard")
