"""Application Insights setup. Call init_telemetry() before other imports."""
import logging
import os

from azure.monitor.opentelemetry import configure_azure_monitor


def init_telemetry() -> bool:
    connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        logging.getLogger(__name__).warning(
            "APPLICATIONINSIGHTS_CONNECTION_STRING is not set, so telemetry is off."
        )
        return False
    configure_azure_monitor(connection_string=connection_string)
    return True
