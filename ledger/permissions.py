from functools import wraps

from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect

def require_company(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "company", None):
            messages.error(request, "Aucune entreprise n’est associée à ce compte.")
            if request.user.is_authenticated:
                logout(request)
            return redirect("login")
        return view(request, *args, **kwargs)
    return wrapped

def roles(*allowed):
    def decorate(view):
        @wraps(view)
        @require_company
        def wrapped(request, *args, **kwargs):
            if request.membership.role not in allowed:
                messages.error(request, "Votre rôle ne permet pas cette opération.")
                return redirect("dashboard")
            return view(request, *args, **kwargs)
        return wrapped
    return decorate

def can_post(request):
    return request.membership and request.membership.role in {"OWNER", "ACCOUNTANT"}

def can_manage(request):
    return request.membership and request.membership.role == "OWNER"
