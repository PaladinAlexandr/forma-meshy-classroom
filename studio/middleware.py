import hashlib
from django.core.cache import cache
from django.http import HttpResponse


class PrivateResponsesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'POST' and request.path in ('/login/', '/admin/login/'):
            name = request.POST.get('username', '').casefold()[:150]
            key = 'login:' + hashlib.sha256(name.encode()).hexdigest()
            cache.add(key, 0, timeout=300)
            try:
                attempts = cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=300)
                attempts = 1
            if attempts > 12:
                response = HttpResponse('Слишком много попыток входа. Попробуйте через 5 минут.', status=429, content_type='text/plain; charset=utf-8')
                response['Retry-After'] = '300'
                response['Cache-Control'] = 'no-store'
                return response
        response = self.get_response(request)
        if not request.path.startswith('/static/'):
            response['Cache-Control'] = 'private, no-store'
        return response
