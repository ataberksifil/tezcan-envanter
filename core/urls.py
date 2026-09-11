from django.urls import path

from . import views

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('management/', views.ManagementView.as_view(), name='management'),
    path('health/', views.health, name='health'),
]
