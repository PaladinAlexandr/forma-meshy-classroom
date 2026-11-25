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
    path('requests/<uuid:pk>/recover/', views.recover_tasks, name='recover_tasks'),
    path('requests/<uuid:pk>/delete/', views.request_manage, {'operation': 'delete'}, name='request_delete'),
    path('requests/<uuid:pk>/close/', views.request_manage, {'operation': 'close'}, name='request_close'),
    path('requests/<uuid:pk>/restore/', views.request_restore, name='request_restore'),
    path('files/<uuid:pk>/<str:kind>/', views.private_file, name='file'),
    path('updates/', views.updates, name='updates'),
    path('students/', views.students, name='students'),
    path('students/<int:pk>/edit/', views.student_edit, name='student_edit'),
    path('students/<int:pk>/delete/', views.student_delete, name='student_delete'),
    path('settings/', views.center_settings, name='settings'),
]
