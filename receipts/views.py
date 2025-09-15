from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import TemplateView, FormView, ListView, DetailView

from django.core.files.base import ContentFile

from .forms import SignUpForm, CaseUploadForm
from .models import Case
import csv
try:
    import requests
except Exception:
    requests = None
from django.conf import settings
from django.http import JsonResponse
from django.http import HttpResponse, FileResponse
import os
from django.views import View

# Landing / Home page
class LandingPageView(TemplateView):
    template_name = "landing/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Provide the login form to the template
        context['login_form'] = AuthenticationForm()
        return context


# Signup page
class SignUpView(FormView):
    template_name = "receipts/signup.html"
    form_class = SignUpForm
    success_url = reverse_lazy("home")  # redirect to landing page after signup

    def form_valid(self, form):
        form.save()  # Do NOT log in automatically
        return super().form_valid(form)

class HomePageView(LoginRequiredMixin, FormView):
    template_name = "home/home.html"
    form_class = CaseUploadForm
    success_url = reverse_lazy('home_signedin')  # reload page after upload

    def form_valid(self, form):
        # Assign the user
        case = form.save(commit=False)
        case.user = self.request.user
        case.save()
        # Send the saved receipt image to n8n for processing (binary upload)
        try:
            resp = send_file_to_n8n(case.receipt_image)
        except Exception as exc:
            messages.error(self.request, f"Failed to send to n8n: {exc}")
            return super().form_valid(form)

        if resp.status_code == 200 and resp.text:
            # assume the webhook returns CSV text in body
            csv_text = resp.text
            case.csv_file.save(f"case_{case.id}.csv", ContentFile(csv_text))
            case.processed = True
            case.save()
            messages.success(self.request, "Receipt uploaded and processed successfully!")
        else:
            messages.warning(self.request, f"Uploaded but n8n returned {resp.status_code}")

        return super().form_valid(form)


def send_file_to_n8n(file_field, webhook_path=None):
    """
    Send a Django FileField (or path) to n8n webhook in binary as multipart/form-data.

    - file_field: an instance of FileField (e.g. case.receipt_image) or a filesystem path string
    - webhook_path: optional path override; if not provided uses settings.N8N_WEBHOOK_URL

    Returns requests.Response
    """
    if requests is None:
        raise RuntimeError('The "requests" library is required to send files to n8n. Install it with "pip install requests"')

    if webhook_path:
        url = webhook_path
    else:
        url = getattr(settings, 'N8N_WEBHOOK_URL', None)
    if not url:
        raise ValueError('N8N_WEBHOOK_URL not configured in settings')

    # Accept either a FileField-like object or a path
    if hasattr(file_field, 'open'):
        # Ensure the field's file is open and use the underlying file object
        file_field.open('rb')
        # FieldFile exposes .file which is a file-like object
        fileobj = getattr(file_field, 'file', file_field)
        filename = getattr(file_field, 'name', 'upload')
    else:
        fileobj = open(file_field, 'rb')
        filename = file_field

    # Guess a content type if possible
    import mimetypes
    content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

    files = {'file': (filename, fileobj, content_type)}
    try:
        try:
            fileobj.seek(0)
        except Exception:
            pass
        resp = requests.post(url, files=files, timeout=30)
    finally:
        try:
            fileobj.close()
        except Exception:
            pass

    return resp


class SendReceiptToN8nView(LoginRequiredMixin, View):
    """POST to this view with a `case_id` to send that case's receipt image to n8n.

    Example: POST /cases/1/send-to-n8n/
    """
    def post(self, request, pk):
        case = Case.objects.filter(pk=pk, user=request.user).first()
        if not case:
            return JsonResponse({'error': 'Case not found'}, status=404)
        if not case.receipt_image:
            return JsonResponse({'error': 'No receipt image for case'}, status=400)

        try:
            resp = send_file_to_n8n(case.receipt_image)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

        return JsonResponse({'status': resp.status_code, 'body': resp.text}, status=200)


class DownloadCSVView(LoginRequiredMixin, View):
    """Return a CSV for a Case. If the CSV is not yet present, request processing from n8n and save the result."""
    def get(self, request, pk):
        case = Case.objects.filter(pk=pk, user=request.user).first()
        if not case:
            return JsonResponse({'error': 'Case not found'}, status=404)

        # If CSV already saved, serve it as attachment
        if case.csv_file:
            try:
                case.csv_file.open('rb')
                filename = os.path.basename(case.csv_file.name)
                response = HttpResponse(case.csv_file.read(), content_type='text/csv')
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                return response
            finally:
                try:
                    case.csv_file.close()
                except Exception:
                    pass

        # Otherwise, send file to n8n to request processing
        try:
            resp = send_file_to_n8n(case.receipt_image)
        except Exception as exc:
            return JsonResponse({'error': str(exc)}, status=500)

        if resp.status_code != 200:
            return JsonResponse({'error': f'n8n returned {resp.status_code}', 'body': resp.text}, status=502)

        # Try to parse JSON response that contains 'csv' field; otherwise use raw text
        csv_text = None
        try:
            data = resp.json()
            if isinstance(data, dict) and 'csv' in data:
                csv_text = data['csv']
            else:
                # if JSON but no csv field, fall back to text
                csv_text = resp.text
        except ValueError:
            csv_text = resp.text

        if not csv_text:
            return JsonResponse({'error': 'No CSV returned by n8n'}, status=502)

        # Save CSV to Case and serve
        case.csv_file.save(f"case_{case.id}.csv", ContentFile(csv_text))
        case.processed = True
        case.save()

        response = HttpResponse(csv_text, content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="case_{case.id}.csv"'
        return response

class CaseListView(LoginRequiredMixin, ListView):
    model = Case
    template_name = "cases/case_list.html"
    context_object_name = "cases"

    def get_queryset(self):
        return self.request.user.cases.all().order_by('-created_at')


class CaseDetailView(LoginRequiredMixin, DetailView):
    model = Case
    template_name = "cases/case_detail.html"
    context_object_name = "case"

    def get_queryset(self):
        return self.request.user.cases.all()  # only allow owner to see their cases
