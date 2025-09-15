from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import TemplateView, FormView, ListView, DetailView

from django.core.files.base import ContentFile

from .forms import SignUpForm, CaseUploadForm
from .models import Case
import csv

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

        # TODO: Send case.receipt_image to n8n for processing
        # Let's assume n8n returns CSV-formatted text
        csv_text = "merchant,item,price\nExample Store,Item1,9.99\nExample Store,Item2,5.50"

        # Save CSV file in the Case instance
        case.csv_file.save(f"case_{case.id}.csv", ContentFile(csv_text))
        case.processed = True
        case.save()
        messages.success(self.request, "Receipt uploaded successfully!")  # <-- confirmation

        return super().form_valid(form)

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
