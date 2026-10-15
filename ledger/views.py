import json
import os
import secrets
from datetime import date
from decimal import Decimal, InvalidOperation
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
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_POST
from cloudinary.exceptions import Error as CloudinaryError
import requests

from ledger.forms import AccountForm, CompanyForm, LoginForm, MemberForm, OwnerRegistrationForm, ThirdPartyForm
from ledger.models import Account, AuditEvent, BankReconciliation, Company, FiscalPeriod, InstallationState, JournalEntry, JournalLine, Membership, ReconciliationMatch, SupportingDocument, ThirdParty
from ledger.permissions import can_manage, can_post, require_company, roles
from ledger.reports import account_balances
from ledger.services.accounting import approve_entry, create_company_for_owner, post_entry, reject_entry, reverse_entry, update_pending_entry, validate_upload

def cloudinary_upload_error_message(exc):
    if "cloud_name mismatch" in str(exc).lower():
        return "Cloudinary refuse cette configuration : le nom du cloud, la clé API et le secret doivent provenir du même environnement produit (Console > Settings > API Keys)."
    return "Cloudinary a refusé la pièce. Vérifie les identifiants du même environnement produit, puis réessaie."

def setup(request):
    # Local development remains convenient. In production the setup endpoint is
    # enabled only by an out-of-band, high-entropy Render environment secret.
    if not settings.DEBUG and not settings.DJANGO_SETUP_TOKEN:
        raise Http404
    if Membership.objects.exists():
        InstallationState.objects.filter(pk="primary", claimed=False).update(claimed=True, claimed_at=timezone.now())
        return redirect("login")
    state = InstallationState.objects.get(pk="primary")
    if state.claimed:
        raise Http404
    token_error = ""
    if request.method == "POST":
        supplied_token = request.POST.get("setup_token", "")
        token_valid = settings.DEBUG or secrets.compare_digest(supplied_token, settings.DJANGO_SETUP_TOKEN)
        if not token_valid:
            token_error = "Jeton d’initialisation invalide. Copie-le depuis les variables Render."
        form = OwnerRegistrationForm(request.POST)
        if token_valid and form.is_valid():
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
            # The configured Axes + Model backends make Django require an
            # explicit backend here. Keep login outside the DB transaction so
            # a session error cannot roll back the newly created company.
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, "Votre espace comptable est prêt. Pour verrouiller aussi l’initialisation, supprimez DJANGO_SETUP_TOKEN dans Render.")
            return redirect("dashboard")
    else:
        form = OwnerRegistrationForm()
    return render(request, "ledger/setup.html", {"form": form, "token_required": not settings.DEBUG, "token_error": token_error})

def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
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

def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = OwnerRegistrationForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save(commit=False)
                    user.first_name = form.cleaned_data["first_name"]
                    user.last_name = form.cleaned_data["last_name"]
                    user.email = form.cleaned_data["email"]
                    user.save()
                    create_company_for_owner(
                        user=user,
                        name=form.cleaned_data["company_name"],
                        tax_id=form.cleaned_data["tax_id"],
                        country=form.cleaned_data["country"],
                    )
            except IntegrityError:
                form.add_error(None, "Ce compte vient d’être créé ou l’identifiant est déjà utilisé. Vérifie les champs et connecte-toi si tu as déjà un compte.")
            else:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                messages.success(request, "Ton espace comptable est prêt. Tu es administrateur de cette entreprise.")
                return redirect("dashboard")
    else:
        form = OwnerRegistrationForm()
    return render(request, "ledger/signup.html", {"form": form})

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
        search = (
            Q(description__icontains=query)
            | Q(document_reference__icontains=query)
            | Q(lines__account__code__icontains=query)
            | Q(lines__account__name__icontains=query)
            | Q(lines__description__icontains=query)
            | Q(documents__original_name__icontains=query)
        )
        entry_number = query.upper().removeprefix("E-")
        if entry_number.isdigit():
            search |= Q(number=int(entry_number))
        qs = qs.filter(search).distinct()
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
    initial_lines = []
    reference = ""
    entry_date = timezone.localdate().isoformat()
    selected_journal = "ACH"
    description = ""
    if not can_post(request):
        messages.error(request, "Votre rôle ne permet pas de saisir des écritures.")
        return redirect("journal")
    if request.method == "POST":
        try:
            rows = json.loads(request.POST.get("lines_json", "[]"))
            initial_lines = rows
            reference = request.POST.get("document_reference", "")
            entry_date = request.POST.get("date", entry_date)
            selected_journal = request.POST.get("journal", "ACH")
            description = request.POST.get("description", "")
            target_state = JournalEntry.State.PENDING if request.membership.role == "ACCOUNTANT" else JournalEntry.State.POSTED
            entry = post_entry(company=request.company, user=request.user, date=date.fromisoformat(request.POST.get("date", "")),
                journal=request.POST.get("journal", "ACH"), description=request.POST.get("description", ""),
                lines=rows, documents=request.FILES.getlist("documents"), state=target_state, document_reference=reference)
            messages.success(request, f"Écriture E-{entry.number:06d} envoyée au responsable pour validation." if target_state == JournalEntry.State.PENDING else f"Écriture E-{entry.number:06d} comptabilisée et équilibrée.")
            return redirect("entry_detail", pk=entry.pk)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Vérifie la date et les lignes de l’écriture.")
        except CloudinaryError as exc:
            messages.error(request, cloudinary_upload_error_message(exc) + " L’écriture n’a pas été enregistrée.")
        except requests.RequestException:
            messages.error(request, "Cloudinary est momentanément inaccessible. L’écriture n’a pas été enregistrée ; réessaie sans pièce ou plus tard.")
    return render(request, "ledger/entry_form.html", {"accounts": accounts, "today": entry_date,
        "initial_lines": initial_lines, "reference": reference, "selected_journal": selected_journal, "description": description})

@login_required
@require_company
def entry_detail(request, pk):
    entry = get_object_or_404(JournalEntry.objects.prefetch_related("lines__account", "documents"), pk=pk, company=request.company)
    return render(request, "ledger/entry_detail.html", {"entry": entry})


@login_required
@roles("OWNER", "ACCOUNTANT")
def entry_edit(request, pk):
    entry = get_object_or_404(JournalEntry.objects.prefetch_related("lines__account", "documents"), pk=pk, company=request.company)
    if entry.state != JournalEntry.State.PENDING:
        messages.error(request, "Cette écriture est déjà comptabilisée ou clôturée. Corrige-la par contrepassation pour conserver la piste d’audit.")
        return redirect("entry_detail", pk=pk)
    accounts = Account.objects.filter(company=request.company, active=True).order_by(Cast("code", IntegerField()))
    initial_lines = [{"account_id": line.account_id, "description": line.description, "debit": str(line.debit), "credit": str(line.credit)} for line in entry.lines.all()]
    reference, entry_date = entry.document_reference, entry.date.isoformat()
    selected_journal, description = entry.journal, entry.description
    if request.method == "POST":
        try:
            rows = json.loads(request.POST.get("lines_json", "[]"))
            initial_lines = rows
            reference = request.POST.get("document_reference", "")
            entry_date = request.POST.get("date", entry_date)
            selected_journal = request.POST.get("journal", entry.journal)
            description = request.POST.get("description", "")
            update_pending_entry(entry=entry, company=request.company, user=request.user,
                date=date.fromisoformat(entry_date), journal=selected_journal, description=description,
                document_reference=reference, lines=rows, documents=request.FILES.getlist("documents"))
            messages.success(request, f"Écriture E-{entry.number:06d} mise à jour et renvoyée pour validation.")
            return redirect("entry_detail", pk=entry.pk)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Vérifie la date et les lignes de l’écriture.")
        except CloudinaryError as exc:
            messages.error(request, cloudinary_upload_error_message(exc) + " L’écriture n’a pas été modifiée.")
        except requests.RequestException:
            messages.error(request, "Le stockage des pièces est momentanément inaccessible. L’écriture n’a pas été modifiée.")
    return render(request, "ledger/entry_form.html", {"entry": entry, "accounts": accounts,
        "today": entry_date, "initial_lines": initial_lines, "reference": reference,
        "selected_journal": selected_journal, "description": description})

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
def third_parties(request):
    form = ThirdPartyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            company = Company.objects.select_for_update().get(pk=request.company.pk)
            kind = form.cleaned_data["kind"]
            prefix = "401" if kind == ThirdParty.Kind.SUPPLIER else "411"
            sequence = ThirdParty.objects.filter(company=company, kind=kind).count() + 1
            third_code = f"F{sequence:06d}" if kind == ThirdParty.Kind.SUPPLIER else f"C{sequence:06d}"
            account_code = f"{prefix}{sequence:06d}"
            while Account.objects.filter(company=company, code=account_code).exists():
                sequence += 1
                third_code = f"F{sequence:06d}" if kind == ThirdParty.Kind.SUPPLIER else f"C{sequence:06d}"
                account_code = f"{prefix}{sequence:06d}"
            account = Account.objects.create(company=company, code=account_code, name=form.cleaned_data["name"][:160])
            third_party = form.save(commit=False)
            third_party.company, third_party.code, third_party.account = company, third_code, account
            third_party.save()
            AuditEvent.objects.create(company=company, actor=request.user, action="third_party.created", object_type="ThirdParty", object_id=str(third_party.pk), details={"kind": kind, "account": account_code})
        messages.success(request, f"{third_party.get_kind_display()} ajouté à {request.company.name} avec le compte {account_code}.")
        return redirect("third_parties")
    parties = ThirdParty.objects.filter(company=request.company, active=True).select_related("account")
    return render(request, "ledger/third_parties.html", {"form": form, "third_parties": parties})


@login_required
@roles("OWNER", "ACCOUNTANT", "MANAGER", "AUDITOR")
def general_ledger(request):
    accounts = Account.objects.filter(company=request.company, active=True).order_by(Cast("code", IntegerField()))
    account = accounts.filter(pk=request.GET.get("account", "")).first() if request.GET.get("account") else None
    start_raw, end_raw = request.GET.get("start", ""), request.GET.get("end", "")
    start = end = None
    try:
        start = date.fromisoformat(start_raw) if start_raw else None
        end = date.fromisoformat(end_raw) if end_raw else None
        if start and end and end < start:
            raise ValueError
    except ValueError:
        messages.error(request, "La période du grand livre est invalide.")
        start_raw, end_raw, start, end = "", "", None, None

    rows = []
    opening = 0
    if account:
        posted = JournalLine.objects.filter(entry__company=request.company, entry__state=JournalEntry.State.POSTED, account=account)
        if start:
            prior = posted.filter(entry__date__lt=start).aggregate(debit=Sum("debit"), credit=Sum("credit"))
            opening = (prior["debit"] or 0) - (prior["credit"] or 0)
        period_lines = posted
        if start:
            period_lines = period_lines.filter(entry__date__gte=start)
        if end:
            period_lines = period_lines.filter(entry__date__lte=end)
        running = opening
        for line in period_lines.select_related("entry", "account").order_by("entry__date", "entry__number", "pk")[:10000]:
            running += line.debit - line.credit
            line.running_balance = running
            rows.append(line)
    page_obj = Paginator(rows, 100).get_page(request.GET.get("page"))
    return render(request, "ledger/general_ledger.html", {"accounts": accounts, "account": account,
        "opening": opening, "rows": page_obj, "start": start_raw, "end": end_raw})


def _reconciliation_totals(reconciliation):
    matched_ids = ReconciliationMatch.objects.filter(
        reconciliation__company=reconciliation.company,
        reconciliation__account=reconciliation.account,
        reconciliation__created_at__lte=reconciliation.created_at,
        reconciliation__end_date__lte=reconciliation.end_date,
    ).values("journal_line_id")
    book = JournalLine.objects.filter(entry__company=reconciliation.company,
        entry__state=JournalEntry.State.POSTED, entry__date__lte=reconciliation.end_date,
        account=reconciliation.account).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    outstanding = JournalLine.objects.filter(entry__company=reconciliation.company,
        entry__state=JournalEntry.State.POSTED, entry__date__lte=reconciliation.end_date,
        account=reconciliation.account).exclude(pk__in=matched_ids).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    book_balance = (book["debit"] or Decimal("0")) - (book["credit"] or Decimal("0"))
    outstanding_net = (outstanding["debit"] or Decimal("0")) - (outstanding["credit"] or Decimal("0"))
    adjusted_statement = reconciliation.statement_balance + outstanding_net
    return {"book_balance": book_balance, "outstanding_net": outstanding_net,
        "adjusted_statement": adjusted_statement, "difference": book_balance - adjusted_statement}


@login_required
@roles("OWNER", "ACCOUNTANT", "MANAGER", "AUDITOR")
def bank_reconciliation(request):
    accounts = Account.objects.filter(company=request.company, active=True, code__startswith="52").order_by(Cast("code", IntegerField()))
    account_id = request.POST.get("account") if request.method == "POST" else request.GET.get("account")
    account = accounts.filter(pk=account_id).first() if account_id else None
    start_raw = request.POST.get("start_date", "") if request.method == "POST" else request.GET.get("start_date", "")
    end_raw = request.POST.get("end_date", "") if request.method == "POST" else request.GET.get("end_date", "")
    start = end = None
    if start_raw and end_raw:
        try:
            start, end = date.fromisoformat(start_raw), date.fromisoformat(end_raw)
            if end < start:
                raise ValueError
        except ValueError:
            messages.error(request, "La période de rapprochement est invalide.")
            start = end = None
    if request.method == "POST" and request.membership.role in {"OWNER", "ACCOUNTANT"} and account and start and end:
        try:
            raw_balance = request.POST.get("statement_balance", "0").replace(" ", "").replace(",", ".")
            statement_balance = Decimal(raw_balance)
            if not statement_balance.is_finite() or statement_balance.as_tuple().exponent < -2:
                raise InvalidOperation
            with transaction.atomic():
                reconciliation = BankReconciliation.objects.create(company=request.company, account=account,
                    start_date=start, end_date=end, statement_balance=statement_balance, created_by=request.user)
                selected = request.POST.getlist("lines")
                eligible = JournalLine.objects.filter(pk__in=selected, entry__company=request.company,
                    entry__state=JournalEntry.State.POSTED, entry__date__lte=end, account=account)
                if eligible.count() != len(set(selected)) or ReconciliationMatch.objects.filter(journal_line__in=eligible).exists():
                    raise ValueError("Un mouvement choisi est déjà rapproché ou n’appartient pas à ce compte.")
                ReconciliationMatch.objects.bulk_create([ReconciliationMatch(reconciliation=reconciliation, journal_line=line) for line in eligible])
                AuditEvent.objects.create(company=request.company, actor=request.user, action="bank.reconciliation.created",
                    object_type="BankReconciliation", object_id=str(reconciliation.pk), details={"account": account.code, "end_date": end.isoformat(), "matched_count": eligible.count()})
            messages.success(request, "État de rapprochement enregistré. Consulte le solde ajusté et l’écart calculé ci-dessous.")
            return redirect(f"/rapprochement-bancaire/?account={account.pk}&start_date={start.isoformat()}&end_date={end.isoformat()}&reconciliation={reconciliation.pk}")
        except (InvalidOperation, ValueError, IntegrityError):
            messages.error(request, "Vérifie le solde du relevé et les mouvements sélectionnés. Aucun rapprochement n’a été enregistré.")
    elif request.method == "POST":
        messages.error(request, "Il faut un compte et une période valides ; seul un administrateur ou un comptable peut enregistrer le rapprochement.")
    candidates = JournalLine.objects.none()
    if account and end:
        candidates = JournalLine.objects.filter(entry__company=request.company, entry__state=JournalEntry.State.POSTED,
            entry__date__lte=end, account=account).exclude(pk__in=ReconciliationMatch.objects.values("journal_line_id"))
        candidates = candidates.select_related("entry").order_by("entry__date", "entry__number")[:500]
    reconciliations = BankReconciliation.objects.filter(company=request.company).select_related("account", "created_by")[:30]
    reports = [{"item": item, **_reconciliation_totals(item)} for item in reconciliations]
    selected_report = None
    selected_pk = request.GET.get("reconciliation")
    if selected_pk:
        item = next((row["item"] for row in reports if str(row["item"].pk) == selected_pk), None)
        if item:
            selected_report = {"item": item, **_reconciliation_totals(item)}
    return render(request, "ledger/reconciliation.html", {"accounts": accounts, "account": account,
        "start": start_raw, "end": end_raw, "candidates": candidates, "reports": reports, "selected_report": selected_report,
        "statement_balance": request.POST.get("statement_balance", "") if request.method == "POST" else ""})

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
