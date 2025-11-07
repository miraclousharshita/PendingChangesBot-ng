"""
URL configuration for reviewer project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from pathlib import Path

import yaml
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.cache import never_cache

BASE_DIR = Path(__file__).resolve().parent.parent.parent


@never_cache
def openapi_spec(request, format=None):
    """Serve the OpenAPI spec from swagger.yaml file."""
    swagger_file = BASE_DIR / "swagger.yaml"
    with open(swagger_file, encoding="utf-8") as f:
        content = f.read()
        if format == "yaml" or request.GET.get("format") == "yaml":
            from django.http import HttpResponse

            return HttpResponse(content, content_type="application/yaml")
        f.seek(0)  # Reset file pointer
        swagger_dict = yaml.safe_load(f)
    return JsonResponse(swagger_dict)


@never_cache
def swagger_ui(request):
    """Render Swagger UI with the OpenAPI spec."""
    from django.shortcuts import render

    return render(request, "swagger_ui.html")


@never_cache
def redoc_ui(request):
    """Render ReDoc UI with the OpenAPI spec."""
    from django.shortcuts import render

    return render(request, "redoc_ui.html")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("reviews.urls")),
    path("", include("review_statistics.urls")),
    path("openapi.json", openapi_spec, name="openapi-spec"),
    path("openapi.yaml", openapi_spec, {"format": "yaml"}, name="openapi-spec-yaml"),
    path("swagger/", swagger_ui, name="swagger-ui"),
    path("redoc/", redoc_ui, name="redoc-ui"),
]
