# Generated manually for Phase 2.5A-1 — AuditEvent append-only immutability guard.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
CREATE OR REPLACE FUNCTION audit_auditevent_immutability_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditevent rows are immutable';
END;
$$;

CREATE TRIGGER audit_auditevent_immutability_trg
BEFORE UPDATE OR DELETE ON audit_auditevent
FOR EACH ROW
EXECUTE PROCEDURE audit_auditevent_immutability_guard();
""",
            reverse_sql="""
DROP TRIGGER IF EXISTS audit_auditevent_immutability_trg ON audit_auditevent;
DROP FUNCTION IF EXISTS audit_auditevent_immutability_guard();
""",
        ),
    ]
