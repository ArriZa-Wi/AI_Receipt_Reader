# receipts/urls.py
from django.urls import path
from .views import LandingPageView, SignUpView, HomePageView, CaseListView, CaseDetailView

urlpatterns = [
    path('', LandingPageView.as_view(), name='home'),
    path('signup/', SignUpView.as_view(), name='signup'),
    path('home/', HomePageView.as_view(), name='home_signedin'),
    path('cases/', CaseListView.as_view(), name='case_list'),
    path('cases/<int:pk>/', CaseDetailView.as_view(), name='case_detail'),
]
