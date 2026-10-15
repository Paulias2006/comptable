def workspace(request):
    companies = request.user.company_memberships.filter(active=True).select_related("company") if request.user.is_authenticated else []
    company = getattr(request, "company", None)
    pending_count = company.entries.filter(state="PENDING").count() if company else 0
    return {"current_company": company, "current_membership": getattr(request, "membership", None), "user_companies": companies, "pending_entries_count": pending_count}
