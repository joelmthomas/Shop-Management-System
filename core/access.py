"""Who may see which screens.

Three roles:
  owner  = everything: all screens, the Users page and the Django admin (a superuser)
  staff  = the front desk screens under /staff/
  tech   = the phone and iPad screens under /tech/

Staff and tech roles are Django groups called "Staff" and "Tech". An existing account that is
marked "Staff status" but has no group keeps working as front desk, so nobody is locked out.
"""
from functools import wraps

from django.contrib.auth.models import Group
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render
from django.urls import reverse

OWNER, STAFF, TECH = "owner", "staff", "tech"
GROUP_NAMES = {STAFF: "Staff", TECH: "Tech"}
ROLE_LABELS = {
    OWNER: "Everything (owner)",
    STAFF: "Staff (front desk screens)",
    TECH: "Tech (phone and iPad screens)",
}


def role_of(user):
    """The user's role, or None if they are signed out, switched off, or have no role."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    return stored_role(user)


def stored_role(user):
    """The role set on the account, whether or not it is switched on."""
    if user.is_superuser:
        return OWNER
    names = set(user.groups.values_list("name", flat=True))
    if GROUP_NAMES[STAFF] in names:
        return STAFF
    if GROUP_NAMES[TECH] in names:
        return TECH
    return STAFF if user.is_staff else None


def apply_role(user, role):
    """Give a user exactly one role."""
    groups = {r: Group.objects.get_or_create(name=n)[0] for r, n in GROUP_NAMES.items()}
    user.groups.remove(*groups.values())
    if role == OWNER:
        user.is_superuser = True
        user.is_staff = True  # needed to open the Django admin
    else:
        user.is_superuser = False
        user.is_staff = False  # these roles never need the Django admin
        user.groups.add(groups[role])
    user.save()


def home_for(role):
    return reverse("tech_jobs") if role == TECH else reverse("staff_today")


def role_required(*allowed):
    """Allow these roles (owners are always allowed). Others see a friendly 'no access' page."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            role = role_of(request.user)
            request.role = role
            if role is None and not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), reverse("role_login"))
            if role != OWNER and role not in allowed:
                return render(request, "core/staff/denied.html", {"role": role, "home": home_for(role) if role else ""}, status=403)
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


staff_screen = role_required(STAFF)
staff_or_tech = role_required(STAFF, TECH)  # shared helpers such as the VIN lookup
tech_screen = role_required(TECH)
owner_screen = role_required()
