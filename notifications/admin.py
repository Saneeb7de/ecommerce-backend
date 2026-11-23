from django.contrib import admin
from .models import EmailTemplate


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'template_key', 'subject', 'is_active', 'updated_at')
    list_editable = ('is_active',)
    search_fields = ('name', 'subject', 'body_html')
    readonly_fields = ('template_key', 'available_variables', 'updated_at')
    fieldsets = (
        ('Template Configuration', {
            'fields': ('template_key', 'name', 'is_active', 'available_variables')
        }),
        ('Email Content (Live Editable Without Server Restart)', {
            'description': 'Edit subject and HTML body. Any changes take effect immediately on next email sent.',
            'fields': ('subject', 'body_html', 'body_text')
        }),
        ('Audit', {
            'fields': ('updated_at',)
        }),
    )