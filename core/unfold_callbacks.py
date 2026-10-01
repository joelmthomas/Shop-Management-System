from .models import Part


def low_stock_badge(request):
    """Number shown next to Inventory in the sidebar: parts at or below their reorder level."""
    from django.db.models import F

    count = Part.objects.filter(quantity__lte=F("reorder_level")).count()
    return count or None