from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

class StandardResultsSetPagination(PageNumberPagination):
    """
    Standard pagination for API lists:
    Default: 10 items per page
    Client can customize: ?page_size=20 (up to max 100)
    """

    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100