from django.contrib import admin

from ledger.models import Account, AuditEvent, Company, JournalEntry, JournalLine, Membership, SupportingDocument

class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    can_delete = False

@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("number", "company", "date", "journal", "description", "state", "created_by")
    list_filter = ("company", "state", "journal", "date")
    search_fields = ("description", "number")
    inlines = [JournalLineInline]
    def has_delete_permission(self, request, obj=None): return False

@admin.register(AuditEvent)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "company", "actor", "action", "object_type", "object_id")
    readonly_fields = ("company", "actor", "action", "object_type", "object_id", "details", "created_at")
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

admin.site.register([Company, Membership, Account, SupportingDocument])
