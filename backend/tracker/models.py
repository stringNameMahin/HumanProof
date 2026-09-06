from django.db import models


class TelemetryEvent(models.Model):
    MOVE = "move"
    CLICK = "click"

    session_id = models.CharField(max_length=64, db_index=True)
    event_type = models.CharField(max_length=16, db_index=True)
    x = models.IntegerField()
    y = models.IntegerField()
    ts = models.BigIntegerField(help_text="Client clock, epoch milliseconds")
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["ts", "id"]

    def __str__(self):
        return f"{self.event_type} ({self.x},{self.y}) @ {self.ts}"
