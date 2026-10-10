"""The Users page (owners only): add people, set their role, turn accounts off, reset passwords."""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .access import OWNER, ROLE_LABELS, TECH, apply_role, owner_screen, stored_role

User = get_user_model()


class NewUserForm(forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    role = forms.ChoiceField(choices=[(r, l) for r, l in ROLE_LABELS.items()])
    password = forms.CharField()
    password2 = forms.CharField()

    def clean_username(self):
        name = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=name).exists():
            raise forms.ValidationError("That username is already taken.")
        return name

    def clean(self):
        data = super().clean()
        pw, pw2 = data.get("password"), data.get("password2")
        if pw and pw2 and pw != pw2:
            self.add_error("password2", "The two passwords do not match.")
        elif pw:
            probe = User(username=data.get("username", ""), first_name=data.get("first_name", ""),
                         last_name=data.get("last_name", ""), email=data.get("email", ""))
            try:
                validate_password(pw, probe)
            except ValidationError as e:
                self.add_error("password", e)
        return data


def other_active_owners(user):
    return User.objects.filter(is_superuser=True, is_active=True).exclude(pk=user.pk).exists()


def display_name(u):
    return (f"{u.first_name} {u.last_name}".strip()) or u.username


@owner_screen
def users(request):
    form = None
    if request.method == "POST":
        do = request.POST.get("do")
        here = reverse("staff_users")

        if do == "add":
            form = NewUserForm(request.POST)
            if form.is_valid():
                d = form.cleaned_data
                u = User.objects.create_user(
                    d["username"], email=d["email"], password=d["password"],
                    first_name=d["first_name"], last_name=d["last_name"],
                )
                apply_role(u, d["role"])
                messages.success(request, f"Added {display_name(u)} as {ROLE_LABELS[d['role']]}. They sign in at /signin/ with the username and password you set.")
                return redirect(here)
            messages.error(request, "That person was not added. Check the red messages in the form.")

        else:
            target = get_object_or_404(User, pk=request.POST.get("user_id") or 0)
            me = target.pk == request.user.pk
            if do == "role":
                new = request.POST.get("role")
                if new not in ROLE_LABELS:
                    messages.error(request, "Pick a role from the list.")
                elif me and new != OWNER:
                    messages.error(request, "You cannot take away your own owner access. Ask another owner to change it.")
                elif target.is_superuser and new != OWNER and not other_active_owners(target):
                    messages.error(request, "There must always be at least one active owner.")
                else:
                    apply_role(target, new)
                    messages.success(request, f"{display_name(target)} is now {ROLE_LABELS[new]}.")
            elif do == "active":
                turn_on = request.POST.get("active") == "1"
                if me and not turn_on:
                    messages.error(request, "You cannot turn off your own account.")
                elif not turn_on and target.is_superuser and not other_active_owners(target):
                    messages.error(request, "There must always be at least one active owner.")
                else:
                    target.is_active = turn_on
                    target.save(update_fields=["is_active"])
                    messages.success(request, f"{display_name(target)} is {'on' if turn_on else 'turned off and cannot sign in'}.")
            elif do == "password":
                pw, pw2 = request.POST.get("password", ""), request.POST.get("password2", "")
                if not pw or pw != pw2:
                    messages.error(request, f"Password for {display_name(target)} not changed: the two boxes must match.")
                else:
                    try:
                        validate_password(pw, target)
                    except ValidationError as e:
                        messages.error(request, "Password not changed: " + " ".join(e.messages))
                    else:
                        target.set_password(pw)
                        target.save(update_fields=["password"])
                        messages.success(request, f"New password set for {display_name(target)}.")
            return redirect(here)

    rows = []
    for u in User.objects.order_by("-is_active", "username"):
        r = stored_role(u)
        rows.append({
            "obj": u, "name": display_name(u), "role": r or "none", "role_label": ROLE_LABELS.get(r, "No access"),
            "me": u.pk == request.user.pk,
            "last": u.last_login.strftime("%b %-d, %Y") if u.last_login else "never",
        })
    fields = ("username", "first_name", "last_name", "email", "role")
    blank = {"role": TECH}
    return render(request, "core/staff/users.html", {
        "page": "users", "rows": rows, "form": form, "roles": list(ROLE_LABELS.items()),
        "new": {k: form.data.get(k, "") for k in fields} if form is not None else blank,
    })
