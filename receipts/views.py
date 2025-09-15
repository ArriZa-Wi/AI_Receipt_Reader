from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import TemplateView, FormView, ListView, DetailView

from django.core.files.base import ContentFile
import csv
from io import StringIO
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
import json
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View
import re


def send_file_to_n8n(file_field, webhook_path=None):
    """Send a Django FileField (or path) to the configured n8n webhook as multipart/form-data.

    Returns a requests.Response-like object. Raises RuntimeError if `requests` isn't available.
    """
    if requests is None:
        raise RuntimeError('The "requests" library is required to send files to n8n. Install it with "pip install requests"')

    url = webhook_path or getattr(settings, 'N8N_WEBHOOK_URL', None)
    if not url:
        raise ValueError('N8N_WEBHOOK_URL not configured in settings')

    # Accept either a FileField-like object or a filesystem path
    if hasattr(file_field, 'open'):
        file_field.open('rb')
        fileobj = getattr(file_field, 'file', file_field)
        filename = getattr(file_field, 'name', 'upload')
    else:
        fileobj = open(file_field, 'rb')
        filename = file_field

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

# Landing / Home page
class LandingPageView(TemplateView):
    template_name = "landing/index.html"
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Provide the login form to the template
        context['login_form'] = AuthenticationForm()
        return context


class SignUpView(FormView):
    template_name = "receipts/signup.html"
    form_class = SignUpForm
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        form.save()
        return super().form_valid(form)


class HomePageView(LoginRequiredMixin, FormView):
    template_name = "home/home.html"
    form_class = CaseUploadForm
    success_url = reverse_lazy('home_signedin')

    def form_valid(self, form):
        case = form.save(commit=False)
        case.user = self.request.user
        case.save()
        try:
            resp = send_file_to_n8n(case.receipt_image)
        except Exception as exc:
            messages.error(self.request, f"Failed to send to n8n: {exc}")
            return super().form_valid(form)

        if getattr(resp, 'status_code', None) == 200 and getattr(resp, 'text', None):
            csv_text = resp.text
            case.csv_file.save(f"case_{case.id}.csv", ContentFile(csv_text))
            case.processed = True
            case.save()
            messages.success(self.request, "Receipt uploaded and processed successfully!")
        else:
            messages.warning(self.request, f"Uploaded but n8n returned {getattr(resp, 'status_code', 'unknown')}")

        return super().form_valid(form)


class SendReceiptToN8nView(LoginRequiredMixin, View):
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

        # Otherwise, trigger async processing in n8n and instruct user to check back
        try:
            resp = send_file_to_n8n(case.receipt_image)
        except Exception as exc:
            messages.error(request, f"Failed to send to n8n: {exc}")
            return JsonResponse({'error': str(exc)}, status=500)

        # We expect n8n to start a workflow and callback when done. Inform the user.
        messages.info(request, "Processing started — the CSV will be available when n8n finishes.")
        # Redirect back to case detail where the CSV button will serve the file when ready
        from django.shortcuts import redirect
        return redirect('case_detail', pk=case.id)


@method_decorator(csrf_exempt, name='dispatch')
class N8nCallbackView(View):
    """Endpoint for n8n to POST processing results.

    Expected JSON body: { "case_id": <id>, "csv": "...csv text..." }
    Or multipart with a file field named 'file' (CSV file) and form field 'case_id'.

    If `N8N_CALLBACK_SECRET` is set in settings, the request must include header
    `X-N8N-SECRET` with that secret value.
    """
    def post(self, request):
        # Optional secret check
        secret = getattr(settings, 'N8N_CALLBACK_SECRET', None)
        if secret:
            header = request.META.get('HTTP_X_N8N_SECRET')
            if header != secret:
                return JsonResponse({'error': 'invalid secret'}, status=403)

        # Try JSON first. New n8n format is expected to be a list of objects like:
        # [ { "success": true, "data": { "merchant": "...", "date": "...", "total": "..." } } ]
        case_id = None
        csv_text = None

        if request.content_type == 'application/json':
            try:
                payload = json.loads(request.body.decode('utf-8'))
            except Exception:
                payload = None

            # payload might be a dict (legacy) or a list (new format)
            if isinstance(payload, dict):
                # legacy single-object JSON
                case_id = payload.get('case_id')
                csv_text = payload.get('csv')
            elif isinstance(payload, list) and len(payload) > 0:
                def _sanitize_key(k: str) -> str:
                    # remove surrounding quotes, braces, colons, and whitespace
                    if not isinstance(k, str):
                        k = str(k)
                    k = k.strip()
                    # remove leading/trailing braces or quotes
                    k = re.sub(r'^[\{\}\s\'"]+|[\{\}\s\'"]+$', '', k)
                    # replace inner spaces with underscore
                    k = k.replace(' ', '_')
                    return k

                def _sanitize_value(v: object) -> str:
                    if v is None:
                        return ''
                    s = str(v).strip()
                    # strip wrapping braces/quotes/colons if present
                    s = re.sub(r'^[:\{\}\s\'"]+|[:\{\}\s\'"]+$', '', s)
                    return s

                # # Helper to detect and parse stringified data like: data:{"merchant":"Walmart"}
                # def _maybe_parse_stringified_data(s: str):
                #     if not isinstance(s, str):
                #         return None
                #     s_strip = s.strip()
                #     # if it looks like a JSON object, attempt to parse
                #     if (s_strip.startswith('{') and s_strip.endswith('}')) or ('"' in s_strip and ':' in s_strip):
                #         try:
                #             return json.loads(s_strip)
                #         except Exception:
                #             # try to recover simple key:value string pairs like data: {merchant: Walmart}
                #             try:
                #                 # remove leading 'data:' if present
                #                 s2 = re.sub(r'^\s*data\s*:\s*', '', s_strip, flags=re.IGNORECASE)
                #                 # convert key: value; pairs separated by commas
                #                 parts = [p.strip() for p in re.split(r',(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)', s2) if p.strip()]
                #                 out = {}
                #                 for p in parts:
                #                     if ':' in p:
                #                         k, v = p.split(':', 1)
                #                         out[_sanitize_key(k)] = _sanitize_value(v)
                #                 return out
                #             except Exception:
                #                 return None
                    # return None
                # new n8n structure: list of { success: bool, data: {...}, case_id?: id }


                rows = []
                headers = set()
                found_case_id = None


                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    # validate success key
                    if 'success' in item and item.get('success') is not True:
                        return JsonResponse({'error': 'n8n reported failure', 'item': item}, status=400)

                    data_obj = item.get('data')
                    if not isinstance(data_obj, dict):
                        continue

                    rows.append(data_obj)
                    headers.update(data_obj.keys())

                    if not found_case_id and 'case_id' in item:
                        found_case_id = item.get('case_id')

                # Build CSV text cleanly with DictWriter
                # if rows and headers:
                #     output = StringIO()
                #     writer = csv.DictWriter(output, fieldnames=list(headers))
                #     writer.writeheader()
                #     for r in rows:
                #         writer.writerow(r)
                #     csv_text = output.getvalue()

                if rows:
                    # 1) stable header order: use the first row's keys, then add any extras (sorted)
                    first = list(rows[0].keys())
                    extras = sorted({k for r in rows for k in r.keys()} - set(first))
                    ordered_headers = first + extras
                
                    buf = io.StringIO()
                    writer = csv.DictWriter(
                        buf,
                        fieldnames=ordered_headers,
                        extrasaction="ignore",   # ignore unexpected keys safely
                        lineterminator="\n"      # avoid blank lines on Windows
                    )
                    writer.writeheader()
                    for r in rows:
                        writer.writerow({k: (r.get(k, "") if r.get(k, "") is not None else "") for k in ordered_headers})
                    csv_text = buf.getvalue()

                if not case_id and found_case_id:
                    case_id = found_case_id

        # If not JSON or CSV not set, check form or files (backwards compatibility)
        if not case_id:
            case_id = request.POST.get('case_id')
        if not csv_text and 'csv' in request.POST:
            csv_text = request.POST.get('csv')
        if not csv_text and 'file' in request.FILES:
            uploaded = request.FILES['file']
            try:
                csv_text = uploaded.read().decode('utf-8')
            except Exception:
                csv_text = None

        if not case_id:
            return JsonResponse({'error': 'case_id missing'}, status=400)

        try:
            case = Case.objects.get(pk=int(case_id))
        except Case.DoesNotExist:
            return JsonResponse({'error': 'case not found'}, status=404)

        if not csv_text:
            return JsonResponse({'error': 'no csv found in payload'}, status=400)

        # Save CSV to case
        case.csv_file.save(f"case_{case.id}.csv", ContentFile(csv_text))
        case.processed = True
        case.save()

        return JsonResponse({'status': 'ok'})

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
