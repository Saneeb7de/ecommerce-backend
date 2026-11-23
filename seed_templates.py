# import os
# import django

# # 1. Initialize Django environment
# os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
# django.setup()

# from notifications.models import EmailTemplate

# # 2. Define initial templates
# templates_data = [
#     {
#         'template_key': EmailTemplate.TemplateKey.ORDER_CONFIRMATION,
#         'name': 'Order Confirmation Email',
#         'subject': 'Order Confirmed: #{{ order.order_number }}',
#         'available_variables': 'order, customer_name, items, shipping_address',
#         'body_html': """<div style="font-family: Arial, sans-serif; max-width: 540px; margin: auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 8px;">
#   <h2 style="color: #111827;">Order Confirmed! 🎉</h2>
#   <p>Hello <strong>{{ customer_name }}</strong>, thank you for shopping with us! Your order <strong>#{{ order.order_number }}</strong> has been placed.</p>
#   <div style="background: #f9fafb; padding: 12px; border-radius: 6px; margin: 15px 0;">
#     <p style="margin: 4px 0;"><strong>Payment:</strong> {{ order.get_payment_method_display }} ({{ order.get_payment_status_display }})</p>
#     <p style="margin: 4px 0;"><strong>Total:</strong> ₹{{ order.grand_total }}</p>
#   </div>
#   <p style="color: #6b7280; font-size: 13px;">Track your order anytime from your customer account dashboard.</p>
# </div>""",
#         'body_text': 'Hi {{ customer_name }}, your order #{{ order.order_number }} for ₹{{ order.grand_total }} has been confirmed.'
#     },
#     {
#         'template_key': EmailTemplate.TemplateKey.ORDER_SHIPPED,
#         'name': 'Order Shipped Email',
#         'subject': 'Your Order #{{ order.order_number }} has been Shipped! 🚀',
#         'available_variables': 'order, customer_name',
#         'body_html': """<div style="font-family: Arial, sans-serif; max-width: 540px; margin: auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 8px;">
#   <h2 style="color: #1d4ed8;">Order Shipped! 🚀</h2>
#   <p>Hello <strong>{{ customer_name }}</strong>, your order <strong>#{{ order.order_number }}</strong> is on its way.</p>
#   <div style="background: #eff6ff; padding: 12px; border-radius: 6px; margin: 15px 0;">
#     <p style="margin: 4px 0;"><strong>Carrier:</strong> {{ order.carrier_name|default:"Courier Partner" }}</p>
#     <p style="margin: 4px 0;"><strong>Tracking Number:</strong> {{ order.tracking_number|default:"Pending" }}</p>
#   </div>
# </div>""",
#         'body_text': 'Hi {{ customer_name }}, your order #{{ order.order_number }} has been shipped via {{ order.carrier_name }}. Tracking: {{ order.tracking_number }}.'
#     },
#     {
#         'template_key': EmailTemplate.TemplateKey.ORDER_DELIVERED,
#         'name': 'Order Delivered Email',
#         'subject': 'Delivered: Order #{{ order.order_number }} 🎉',
#         'available_variables': 'order, customer_name',
#         'body_html': """<div style="font-family: Arial, sans-serif; max-width: 540px; margin: auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 8px;">
#   <h2 style="color: #166534;">Delivered! 🎉</h2>
#   <p>Hello <strong>{{ customer_name }}</strong>, your order <strong>#{{ order.order_number }}</strong> has been delivered.</p>
#   <div style="background: #f0fdf4; border: 1px solid #bbf7d0; padding: 12px; border-radius: 6px; margin: 15px 0;">
#     <strong style="color: #166534;">7-Day Return Policy:</strong>
#     <p style="margin: 4px 0; font-size: 13px; color: #15803d;">You can submit a return request from your order dashboard within 7 days.</p>
#   </div>
# </div>""",
#         'body_text': 'Hi {{ customer_name }}, your order #{{ order.order_number }} has been delivered. You have a 7-day return window.'
#     },
#     {
#         'template_key': EmailTemplate.TemplateKey.ORDER_CANCELLED,
#         'name': 'Order Cancelled Email',
#         'subject': 'Order Cancelled: #{{ order.order_number }}',
#         'available_variables': 'order, customer_name',
#         'body_html': """<div style="font-family: Arial, sans-serif; max-width: 540px; margin: auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 8px;">
#   <h2 style="color: #b91c1c;">Order Cancelled</h2>
#   <p>Hello <strong>{{ customer_name }}</strong>, your order <strong>#{{ order.order_number }}</strong> has been cancelled.</p>
#   <p style="color: #4b5563;">Reason: {{ order.cancellation_reason|default:"Requested by customer" }}</p>
# </div>""",
#         'body_text': 'Hi {{ customer_name }}, your order #{{ order.order_number }} was cancelled. Reason: {{ order.cancellation_reason }}.'
#     }
# ]

# # 3. Create or update records in PostgreSQL
# for item in templates_data:
#     key = item.pop('template_key')
#     obj, created = EmailTemplate.objects.update_or_create(
#         template_key=key,
#         defaults=item
#     )
#     status_str = "Created" if created else "Updated"
#     print(f"[{status_str}] {obj.name} ({key})")

# print("\nAll default email templates successfully seeded!")


import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from notifications.models import EmailTemplate

extra_templates = [
    {
        'template_key': EmailTemplate.TemplateKey.RETURN_STATUS,
        'name': 'Return Request Decision Email',
        'subject': 'Return Request {{ return_status|title }}: #{{ order.order_number }}',
        'available_variables': 'order, customer_name, return_status, admin_notes',
        'body_html': """<div style="font-family: Arial, sans-serif; max-width: 540px; margin: auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 8px;">
  <h2 style="color: #111827;">Return Request {{ return_status|title }}</h2>
  <p>Hello <strong>{{ customer_name }}</strong>, your return request for order <strong>#{{ order.order_number }}</strong> has been reviewed.</p>
  <div style="background: #f9fafb; padding: 12px; border-radius: 6px; margin: 15px 0;">
    <p style="margin: 4px 0;"><strong>Status:</strong> {{ return_status|title }}</p>
    {% if admin_notes %}
    <p style="margin: 4px 0;"><strong>Admin Remarks:</strong> {{ admin_notes }}</p>
    {% endif %}
  </div>
  <p style="color: #6b7280; font-size: 13px;">If your return was approved, a refund has been credited to your store wallet.</p>
</div>""",
        'body_text': 'Hi {{ customer_name }}, your return request for order #{{ order.order_number }} is {{ return_status }}. Remarks: {{ admin_notes }}'
    },
    {
        'template_key': EmailTemplate.TemplateKey.WALLET_TOPUP,
        'name': 'Wallet Top-Up Email',
        'subject': 'Wallet Credited: ₹{{ amount }} 💳',
        'available_variables': 'customer_name, amount, balance, reference_id',
        'body_html': """<div style="font-family: Arial, sans-serif; max-width: 540px; margin: auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 8px;">
  <h2 style="color: #166534;">Wallet Top-Up Successful 💳</h2>
  <p>Hello <strong>{{ customer_name }}</strong>, your wallet has been credited with ₹<strong>{{ amount }}</strong>.</p>
  <div style="background: #f0fdf4; border: 1px solid #bbf7d0; padding: 12px; border-radius: 6px; margin: 15px 0;">
    <p style="margin: 4px 0;"><strong>Amount Added:</strong> ₹{{ amount }}</p>
    <p style="margin: 4px 0;"><strong>Updated Balance:</strong> ₹{{ balance }}</p>
    <p style="margin: 4px 0; font-size: 12px; color: #6b7280;"><strong>Ref ID:</strong> {{ reference_id }}</p>
  </div>
</div>""",
        'body_text': 'Hi {{ customer_name }}, ₹{{ amount }} was added to your wallet. Current balance: ₹{{ balance }}.'
    }
]

for item in extra_templates:
    key = item.pop('template_key')
    obj, created = EmailTemplate.objects.update_or_create(template_key=key, defaults=item)
    print(f"[{'Created' if created else 'Updated'}] {obj.name} ({key})")

print("All extra email templates seeded!")