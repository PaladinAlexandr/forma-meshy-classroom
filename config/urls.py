from django.contrib import admin
from django.contrib.auth.views import LogoutView, PasswordChangeView, PasswordChangeDoneView
from django.urls import path, include
from studio.views import StudioLoginView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', StudioLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('password/', PasswordChangeView.as_view(template_name='password.html', success_url='/password/done/'), name='password_change'),
    path('password/done/', PasswordChangeDoneView.as_view(template_name='password_done.html'), name='password_change_done'),
    path('', include('studio.urls')),
]
