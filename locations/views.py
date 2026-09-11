from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from locations.forms import LocationForm
from locations.models import Location
from locations.services.locations import (
    create_location,
    set_location_active,
    update_location,
)

LOCATION_LIST_PAGE_SIZE = 50
STATUS_ALL = "all"
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
ALLOWED_STATUS_FILTERS = frozenset({STATUS_ALL, STATUS_ACTIVE, STATUS_INACTIVE})
STOCK_ALL = "all"
STOCK_YES = "yes"
STOCK_NO = "no"
ALLOWED_STOCK_FILTERS = frozenset({STOCK_ALL, STOCK_YES, STOCK_NO})


def normalize_status_filter(value: str | None) -> str:
    if value in ALLOWED_STATUS_FILTERS:
        return value
    return STATUS_ALL


def normalize_stock_filter(value: str | None) -> str:
    if value in ALLOWED_STOCK_FILTERS:
        return value
    return STOCK_ALL


def _attach_validation_error(form, exc: ValidationError) -> None:
    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            if field == "__all__":
                for error in errors:
                    form.add_error(None, error)
            else:
                for error in errors:
                    form.add_error(field, error)
    else:
        form.add_error(None, exc)


class LocationListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "locations.view_location"
    model = Location
    context_object_name = "locations"
    template_name = "locations/location_list.html"
    paginate_by = LOCATION_LIST_PAGE_SIZE

    def get_queryset(self):
        queryset = Location.objects.select_related("parent").order_by("name", "id")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(Q(name__icontains=query) | Q(code__icontains=query))

        status = self._status_filter()
        if status == STATUS_ACTIVE:
            queryset = queryset.filter(active=True)
        elif status == STATUS_INACTIVE:
            queryset = queryset.filter(active=False)

        stock = self._stock_filter()
        if stock == STOCK_YES:
            queryset = queryset.filter(can_hold_stock=True)
        elif stock == STOCK_NO:
            queryset = queryset.filter(can_hold_stock=False)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "").strip()
        context["status"] = self._status_filter()
        context["stock"] = self._stock_filter()
        return context

    def _status_filter(self) -> str:
        return normalize_status_filter(self.request.GET.get("status", STATUS_ALL))

    def _stock_filter(self) -> str:
        return normalize_stock_filter(self.request.GET.get("stock", STOCK_ALL))


class LocationDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "locations.view_location"
    model = Location
    context_object_name = "location"
    template_name = "locations/location_detail.html"

    def get_queryset(self):
        return Location.objects.select_related("parent")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["children"] = (
            Location.objects.filter(parent_id=self.object.pk)
            .order_by("name", "id")
        )
        return context


class LocationCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    permission_required = "locations.add_location"
    form_class = LocationForm
    template_name = "locations/location_form.html"
    success_url = reverse_lazy("locations:location-list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Yeni lokasyon"
        context["submit_label"] = "Kaydet"
        return context

    def form_valid(self, form):
        parent = form.cleaned_data.get("parent")
        try:
            result = create_location(
                actor=self.request.user,
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                parent_id=parent.pk if parent is not None else None,
                can_hold_stock=form.cleaned_data.get("can_hold_stock", False),
            )
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        self.object = result.location
        messages.success(self.request, "Lokasyon oluşturuldu.")
        return redirect(self.get_success_url())


class LocationUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = "locations.change_location"
    model = Location
    form_class = LocationForm
    template_name = "locations/location_form.html"
    success_url = reverse_lazy("locations:location-list")
    context_object_name = "location"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Lokasyonu düzenle"
        context["submit_label"] = "Kaydet"
        return context

    def form_valid(self, form):
        parent = form.cleaned_data.get("parent")
        try:
            result = update_location(
                actor=self.request.user,
                location_id=self.object.pk,
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                parent_id=parent.pk if parent is not None else None,
                can_hold_stock=form.cleaned_data.get("can_hold_stock", False),
            )
        except Location.DoesNotExist as exc:
            raise Http404("No location found matching the query") from exc
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        self.object = result.location
        if result.changed:
            messages.success(self.request, "Lokasyon güncellendi.")
        else:
            messages.success(self.request, "Değişiklik yapılmadı.")
        return redirect(self.get_success_url())


class LocationStateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "locations.change_location"
    http_method_names = ["post"]
    target_active: bool
    success_message: str
    noop_message: str

    def post(self, request, pk):
        try:
            result = set_location_active(
                actor=request.user,
                location_id=pk,
                active=self.target_active,
            )
        except Location.DoesNotExist as exc:
            raise Http404("No location found matching the query") from exc
        if result.changed:
            messages.success(request, self.success_message)
        else:
            messages.success(request, self.noop_message)
        return redirect("locations:location-list")


class LocationDeactivateView(LocationStateView):
    target_active = False
    success_message = "Lokasyon pasifleştirildi."
    noop_message = "Lokasyon zaten pasif."


class LocationReactivateView(LocationStateView):
    target_active = True
    success_message = "Lokasyon aktifleştirildi."
    noop_message = "Lokasyon zaten aktif."
