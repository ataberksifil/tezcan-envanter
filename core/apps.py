from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from core.ui_session import remember_role_label

        user_logged_in.connect(remember_role_label, dispatch_uid='core.remember_role_label')
