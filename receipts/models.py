from django.db import models
from django.contrib.auth.models import User

class Case(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='cases')
    receipt_image = models.ImageField(upload_to='receipts/')
    csv_file = models.FileField(upload_to='cases_csv/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)

    def __str__(self):
        return f"Case {self.id} for {self.user.username}"

