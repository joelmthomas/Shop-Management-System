from django.contrib.auth.views import LoginView, LogoutView, redirect_to_login
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy

from .access import home_for, role_of


class RoleLoginView(LoginView):
    """Sign-in page. Everyone lands on the screen that fits their role."""
    template_name = "core/staff/login.html"
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        # someone signed in with no role must not be bounced around in a loop
        self.redirect_authenticated_user = role_of(request.user) is not None
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return self.get_redirect_url() or reverse("role_home")


class RoleLogoutView(LogoutView):
    next_page = reverse_lazy("role_login")


def go(request):
    """Send the signed-in user to their own home screen."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), reverse("role_login"))
    role = role_of(request.user)
    if not role:
        return render(request, "core/staff/denied.html", {"role": None, "home": ""}, status=403)
    return redirect(home_for(role))
