from django.contrib import admin
from .models import Preset, Policy, Request, Event

admin.site.site_header = 'FORMA · Администрация центра'
admin.site.site_title = 'FORMA'
admin.site.index_title = 'Управление мастерской'

@admin.register(Preset)
class PresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'active']
    list_filter = ['active']

@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not Policy.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ['title', 'student', 'status', 'created_at', 'consumed_credits']
    list_filter = ['status', 'provider_mode']
    search_fields = ['title', 'student__username']
    readonly_fields = [f.name for f in Request._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['request', 'text', 'created_at']
    readonly_fields = ['request', 'actor', 'text', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
