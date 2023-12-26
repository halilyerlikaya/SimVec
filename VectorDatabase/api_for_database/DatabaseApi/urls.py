from django.urls import path
from .views import image_based_search, text_based_search, image_embedding_and_storage

urlpatterns = [
    path('api/image-based-search', image_based_search, name='image_based_search'),
    path('api/text-based-search', text_based_search, name='text_based_search'),
    path('api/image-embedding-and-storage', image_embedding_and_storage, name='image_embedding_and_storage'),
]
