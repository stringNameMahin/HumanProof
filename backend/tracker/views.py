from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import TelemetryEvent
from .serializers import TelemetryEventSerializer


class TelemetryIngestView(APIView):
    """POST a bare JSON array of events, or {"events": [...]}."""

    def post(self, request):
        payload = request.data
        if isinstance(payload, dict):
            if "events" not in payload:
                return Response(
                    {"detail": "Object body must carry an 'events' array."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            payload = payload["events"]
        if not isinstance(payload, list):
            return Response(
                {"detail": "Expected a JSON array of events."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = TelemetryEventSerializer(data=payload, many=True)
        serializer.is_valid(raise_exception=True)
        created = serializer.save()

        return Response({"stored": len(created)}, status=status.HTTP_201_CREATED)

    def get(self, request):
        """Last 50 rows, for checking the pipe without the dashboard."""
        recent = TelemetryEvent.objects.order_by("-id")[:50]
        return Response(TelemetryEventSerializer(recent, many=True).data)
