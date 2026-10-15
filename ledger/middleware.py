from django.utils.deprecation import MiddlewareMixin
from django.shortcuts import redirect

class CompanyContextMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.company = None
        request.membership = None
        if request.user.is_authenticated:
            memberships = request.user.company_memberships.filter(active=True).select_related("company").order_by("id")
            selected = request.session.get("company_id")
            membership = memberships.filter(company_id=selected).first() if selected else None
            membership = membership or memberships.first()
            if membership:
                request.membership = membership
                request.company = membership.company

    def process_view(self, request, view_func, view_args, view_kwargs):
        membership = getattr(request, "membership", None)
        if membership and membership.must_change_password and request.resolver_match.url_name not in {"password_change", "logout"}:
            return redirect("password_change")
