# receipts/urls.py
from django.urls import path
from .views import (
    LandingPageView,
    SignUpView,
    HomePageView,
    CaseListView,
    CaseDetailView,
    SendReceiptToN8nView,
    DownloadCSVView,
)

urlpatterns = [
    path('', LandingPageView.as_view(), name='home'),
    path('signup/', SignUpView.as_view(), name='signup'),
    path('home/', HomePageView.as_view(), name='home_signedin'),
    path('cases/', CaseListView.as_view(), name='case_list'),
    path('cases/<int:pk>/', CaseDetailView.as_view(), name='case_detail'),
    path('cases/<int:pk>/send-to-n8n/', SendReceiptToN8nView.as_view(), name='case_send_to_n8n'),
    path('cases/<int:pk>/download-csv/', DownloadCSVView.as_view(), name='case_download_csv'),
]
