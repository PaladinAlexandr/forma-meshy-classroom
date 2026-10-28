from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('health/', views.health, name='health'),
    path('demo/<str:role>/', views.demo_login, name='demo_login'),
    path('new/', views.new_request, name='new'),
    path('requests/<uuid:pk>/', views.detail, name='detail'),
    path('requests/<uuid:pk>/review/', views.review, name='review'),
    path('requests/<uuid:pk>/choose-image/', views.choose_image, name='choose_image'),
    path('requests/<uuid:pk>/animate/', views.animate, name='animate'),
    path('animation-files/<uuid:pk>/<str:kind>/', views.animation_file, name='animation_file'),
    path('requests/<uuid:pk>/reconcile/', views.reconcile, name='reconcile'),
    path('files/<uuid:pk>/<str:kind>/', views.private_file, name='file'),
    path('updates/', views.updates, name='updates'),
    path('students/', views.students, name='students'),
    path('settings/', views.center_settings, name='settings'),
]
